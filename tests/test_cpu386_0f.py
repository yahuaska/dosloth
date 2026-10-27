"""The instructions of two bytes that start with 0F (step 2c): the long jumps, SETcc, MOVZX, MOVSX, IMUL, SHLD, SHRD,
BT and its kin, BSF, BSR and BSWAP. Each is checked against a definition written out on Python numbers."""
import unittest

from mydos import flags
from mydos.regs import AX, BX, CX, DX, SI
from tests.test_cpu386 import SAMPLES, Machine, le32

MASK = 0xFFFFFFFF
CARRY, PARITY, ZERO, SIGN, OVERFLOW = flags.CF, flags.PF, flags.ZF, flags.SF, flags.OF

# the sixteen conditions, written out as they are defined in the manuals: (name, a test of (cf, pf, zf, sf, of))
CONDITIONS = [
    ("o", lambda cf, pf, zf, sf, of: of), ("no", lambda cf, pf, zf, sf, of: not of),
    ("b", lambda cf, pf, zf, sf, of: cf), ("ae", lambda cf, pf, zf, sf, of: not cf),
    ("e", lambda cf, pf, zf, sf, of: zf), ("ne", lambda cf, pf, zf, sf, of: not zf),
    ("be", lambda cf, pf, zf, sf, of: cf or zf), ("a", lambda cf, pf, zf, sf, of: not cf and not zf),
    ("s", lambda cf, pf, zf, sf, of: sf), ("ns", lambda cf, pf, zf, sf, of: not sf),
    ("p", lambda cf, pf, zf, sf, of: pf), ("np", lambda cf, pf, zf, sf, of: not pf),
    ("l", lambda cf, pf, zf, sf, of: sf != of), ("ge", lambda cf, pf, zf, sf, of: sf == of),
    ("le", lambda cf, pf, zf, sf, of: zf or sf != of), ("g", lambda cf, pf, zf, sf, of: not zf and sf == of),
]


def every_flag_combination():
    for bits in range(32):
        cf, pf, zf, sf, of = (bool(bits >> i & 1) for i in range(5))
        value = 0x0002 | (CARRY if cf else 0) | (PARITY if pf else 0) | (ZERO if zf else 0) | (SIGN if sf else 0) \
            | (OVERFLOW if of else 0)
        yield (cf, pf, zf, sf, of), value


def signed(value, bits):
    return value - (1 << bits) if value >> (bits - 1) else value


def parity_even(value):
    return bin(value & 0xFF).count("1") % 2 == 0


class TestLongJumps(Machine):
    def test_every_condition_with_every_combination_of_flags_with_a_distance_of_16_and_of_32_bits(self):
        for number, (name, holds) in enumerate(CONDITIONS):
            for combination, value in every_flag_combination():
                for prefix, distance in ((b"", (0x0100).to_bytes(2, "little")), (b"\x66", le32(0x0100))):
                    cpu = self.start(prefix + bytes([0x0F, 0x80 + number]) + distance)
                    cpu.flags = value
                    cpu.step()
                    length = len(prefix) + 2 + len(distance)
                    expected = length + 0x100 if holds(*combination) else length
                    self.assertEqual(cpu.ip, expected, f"j{name} {combination} {prefix.hex()}")
                    self.assertEqual(cpu.flags, value)

    def test_a_distance_can_be_backwards(self):
        cpu = self.start(b"")
        cpu.mem[0x10200:0x10206] = bytes([0x0F, 0x84]) + le32(-0x80)
        cpu.ip = 0x200
        cpu.flags = 0x0002 | ZERO
        cpu.step()
        self.assertEqual(cpu.ip, 0x204 - 0x80)  # the 16-bit form has a distance of two bytes: FF80h
        cpu = self.start(b"")
        cpu.mem[0x10200:0x10207] = bytes([0x66, 0x0F, 0x84]) + le32(-0x80)
        cpu.ip = 0x200
        cpu.flags = 0x0002 | ZERO
        cpu.step()
        self.assertEqual(cpu.ip, 0x207 - 0x80)


class TestSetcc(Machine):
    def test_every_condition_with_every_combination_of_flags_writes_one_or_zero(self):
        for number, (name, holds) in enumerate(CONDITIONS):
            for combination, value in every_flag_combination():
                cpu = self.start(bytes([0x0F, 0x90 + number, 0xC3]), ebx=0x12345678)  # setcc bl
                cpu.flags = value
                cpu.step()
                expected = 0x12345600 | (1 if holds(*combination) else 0)
                self.assertEqual(cpu.get32(BX), expected, f"set{name} {combination}")
                self.assertEqual(cpu.flags, value)

    def test_to_memory_and_the_size_prefix_does_not_matter(self):
        for prefix in (b"", b"\x66"):
            cpu = self.start(prefix + bytes([0x0F, 0x94, 0x06, 0x00, 0x01]))  # sete [0100]
            cpu.flags = 0x0002 | ZERO
            cpu.wd(0x2000, 0x0100, 0xFFFFFFFF)
            cpu.step()
            self.assertEqual(cpu.rd(0x2000, 0x0100), 0xFFFFFF01)


class TestMovzxAndMovsx(Machine):
    def test_the_four_opcodes_for_a_register_and_for_memory_with_both_sizes_of_the_destination(self):
        for second, source_bits, sign in ((0xB6, 8, False), (0xB7, 16, False), (0xBE, 8, True), (0xBF, 16, True)):
            for value in SAMPLES:
                low = value & ((1 << source_bits) - 1)
                widened = signed(low, source_bits) if sign else low
                for prefix, dest_bits in ((b"\x66", 32), (b"", 16)):
                    cpu = self.start(prefix + bytes([0x0F, second, 0xCB]), ebx=value, ecx=0x55555555)  # reg ecx, bx/bl
                    cpu.flags = 0x0002 | CARRY
                    cpu.step()
                    mask = (1 << dest_bits) - 1
                    expected = widened & mask if dest_bits == 32 else (0x55550000 | widened & mask)
                    self.assertEqual(cpu.get32(CX), expected, f"{second:02X} {value:08X} {dest_bits}")
                    self.assertEqual(cpu.flags, 0x0002 | CARRY)
                    cpu = self.start(prefix + bytes([0x0F, second, 0x0E, 0x00, 0x01]), ecx=0x55555555)  # reg, [0100]
                    cpu.wd(0x2000, 0x0100, value)
                    cpu.step()
                    self.assertEqual(cpu.get32(CX), expected, f"memory {second:02X} {value:08X} {dest_bits}")

    def test_the_byte_register_source_is_al_to_bh(self):
        cpu = self.start(bytes([0x66, 0x0F, 0xB6, 0xC7]), ebx=0x0000AB00)  # movzx eax, bh
        cpu.step()
        self.assertEqual(cpu.get32(AX), 0xAB)
        cpu = self.start(bytes([0x66, 0x0F, 0xBE, 0xC7]), ebx=0x0000AB00)  # movsx eax, bh
        cpu.step()
        self.assertEqual(cpu.get32(AX), 0xFFFFFFAB)


class TestImulWithTwoOperands(Machine):
    def test_the_32_bit_and_the_16_bit_form_agree_with_the_definition(self):
        for a in SAMPLES:
            for b in SAMPLES:
                cpu = self.start(bytes([0x66, 0x0F, 0xAF, 0xCB]), ecx=a, ebx=b)  # imul ecx, ebx
                cpu.step()
                product = signed(a, 32) * signed(b, 32)
                fits = -(1 << 31) <= product < 1 << 31
                self.assertEqual(cpu.get32(CX), product & MASK, f"{a:08X}*{b:08X}")
                self.assertEqual((bool(cpu.flags & CARRY), bool(cpu.flags & OVERFLOW)), (not fits, not fits))
                cpu = self.start(bytes([0x0F, 0xAF, 0xCB]), ecx=a, ebx=b)  # imul cx, bx
                cpu.step()
                product = signed(a & 0xFFFF, 16) * signed(b & 0xFFFF, 16)
                fits = -(1 << 15) <= product < 1 << 15
                self.assertEqual(cpu.get32(CX), (a & 0xFFFF0000) | product & 0xFFFF)
                self.assertEqual(bool(cpu.flags & CARRY), not fits)

    def test_a_memory_operand(self):
        cpu = self.start(bytes([0x66, 0x0F, 0xAF, 0x0E, 0x00, 0x01]), ecx=7)
        cpu.wd(0x2000, 0x0100, 0xFFFFFFFD)  # -3
        cpu.step()
        self.assertEqual(cpu.get32(CX), (-21) & MASK)


def double_shift(left, bits, value, fill, count):
    """(result, CF) of SHLD or SHRD by the definition, for a count of 1 to 31."""
    mask = (1 << bits) - 1
    if left:
        return ((value << count) | (fill >> (bits - count))) & mask, (value >> (bits - count)) & 1
    return ((value >> count) | (fill << (bits - count))) & mask, (value >> (count - 1)) & 1


class TestDoubleShifts(Machine):
    """shld ebx, edx, count: the destination is EBX (the r/m field), the register that fills is EDX (the reg field)."""

    def run_shift(self, prefix, second, value, fill, count, via_cl):
        code = prefix + bytes([0x0F, second, 0xD3] + ([] if via_cl else [count]))
        cpu = self.start(code, ebx=value, edx=fill, ecx=count if via_cl else 0x77)
        cpu.flags = 0x0002 | CARRY | ZERO
        cpu.step()
        return cpu

    def test_shld_and_shrd_by_a_byte_and_by_cl_agree_with_the_definition(self):
        for left, by_byte, by_cl in ((True, 0xA4, 0xA5), (False, 0xAC, 0xAD)):
            for value in SAMPLES:
                for fill in (0, 0xFFFFFFFF, 0x9ABCDEF0):
                    for count in (0, 1, 2, 8, 16, 31, 32, 33):
                        for prefix, bits in ((b"\x66", 32), (b"", 16)):
                            if bits == 16 and count > 16:
                                continue  # the 386 does not say what it does
                            for via_cl in (False, True):
                                cpu = self.run_shift(prefix, by_cl if via_cl else by_byte, value, fill, count, via_cl)
                                mask, real = (1 << bits) - 1, count & 31
                                text = f"{'shld' if left else 'shrd'} {value:08X} {fill:08X} {count} {bits} {via_cl}"
                                self.assertEqual(cpu.get32(DX), fill, text)  # the register that fills is not changed
                                if real == 0:  # nothing happens, not even to the flags
                                    self.assertEqual((cpu.get32(BX), cpu.flags), (value, 0x0002 | CARRY | ZERO), text)
                                    continue
                                result, carry = double_shift(left, bits, value & mask, fill & mask, real)
                                expected = result if bits == 32 else (value & 0xFFFF0000) | result
                                self.assertEqual(cpu.get32(BX), expected, text)
                                self.assertEqual(bool(cpu.flags & CARRY), bool(carry), text)
                                self.assertEqual(bool(cpu.flags & ZERO), result == 0, text)
                                self.assertEqual(bool(cpu.flags & SIGN), bool(result >> (bits - 1)), text)
                                self.assertEqual(bool(cpu.flags & PARITY), parity_even(result), text)
                                if real == 1:
                                    sign_before = (value >> (bits - 1)) & 1
                                    sign_after = result >> (bits - 1)
                                    self.assertEqual(bool(cpu.flags & OVERFLOW), sign_before != sign_after, text)

    def test_a_memory_destination(self):
        cpu = self.start(bytes([0x66, 0x0F, 0xA4, 0x0E, 0x00, 0x01, 0x04]), ecx=0xF0000000)  # shld [0100], ecx, 4
        cpu.wd(0x2000, 0x0100, 0x12345678)
        cpu.step()
        self.assertEqual((cpu.rd(0x2000, 0x0100), cpu.ip), (0x2345678F, 7))


class TestBitInstructions(Machine):
    KINDS = [("bt", lambda v, b: v), ("bts", lambda v, b: v | b), ("btr", lambda v, b: v & ~b),
             ("btc", lambda v, b: v ^ b)]

    def test_a_register_destination_with_a_number_from_a_register_counts_modulo_the_size(self):
        for kind, (name, change) in enumerate(self.KINDS):
            for value in (0, 1, 0x80000000, 0xFFFFFFFF, 0x12345678):
                for number in (0, 1, 5, 31, 32, 33, 63, 0xFFFFFFFF):
                    cpu = self.start(bytes([0x66, 0x0F, 0xA3 + 8 * kind, 0xCB]), ebx=value, ecx=number)  # bt ebx, ecx
                    cpu.flags = 0x0002 | ZERO
                    cpu.step()
                    bit = 1 << (number % 32)
                    self.assertEqual(cpu.get32(BX), change(value, bit) & MASK, f"{name} {value:08X} {number}")
                    self.assertEqual(bool(cpu.flags & CARRY), bool(value & bit))
                    self.assertTrue(cpu.flags & ZERO)  # the other flags are left alone
                    self.assertEqual(cpu.get32(CX), number)

    def test_the_16_bit_forms_count_modulo_16(self):
        cpu = self.start(bytes([0x0F, 0xAB, 0xCB]), ebx=0xAAAA0000, ecx=17)  # bts bx, cx: bit 1
        cpu.step()
        self.assertEqual((cpu.get32(BX), bool(cpu.flags & CARRY)), (0xAAAA0002, False))

    def test_a_memory_operand_with_a_number_from_a_register_may_be_outside_the_operand(self):
        for kind, (name, change) in enumerate(self.KINDS):
            for number in (0, 31, 32, 33, 100, -1, -32, -33):
                cpu = self.start(bytes([0x66, 0x0F, 0xA3 + 8 * kind, 0x0E, 0x00, 0x04]), ecx=number & MASK)
                for offset in range(0x03E0, 0x0420, 4):
                    cpu.wd(0x2000, offset, 0x0F0F0F0F ^ offset)
                before = {offset: cpu.rd(0x2000, offset) for offset in range(0x03E0, 0x0420, 4)}
                cpu.step()
                dword = 0x0400 + number // 32 * 4  # the doubleword that has the bit, found by the definition
                bit = 1 << (number % 32)
                for offset, old in before.items():
                    expected = change(old, bit) & MASK if offset == dword else old
                    self.assertEqual(cpu.rd(0x2000, offset), expected, f"{name} {number} at {offset:04X}")
                self.assertEqual(bool(cpu.flags & CARRY), bool(before[dword] & bit), f"{name} {number}")

    def test_a_number_from_a_byte_counts_modulo_the_size_and_does_not_move_the_address(self):
        for kind in range(4):
            name, change = self.KINDS[kind]
            for number in (0, 5, 31, 32, 37, 255):
                # the ModRM byte: mod 00, reg 4 + kind, r/m 110 (a bare 16-bit address)
                cpu = self.start(bytes([0x66, 0x0F, 0xBA, 0x06 | (4 + kind) << 3, 0x00, 0x01, number]))
                cpu.wd(0x2000, 0x0100, 0x12345678)
                cpu.wd(0x2000, 0x0104, 0xAAAAAAAA)
                cpu.step()
                bit = 1 << (number % 32)
                self.assertEqual(cpu.rd(0x2000, 0x0100), change(0x12345678, bit), f"{name} {number}")
                self.assertEqual(cpu.rd(0x2000, 0x0104), 0xAAAAAAAA)
                self.assertEqual((bool(cpu.flags & CARRY), cpu.ip), (bool(0x12345678 & bit), 7), f"{name} {number}")

    def test_the_group_ba_with_a_register_and_the_numbers_below_4_are_nothing(self):
        cpu = self.start(bytes([0x66, 0x0F, 0xBA, 0xEB, 0x1F]), ebx=0)  # bts ebx, 31
        cpu.step()
        self.assertEqual(cpu.get32(BX), 0x80000000)
        for reg in range(4):
            cpu = self.start(bytes([0x66, 0x0F, 0xBA, 0xC3 | reg << 3, 0x01]))
            with self.assertRaisesRegex(NotImplementedError, f"opcode 0F BA /{reg}"):
                cpu.step()

    def test_the_address_of_a_16_bit_offset_wraps_at_64_kb(self):
        cpu = self.start(bytes([0x66, 0x0F, 0xAB, 0x0E, 0xFE, 0xFF]), ecx=32)  # bts [FFFE], ecx: a doubleword on
        cpu.step()
        self.assertEqual(cpu.rd(0x2000, 0x0002), 1)  # 10002h wraps to 0002h
        self.assertEqual(cpu.rd(0x2000, 0x10002), 0)

    def test_with_32_bit_addresses_the_number_of_the_bit_is_signed_and_the_address_does_not_wrap(self):
        cpu = self.start(bytes([0x67, 0x66, 0x0F, 0xAB, 0x0B]), ebx=0x00010100, ecx=0xFFFFFFFF)  # bts [ebx], ecx
        cpu.step()  # the number is -1: bit 31 of the doubleword before
        self.assertEqual(cpu.rd(0x2000, 0x100FC), 0x80000000)
        cpu = self.start(bytes([0x67, 0x66, 0x0F, 0xAB, 0x0B]), ebx=0x0000FFFC, ecx=64)  # two doublewords on
        cpu.step()
        self.assertEqual(cpu.rd(0x2000, 0x10004), 1)  # 0xFFFC + 8: past 64 KB, and it stays there


class TestBitScanAndBswap(Machine):
    def test_bsf_finds_the_lowest_bit_and_bsr_the_highest(self):
        for value in SAMPLES:
            for second, find in ((0xBC, lambda v: (v & -v).bit_length() - 1), (0xBD, lambda v: v.bit_length() - 1)):
                cpu = self.start(bytes([0x66, 0x0F, second, 0xCB]), ebx=value, ecx=0x77777777)
                cpu.flags = 0x0002 | CARRY | SIGN
                cpu.step()
                if value == 0:
                    self.assertEqual(cpu.get32(CX), 0x77777777)  # left alone
                    self.assertTrue(cpu.flags & ZERO)
                else:
                    self.assertEqual(cpu.get32(CX), find(value), f"{second:02X} {value:08X}")
                    self.assertFalse(cpu.flags & ZERO)
                self.assertTrue(cpu.flags & CARRY and cpu.flags & SIGN)  # the others stay

    def test_the_16_bit_forms_look_at_16_bits(self):
        cpu = self.start(bytes([0x0F, 0xBC, 0xCB]), ebx=0x00010000, ecx=0x5555)  # bsf cx, bx: BX is 0
        cpu.step()
        self.assertEqual((cpu.r[CX], bool(cpu.flags & ZERO)), (0x5555, True))
        cpu = self.start(bytes([0x0F, 0xBD, 0xCB]), ebx=0x00010100, ecx=0)
        cpu.step()
        self.assertEqual(cpu.r[CX], 8)

    def test_a_memory_operand(self):
        cpu = self.start(bytes([0x66, 0x0F, 0xBD, 0x0E, 0x00, 0x01]))
        cpu.wd(0x2000, 0x0100, 0x00F00000)
        cpu.step()
        self.assertEqual(cpu.get32(CX), 23)

    def test_bswap_reverses_the_bytes_and_leaves_the_flags(self):
        for reg in range(8):
            cpu = self.start(bytes([0x0F, 0xC8 + reg]))
            cpu.set32(reg, 0x11223344)
            cpu.flags = 0x0002 | CARRY
            cpu.step()
            self.assertEqual(cpu.get32(reg), 0x44332211)
            self.assertEqual(cpu.flags, 0x0002 | CARRY)
            others = [cpu.get32(i) for i in range(8) if i not in (reg, 4)]  # ESP is the stack pointer of the test
            self.assertEqual(others, [0] * len(others))


class TestCode32(Machine):
    def test_in_a_code_segment_of_32_bits_the_two_byte_instructions_work_on_32_bits(self):
        cpu = self.start(bytes([0x0F, 0xB6, 0xC3]), ebx=0x000000FF, eax=0x12345678)  # movzx eax, bl
        cpu.code32 = True
        cpu.step()
        self.assertEqual(cpu.get32(AX), 0xFF)
        cpu = self.start(bytes([0x66, 0x0F, 0xB6, 0xC3]), ebx=0x000000FF, eax=0x12345678)  # with 66: movzx ax, bl
        cpu.code32 = True
        cpu.step()
        self.assertEqual(cpu.get32(AX), 0x123400FF)
        cpu = self.start(bytes([0x0F, 0x85]) + le32(0x10))  # jne rel32
        cpu.code32 = True
        cpu.flags = 0x0002
        cpu.step()
        self.assertEqual(cpu.ip, 6 + 0x10)

    def test_an_unknown_second_byte_stops_with_its_name(self):
        cpu = self.start(bytes([0x66, 0x0F, 0xA2]))
        with self.assertRaisesRegex(NotImplementedError, "opcode 0F A2 at 1000:0000: 66 0f a2"):
            cpu.step()


if __name__ == "__main__":
    unittest.main()
