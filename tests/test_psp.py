import os
import subprocess
import sys
import tempfile
import unittest

from mydos import psp
from mydos.cpu import CPU


def word(cpu, seg, off):
    return cpu.rw(seg, off)


class TestPlaces(unittest.TestCase):
    def test_the_default_disk_transfer_area_is_the_last_128_bytes_of_the_256_of_the_psp(self):
        self.assertEqual(psp.DTA_OFFSET, 0x80)
        self.assertEqual(psp.DTA_OFFSET + 128, 0x100)

    def test_the_program_is_loaded_at_a_segment_with_room_for_the_environment_below_it(self):
        self.assertEqual(psp.PSP_SEG, 0x1000)


class TestBuild(unittest.TestCase):
    def build(self, dos_path="C:\\ESETUP.EXE"):
        cpu = CPU()
        psp.build(cpu, 0x1000, 0x0FF0, 0xA000, dos_path)
        return cpu

    def test_the_fields_programs_read(self):
        cpu = self.build()
        self.assertEqual(bytes(cpu.mem[0x10000:0x10002]), b"\xCD\x20")  # int 20h
        self.assertEqual(word(cpu, 0x1000, 0x02), 0xA000)  # the first segment beyond the program's memory
        self.assertEqual(word(cpu, 0x1000, 0x2C), 0x0FF0)  # the environment
        self.assertEqual(word(cpu, 0x1000, 0x32), 20)
        self.assertEqual((word(cpu, 0x1000, 0x34), word(cpu, 0x1000, 0x36)), (0x18, 0x1000))  # the handle table
        self.assertEqual(bytes(cpu.mem[0x10050:0x10053]), b"\xCD\x21\xCB")

    def test_the_handle_table_has_the_standard_handles_open_and_the_rest_free(self):
        cpu = self.build()
        table = bytes(cpu.mem[0x10018:0x10018 + 20])
        self.assertEqual(table[:5], bytes([1, 1, 1, 0, 2]))
        self.assertEqual(table[5:], b"\xFF" * 15)

    def test_the_command_line_is_empty(self):
        cpu = self.build()
        self.assertEqual(bytes(cpu.mem[0x10080:0x10082]), b"\x00\x0D")  # length 0, then a carriage return

    def test_the_default_file_control_blocks_are_blank(self):
        cpu = self.build()
        for fcb in (0x5C, 0x6C):
            self.assertEqual(bytes(cpu.mem[0x10000 + fcb:0x10000 + fcb + 12]), b"\x00" + b" " * 11)

    def test_only_the_psp_is_written_and_it_is_256_bytes(self):
        cpu = self.build()
        self.assertEqual(bytes(cpu.mem[0x10100:0x10200]), bytes(256))  # the program's own area is untouched
        self.assertEqual(bytes(cpu.mem[0x0FEFF:0x0FF00]), b"\0")  # nothing is written below the environment

    def test_the_environment_block(self):
        cpu = self.build("C:\\SOME\\DIR\\ESETUP.EXE")
        env = bytes(cpu.mem[0x0FF00:0x0FF00 + 256])
        strings, rest = env.split(b"\0\0", 1)
        self.assertEqual(strings.split(b"\0"), [b"COMSPEC=C:\\COMMAND.COM", b"PATH=C:\\"])
        self.assertEqual(rest[:2], b"\x01\x00")  # one more string follows
        self.assertEqual(rest[2:].split(b"\0")[0], b"C:\\SOME\\DIR\\ESETUP.EXE")  # the program's name, in full

    def test_the_environment_ends_with_two_zero_bytes_before_the_name(self):
        """A C library finds the end of the strings by looking for an empty one."""
        env = psp.environment("C:\\GAMES\\X.COM")
        self.assertIn(b"\0\0\x01\x00C:\\GAMES\\X.COM\0", env)

    def test_the_name_is_shown_in_capitals(self):
        self.assertIn(b"\x01\x00D:\\DIR\\PROG.EXE\0", psp.environment("d:\\dir\\prog.exe"))


class TestAProgramSeesIt(unittest.TestCase):
    """Run small .COM programs that read the PSP and report what they found as their exit code."""

    def exit_code(self, program):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "t.com")
            with open(path, "wb") as f:
                f.write(program)
            repository = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            environment = {**os.environ, "PYTHONPATH": repository}
            # the program is C:\T.COM: the directory it was written to is the root of the built-in drive C:
            result = subprocess.run([sys.executable, "-m", "mydos", "t.com"], capture_output=True, timeout=30,
                                    cwd=directory, env=environment)
            self.assertEqual(result.stderr, b"", result.stderr)
            return result.returncode

    def test_the_top_of_memory_word(self):
        # mov ax,[0002] ; mov al,ah ; mov ah,4Ch ; int 21h    -> exit code = high byte of A000h
        self.assertEqual(self.exit_code(bytes([0xA1, 0x02, 0x00, 0x88, 0xE0, 0xB4, 0x4C, 0xCD, 0x21])), 0xA0)

    def test_the_environment_is_where_the_psp_says(self):
        # mov es,[002Ch] ; mov al,es:[0] ; mov ah,4Ch ; int 21h  -> the first character of "COMSPEC=", 'C'
        code = bytes([0x8E, 0x06, 0x2C, 0x00, 0x26, 0xA0, 0x00, 0x00, 0xB4, 0x4C, 0xCD, 0x21])
        self.assertEqual(self.exit_code(code), ord("C"))

    def test_the_standard_handles_are_open_in_the_handle_table(self):
        # mov al,[0018h] ; mov ah,4Ch ; int 21h   -> the entry for handle 0
        self.assertEqual(self.exit_code(bytes([0xA0, 0x18, 0x00, 0xB4, 0x4C, 0xCD, 0x21])), 1)


if __name__ == "__main__":
    unittest.main()
