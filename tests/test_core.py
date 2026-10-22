"""The places where the CPU spends its time: reading memory, setting flags, prefixes. They have been written to be
fast, so each is held against what it is meant to do, worked out in a plainer way."""
import unittest

from mydos import flags
from mydos.cpu import CPU, EVEN_PARITY, PREFIXES, signed

ALL_CLEAR = 0x0002
ALL_RAISED = 0x0002 | flags.CF | flags.PF | flags.AF | flags.ZF | flags.SF | flags.OF | flags.DF | flags.IF


def physical(seg, off):
    """Where seg:off is in the 1 MB, the plain way: the segment times 16, plus the offset, round at 1 MB."""
    return (seg * 16 + off) % (1 << 20)


class Window:
    """A device that owns a stretch of the address space and writes down what is asked of it."""

    def __init__(self):
        self.reads, self.writes = [], []

    def read_memory(self, offset):
        self.reads.append(offset)
        return 0xA0 | offset & 0x0F

    def write_memory(self, offset, value):
        self.writes.append((offset, value))


class TestMemoryAccess(unittest.TestCase):
    PLACES = [(0, 0), (0, 0xFFFF), (0x1000, 0x0234), (0x2000, 0xFFFF), (0xFFFF, 0x000F), (0xFFFF, 0x0010),
              (0xFFFF, 0xFFFF), (0xF000, 0xFFFF), (0x9FFF, 0x000F), (0xA000, 0), (0x1234, 0x8000), (0x0001, 0x0001)]

    def test_a_byte_is_where_the_segment_times_16_plus_the_offset_says_and_wraps_at_1_mb(self):
        for seg, off in self.PLACES:
            cpu = CPU()
            cpu.wb(seg, off, 0x5A)
            self.assertEqual(cpu.mem[physical(seg, off)], 0x5A, (seg, off))
            self.assertEqual(sum(1 for byte in cpu.mem if byte), 1, (seg, off))  # and nowhere else
            self.assertEqual(cpu.rb(seg, off), 0x5A, (seg, off))

    def test_two_places_that_are_the_same_byte_are_the_same_byte(self):
        cpu = CPU()
        cpu.wb(0x1000, 0x0010, 0x77)
        for seg, off in ((0x1001, 0x0000), (0x0FFF, 0x0020), (0x1000, 0x0010)):
            self.assertEqual(cpu.rb(seg, off), 0x77)
        cpu.wb(0xFFFF, 0x0010, 0x66)  # the last of the 1 MB and a bit more: back at the start
        self.assertEqual(cpu.mem[0], 0x66)
        self.assertEqual(cpu.rb(0x0000, 0x0000), 0x66)

    def test_an_offset_over_65535_goes_on_into_the_next_bytes(self):
        cpu = CPU()
        cpu.wb(0x1000, 0x10000, 0x44)  # as a word at offset FFFF would make its second byte
        self.assertEqual(cpu.mem[0x20000], 0x44)
        self.assertEqual(cpu.rb(0x1000, 0x10000), 0x44)

    def test_a_write_keeps_the_low_8_bits(self):
        cpu = CPU()
        for value, kept in ((0x1FF, 0xFF), (0x100, 0x00), (0xFF05, 0x05), (0x7F, 0x7F)):
            cpu.wb(0x1000, 5, value)
            self.assertEqual(cpu.rb(0x1000, 5), kept, hex(value))

    def test_a_word_is_two_bytes_low_first(self):
        cpu = CPU()
        cpu.ww(0x1000, 0x0100, 0xBEEF)
        self.assertEqual((cpu.rb(0x1000, 0x0100), cpu.rb(0x1000, 0x0101)), (0xEF, 0xBE))
        self.assertEqual(cpu.rw(0x1000, 0x0100), 0xBEEF)

    def test_the_fetch_reads_at_cs_ip_and_moves_ip_on(self):
        cpu = CPU()
        cpu.s[CPU.CS], cpu.s[CPU.DS], cpu.ip = 0x1000, 0x2000, 0x0010
        cpu.mem[0x10010:0x10013] = b"\x11\x22\x33"
        cpu.mem[0x20010] = 0x99  # the same offset in DS: not what is wanted
        self.assertEqual((cpu.fetch8(), cpu.ip), (0x11, 0x0011))
        self.assertEqual((cpu.fetch16(), cpu.ip), (0x3322, 0x0013))

    def test_the_fetch_takes_ip_round_the_segment_and_reads_the_byte_there(self):
        cpu = CPU()
        cpu.s[CPU.CS], cpu.ip = 0x1000, 0xFFFF
        cpu.mem[0x1FFFF] = 0x5A
        cpu.mem[0x10000] = 0x6B  # IP went round to 0: this is what comes next
        self.assertEqual((cpu.fetch8(), cpu.ip), (0x5A, 0x0000))
        self.assertEqual((cpu.fetch8(), cpu.ip), (0x6B, 0x0001))

    def test_the_fetch_at_the_end_of_the_1_mb_goes_round_to_the_start(self):
        cpu = CPU()
        cpu.s[CPU.CS], cpu.ip = 0xFFFF, 0x0010
        cpu.mem[0] = 0x42
        self.assertEqual(cpu.fetch8(), 0x42)

    def test_the_fetch_changes_nothing_but_ip(self):
        cpu = CPU()
        cpu.r[:] = range(1, 9)
        cpu.s[:] = [0x10, 0x1000, 0x30, 0x40]
        cpu.ip, cpu.flags = 5, ALL_RAISED
        registers, segments = list(cpu.r), list(cpu.s)
        cpu.fetch8()
        self.assertEqual((cpu.r, cpu.s, cpu.flags), (registers, segments, ALL_RAISED))

    def test_a_device_owns_its_window_for_reads_writes_and_fetches(self):
        cpu = CPU()
        window = Window()
        cpu.video, cpu.video_start, cpu.video_end = window, 0xA0000, 0xB0000
        cpu.mem[0xA0005] = 0x11  # the plain memory behind the window is not there to be seen
        self.assertEqual(cpu.rb(0xA000, 0x0005), 0xA5)
        cpu.wb(0xA000, 0x0007, 0x12)
        cpu.s[CPU.CS], cpu.ip = 0xA000, 0x0009
        self.assertEqual(cpu.fetch8(), 0xA9)
        self.assertEqual((window.reads, window.writes, cpu.ip), ([5, 9], [(7, 0x12)], 0x000A))
        self.assertEqual(cpu.mem[0xA0007], 0)

    def test_the_window_starts_and_ends_where_it_says(self):
        cpu = CPU()
        window = Window()
        cpu.video, cpu.video_start, cpu.video_end = window, 0xA0000, 0xB0000
        cpu.mem[0x9FFFF], cpu.mem[0xB0000] = 0x31, 0x32
        self.assertEqual((cpu.rb(0x9FFF, 0x000F), cpu.rb(0xB000, 0)), (0x31, 0x32))
        cpu.s[CPU.CS], cpu.ip = 0x9FFF, 0x000F
        self.assertEqual(cpu.fetch8(), 0x31)
        cpu.s[CPU.CS], cpu.ip = 0xB000, 0
        self.assertEqual(cpu.fetch8(), 0x32)
        self.assertEqual(window.reads, [])  # neither is in the window
        cpu.rb(0xA000, 0)
        cpu.rb(0xA000, 0xFFFF)
        self.assertEqual(window.reads, [0, 0xFFFF])
        cpu.s[CPU.CS], cpu.ip = 0xA000, 0x0000  # the very first byte of the window is in it for a fetch too
        self.assertEqual(cpu.fetch8(), 0xA0)
        self.assertEqual(window.reads, [0, 0xFFFF, 0])

    def test_a_window_reached_by_a_segment_that_wraps_is_still_the_window(self):
        cpu = CPU()
        window = Window()
        cpu.video, cpu.video_start, cpu.video_end = window, 0xA0000, 0xB0000
        cpu.rb(0x9000, 0x1234 + 0x10000)  # 0x90000 + 0x11234 = 0xA1234
        self.assertEqual(window.reads, [0x1234])


class TestSetSzp(unittest.TestCase):
    def after(self, result, bits, pattern):
        cpu = CPU()
        cpu.flags = pattern
        cpu.set_szp(result, bits)
        return cpu.flags

    def reference(self, result, bits):
        """The three flags as the manual says: zero, the top bit, and an even count of ones in the low byte."""
        result &= (1 << bits) - 1
        return (result == 0, bool(result >> (bits - 1)), bin(result & 0xFF).count("1") % 2 == 0)

    def triple(self, flags_value):
        return (bool(flags_value & flags.ZF), bool(flags_value & flags.SF), bool(flags_value & flags.PF))

    def test_the_parity_table_is_even_for_every_byte(self):
        self.assertEqual(len(EVEN_PARITY), 256)
        for byte in range(256):
            ones = sum(byte >> n & 1 for n in range(8))
            self.assertEqual(EVEN_PARITY[byte], ones % 2 == 0, byte)

    def test_every_byte_in_8_bits_with_both_patterns_of_the_other_flags(self):
        for pattern in (ALL_CLEAR, ALL_RAISED):
            for value in range(256):
                got = self.after(value, 8, pattern)
                self.assertEqual(self.triple(got), self.reference(value, 8), (value, pattern))

    def test_sixteen_bit_values_look_at_bit_15_for_the_sign_and_at_the_low_byte_for_the_parity(self):
        values = [0, 1, 2, 3, 0xFF, 0x100, 0x101, 0x1FF, 0x7FFF, 0x8000, 0x8001, 0xFFFF, 0x0300, 0x0301, 0xFF00,
                  0x1234, 0xABCD, 0x4000, 0x00FE]
        for pattern in (ALL_CLEAR, ALL_RAISED):
            for value in values:
                self.assertEqual(self.triple(self.after(value, 16, pattern)), self.reference(value, 16), value)

    def test_a_16_bit_result_whose_low_byte_is_zero_has_even_parity_whatever_the_high_one_is(self):
        self.assertTrue(self.after(0x0100, 16, ALL_CLEAR) & flags.PF)
        self.assertFalse(self.after(0x0101, 16, ALL_RAISED) & flags.PF)
        self.assertTrue(self.after(0x0103, 16, ALL_CLEAR) & flags.PF)

    def test_the_sign_is_bit_7_for_8_bits_and_bit_15_for_16(self):
        self.assertTrue(self.after(0x80, 8, ALL_CLEAR) & flags.SF)
        self.assertFalse(self.after(0x80, 16, ALL_RAISED) & flags.SF)
        self.assertTrue(self.after(0x8000, 16, ALL_CLEAR) & flags.SF)
        self.assertFalse(self.after(0x7FFF, 16, ALL_RAISED) & flags.SF)

    def test_bits_above_the_width_are_not_part_of_the_result(self):
        self.assertTrue(self.after(0x100, 8, ALL_CLEAR) & flags.ZF)  # 0x100 in 8 bits is 0
        self.assertTrue(self.after(0x10000, 16, ALL_CLEAR) & flags.ZF)
        self.assertFalse(self.after(0x1FF, 8, ALL_RAISED) & flags.ZF)
        self.assertTrue(self.after(-1, 8, ALL_CLEAR) & flags.SF)  # a negative number is its two's complement
        self.assertEqual(self.after(-1, 16, ALL_CLEAR) & flags.PF, flags.PF)  # 0xFFFF: low byte FF, eight ones

    def test_no_other_flag_moves(self):
        others = flags.CF | flags.AF | flags.OF | flags.DF | flags.IF | 0x0002
        for pattern in (ALL_CLEAR, ALL_RAISED):
            for value in (0, 1, 0x80, 0xFF, 0x8000):
                for bits in (8, 16):
                    self.assertEqual(self.after(value, bits, pattern) & others, pattern & others, (value, bits))

    def test_each_of_the_three_flags_goes_down_as_well_as_up(self):
        got = self.after(0x01, 8, ALL_RAISED)  # not zero, not negative, odd
        self.assertEqual(got & (flags.ZF | flags.SF | flags.PF), 0)
        got = self.after(0x00, 8, ALL_CLEAR)  # zero, even
        self.assertEqual(got & (flags.ZF | flags.SF | flags.PF), flags.ZF | flags.PF)


class TestPrefixes(unittest.TestCase):
    ES, CS, SS, DS = CPU.ES, CPU.CS, CPU.SS, CPU.DS

    def machine(self, code):
        cpu = CPU()
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10000 + len(code)] = code
        self.seen = []
        cpu.ops[0x90] = lambda op: self.seen.append((cpu.seg_override, cpu.rep, cpu.ip))  # what a NOP sees
        return cpu

    def test_there_are_six_prefixes(self):
        self.assertEqual(PREFIXES, frozenset((0x26, 0x2E, 0x36, 0x3E, 0xF2, 0xF3)))

    def test_no_prefix_is_an_instruction_of_its_own(self):
        cpu = CPU()
        for prefix in PREFIXES:
            self.assertNotIn(prefix, cpu.ops, hex(prefix))

    def test_every_prefix_of_the_segment_kind_is_the_segment_it_names(self):
        for prefix, segment in ((0x26, self.ES), (0x2E, self.CS), (0x36, self.SS), (0x3E, self.DS)):
            cpu = self.machine(bytes([prefix, 0x90]))
            cpu.step()
            self.assertEqual(self.seen, [(segment, None, 2)], hex(prefix))

    def test_the_repeat_prefixes(self):
        for prefix in (0xF2, 0xF3):
            cpu = self.machine(bytes([prefix, 0x90]))
            cpu.step()
            self.assertEqual(self.seen, [(None, prefix, 2)], hex(prefix))

    def test_a_segment_and_a_repeat_prefix_together_in_either_order(self):
        for code in (bytes([0x26, 0xF3, 0x90]), bytes([0xF3, 0x26, 0x90])):
            cpu = self.machine(code)
            cpu.step()
            self.assertEqual(self.seen, [(self.ES, 0xF3, 3)], code.hex())

    def test_of_two_prefixes_of_one_kind_the_last_counts(self):
        for code, expected in ((bytes([0x26, 0x3E, 0x90]), (self.DS, None)),
                               (bytes([0x3E, 0x26, 0x90]), (self.ES, None)),
                               (bytes([0xF2, 0xF3, 0x90]), (None, 0xF3)), (bytes([0xF3, 0xF2, 0x90]), (None, 0xF2)),
                               (bytes([0x26, 0xF3, 0x2E, 0xF2, 0x90]), (self.CS, 0xF2))):
            cpu = self.machine(code)
            cpu.step()
            self.assertEqual(self.seen, [expected + (len(code),)], code.hex())

    def test_a_prefix_is_for_one_instruction_only(self):
        cpu = self.machine(bytes([0x26, 0xF3, 0x90, 0x90]))
        cpu.step()
        cpu.step()
        self.assertEqual(self.seen, [(self.ES, 0xF3, 3), (None, None, 4)])

    def test_a_prefix_before_a_prefix_before_an_opcode_in_a_row_of_instructions(self):
        cpu = self.machine(bytes([0x90, 0x3E, 0x90, 0xF2, 0x26, 0x90, 0x90]))
        for _ in range(4):
            cpu.step()
        self.assertEqual(self.seen, [(None, None, 1), (self.DS, None, 3), (self.ES, 0xF2, 6), (None, None, 7)])

    def test_an_opcode_that_is_not_there_stops_the_emulator_at_the_start_of_the_instruction_with_its_prefixes(self):
        cpu = CPU()
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10005:0x10008] = bytes([0x26, 0xF3, 0x0F])
        cpu.ip = 5
        with self.assertRaises(NotImplementedError) as caught:
            cpu.step()
        self.assertIn("0F at 1000:0005", str(caught.exception))

    def test_the_prefix_changes_what_a_memory_operand_means(self):
        cpu = CPU()
        cpu.s[CPU.CS], cpu.s[CPU.DS], cpu.s[CPU.ES] = 0x1000, 0x2000, 0x3000
        cpu.mem[0x10000:0x10004] = bytes([0x26, 0x8A, 0x07, 0x8A])  # mov al, es:[bx]; then the start of mov al,..
        cpu.mem[0x30010] = 0x33
        cpu.mem[0x20010] = 0x22
        cpu.r[CPU.BX] = 0x10
        cpu.step()
        self.assertEqual(cpu.get8(0), 0x33)
        cpu.mem[0x10003:0x10006] = bytes([0x8A, 0x07, 0x90])  # mov al, [bx]: no prefix, so DS
        cpu.step()
        self.assertEqual(cpu.get8(0), 0x22)


class TestSigned(unittest.TestCase):
    def test_two_complement(self):
        self.assertEqual([signed(v, 8) for v in (0, 1, 127, 128, 255)], [0, 1, 127, -128, -1])
        self.assertEqual([signed(v, 16) for v in (0, 32767, 32768, 65535)], [0, 32767, -32768, -1])


if __name__ == "__main__":
    unittest.main()
