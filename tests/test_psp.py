import os
import subprocess
import sys
import tempfile
import unittest

from dosloth import psp
from dosloth.cpu import CPU


def word(cpu, seg, off):
    return cpu.rw(seg, off)


class TestPlaces(unittest.TestCase):
    def test_the_default_disk_transfer_area_is_the_last_128_bytes_of_the_256_of_the_psp(self):
        self.assertEqual(psp.DTA_OFFSET, 0x80)
        self.assertEqual(psp.DTA_OFFSET + 128, 0x100)

    def test_the_program_is_loaded_at_a_segment_with_room_for_the_environment_below_it(self):
        self.assertEqual(psp.PSP_SEG, 0x1000)


class TestBuild(unittest.TestCase):
    def build(self, dos_path="C:\\ESETUP.EXE", **keywords):
        cpu = CPU()
        psp.build(cpu, 0x1000, 0x0FF0, 0xA000, dos_path, **keywords)
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

    def test_the_command_tail_is_after_the_length_and_before_the_carriage_return(self):
        cpu = self.build(tail=b" 0 GOFORIT SOUND")
        self.assertEqual(bytes(cpu.mem[0x10080:0x10092]), b"\x10 0 GOFORIT SOUND\x0D")
        self.assertEqual(bytes(cpu.mem[0x10092:0x10100]), bytes(0x6E))  # nothing after it: the rest of the PSP is zeros

    def test_the_longest_tail_ends_with_the_carriage_return_in_the_last_byte_of_the_psp(self):
        cpu = self.build(tail=b" " + b"x" * 125)
        self.assertEqual((cpu.mem[0x10080], cpu.mem[0x100FF]), (126, 0x0D))
        self.assertEqual(bytes(cpu.mem[0x10081:0x100FF]), b" " + b"x" * 125)

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


class TestTheTail(unittest.TestCase):
    def test_no_arguments_are_no_tail(self):
        self.assertEqual(psp.tail_of([]), b"")

    def test_the_arguments_are_after_a_space_and_with_one_between_them_as_they_are(self):
        self.assertEqual(psp.tail_of(["0", "GOFORIT", "SOUND"]), b" 0 GOFORIT SOUND")
        self.assertEqual(psp.tail_of(["a/B", "-w", "c d"]), b" a/B -w c d")  # no case change, no quotes
        self.assertEqual(psp.tail_of([""]), b" ")  # an argument that is empty is still a space

    def test_the_characters_are_those_of_the_dos_set(self):
        # e acute and a light shade block, in code page 437
        self.assertEqual(psp.tail_of(["\u00e9\u2591"]), b" \x82\xb0")

    def test_a_character_it_has_not_is_a_value_error_that_names_it(self):
        with self.assertRaisesRegex(ValueError, "has not: '\\u4e2d'"):
            psp.tail_of(["a", "\u4e2d"])

    def test_the_longest_is_126_characters_and_one_more_is_a_value_error_that_says_how_many(self):
        self.assertEqual(len(psp.tail_of(["x" * 125])), 126)
        with self.assertRaisesRegex(ValueError, "127 characters, and DOS takes 126 at most"):
            psp.tail_of(["x" * 126])

    def test_the_command_line_of_the_psp_is_the_length_the_tail_and_a_carriage_return(self):
        self.assertEqual(psp.command_line(b""), b"\x00\r")
        self.assertEqual(psp.command_line(b" ab"), b"\x03 ab\r")


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
            result = subprocess.run([sys.executable, "-m", "dosloth", "t.com"], capture_output=True, timeout=30,
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


    def test_the_parent_is_0_unless_it_is_told(self):
        cpu = CPU()
        psp.build(cpu, 0x1000, 0x0FF0, 0xA000, "C:\\X.COM")
        self.assertEqual(word(cpu, 0x1000, 0x16), 0)
        psp.build(cpu, 0x2000, 0x1FF0, 0xA000, "C:\\X.COM", parent_seg=0x1000)
        self.assertEqual(word(cpu, 0x2000, 0x16), 0x1000)

    def test_the_strings_of_the_environment_can_be_given(self):
        cpu = CPU()
        psp.build(cpu, 0x2000, 0x1FF0, 0xA000, "C:\\X.COM", strings=[b"A=1", b"B=2"])
        self.assertEqual(bytes(cpu.mem[0x1FF00:0x1FF00 + 20]), b"A=1\0B=2\0\0\x01\0C:\\X.COM\0")

    def test_no_strings_at_all_are_just_the_two_zeros_before_the_name(self):
        self.assertEqual(psp.environment("c:\\x.com", []), b"\0\0\x01\0C:\\X.COM\0")

if __name__ == "__main__":
    unittest.main()
