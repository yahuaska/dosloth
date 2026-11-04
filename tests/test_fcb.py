import unittest

from dosloth import dos, fcb
from dosloth.cpu import CPU


def parse(text, control=0, old=b"\x07ABCDEFGHXYZ", drives=(1, 2, 3, 4)):
    """`text` taken apart: the new 12 bytes, how much was used, and the code."""
    return fcb.parse(text, control, old, lambda drive: drive in drives)


class TestParse(unittest.TestCase):
    def test_a_plain_name_is_put_in_capitals_and_padded(self):
        self.assertEqual(parse(b"setup.exe\r"), (b"\0SETUP   EXE", 9, 0))

    def test_a_name_without_extension_has_a_blank_one(self):
        self.assertEqual(parse(b"readme\r"), (b"\0README     ", 6, 0))

    def test_a_dot_with_nothing_after_it_is_a_blank_extension(self):
        self.assertEqual(parse(b"readme.\r"), (b"\0README     ", 7, 0))

    def test_a_drive_comes_as_its_number_and_is_taken_in_either_case(self):
        self.assertEqual(parse(b"C:file.txt\0"), (b"\x03FILE    TXT", 10, 0))
        self.assertEqual(parse(b"a:f\0"), (b"\x01F          ", 3, 0))

    def test_no_drive_is_the_current_one_unless_the_bit_for_keeping_it_is_set(self):
        self.assertEqual(parse(b"f\0")[0][0], 0)
        self.assertEqual(parse(b"f\0", 2)[0][0], 7)
        self.assertEqual(parse(b"b:f\0", 2)[0][0], 2)

    def test_a_drive_that_is_not_there_gives_ff_and_the_name_is_still_taken(self):
        self.assertEqual(parse(b"z:f.t\0"), (b"\x1AF       T  ", 5, 0xFF))

    def test_a_letter_and_a_colon_are_a_drive_only_in_front(self):
        self.assertEqual(parse(b"ab:c\0"), (b"\0AB         ", 2, 0))

    def test_a_star_fills_the_rest_of_the_name_and_of_the_extension_with_question_marks(self):
        self.assertEqual(parse(b"ab*.c*\0"), (b"\0AB??????C??", 6, 1))
        self.assertEqual(parse(b"*.*\0"), (b"\0????????" + b"???", 3, 1))

    def test_a_question_mark_is_kept_and_counts(self):
        self.assertEqual(parse(b"a?c.?\0"), (b"\0A?C     ?  ", 5, 1))

    def test_no_wildcards_in_the_name_with_one_in_the_extension_still_counts(self):
        self.assertEqual(parse(b"abc.x?\0")[2], 1)

    def test_too_many_characters_are_lost_but_passed_over(self):
        self.assertEqual(parse(b"abcdefghijk.xyzw\0"), (b"\0ABCDEFGHXYZ", 16, 0))

    def test_a_name_ends_at_a_separator_and_the_rest_is_left(self):
        for text in (b"a b", b"a,b", b"a;b", b"a=b", b"a+b", b"a/b", b'a"b', b"a[b", b"a]b", b"a<b", b"a>b", b"a|b",
                     b"a\tb", b"a\rb", b"a\\b", b"a\0b"):
            self.assertEqual(parse(text), (b"\0A          ", 1, 0), text)

    def test_nothing_to_take_gives_blanks_or_what_was_there(self):
        self.assertEqual(parse(b"\r"), (b"\0           ", 0, 0))
        self.assertEqual(parse(b"\r", 4)[0], b"\0ABCDEFGH   ")
        self.assertEqual(parse(b"\r", 8)[0], b"\0        XYZ")
        self.assertEqual(parse(b"\r", 12)[0], b"\0ABCDEFGHXYZ")

    def test_a_name_that_is_there_replaces_what_was_kept(self):
        self.assertEqual(parse(b"q\r", 12)[0], b"\0Q       XYZ")
        self.assertEqual(parse(b".q\r", 12)[0], b"\0ABCDEFGHQ  ")
        self.assertEqual(parse(b"q.w\r", 12)[0], b"\0Q       W  ")

    def test_the_extension_alone_is_taken(self):
        self.assertEqual(parse(b".cfg\0"), (b"\0        CFG", 4, 0))

    def test_without_the_bit_for_leaving_the_extension_it_goes_blank_when_there_is_none(self):
        self.assertEqual(parse(b"q\r", 4)[0], b"\0Q          ")
        self.assertEqual(parse(b"q\r", 8)[0], b"\0Q       XYZ")

    def test_leading_blanks_stop_the_parse_unless_the_first_bit_is_set(self):
        self.assertEqual(parse(b"  ab\r"), (b"\0           ", 0, 0))
        self.assertEqual(parse(b"  ab\r", 1), (b"\0AB         ", 4, 0))
        self.assertEqual(parse(b"\t ab\r", 1)[1], 4)

    def test_one_separator_in_front_is_skipped_with_the_first_bit(self):
        self.assertEqual(parse(b" , ab\r", 1), (b"\0AB         ", 5, 0))
        self.assertEqual(parse(b",,ab\r", 1)[1], 1)  # the second one is not skipped: the name is empty
        self.assertEqual(parse(b",ab\r")[1], 0)

    def test_each_of_the_separators_is_skipped_in_front(self):
        for separator in b":.;,=+":
            self.assertEqual(parse(bytes([separator]) + b"ab\r", 1), (b"\0AB         ", 3, 0), chr(separator))

    def test_a_star_in_the_name_counts_when_the_extension_is_plain(self):
        self.assertEqual(parse(b"a*.b\r")[2], 1)
        self.assertEqual(parse(b"a.b*\r")[2], 1)

    def test_every_control_character_ends_a_name(self):
        for byte in range(0x20):
            self.assertEqual(parse(b"a" + bytes([byte]) + b"b"), (b"\0A          ", 1, 0), byte)

    def test_the_drive_is_looked_for_after_the_skipped_part(self):
        self.assertEqual(parse(b" c:x\r", 1), (b"\x03X          ", 4, 0))

    def test_it_does_not_read_past_the_end_of_what_it_is_given(self):
        self.assertEqual(parse(b"abc"), (b"\0ABC        ", 3, 0))
        self.assertEqual(parse(b"a."), (b"\0A          ", 2, 0))
        self.assertEqual(parse(b"c:"), (b"\x03           ", 2, 0))
        self.assertEqual(parse(b"c"), (b"\0C          ", 1, 0))
        self.assertEqual(parse(b""), (b"\0           ", 0, 0))

    def test_non_ascii_characters_are_kept_as_they_are(self):
        self.assertEqual(parse(b"\x84b\0")[0], b"\0\x84B         ")

    def test_the_old_bytes_of_the_fcb_are_not_changed_by_parse(self):
        old = bytearray(b"\x07ABCDEFGHXYZ")
        fcb.parse(b"q\0", 0, old, lambda drive: True)
        self.assertEqual(bytes(old), b"\x07ABCDEFGHXYZ")


class TestParseFileNameFunction(unittest.TestCase):
    """INT 21h AH=29h: the text at DS:SI goes into the FCB at ES:DI."""

    def setUp(self):
        self.cpu = CPU()
        dos.install(self.cpu)
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        self.cpu.s[CPU.CS] = 0x1000

    def run_with(self, text, al=0, si=0x0100, di=0x0200, fill=0x5A):
        cpu = self.cpu
        for i in range(60):
            cpu.wb(0x3000, (di + i) & 0xFFFF, fill)
        for i, byte in enumerate(text):
            cpu.wb(0x2000, (si + i) & 0xFFFF, byte)
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x2900 | al, 0x1111, 0x2222, 0x3333
        cpu.r[CPU.SI], cpu.r[CPU.DI] = si, di
        cpu.s[CPU.DS], cpu.s[CPU.ES] = 0x2000, 0x3000
        cpu.ip = 0
        cpu.step()
        return bytes(cpu.rb(0x3000, (di + i) & 0xFFFF) for i in range(16))

    def test_the_name_is_written_to_es_di_and_si_goes_after_it(self):
        fcb_bytes = self.run_with(b"d:xcom.cfg /x\r")
        self.assertEqual(fcb_bytes[:12], b"\x04XCOM    CFG")
        self.assertEqual(self.cpu.r[CPU.SI], 0x0100 + 10)
        self.assertEqual(self.cpu.get8(0), 0)

    def test_the_bytes_after_the_name_in_the_fcb_are_not_touched(self):
        self.assertEqual(self.run_with(b"a\r")[12:], bytes([0x5A]) * 4)

    def test_al_is_1_for_wildcards_and_ff_for_a_drive_that_is_not_there(self):
        self.run_with(b"*.*\r")
        self.assertEqual(self.cpu.get8(0), 1)
        self.run_with(b"q:a\r")
        self.assertEqual(self.cpu.get8(0), 0xFF)

    def test_ah_and_the_other_registers_stay(self):
        self.run_with(b"a\r")
        cpu = self.cpu
        self.assertEqual(cpu.get8(4), 0x29)  # AH
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.DI]), (0x1111, 0x2222, 0x3333, 0x0200))

    def test_the_control_bits_come_from_al(self):
        self.assertEqual(self.run_with(b"  ab\r", al=1)[:12], b"\0AB         ")

    def test_the_kept_parts_are_those_that_were_in_the_fcb(self):
        fcb_bytes = self.run_with(b"\r", al=14, fill=0x41)
        self.assertEqual(fcb_bytes[:12], b"\x41" * 12)

    def test_the_text_and_the_fcb_may_go_round_the_end_of_their_segments(self):
        fcb_bytes = self.run_with(b"abc.de\r", si=0xFFFC, di=0xFFFA)
        self.assertEqual(fcb_bytes[:12], b"\0ABC     DE ")
        self.assertEqual(self.cpu.r[CPU.SI], 0xFFFC + 6 - 0x10000)

    def test_drives_come_from_the_machine(self):
        self.run_with(b"c:a\r")
        self.assertEqual(self.cpu.get8(0), 0)
        self.run_with(b"e:a\r")
        self.assertEqual(self.cpu.get8(0), 0xFF)


if __name__ == "__main__":
    unittest.main()
