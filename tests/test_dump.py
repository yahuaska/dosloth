import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from dosloth import dump
from dosloth.cpu import CPU, MEMORY_SIZE
from dosloth.flat import Bases


class DumpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.program_dir = self.root / "program"
        self.here = self.root / "here"  # the current directory of the emulator
        self.program_dir.mkdir()
        self.here.mkdir()
        old = os.getcwd()
        os.chdir(self.here)
        self.addCleanup(os.chdir, old)
        self.cpu = CPU()
        self.cpu.r[CPU.AX], self.cpu.r[CPU.SP] = 0x1234, 0x0100
        self.cpu.s[CPU.CS], self.cpu.s[CPU.SS] = 0x1000, 0x2000
        self.cpu.start_ip = 0x0020
        self.cpu.mem[0x10020:0x10023] = b"\xCD\x21\x90"
        self.cpu.mem[0x12100] = 0x77
        self.error = NotImplementedError("INT 21h AH=3F")

    def write(self, path):
        """Write the dump and give back (the base it went to, what was said on the error output)."""
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            base = dump.write(self.cpu, str(path), self.error)
        return base, said.getvalue()

    def read_only(self, directory):
        if os.geteuid() == 0:
            self.skipTest("root can write to a directory that is read-only")
        directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(directory.chmod, stat.S_IRWXU)


class TestWhereItGoes(DumpTest):
    def test_beside_the_program_with_what_the_cpu_was_doing(self):
        program = self.program_dir / "T.COM"
        base, said = self.write(program)
        self.assertEqual(base, str(program))
        info = json.loads((self.program_dir / "T.COM.crash.json").read_text())
        self.assertEqual(info["error"], "NotImplementedError: INT 21h AH=3F")
        self.assertEqual((info["registers"]["AX"], info["segments"]["CS"], info["start_ip"]), ("1234", "1000", "0020"))
        self.assertEqual(list(info["segments"]), ["ES", "CS", "SS", "DS", "FS", "GS"])
        self.assertEqual(info["code"].split()[:3], ["cd", "21", "90"])
        self.assertEqual(len(info["code"].split()), 16)
        self.assertEqual(info["program"], str(program))
        self.assertEqual(info["memory_file"], str(program) + ".crash.mem")
        with open(self.program_dir / "T.COM.crash.mem", "rb") as dump:  # as big as the memory, the same where not zero
            self.assertEqual(os.fstat(dump.fileno()).st_size, len(self.cpu.mem))
            self.assertEqual(dump.read(1024 * 1024), bytes(self.cpu.mem[:1024 * 1024]))
            dump.seek(-4096, os.SEEK_END)
            self.assertEqual(dump.read(), bytes(4096))
        self.assertIn(f"crash dump: {program}.crash.json, {program}.crash.mem", said)
        self.assertNotIn("cannot be written", said)
        self.assertEqual(os.listdir(self.here), [])

    def test_a_program_in_a_directory_that_cannot_be_written_to_gets_its_dump_in_the_current_directory(self):
        self.read_only(self.program_dir)
        program = self.program_dir / "T.COM"
        base, said = self.write(program)
        self.assertEqual(base, str(self.here / "T.COM"))
        info = json.loads((self.here / "T.COM.crash.json").read_text())
        self.assertEqual(info["program"], str(program))  # it still says which program it is of
        self.assertEqual(info["memory_file"], str(self.here / "T.COM.crash.mem"))
        self.assertEqual((self.here / "T.COM.crash.mem").stat().st_size, MEMORY_SIZE)
        self.assertIn(f"crash dump: {self.here}/T.COM.crash.json", said)
        self.assertIn("the directory of the program cannot be written to", said)
        self.assertEqual(os.listdir(self.program_dir), [])

    def test_a_directory_that_is_not_there_is_a_directory_that_cannot_be_written_to_too(self):
        base, said = self.write(self.root / "no" / "such" / "dir" / "E.EXE")
        self.assertEqual(base, str(self.here / "E.EXE"))  # only the name of the file is kept
        self.assertTrue((self.here / "E.EXE.crash.json").exists())
        self.assertIn("cannot be written to", said)

    def test_the_file_name_is_cut_from_a_path_written_with_slashes_backslashes_or_a_drive(self):
        for path in ("no/such/dir/E.EXE", "no/such\\E.EXE", "no/such/C:E.EXE"):  # no/ is not there: it fails
            base, _ = self.write(path)  # the directory is not there, so the dump goes to the current one
            self.assertEqual(os.path.basename(base), "E.EXE", path)
            (self.here / "E.EXE.crash.json").unlink()
            (self.here / "E.EXE.crash.mem").unlink()

    def test_a_dump_that_fails_half_way_leaves_no_half_dump_behind(self):
        from unittest import mock
        real_open = open

        def flaky_open(path, mode="r", *args):
            file = real_open(path, mode, *args)
            if str(path).startswith(str(self.program_dir)) and str(path).endswith(".crash.mem"):
                file.write = mock.Mock(side_effect=OSError(28, "No space left on device"))
            return file

        with mock.patch("dosloth.dump.open", flaky_open, create=True):
            base, said = self.write(self.program_dir / "T.COM")
        self.assertEqual(base, str(self.here / "T.COM"))
        self.assertEqual(os.listdir(self.program_dir), [])  # neither the json nor the empty memory file
        self.assertIn("cannot be written to", said)

    def test_with_nowhere_to_write_there_is_no_dump_and_no_exception_and_both_reasons_are_given(self):
        self.read_only(self.program_dir)
        self.read_only(self.here)
        base, said = self.write(self.program_dir / "T.COM")
        self.assertIsNone(base)
        self.assertIn("no crash dump could be written", said)
        self.assertIn(str(self.program_dir / "T.COM"), said)
        self.assertIn(str(self.here / "T.COM"), said)
        self.assertEqual(os.listdir(self.program_dir) + os.listdir(self.here), [])

    def test_the_program_may_already_be_in_the_current_directory_and_then_it_is_tried_once(self):
        self.read_only(self.here)
        base, said = self.write(self.here / "T.COM")
        self.assertIsNone(base)
        self.assertEqual(said.count("T.COM:"), 1)  # one reason, not the same one twice

    def test_half_a_dump_is_taken_away_before_the_other_place_is_tried(self):
        (self.program_dir / "T.COM.crash.mem").mkdir()  # the memory file cannot be written there
        base, said = self.write(self.program_dir / "T.COM")
        self.assertEqual(base, str(self.here / "T.COM"))
        self.assertFalse((self.program_dir / "T.COM.crash.json").exists())
        self.assertTrue((self.here / "T.COM.crash.json").exists())
        self.assertIn("cannot be written to", said)

    def test_the_old_dump_of_another_crash_is_replaced(self):
        (self.program_dir / "T.COM.crash.json").write_text("old")
        self.write(self.program_dir / "T.COM")
        self.assertEqual(json.loads((self.program_dir / "T.COM.crash.json").read_text())["error"][:5], "NotIm")


class TestDescribe(DumpTest):
    def test_the_stack_and_the_flags(self):
        self.cpu.mem[0x20100:0x20104] = b"\x34\x12\x78\x56"
        self.cpu.flags = 0x0246
        info = dump.describe(self.cpu, "x", self.error)
        self.assertEqual(info["stack"][:2], ["1234", "5678"])
        self.assertEqual((info["flags"], len(info["stack"])), ("0246", 8))

    def test_the_high_halves_of_the_registers_are_said_only_when_a_program_has_put_something_in_them(self):
        self.assertNotIn("high_halves", dump.describe(self.cpu, "x", self.error))
        self.cpu.rh[0], self.cpu.rh[6] = 0xDEAD, 0x0001
        info = dump.describe(self.cpu, "x", self.error)
        self.assertEqual(info["high_halves"], {"EAX": "DEAD", "ECX": "0000", "EDX": "0000", "EBX": "0000",
                                               "ESP": "0000", "EBP": "0000", "ESI": "0001", "EDI": "0000"})

    def test_a_machine_in_real_mode_says_nothing_of_a_flat_one(self):
        self.assertNotIn("flat_mode", dump.describe(self.cpu, "x", self.error))

    def test_a_program_in_the_flat_mode_has_its_selectors_and_widths_said_and_its_stack_read_by_esp(self):
        cpu = self.cpu
        cpu.use_flat_memory(Bases({0x10: 0, 0x18: 0x30000}))
        cpu.ip_mask, cpu.stack32, cpu.code32 = 0xFFFFFFFF, True, False
        cpu.s[CPU.CS], cpu.s[CPU.SS] = 0x18, 0x10
        cpu.set32(CPU.SP, 0x00123450)
        cpu.start_ip = 0x20
        cpu.mem[0x123450:0x123454] = b"\x34\x12\x78\x56"
        cpu.mem[0x30020:0x30023] = b"\xCD\x21\x90"
        info = dump.describe(cpu, "x", self.error)
        self.assertEqual(info["flat_mode"], {"selector_bases": {"0010": "00000000", "0018": "00030000"},
                                             "eip_mask": "FFFFFFFF", "stack32": True, "code32": False})
        self.assertEqual((info["stack"][:2], info["code"].split()[:3]), (["1234", "5678"], ["cd", "21", "90"]))

    def test_a_stack_or_a_code_that_cannot_be_read_is_a_question_mark_and_does_not_fail_the_dump(self):
        cpu = self.cpu
        cpu.use_flat_memory(Bases({0x10: 0}))
        cpu.s[CPU.CS], cpu.s[CPU.SS] = 0x47, 0x10  # the code selector is not in the table
        cpu.stack32 = True
        cpu.set32(CPU.SP, MEMORY_SIZE - 6)  # the stack runs out of the memory: the first three words are there
        info = dump.describe(cpu, "x", self.error)
        self.assertEqual(info["code"], "?")
        self.assertEqual(info["stack"], ["0000", "0000", "0000"] + ["?"] * 5)
        cpu.s[CPU.CS] = 0x10  # the code is at an address that is not in the memory: that is an IndexError
        cpu.start_ip = MEMORY_SIZE - 4
        self.assertEqual(dump.describe(cpu, "x", self.error)["code"], "?")

    def test_the_error_names_its_kind(self):
        self.assertEqual(dump.describe(self.cpu, "x", ValueError("boom"))["error"], "ValueError: boom")


if __name__ == "__main__":
    unittest.main()
