"""The places where the CPU spends its time: reading memory, setting flags, prefixes. They have been written to be
fast, so each is held against what it is meant to do, worked out in a plainer way."""
import unittest

from mydos import flags
from mydos.cpu import CONDITION_FLAGS, CPU, EVEN_PARITY, PREFIXES, condition_holds, signed

ALL_CLEAR = 0x0002
ALL_RAISED = 0x0002 | flags.CF | flags.PF | flags.AF | flags.ZF | flags.SF | flags.OF | flags.DF | flags.IF


CONDITION_BITS = flags.CF | flags.PF | flags.ZF | flags.SF | flags.OF


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
        self.assertIn("opcode 0F 00 at 1000:0005", str(caught.exception))  # 0F 00 is not an instruction we know

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


class Reference:
    """The arithmetic of the CPU written out bit by bit and flag by flag, the plain way, to hold the fast one to."""

    @staticmethod
    def add(a, b, bits, carry=0):
        """(result, CF, OF, AF, ZF, SF, PF)"""
        mask = (1 << bits) - 1
        total = a + b + carry
        result = total & mask
        cf = total > mask
        sa, sb, sr = a >> (bits - 1) & 1, b >> (bits - 1) & 1, result >> (bits - 1) & 1
        of = sa == sb and sr != sa
        af = (a & 0xF) + (b & 0xF) + carry > 0xF
        return result, cf, of, af, result == 0, bool(sr), bin(result & 0xFF).count("1") % 2 == 0

    @staticmethod
    def sub(a, b, bits, borrow=0):
        mask = (1 << bits) - 1
        result = (a - b - borrow) & mask
        cf = a < b + borrow
        sa, sb, sr = a >> (bits - 1) & 1, b >> (bits - 1) & 1, result >> (bits - 1) & 1
        of = sa != sb and sr != sa
        af = (a & 0xF) < (b & 0xF) + borrow
        return result, cf, of, af, result == 0, bool(sr), bin(result & 0xFF).count("1") % 2 == 0

    @staticmethod
    def shift(kind, value, count, bits, cf):
        """The old way: one bit at a time. Returns (value, CF, OF)."""
        mask = (1 << bits) - 1
        top = 1 << (bits - 1)
        of = False
        for _ in range(count):
            if kind in (4, 6):
                cf = bool(value & top)
                value = (value << 1) & mask
                of = bool(value & top) != cf
            elif kind == 5:
                of = bool(value & top)
                cf = bool(value & 1)
                value >>= 1
            elif kind == 7:
                cf = bool(value & 1)
                value = (value >> 1) | (value & top)
                of = False
            elif kind == 0:
                cf = bool(value & top)
                value = ((value << 1) & mask) | cf
                of = bool(value & top) != cf
            elif kind == 1:
                cf = bool(value & 1)
                value = (value >> 1) | (top if cf else 0)
                of = bool(value & top) != bool(value & (top >> 1))
            elif kind == 2:
                out = bool(value & top)
                value = ((value << 1) & mask) | cf
                cf = out
                of = bool(value & top) != cf
            else:
                out = bool(value & 1)
                value = (value >> 1) | (top if cf else 0)
                cf = out
                of = bool(value & top) != bool(value & (top >> 1))
        return value, cf, of


ARITHMETIC = flags.CF | flags.OF | flags.AF | flags.ZF | flags.SF | flags.PF


CALCULATOR = CPU()  # one machine for the many calculations that need only its flags: making a CPU is slow


def calculator(pattern):
    """The CPU for a calculation, with the flags as given and nothing else changed by the calculation before."""
    CALCULATOR.flags = pattern
    return CALCULATOR


def six(flags_value):
    """(CF, OF, AF, ZF, SF, PF) of a flags register"""
    return tuple(bool(flags_value & bit) for bit in (flags.CF, flags.OF, flags.AF, flags.ZF, flags.SF, flags.PF))


class TestArithmeticFlags(unittest.TestCase):
    def run_op(self, name, a, b, bits, extra, pattern):
        cpu = calculator(pattern)
        result = getattr(cpu, name)(a, b, bits, extra)
        return result, cpu.flags

    def check(self, name, reference, a, b, bits, extra, pattern):
        result, got = self.run_op(name, a, b, bits, extra, pattern)
        expected = reference(a, b, bits, extra)
        self.assertEqual((result,) + six(got), expected, (name, a, b, bits, extra, hex(pattern)))
        self.assertEqual(got & ~ARITHMETIC, pattern & ~ARITHMETIC, (name, a, b))  # no other flag moves

    def test_add_over_every_pair_of_bytes_with_and_without_a_carry(self):
        for a in range(256):
            for b in range(0, 256, 3):
                for carry in (0, 1):
                    pattern = ALL_CLEAR if (a + b) % 2 else ALL_RAISED
                    self.check("add_with_flags", Reference.add, a, b, 8, carry, pattern)

    def test_sub_over_every_pair_of_bytes_with_and_without_a_borrow(self):
        for a in range(256):
            for b in range(0, 256, 3):
                for borrow in (0, 1):
                    pattern = ALL_RAISED if (a + b) % 2 else ALL_CLEAR
                    self.check("sub_with_flags", Reference.sub, a, b, 8, borrow, pattern)

    def test_both_over_edges_and_random_words(self):
        import random
        rng = random.Random(8086)
        edges = [0, 1, 2, 0x0F, 0x10, 0x7F, 0x80, 0xFF, 0x7FFF, 0x8000, 0x8001, 0xFFFE, 0xFFFF, 0x00FF, 0x0100, 0xFF00]
        pairs = [(a, b) for a in edges for b in edges]
        pairs += [(rng.randrange(65536), rng.randrange(65536)) for _ in range(3000)]
        for a, b in pairs:
            for extra in (0, 1):
                for pattern in (ALL_CLEAR, ALL_RAISED):
                    self.check("add_with_flags", Reference.add, a, b, 16, extra, pattern)
                    self.check("sub_with_flags", Reference.sub, a, b, 16, extra, pattern)

    def test_the_values_are_cut_to_the_width_first(self):
        result, got = self.run_op("add_with_flags", 0x1FF, 0x101, 8, 0, ALL_CLEAR)  # FF + 01
        self.assertEqual((result,) + six(got), Reference.add(0xFF, 0x01, 8))
        result, got = self.run_op("sub_with_flags", 0x10000, 1, 16, 0, ALL_CLEAR)  # 0 - 1
        self.assertEqual((result,) + six(got), Reference.sub(0, 1, 16))

    def test_the_six_flags_are_set_by_set_arithmetic_flags_as_given(self):
        for bits_set in range(8):
            cf, of, af = bool(bits_set & 1), bool(bits_set & 2), bool(bits_set & 4)
            for result, bits in ((0, 8), (0x80, 8), (0x01, 8), (0x8000, 16), (0x0100, 16), (0x1FF, 8)):
                for pattern in (ALL_CLEAR, ALL_RAISED):
                    cpu = calculator(pattern)
                    cpu.set_arithmetic_flags(cf, of, af, result, bits)
                    masked = result & ((1 << bits) - 1)
                    parity = bin(masked & 0xFF).count("1") % 2 == 0
                    expected = (cf, of, af, masked == 0, bool(masked >> (bits - 1)), parity)
                    self.assertEqual(six(cpu.flags), expected, (cf, of, af, result, bits))
                    self.assertEqual(cpu.flags & ~ARITHMETIC, pattern & ~ARITHMETIC)

    def test_inc_and_dec_keep_the_carry_flag_and_set_the_rest_like_add_and_sub(self):
        for carry in (False, True):
            for value in (0, 1, 0x7F, 0x80, 0xFF, 0x7FFF, 0xFFFF):
                for bits in (8, 16):
                    for decrement in (False, True):
                        cpu = calculator(ALL_CLEAR | (flags.CF if carry else 0))
                        got = cpu.inc_dec(value, bits, decrement)
                        want = (Reference.sub if decrement else Reference.add)(value & ((1 << bits) - 1), 1, bits)
                        self.assertEqual((got,) + six(cpu.flags)[1:], (want[0],) + want[2:], (value, bits, decrement))
                        self.assertEqual(bool(cpu.flags & flags.CF), carry)


class TestShiftFlags(unittest.TestCase):
    def test_every_kind_on_bytes_with_counts_0_to_20_and_both_carries(self):
        for kind in range(8):
            for value in range(0, 256, 5):
                for count in (0, 1, 2, 3, 7, 8, 9, 16, 20):
                    for cf in (False, True):
                        self.check(kind, value, count, 8, cf, ALL_RAISED if value % 2 else ALL_CLEAR)

    def test_every_kind_on_words_with_counts_0_to_20_and_both_carries(self):
        values = [0, 1, 2, 0x7FFF, 0x8000, 0x8001, 0xFFFF, 0x1234, 0xABCD, 0x00FF, 0xFF00, 0x5555, 0xAAAA]
        for kind in range(8):
            for value in values:
                for count in (0, 1, 2, 4, 8, 15, 16, 17, 20):
                    for cf in (False, True):
                        self.check(kind, value, count, 16, cf, ALL_CLEAR if count % 2 else ALL_RAISED)

    def check(self, kind, value, count, bits, cf, pattern):
        cpu = calculator((pattern & ~flags.CF) | (flags.CF if cf else 0))
        got = cpu.shift_with_flags(kind, value, count, bits)
        if count == 0:
            self.assertEqual((got, cpu.flags), (value, (pattern & ~flags.CF) | (flags.CF if cf else 0)))
            return
        want, want_cf, want_of = Reference.shift(kind, value, count, bits, cf)
        self.assertEqual(got, want, (kind, value, count, bits, cf))
        self.assertEqual((bool(cpu.flags & flags.CF), bool(cpu.flags & flags.OF)), (want_cf, want_of),
                         (kind, value, count, bits, cf))
        moved = flags.CF | flags.OF
        if kind in (4, 5, 6, 7):  # the shifts set the other three flags from the result, the rotations leave them
            moved |= flags.ZF | flags.SF | flags.PF
            parity = bin(want & 0xFF).count("1") % 2 == 0
            self.assertEqual(six(cpu.flags)[3:], (want == 0, bool(want >> (bits - 1)), parity))
        self.assertEqual(cpu.flags & ~moved, ((pattern & ~flags.CF) | (flags.CF if cf else 0)) & ~moved,
                         (kind, value, count, bits))


class TestConditions(unittest.TestCase):
    def test_every_combination_of_the_flags_for_all_16_conditions(self):
        for bits in range(1 << 5):
            cf, pf, zf, sf, of = (bool(bits >> n & 1) for n in range(5))
            for others in (0, ALL_RAISED & ~CONDITION_BITS, 0x0002):
                cpu = calculator((flags.CF if cf else 0) | (flags.PF if pf else 0) | (flags.ZF if zf else 0) |
                                 (flags.SF if sf else 0) | (flags.OF if of else 0) | others | 0x0002)
                expected = [of, cf, zf, cf or zf, sf, pf, sf != of, zf or sf != of]
                for cc in range(16):
                    want = expected[cc >> 1] != bool(cc & 1)
                    self.assertEqual(cpu.condition(cc), want, (cc, bin(bits), hex(others)))

    def test_it_follows_the_flags_when_they_change_and_each_cpu_has_its_own_flags(self):
        first, second = CPU(), CPU()
        first.flags, second.flags = flags.ZF, 0
        self.assertEqual((first.condition(4 // 2 * 2), second.condition(4)), (True, False))  # JE
        second.flags = flags.ZF
        first.flags = 0
        self.assertEqual((first.condition(4), second.condition(4)), (False, True))
        self.assertEqual((first.condition(5), second.condition(5)), (True, False))  # JNE

    def test_the_names_of_the_jumps(self):
        cpu = CPU()
        cpu.flags = flags.CF  # below
        self.assertEqual([cpu.condition(cc) for cc in (2, 3, 6, 7, 8, 9)], [True, False, True, False, False, True])
        cpu.flags = flags.SF | flags.OF  # not less
        self.assertEqual([cpu.condition(cc) for cc in (12, 13, 14, 15)], [False, True, False, True])
        cpu.flags = flags.SF  # less
        self.assertEqual([cpu.condition(cc) for cc in (12, 13, 14, 15)], [True, False, True, False])

    def test_the_pure_function_is_what_the_method_answers(self):
        for key in range(0x1000):
            key &= CONDITION_FLAGS
            cpu = calculator(key)
            for cc in range(16):
                self.assertEqual(cpu.condition(cc), condition_holds(key, cc))


class TestStepReadsTheFirstByteItself(unittest.TestCase):
    def test_the_first_byte_is_read_from_cs_ip_and_ip_goes_on(self):
        cpu = CPU()
        cpu.s[CPU.CS], cpu.s[CPU.DS], cpu.ip = 0x1000, 0x2000, 0x0010
        cpu.mem[0x10010] = 0x90
        cpu.mem[0x20010] = 0xF4  # in DS: a HLT, which would end the run
        cpu.step()
        self.assertEqual((cpu.ip, cpu.start_ip, cpu.instructions), (0x0011, 0x0010, 1))

    def test_the_first_byte_at_the_end_of_the_segment_and_ip_goes_round(self):
        cpu = CPU()
        cpu.s[CPU.CS], cpu.ip = 0x1000, 0xFFFF
        cpu.mem[0x1FFFF] = 0x90
        cpu.step()
        self.assertEqual((cpu.ip, cpu.start_ip), (0x0000, 0xFFFF))

    def test_the_first_byte_at_the_end_of_the_1_mb_is_from_the_start(self):
        cpu = CPU()
        cpu.s[CPU.CS], cpu.ip = 0xFFFF, 0x0010
        cpu.mem[0] = 0x90
        cpu.mem[0xFFFFF] = 0xF4
        cpu.step()
        self.assertEqual(cpu.ip, 0x0011)

    def test_the_first_byte_from_the_window_of_a_device_comes_from_the_device(self):
        cpu = CPU()
        window = Window()
        window.read_memory = lambda offset: window.reads.append(offset) or 0x90
        cpu.video, cpu.video_start, cpu.video_end = window, 0xA0000, 0xB0000
        cpu.s[CPU.CS], cpu.ip = 0xA000, 0x0000
        cpu.step()
        self.assertEqual((window.reads, cpu.ip), ([0], 1))
        cpu.s[CPU.CS], cpu.ip = 0x9FFF, 0x000F  # the byte before the window: plain memory, a NOP
        cpu.mem[0x9FFFF] = 0x90
        cpu.step()
        self.assertEqual(window.reads, [0])

    def test_start_ip_is_where_the_prefixes_began(self):
        cpu = CPU()
        cpu.s[CPU.CS], cpu.ip = 0x1000, 0x0020
        cpu.mem[0x10020:0x10023] = bytes([0x26, 0xF3, 0x90])
        cpu.step()
        self.assertEqual((cpu.start_ip, cpu.ip), (0x0020, 0x0023))

    def test_the_trace_hook_is_called_before_the_instruction_with_the_ip_of_it(self):
        cpu = CPU()
        seen = []
        cpu.s[CPU.CS], cpu.ip = 0x1000, 0x0005
        cpu.mem[0x10005] = 0x90
        cpu.trace = lambda c: seen.append((c.ip, c.instructions))
        cpu.step()
        self.assertEqual(seen, [(0x0005, 1)])


class TestSigned(unittest.TestCase):
    def test_two_complement(self):
        self.assertEqual([signed(v, 8) for v in (0, 1, 127, 128, 255)], [0, 1, 127, -128, -1])
        self.assertEqual([signed(v, 16) for v in (0, 32767, 32768, 65535)], [0, 32767, -32768, -1])


if __name__ == "__main__":
    unittest.main()
