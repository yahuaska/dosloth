"""The 32-bit forms of the integer instructions (step 2b): shifts, NOT NEG MUL DIV, IMUL with a constant, the stack,
the near jumps and calls, the string instructions and the loops. Each is checked against a definition written out on
Python numbers, with no help from the CPU class."""
import unittest

from dosloth import flags
from dosloth.cpu import CPU, DivideError
from dosloth.regs import AX, BP, BX, CX, DI, DX, SI, SP
from tests.test_cpu386 import NAMES, SAMPLES, Machine, le32

MASK = 0xFFFFFFFF
SHIFT_NAMES = ["rol", "ror", "rcl", "rcr", "shl", "shr", "sal", "sar"]


def signed32(value):
    return value - (1 << 32) if value >> 31 else value


def shift_reference(kind, value, count, carry):
    """(result, CF) of a rotate or a shift of 32 bits by `count` (its low 5 bits count), from the definitions."""
    count &= 31
    if count == 0:
        return value, carry
    if kind in (4, 6):
        return (value << count) & MASK, (value >> (32 - count)) & 1
    if kind == 5:
        return value >> count, (value >> (count - 1)) & 1
    if kind == 7:
        return (signed32(value) >> count) & MASK, (signed32(value) >> (count - 1)) & 1
    if kind == 0:
        result = ((value << count) | (value >> (32 - count))) & MASK
        return result, result & 1
    if kind == 1:
        result = ((value >> count) | (value << (32 - count))) & MASK
        return result, result >> 31
    ring, size = value | carry << 32, (1 << 33) - 1  # the carry is one more bit of the ring: 33 of them
    if kind == 2:
        ring = ((ring << count) | (ring >> (33 - count))) & size
    else:
        ring = ((ring >> count) | (ring << (33 - count))) & size
    return ring & MASK, ring >> 32


def parity_even(value):
    return bin(value & 0xFF).count("1") % 2 == 0


class TestShifts32(Machine):
    def run_shift(self, code, kind, value, count, carry):
        cpu = self.start(code, ebx=value, ecx=count)
        cpu.flags = 0x0002 | (flags.CF if carry else 0)
        cpu.step()
        return cpu

    def check(self, cpu, kind, value, count, carry, label):
        result, cf = shift_reference(kind, value, count, carry)
        self.assertEqual(cpu.get32(BX), result, label)
        self.assertEqual(bool(cpu.flags & flags.CF), bool(cf), label)
        if count & 31 and kind in (4, 5, 6, 7):
            self.assertEqual(bool(cpu.flags & flags.ZF), result == 0, label)
            self.assertEqual(bool(cpu.flags & flags.SF), bool(result >> 31), label)
            self.assertEqual(bool(cpu.flags & flags.PF), parity_even(result), label)
        if count & 31 == 1:  # the overflow is only defined for a shift of one
            top, below = result >> 31, (result >> 30) & 1
            expected = {4: top != cf, 6: top != cf, 5: bool(value >> 31), 7: False, 0: top != cf, 2: top != cf,
                        1: top != below, 3: top != below}[kind]
            self.assertEqual(bool(cpu.flags & flags.OF), bool(expected), label)

    def test_every_operation_by_one_by_cl_and_by_a_byte_agrees_with_the_definition(self):
        for kind in range(8):
            for value in SAMPLES:
                for carry in (0, 1):
                    for count in (0, 1, 2, 5, 16, 31, 32, 33, 0xFF):
                        modrm = 0xC3 | kind << 3
                        label = f"{SHIFT_NAMES[kind]} {value:08X} by {count} carry {carry}"
                        cpu = self.run_shift(bytes([0x66, 0xD3, modrm]), kind, value, count, carry)  # by CL
                        self.check(cpu, kind, value, count, carry, "cl " + label)
                        cpu = self.run_shift(bytes([0x66, 0xC1, modrm, count]), kind, value, count, carry)  # by a byte
                        self.check(cpu, kind, value, count, carry, "imm " + label)
                        self.assertEqual(cpu.ip, 4, label)
                        if count == 1:
                            cpu = self.run_shift(bytes([0x66, 0xD1, modrm]), kind, value, 1, carry)  # by 1
                            self.check(cpu, kind, value, 1, carry, "one " + label)

    def test_a_count_of_a_multiple_of_32_changes_nothing_not_even_the_flags(self):
        cpu = self.run_shift(bytes([0x66, 0xC1, 0xE3, 32]), 4, 0x80000001, 0, 1)
        self.assertEqual((cpu.get32(BX), cpu.flags), (0x80000001, 0x0002 | flags.CF))

    def test_a_memory_operand(self):
        cpu = self.start(bytes([0x66, 0xC1, 0x26, 0x00, 0x01, 0x04]))  # shl dword [0100], 4
        cpu.wd(0x2000, 0x0100, 0x12345678)
        cpu.step()
        self.assertEqual(cpu.rd(0x2000, 0x0100), 0x23456780)
        self.assertEqual(cpu.ip, 6)

    def test_the_other_half_of_the_register_is_not_a_16_bit_shift(self):
        cpu = self.run_shift(bytes([0x66, 0xC1, 0xE3, 8]), 4, 0x00FF00FF, 0, 0)  # shl ebx, 8
        self.assertEqual(cpu.get32(BX), 0xFF00FF00)

    def test_the_forms_of_the_186_with_a_byte_count_work_for_16_bits_and_for_bytes_too(self):
        cpu = self.start(bytes([0xC1, 0xE3, 0x04]), ebx=0xAAAA1234)  # shl bx, 4
        cpu.step()
        self.assertEqual((cpu.r[BX], cpu.rh[BX]), (0x2340, 0xAAAA))
        self.assertEqual(bool(cpu.flags & flags.CF), True)  # 1 was the last bit out
        cpu = self.start(bytes([0xC0, 0xEB, 0x03]), ebx=0x000000F8)  # shr bl, 3
        cpu.step()
        self.assertEqual(cpu.get8(3), 0x1F)
        cpu = self.start(bytes([0xC1, 0xE3, 0x24]), ebx=0x0003)  # a count of 36 is 4: the count has its low 5 bits
        cpu.step()
        self.assertEqual(cpu.r[BX], 0x0030)


def reference_flags_sub(a, b):
    result = (a - b) & MASK
    return result, {"CF": a < b, "OF": bool((a ^ b) & (a ^ result) & 0x80000000), "ZF": result == 0,
                    "SF": bool(result >> 31), "PF": parity_even(result)}


class TestGroup3With32Bits(Machine):
    def test_test_with_a_constant(self):
        for value in SAMPLES:
            for imm in (0, 0x80000000, 0x0000FFFF, 0xFFFFFFFF, 0x12345678):
                cpu = self.start(bytes([0x66, 0xF7, 0xC3]) + le32(imm), ebx=value)
                cpu.step()
                result = value & imm
                label = f"{value:08X} {imm:08X}"
                self.assertEqual((bool(cpu.flags & flags.ZF), bool(cpu.flags & flags.SF)),
                                 (result == 0, bool(result >> 31)), label)
                self.assertFalse(cpu.flags & (flags.CF | flags.OF), label)
                self.assertEqual((cpu.get32(BX), cpu.ip), (value, 7), label)

    def test_not_flips_every_bit_and_changes_no_flag(self):
        for value in SAMPLES:
            cpu = self.start(bytes([0x66, 0xF7, 0xD3]), ebx=value)
            cpu.flags = 0x0002 | flags.CF | flags.ZF
            cpu.step()
            self.assertEqual((cpu.get32(BX), cpu.flags), (~value & MASK, 0x0002 | flags.CF | flags.ZF), f"{value:08X}")

    def test_neg_is_zero_minus_the_value_with_the_flags_of_a_subtraction(self):
        for value in SAMPLES:
            cpu = self.start(bytes([0x66, 0xF7, 0xDB]), ebx=value)
            cpu.step()
            result, expected = reference_flags_sub(0, value)
            self.assertEqual(cpu.get32(BX), result, f"{value:08X}")
            for name, mask in (("CF", flags.CF), ("OF", flags.OF), ("ZF", flags.ZF), ("SF", flags.SF),
                               ("PF", flags.PF)):
                self.assertEqual(bool(cpu.flags & mask), expected[name], f"{name} {value:08X}")

    def test_mul_puts_the_whole_product_in_edx_eax(self):
        for a in SAMPLES:
            for b in SAMPLES:
                cpu = self.start(bytes([0x66, 0xF7, 0xE3]), eax=a, ebx=b, edx=0x55555555)  # mul ebx
                cpu.step()
                product = a * b
                self.assertEqual((cpu.get32(AX), cpu.get32(DX)), (product & MASK, product >> 32), f"{a:08X}*{b:08X}")
                needs = product >> 32 != 0
                self.assertEqual((bool(cpu.flags & flags.CF), bool(cpu.flags & flags.OF)), (needs, needs))

    def test_imul_is_signed_and_says_whether_the_low_half_is_enough(self):
        for a in SAMPLES:
            for b in SAMPLES:
                cpu = self.start(bytes([0x66, 0xF7, 0xEB]), eax=a, ebx=b)  # imul ebx
                cpu.step()
                product = signed32(a) * signed32(b)
                wide = product & ((1 << 64) - 1)
                self.assertEqual((cpu.get32(AX), cpu.get32(DX)), (wide & MASK, wide >> 32), f"{a:08X}*{b:08X}")
                needs = not -(1 << 31) <= product < 1 << 31
                self.assertEqual((bool(cpu.flags & flags.CF), bool(cpu.flags & flags.OF)), (needs, needs))

    def test_div_gives_the_quotient_in_eax_and_the_remainder_in_edx(self):
        for high, low in ((0, 0), (0, 100), (1, 5), (0x12345678, 0x9ABCDEF0), (0, 0xFFFFFFFF), (3, 0)):
            for divisor in (1, 3, 7, 0x10000, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF):
                dividend = high << 32 | low
                cpu = self.start(bytes([0x66, 0xF7, 0xF3]), eax=low, edx=high, ebx=divisor)  # div ebx
                if dividend // divisor >> 32:
                    with self.assertRaises(DivideError):
                        cpu.step()
                    self.assertEqual((cpu.get32(AX), cpu.get32(DX)), (low, high))  # nothing is changed
                    continue
                cpu.step()
                self.assertEqual((cpu.get32(AX), cpu.get32(DX)), divmod(dividend, divisor), f"{dividend:X}/{divisor:X}")

    def test_idiv_cuts_towards_zero_and_the_remainder_has_the_sign_of_the_dividend(self):
        for dividend in (0, 7, -7, 100, -100, 1 << 40, -(1 << 40), 0x7FFFFFFF, -0x80000000):
            for divisor in (1, -1, 2, -2, 3, -3, 7, 0x10000, -0x10000):
                wide = dividend & ((1 << 64) - 1)
                cpu = self.start(bytes([0x66, 0xF7, 0xFB]), eax=wide & MASK, edx=wide >> 32, ebx=divisor & MASK)
                quotient = abs(dividend) // abs(divisor) * (1 if (dividend < 0) == (divisor < 0) else -1)
                remainder = dividend - quotient * divisor
                if not -(1 << 31) <= quotient < 1 << 31:
                    with self.assertRaises(DivideError):
                        cpu.step()
                    continue
                cpu.step()
                self.assertEqual((cpu.get32(AX), cpu.get32(DX)), (quotient & MASK, remainder & MASK),
                                 f"{dividend}/{divisor}")

    def test_a_division_by_zero_is_an_error(self):
        cpu = self.start(bytes([0x66, 0xF7, 0xF3]), eax=1, ebx=0)
        with self.assertRaises(DivideError):
            cpu.step()

    def test_the_16_bit_forms_are_as_they_were(self):
        cpu = self.start(bytes([0xF7, 0xE3]), eax=0xAAAA0100, ebx=0xBBBB0100, edx=0xCCCC0000)  # mul bx
        cpu.step()
        self.assertEqual((cpu.get32(AX), cpu.get32(DX)), (0xAAAA0000, 0xCCCC0001))


class TestImulWithAConstant(Machine):
    def test_the_three_operand_form_with_32_bits(self):
        for a in SAMPLES:
            for imm in (0, 1, -1, 3, -7, 0x12345, 0x7FFFFFFF, -0x80000000):
                for code, size in ((bytes([0x66, 0x6B, 0xCB, imm & 0xFF]), 4),
                                   (bytes([0x66, 0x69, 0xCB]) + le32(imm), 7)):
                    if size == 4 and not -128 <= imm < 128:
                        continue
                    cpu = self.start(code, ebx=a)  # imul ecx, ebx, imm
                    cpu.step()
                    product = signed32(a) * imm
                    self.assertEqual(cpu.get32(CX), product & MASK, f"{a:08X}*{imm} {code.hex()}")
                    fits = -(1 << 31) <= product < 1 << 31
                    self.assertEqual((bool(cpu.flags & flags.CF), bool(cpu.flags & flags.OF)), (not fits, not fits))
                    self.assertEqual((cpu.get32(BX), cpu.ip), (a, size))

    def test_the_form_with_a_memory_operand(self):
        cpu = self.start(bytes([0x66, 0x6B, 0x0E, 0x00, 0x01, 0x0A]))  # imul ecx, [0100], 10
        cpu.wd(0x2000, 0x0100, 0xFFFFFFFE)  # -2
        cpu.step()
        self.assertEqual(cpu.get32(CX), (-20) & MASK)

    def test_the_forms_of_the_186_with_16_bits(self):
        cpu = self.start(bytes([0x6B, 0xCB, 0xFE]), ebx=0xAAAA0005, ecx=0xBBBB0000)  # imul cx, bx, -2
        cpu.step()
        self.assertEqual((cpu.get32(CX), bool(cpu.flags & flags.CF)), (0xBBBBFFF6, False))
        cpu = self.start(bytes([0x69, 0xCB, 0x00, 0x10]), ebx=0x0010)  # imul cx, bx, 1000h: 10000h does not fit
        cpu.step()
        self.assertEqual((cpu.r[CX], bool(cpu.flags & flags.CF), bool(cpu.flags & flags.OF)), (0, True, True))
        self.assertEqual(cpu.ip, 4)


class TestGroup5And4(Machine):
    def test_inc_and_dec_of_a_register_and_of_memory_leave_the_carry_alone(self):
        for value in SAMPLES:
            for carry in (0, 1):
                for reg, delta in ((0, 1), (1, -1)):
                    cpu = self.start(bytes([0x66, 0xFF, 0xC3 | reg << 3]), ebx=value)
                    cpu.flags = 0x0002 | (flags.CF if carry else 0)
                    cpu.step()
                    result = (value + delta) & MASK
                    self.assertEqual(cpu.get32(BX), result, f"{value:08X} {delta}")
                    self.assertEqual(bool(cpu.flags & flags.CF), bool(carry))
                    self.assertEqual(bool(cpu.flags & flags.ZF), result == 0)
                    self.assertEqual(bool(cpu.flags & flags.SF), bool(result >> 31))
                    overflow = value == (0x7FFFFFFF if delta == 1 else 0x80000000)
                    self.assertEqual(bool(cpu.flags & flags.OF), overflow, f"{value:08X} {delta}")
        cpu = self.start(bytes([0x66, 0xFF, 0x06, 0x00, 0x01]))  # inc dword [0100]
        cpu.wd(0x2000, 0x0100, 0x0000FFFF)
        cpu.step()
        self.assertEqual(cpu.rd(0x2000, 0x0100), 0x00010000)

    def test_a_call_through_a_register_and_through_memory_pushes_a_doubleword(self):
        cpu = self.start(bytes([0x66, 0xFF, 0xD3]), ebx=0x00000300)  # call ebx
        cpu.step()
        self.assertEqual((cpu.ip, cpu.r[SP], cpu.rd(0x4000, 0x01FC)), (0x0300, 0x01FC, 3))
        cpu = self.start(bytes([0x66, 0xFF, 0x16, 0x00, 0x01]))  # call dword [0100]
        cpu.wd(0x2000, 0x0100, 0x00000444)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.r[SP], cpu.rd(0x4000, 0x01FC)), (0x0444, 0x01FC, 5))

    def test_a_jump_through_a_register_and_through_memory(self):
        cpu = self.start(bytes([0x66, 0xFF, 0xE3]), ebx=0x00000300)  # jmp ebx
        cpu.step()
        self.assertEqual((cpu.ip, cpu.r[SP]), (0x0300, 0x0200))
        cpu = self.start(bytes([0x66, 0xFF, 0x26, 0x00, 0x01]))  # jmp dword [0100]
        cpu.wd(0x2000, 0x0100, 0x00000555)
        cpu.step()
        self.assertEqual(cpu.ip, 0x0555)

    def test_push_of_a_register_and_of_memory(self):
        cpu = self.start(bytes([0x66, 0xFF, 0xF3]), ebx=0xCAFEBABE)  # push ebx
        cpu.step()
        self.assertEqual((cpu.rd(0x4000, 0x01FC), cpu.r[SP]), (0xCAFEBABE, 0x01FC))
        cpu = self.start(bytes([0x66, 0xFF, 0x36, 0x00, 0x01]))  # push dword [0100]
        cpu.wd(0x2000, 0x0100, 0x01234567)
        cpu.step()
        self.assertEqual(cpu.rd(0x4000, 0x01FC), 0x01234567)

    def test_push_esp_pushes_the_value_from_before(self):
        cpu = self.start(bytes([0x66, 0xFF, 0xF4]), esp=0x0200)
        cpu.step()
        self.assertEqual(cpu.rd(0x4000, 0x01FC), 0x0200)

    def test_the_far_forms_with_a_register_and_the_other_numbers_stop_the_emulator(self):
        for reg, modrm_byte in ((3, 0xDB), (5, 0xEB), (7, 0x06 | 7 << 3)):
            cpu = self.start(bytes([0x66, 0xFF, modrm_byte, 0x00, 0x01]))
            with self.assertRaisesRegex(NotImplementedError, f"opcode 66 FF /{reg}"):
                cpu.step()

    def test_a_far_call_through_memory_pushes_cs_and_eip_as_doublewords_and_goes_to_the_pointer(self):
        cpu = self.start(bytes([0x66, 0xFF, 0x1E, 0x00, 0x01]))  # call far [0100]
        cpu.wd(0x2000, 0x0100, 0x00000555)  # the offset of 32 bits
        cpu.ww(0x2000, 0x0104, 0x3000)  # and the selector
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.r[SP]), (0x3000, 0x0555, 0x01F8))
        self.assertEqual((cpu.rd(0x4000, 0x01F8), cpu.rd(0x4000, 0x01FC)), (0x0005, 0x1000))  # EIP, and CS under it

    def test_a_far_jump_through_memory_pushes_nothing(self):
        cpu = self.start(bytes([0x66, 0xFF, 0x2E, 0x00, 0x01]))  # jmp far [0100]
        cpu.wd(0x2000, 0x0100, 0x00000777)
        cpu.ww(0x2000, 0x0104, 0x3000)
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.r[SP]), (0x3000, 0x0777, 0x0200))

    def test_a_far_return_of_doublewords_takes_eip_and_cs_and_the_arguments(self):
        cpu = self.start(bytes([0x66, 0xCB]))  # retf
        cpu.push32(0x1111_2000)  # CS: the high half is not a part of the selector
        cpu.push32(0x0000_0444)
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.r[SP]), (0x2000, 0x0444, 0x0200))
        cpu = self.start(bytes([0x66, 0xCA, 0x08, 0x00]))  # retf 8
        cpu.push32(0x2000)
        cpu.push32(0x0444)
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.r[SP]), (0x2000, 0x0444, 0x0208))

    def test_iretd_takes_eip_cs_and_the_flags_of_32_bits(self):
        cpu = self.start(bytes([0xCF]))
        cpu.code32 = True  # in a 32-bit code segment CF is IRETD
        cpu.push32(flags.CF | flags.ZF | 0x0002)
        cpu.push32(0x2000)
        cpu.push32(0x0444)
        cpu.flags = 0x0002
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.r[SP]), (0x2000, 0x0444, 0x0200))
        self.assertEqual(cpu.flags & (flags.CF | flags.ZF), flags.CF | flags.ZF)

    def test_pop_into_a_register_and_into_memory(self):
        cpu = self.start(bytes([0x66, 0x8F, 0xC3]))  # pop ebx
        cpu.push32(0x89ABCDEF)
        cpu.step()
        self.assertEqual((cpu.get32(BX), cpu.r[SP]), (0x89ABCDEF, 0x0200))
        cpu = self.start(bytes([0x66, 0x8F, 0x06, 0x00, 0x01]))  # pop dword [0100]
        cpu.push32(0x11223344)
        cpu.step()
        self.assertEqual((cpu.rd(0x2000, 0x0100), cpu.r[SP]), (0x11223344, 0x0200))
        cpu = self.start(bytes([0x66, 0x8F, 0xC8]))  # 8F /1 is nothing
        with self.assertRaisesRegex(NotImplementedError, "opcode 66 8F /1"):
            cpu.step()


class TestStackInstructions32(Machine):
    def test_push_of_a_constant_of_32_bits_and_of_a_byte_widened(self):
        cpu = self.start(bytes([0x66, 0x68]) + le32(0xDEADBEEF))
        cpu.step()
        self.assertEqual((cpu.rd(0x4000, 0x01FC), cpu.r[SP], cpu.ip), (0xDEADBEEF, 0x01FC, 6))
        for byte, widened in ((0x7F, 0x7F), (0xFE, 0xFFFFFFFE), (0x80, 0xFFFFFF80), (0x00, 0)):
            cpu = self.start(bytes([0x66, 0x6A, byte]))
            cpu.step()
            self.assertEqual((cpu.rd(0x4000, 0x01FC), cpu.ip), (widened, 3), hex(byte))

    def test_pushad_puts_the_eight_registers_and_popad_takes_them_back_without_esp(self):
        values = [0x11111111 * (i + 1) for i in range(8)]
        cpu = self.start(bytes([0x66, 0x60, 0x66, 0x61]))
        for i, value in enumerate(values):
            cpu.set32(i, value)
        cpu.set32(SP, 0x00010200)  # a high half of ESP, to be pushed whole
        cpu.r[SP] = 0x0200
        cpu.step()
        pushed = [cpu.rd(0x4000, 0x0200 - 4 * (i + 1)) for i in range(8)]
        expected = [values[0], values[1], values[2], values[3], 0x00010200, values[5], values[6], values[7]]
        self.assertEqual(pushed, expected)
        self.assertEqual(cpu.r[SP], 0x0200 - 32)
        for i in range(8):
            if i != 4:
                cpu.set32(i, 0)
        cpu.step()
        self.assertEqual([cpu.get32(i) for i in range(8) if i != 4], [values[i] for i in range(8) if i != 4])
        self.assertEqual(cpu.r[SP], 0x0200)

    def test_leave_gives_the_frame_back(self):
        cpu = self.start(bytes([0x66, 0xC9]), ebp=0x00000180, esp=0x0100)
        cpu.wd(0x4000, 0x0180, 0xAABBCCDD)
        cpu.step()
        self.assertEqual((cpu.get32(BP), cpu.r[SP]), (0xAABBCCDD, 0x0184))

    def test_enter_with_every_level_agrees_with_the_definition(self):
        for level in range(0, 5):
            for size in (0, 12):
                cpu = self.start(bytes([0x66, 0xC8, size, 0, level]), ebp=0x00060300, esp=0x0200)
                for i in range(8):
                    cpu.wd(0x4000, 0x0300 - 4 * i, 0x9000 + i)
                cpu.step()
                bp, sp, memory = 0x0300, 0x0200, {}
                sp -= 4
                memory[sp] = 0x00060300
                frame = sp
                if level:
                    for _ in range(level - 1):
                        bp -= 4
                        sp -= 4
                        memory[sp] = 0x9000 + (0x0300 - bp) // 4
                    sp -= 4
                    memory[sp] = frame
                label = f"level {level} size {size}"
                self.assertEqual((cpu.get32(BP), cpu.r[SP]), (frame, sp - size), label)
                for address, value in memory.items():
                    self.assertEqual(cpu.rd(0x4000, address), value, f"{label} at {address:04X}")


class TestNearJumpsAndCalls32(Machine):
    def test_call_with_a_distance_of_32_bits_pushes_a_doubleword(self):
        cpu = self.start(bytes([0x66, 0xE8]) + le32(0x100))
        cpu.step()
        self.assertEqual((cpu.ip, cpu.r[SP], cpu.rd(0x4000, 0x01FC)), (0x106, 0x01FC, 6))

    def test_a_distance_can_be_backwards(self):
        cpu = self.start(bytes([0x66, 0xE8]) + le32(-0x100))
        cpu.ip = 0x400
        cpu.mem[0x10400:0x10406] = bytes([0x66, 0xE8]) + le32(-0x100)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.rd(0x4000, 0x01FC)), (0x306, 0x406))

    def test_jmp_with_a_distance_of_32_bits(self):
        cpu = self.start(bytes([0x66, 0xE9]) + le32(0x20))
        cpu.step()
        self.assertEqual((cpu.ip, cpu.r[SP]), (0x26, 0x0200))

    def test_an_address_past_64_kb_is_cut_to_16_bits_for_now(self):
        for opcode in (0xE8, 0xE9):
            cpu = self.start(b"")
            cpu.mem[0x1FF00:0x1FF06] = bytes([0x66, opcode]) + le32(0x200)
            cpu.ip = 0xFF00
            cpu.step()
            self.assertEqual(cpu.ip, 0x0106, f"{opcode:02X}")  # FF06h + 200h is 10106h
        cpu = self.start(bytes([0x66, 0xFF, 0xD3]), ebx=0x00010300)  # call ebx
        cpu.step()
        self.assertEqual(cpu.ip, 0x0300)
        cpu = self.start(bytes([0x66, 0xFF, 0xE3]), ebx=0x00020400)  # jmp ebx
        cpu.step()
        self.assertEqual(cpu.ip, 0x0400)

    def test_ret_takes_a_doubleword_and_ret_with_a_count_drops_the_arguments(self):
        cpu = self.start(bytes([0x66, 0xC3]))
        cpu.push32(0x0000ABCD)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.r[SP]), (0xABCD, 0x0200))
        cpu = self.start(bytes([0x66, 0xC2, 0x08, 0x00]))
        cpu.push32(0x0000ABCD)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.r[SP]), (0xABCD, 0x0208))

    def test_a_call_and_its_return_come_back_to_the_next_instruction(self):
        cpu = self.start(bytes([0x66, 0xE8]) + le32(2) + bytes([0x90, 0x90, 0x66, 0xC3]))
        cpu.step()
        self.assertEqual(cpu.ip, 8)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.r[SP]), (6, 0x0200))


def element(value, bits):
    return value.to_bytes(bits // 8, "little")


class TestStringInstructions32(Machine):
    """The data is at DS = ES = 2000h; DF is clear unless a test sets it."""

    def go(self, code, steps=1, **registers):
        cpu = self.start(code, **registers)
        cpu.flags &= ~flags.DF
        for _ in range(steps):
            cpu.step()
        return cpu

    def test_movsd_copies_a_doubleword_and_steps_both_registers_by_4(self):
        cpu = self.start(bytes([0x66, 0xA5]), esi=0x0100, edi=0x0200)
        cpu.flags &= ~flags.DF
        cpu.wd(0x2000, 0x0100, 0xCAFEF00D)
        cpu.step()
        self.assertEqual((cpu.rd(0x2000, 0x0200), cpu.r[SI], cpu.r[DI]), (0xCAFEF00D, 0x0104, 0x0204))

    def test_the_direction_flag_goes_backwards(self):
        cpu = self.start(bytes([0x66, 0xA5]), esi=0x0100, edi=0x0200)  # DF is set by start()
        cpu.wd(0x2000, 0x0100, 0x01020304)
        cpu.step()
        self.assertEqual((cpu.rd(0x2000, 0x0200), cpu.r[SI], cpu.r[DI]), (0x01020304, 0x00FC, 0x01FC))

    def test_rep_movsd_with_a_count_copies_a_block(self):
        cpu = self.start(bytes([0x66, 0xF3, 0xA5]), esi=0x0100, edi=0x0300, ecx=5)
        cpu.flags &= ~flags.DF
        for i in range(5):
            cpu.wd(0x2000, 0x0100 + 4 * i, 0x1000 + i)
        cpu.step()
        self.assertEqual([cpu.rd(0x2000, 0x0300 + 4 * i) for i in range(5)], [0x1000 + i for i in range(5)])
        self.assertEqual((cpu.r[CX], cpu.r[SI], cpu.r[DI]), (0, 0x0114, 0x0314))
        self.assertEqual(cpu.rd(0x2000, 0x0314), 0)

    def test_a_count_of_zero_copies_nothing(self):
        cpu = self.go(bytes([0x66, 0xF3, 0xA5]), esi=0x0100, edi=0x0300, ecx=0)
        self.assertEqual((cpu.r[SI], cpu.r[DI]), (0x0100, 0x0300))

    def test_stosd_stores_eax_and_rep_fills(self):
        cpu = self.go(bytes([0x66, 0xF3, 0xAB]), edi=0x0100, ecx=3, eax=0xA1B2C3D4)
        self.assertEqual([cpu.rd(0x2000, 0x0100 + 4 * i) for i in range(4)], [0xA1B2C3D4] * 3 + [0])
        self.assertEqual((cpu.r[DI], cpu.r[CX]), (0x010C, 0))

    def test_lodsd_loads_eax(self):
        cpu = self.start(bytes([0x66, 0xAD]), esi=0x0100, eax=0)
        cpu.flags &= ~flags.DF
        cpu.wd(0x2000, 0x0100, 0x87654321)
        cpu.step()
        self.assertEqual((cpu.get32(AX), cpu.r[SI]), (0x87654321, 0x0104))

    def test_scasd_compares_eax_with_a_doubleword_and_sets_the_flags_of_a_subtraction(self):
        for a, b in ((5, 5), (5, 6), (6, 5), (0x80000000, 1), (0, 0xFFFFFFFF)):
            cpu = self.start(bytes([0x66, 0xAF]), edi=0x0100, eax=a)
            cpu.flags &= ~flags.DF
            cpu.wd(0x2000, 0x0100, b)
            cpu.step()
            result, expected = reference_flags_sub(a, b)
            for name, mask in (("CF", flags.CF), ("OF", flags.OF), ("ZF", flags.ZF), ("SF", flags.SF)):
                self.assertEqual(bool(cpu.flags & mask), expected[name], f"{name} {a:X} {b:X}")
            self.assertEqual((cpu.get32(AX), cpu.r[DI]), (a, 0x0104))

    def test_cmpsd_compares_two_doublewords(self):
        cpu = self.start(bytes([0x66, 0xA7]), esi=0x0100, edi=0x0200)
        cpu.flags &= ~flags.DF
        cpu.wd(0x2000, 0x0100, 3)
        cpu.wd(0x2000, 0x0200, 9)
        cpu.step()
        self.assertEqual((bool(cpu.flags & flags.CF), bool(cpu.flags & flags.ZF), cpu.r[SI], cpu.r[DI]),
                         (True, False, 0x0104, 0x0204))

    def test_repe_cmpsd_stops_at_the_first_difference_and_repne_scasd_at_the_first_match(self):
        cpu = self.start(bytes([0x66, 0xF3, 0xA7]), esi=0x0100, edi=0x0200, ecx=5)
        cpu.flags &= ~flags.DF
        for i, (a, b) in enumerate(((1, 1), (2, 2), (3, 9), (4, 4), (5, 5))):
            cpu.wd(0x2000, 0x0100 + 4 * i, a)
            cpu.wd(0x2000, 0x0200 + 4 * i, b)
        cpu.step()
        self.assertEqual((cpu.r[CX], cpu.r[SI], bool(cpu.flags & flags.ZF)), (2, 0x010C, False))
        cpu = self.start(bytes([0x66, 0xF2, 0xAF]), edi=0x0100, ecx=5, eax=7)
        cpu.flags &= ~flags.DF
        for i, value in enumerate((1, 2, 7, 4, 5)):
            cpu.wd(0x2000, 0x0100 + 4 * i, value)
        cpu.step()
        self.assertEqual((cpu.r[CX], cpu.r[DI], bool(cpu.flags & flags.ZF)), (2, 0x010C, True))

    def test_the_byte_forms_do_not_change_with_the_size_prefix(self):
        cpu = self.go(bytes([0x66, 0xA4]), esi=0x0100, edi=0x0200)
        self.assertEqual((cpu.r[SI], cpu.r[DI]), (0x0101, 0x0201))

    def test_the_16_bit_address_registers_wrap_at_16_bits_even_with_the_32_bit_size(self):
        cpu = self.go(bytes([0x66, 0xA5]), esi=0xAAAAFFFE, edi=0xBBBB0100)
        self.assertEqual((cpu.r[SI], cpu.rh[SI], cpu.r[DI], cpu.rh[DI]), (0x0002, 0xAAAA, 0x0104, 0xBBBB))

    def test_with_32_bit_addresses_the_registers_are_esi_edi_and_ecx_whole(self):
        cpu = self.start(bytes([0x67, 0xF3, 0xA4]), esi=0x00010000, edi=0x00020000, ecx=0x00010003)
        cpu.flags &= ~flags.DF
        cpu.mem[0x30000:0x30003] = bytes([1, 2, 3])  # DS:10000h
        cpu.step()
        self.assertEqual(bytes(cpu.mem[0x40000:0x40003]), bytes([1, 2, 3]))  # ES:20000h
        self.assertEqual((cpu.get32(SI), cpu.get32(DI), cpu.get32(CX)), (0x00010000 + 0x10003, 0x00020000 + 0x10003,
                                                                           0))

    def test_a_32_bit_address_does_not_wrap_at_64_kb(self):
        cpu = self.start(bytes([0x66, 0x67, 0xAB]), edi=0x0000FFFE, eax=0x11223344)
        cpu.flags &= ~flags.DF
        cpu.step()
        self.assertEqual((cpu.get32(DI), cpu.rd(0x2000, 0xFFFE)), (0x00010002, 0x11223344))

    def test_a_32_bit_address_wraps_at_32_bits(self):
        cpu = self.start(bytes([0x67, 0xAA]), edi=0xFFFFFFFF, eax=0x5A)
        cpu.flags &= ~flags.DF
        cpu.step()
        self.assertEqual(cpu.get32(DI), 0)

    def test_the_count_of_32_bit_addresses_is_ecx_so_a_count_of_the_low_half_only_is_not_the_end(self):
        cpu = self.start(bytes([0x67, 0xF3, 0xAA]), edi=0x0000, ecx=0x00010000, eax=0x7)
        cpu.flags &= ~flags.DF
        cpu.step()
        self.assertEqual((cpu.get32(CX), cpu.get32(DI)), (0, 0x00010000))
        cpu = self.start(bytes([0xF3, 0xAA]), edi=0x0000, ecx=0x00010000, eax=0x7)  # with 16 bits the count is CX: 0
        cpu.flags &= ~flags.DF
        cpu.step()
        self.assertEqual((cpu.get32(CX), cpu.get32(DI)), (0x00010000, 0))

    def test_every_string_instruction_with_32_bit_addresses(self):
        cpu = self.start(bytes([0x67, 0xAD]), esi=0x00000100)  # lodsw: AX
        cpu.flags &= ~flags.DF
        cpu.wd(0x2000, 0x0100, 0x12345678)
        cpu.step()
        self.assertEqual((cpu.r[AX], cpu.get32(SI)), (0x5678, 0x0102))
        cpu = self.start(bytes([0x67, 0xAC]), esi=0x00000100)  # lodsb
        cpu.flags &= ~flags.DF
        cpu.wb(0x2000, 0x0100, 0x99)
        cpu.step()
        self.assertEqual((cpu.get8(0), cpu.get32(SI)), (0x99, 0x0101))
        cpu = self.start(bytes([0x67, 0xAE]), edi=0x00000100, eax=0x42)  # scasb
        cpu.flags &= ~flags.DF
        cpu.wb(0x2000, 0x0100, 0x42)
        cpu.step()
        self.assertEqual((bool(cpu.flags & flags.ZF), cpu.get32(DI)), (True, 0x0101))
        cpu = self.start(bytes([0x67, 0xA6]), esi=0x0100, edi=0x0200)  # cmpsb
        cpu.flags &= ~flags.DF
        cpu.wb(0x2000, 0x0100, 1)
        cpu.wb(0x2000, 0x0200, 2)
        cpu.step()
        self.assertEqual((bool(cpu.flags & flags.CF), cpu.get32(SI), cpu.get32(DI)), (True, 0x0101, 0x0201))
        cpu = self.start(bytes([0x67, 0xA7]), esi=0x0100, edi=0x0200)  # cmpsw
        cpu.flags &= ~flags.DF
        cpu.ww(0x2000, 0x0100, 0x1234)
        cpu.ww(0x2000, 0x0200, 0x1234)
        cpu.step()
        self.assertEqual((bool(cpu.flags & flags.ZF), cpu.get32(SI), cpu.get32(DI)), (True, 0x0102, 0x0202))

    def test_a_segment_prefix_changes_the_source_and_not_the_destination(self):
        cpu = self.start(bytes([0x26, 0x66, 0xA5]), esi=0x0100, edi=0x0200)  # movsd with es: in front
        cpu.flags &= ~flags.DF
        cpu.s[CPU.ES] = 0x3000
        cpu.wd(0x3000, 0x0100, 0x0BADF00D)  # DS:0100 is zeros: the source is read from ES now
        cpu.step()
        self.assertEqual(cpu.rd(0x3000, 0x0200), 0x0BADF00D)  # and the destination is ES as well


class TestLoopsAndXlat(Machine):
    def test_loop_with_32_bit_addresses_counts_ecx(self):
        code = bytes([0x67, 0xE2, 0xFD])  # loop to itself, the displacement from the next instruction
        cpu = self.start(code, ecx=0x00010000)
        cpu.step()
        self.assertEqual((cpu.get32(CX), cpu.ip), (0x0000FFFF, 0))  # taken: ECX is not 0 (CX would be 0)
        cpu = self.start(code, ecx=1)
        cpu.step()
        self.assertEqual((cpu.get32(CX), cpu.ip), (0, 3))
        cpu = self.start(code, ecx=0)  # 0 - 1 wraps to FFFFFFFF: taken
        cpu.step()
        self.assertEqual((cpu.get32(CX), cpu.ip), (0xFFFFFFFF, 0))

    def test_loope_and_loopne_with_32_bit_addresses_also_look_at_zf(self):
        for opcode, zero, taken in ((0xE1, True, True), (0xE1, False, False), (0xE0, True, False), (0xE0, False, True)):
            cpu = self.start(bytes([0x67, opcode, 0x10]), ecx=0x00020000)
            cpu.flags = 0x0002 | (flags.ZF if zero else 0)
            cpu.step()
            self.assertEqual((cpu.ip, cpu.get32(CX)), (0x13 if taken else 3, 0x0001FFFF), f"{opcode:02X} {zero}")

    def test_loop_with_16_bit_addresses_still_counts_cx_and_leaves_the_high_half_alone(self):
        cpu = self.start(bytes([0xE2, 0x10]), ecx=0x00020005)
        cpu.step()
        self.assertEqual((cpu.get32(CX), cpu.ip), (0x00020004, 0x12))

    def test_jecxz_looks_at_all_of_ecx(self):
        for ecx, taken in ((0, True), (0x00010000, False), (1, False), (0x80000000, False)):
            cpu = self.start(bytes([0x67, 0xE3, 0x10]), ecx=ecx)
            cpu.step()
            self.assertEqual(cpu.ip, 0x13 if taken else 3, f"{ecx:X}")
        cpu = self.start(bytes([0xE3, 0x10]), ecx=0x00010000)  # jcxz: only CX
        cpu.step()
        self.assertEqual(cpu.ip, 0x12)

    def test_the_loops_are_not_changed_by_the_size_prefix(self):
        cpu = self.start(bytes([0x66, 0xE2, 0x10]), ecx=2)
        cpu.step()
        self.assertEqual(cpu.ip, 0x13)

    def test_xlat_looks_a_byte_up_in_a_table_at_bx(self):
        cpu = self.start(bytes([0xD7]), ebx=0xAAAA0100, eax=0x00000003)
        cpu.mem[0x20103] = 0x77
        cpu.step()
        self.assertEqual((cpu.get8(0), cpu.get32(BX)), (0x77, 0xAAAA0100))
        cpu = self.start(bytes([0xD7]), ebx=0x0000FFFF, eax=0x00000002)  # the offset wraps at 16 bits
        cpu.mem[0x20001] = 0x55
        cpu.step()
        self.assertEqual(cpu.get8(0), 0x55)

    def test_xlat_with_32_bit_addresses_uses_ebx_whole(self):
        cpu = self.start(bytes([0x67, 0xD7]), ebx=0x0000FFFF, eax=0x00000002)  # no wrap: the offset is 10001h
        cpu.mem[0x30001] = 0x66
        cpu.step()
        self.assertEqual(cpu.get8(0), 0x66)
        cpu = self.start(bytes([0x67, 0xD7]), ebx=0xFFFFFFFF, eax=0x00000002)  # wraps at 32 bits: offset 1
        cpu.mem[0x20001] = 0x44
        cpu.step()
        self.assertEqual(cpu.get8(0), 0x44)

    def test_the_segment_of_xlat_can_be_changed(self):
        cpu = self.start(bytes([0x26, 0xD7]), ebx=0x0100, eax=1)
        cpu.s[CPU.ES] = 0x3000
        cpu.mem[0x30101] = 0x33
        cpu.step()
        self.assertEqual(cpu.get8(0), 0x33)


class TestCode32Defaults(Machine):
    def test_in_a_code_segment_of_32_bits_a_shift_is_of_32_bits_and_the_prefix_makes_it_16(self):
        cpu = self.start(bytes([0xC1, 0xE3, 0x04]), ebx=0x12345678)
        cpu.code32 = True
        cpu.step()
        self.assertEqual(cpu.get32(BX), 0x23456780)
        cpu = self.start(bytes([0x66, 0xC1, 0xE3, 0x04]), ebx=0x12345678)
        cpu.code32 = True
        cpu.step()
        self.assertEqual(cpu.get32(BX), 0x12346780)

    def test_a_string_instruction_in_a_code_segment_of_32_bits_uses_the_whole_registers(self):
        cpu = self.start(bytes([0xF3, 0xA5]), esi=0x00010000, edi=0x00020000, ecx=2)
        cpu.code32 = True
        cpu.flags &= ~flags.DF
        cpu.wd(0x2000, 0x10000, 0x11111111)
        cpu.wd(0x2000, 0x10004, 0x22222222)
        cpu.step()
        self.assertEqual((cpu.rd(0x2000, 0x20000), cpu.rd(0x2000, 0x20004), cpu.get32(CX)), (0x11111111, 0x22222222, 0))
        self.assertEqual((cpu.get32(SI), cpu.get32(DI)), (0x00010008, 0x00020008))

    def test_a_loop_in_a_code_segment_of_32_bits_counts_ecx(self):
        cpu = self.start(bytes([0xE2, 0xFE]), ecx=0x00010000)
        cpu.code32 = True
        cpu.step()
        self.assertEqual(cpu.get32(CX), 0x0000FFFF)


if __name__ == "__main__":
    unittest.main()
