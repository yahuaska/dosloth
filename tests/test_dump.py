import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from mydos import dump
from mydos.cpu import CPU


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
        self.assertEqual(info["code"].split()[:3], ["cd", "21", "90"])
        self.assertEqual(len(info["code"].split()), 16)
        self.assertEqual(info["program"], str(program))
        self.assertEqual(info["memory_file"], str(program) + ".crash.mem")
        self.assertEqual((self.program_dir / "T.COM.crash.mem").read_bytes(), bytes(self.cpu.mem))
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
        self.assertEqual((self.here / "T.COM.crash.mem").stat().st_size, 1024 * 1024)
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

        with mock.patch("mydos.dump.open", flaky_open, create=True):
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

    def test_the_error_names_its_kind(self):
        self.assertEqual(dump.describe(self.cpu, "x", ValueError("boom"))["error"], "ValueError: boom")


if __name__ == "__main__":
    unittest.main()
