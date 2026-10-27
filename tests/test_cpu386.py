import unittest

from mydos import flags, modrm
from mydos.cpu import CPU, Exit
from mydos.regs import AX, BP, BX, CX, DI, DX, SI, SP

# the routine of install.exe that tells the 8086, the 286, the 386 and the 486 from each other: from its memory, as it
# is (the bytes of 19B7:0048 to 19B7:012C). It returns the number 0, 2, 3 or 4 in AL, and leaves it in DS:[0] too.
INSTALLER_CPU_TEST = bytes.fromhex(
    "56571eb809288ed89c5bb8ff0f23c3509d9c582500f03d00f0c6060000000f84ba0081cb00f0539d9c582500f0c60600"
    "00020f84a600669c6658668bc86635000004006650669d669c66586633c1c6060000030f848500c606000004669c6658"
    "668bc86635000020006650669d669c66586633c17466c60601000166b8000000000fa2668916060066890e0a00be0200"
    "bf0e0066b901000000f3a66683f9007505c6061a000166b8010000000fa266a31b006625000f000066c1e808a2000066"
    "a11b00a21f0080261f000f66a11b00a2200080262000f0c02e20000433c0a000001f5f5ecb")

NAMES = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"]
SAMPLES = [0, 1, 2, 0x7F, 0x80, 0xFF, 0x7FFF, 0x8000, 0xFFFF, 0x10000, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x12345678,
           0x89ABCDEF]
ALU = ["add", "or", "adc", "sbb", "and", "sub", "xor", "cmp"]  # the numbers 0-7 of the Alu enum
CARRY, OVERFLOW, ZERO, SIGN, PARITY, AUX = flags.CF, flags.OF, flags.ZF, flags.SF, flags.PF, flags.AF
FIVE = CARRY | OVERFLOW | ZERO | SIGN | PARITY


def le32(value):
    return (value & 0xFFFFFFFF).to_bytes(4, "little")


def reference(name, a, b, carry=0):
    """What the operation gives: (the result, the flags CF OF ZF SF PF AF as a dict), worked out on its own, from the
    definitions: on Python numbers, with no help from the CPU class."""
    mask, sign = 0xFFFFFFFF, 0x80000000
    cf = of = False
    if name in ("add", "adc"):
        total = a + b + (carry if name == "adc" else 0)
        result = total & mask
        cf = total > mask
        of = bool(~(a ^ b) & (a ^ result) & sign)
    elif name in ("sub", "sbb", "cmp"):
        total = a - b - (carry if name == "sbb" else 0)
        result = total & mask
        cf = total < 0
        of = bool((a ^ b) & (a ^ result) & sign)
    else:
        result = {"and": a & b, "or": a | b, "xor": a ^ b}[name]
    return result, {"CF": cf, "OF": of, "ZF": result == 0, "SF": bool(result & sign),
                    "PF": bin(result & 0xFF).count("1") % 2 == 0, "AF": bool((a ^ b ^ result) & 0x10)}


class Machine(unittest.TestCase):
    """A CPU with the code at 1000:0000, data at 2000:0100, a stack at 4000:0200."""

    def setUp(self):
        self.cpu = CPU()

    def start(self, code, **registers):
        cpu = self.cpu
        cpu.mem[0x10000:0x10000 + len(code)] = code
        cpu.s[CPU.CS], cpu.s[CPU.DS], cpu.s[CPU.ES], cpu.s[CPU.SS] = 0x1000, 0x2000, 0x2000, 0x4000
        cpu.r[:] = [0] * 8
        cpu.rh[:] = [0] * 8
        cpu.r[SP] = 0x0200
        cpu.ip = 0
        cpu.flags = 0x0002 | flags.DF | flags.IF
        for name, value in registers.items():
            cpu.set32(NAMES.index(name), value)
        return cpu

    def run_code(self, code, steps=1, **registers):
        cpu = self.start(code, **registers)
        for _ in range(steps):
            cpu.step()
        return cpu

    def register(self, name):
        return self.cpu.get32(NAMES.index(name))

    def flag_values(self):
        cpu = self.cpu
        return {"CF": cpu.get_flag(CARRY), "OF": cpu.get_flag(OVERFLOW), "ZF": cpu.get_flag(ZERO),
                "SF": cpu.get_flag(SIGN), "PF": cpu.get_flag(PARITY), "AF": cpu.get_flag(AUX)}

    def put(self, offset, value, seg=0x2000):
        for i, byte in enumerate(le32(value)):
            self.cpu.wb(seg, offset + i, byte)

    def get(self, offset, seg=0x2000):
        return self.cpu.rd(seg, offset)


class TestTheRegisters(Machine):
    def test_a_32_bit_register_is_its_high_half_and_its_low_half(self):
        cpu = self.start(b"")
        cpu.set32(AX, 0x12345678)
        self.assertEqual((cpu.r[AX], cpu.rh[AX], cpu.get32(AX)), (0x5678, 0x1234, 0x12345678))
        cpu.set32(AX, 0xFFFFFFFF + 5)  # cut to 32 bits
        self.assertEqual(cpu.get32(AX), 4)

    def test_a_write_to_a_16_bit_register_keeps_the_high_half(self):
        cpu = self.run_code(bytes([0xB8, 0xFF, 0xFF]), eax=0x12340000)  # mov ax, FFFF
        self.assertEqual(cpu.get32(AX), 0x1234FFFF)

    def test_a_write_to_an_8_bit_register_keeps_everything_else(self):
        cpu = self.run_code(bytes([0xB0, 0x77, 0xB4, 0x99]), steps=2, eax=0x12345678)  # mov al,77 ; mov ah,99
        self.assertEqual(cpu.get32(AX), 0x12349977)

    def test_arithmetic_on_16_bits_does_not_carry_into_the_high_half(self):
        cpu = self.run_code(bytes([0x05, 0x01, 0x00]), eax=0x1234FFFF)  # add ax, 1
        self.assertEqual((cpu.get32(AX), cpu.get_flag(CARRY)), (0x12340000, True))

    def test_inc_and_dec_of_16_bits_keep_the_high_half(self):
        cpu = self.run_code(bytes([0x40, 0x48, 0x48]), steps=3, eax=0xABCD0000)  # inc ax ; dec ax ; dec ax
        self.assertEqual(cpu.get32(AX), 0xABCDFFFF)

    def test_get_reg_and_set_reg_do_the_three_widths(self):
        cpu = self.start(b"", eax=0x11223344)
        self.assertEqual([cpu.get_reg(AX, bits) for bits in (8, 16, 32)], [0x44, 0x3344, 0x11223344])
        cpu.set_reg(AX, 32, 0xAABBCCDD)
        cpu.set_reg(AX, 8, 0x01)
        cpu.set_reg(AX, 16, 0x0203)
        self.assertEqual(cpu.get32(AX), 0xAABB0203)

    def test_the_32_bit_words_of_memory_are_little_endian(self):
        cpu = self.start(b"")
        cpu.wd(0x2000, 0x0100, 0x12345678)
        self.assertEqual(bytes(cpu.rb(0x2000, 0x0100 + i) for i in range(4)), bytes([0x78, 0x56, 0x34, 0x12]))
        self.assertEqual(cpu.rd(0x2000, 0x0100), 0x12345678)
        cpu.wd(0x2000, 0x0100, 0x1_FFFFFFFF)
        self.assertEqual(cpu.rd(0x2000, 0x0100), 0xFFFFFFFF)

    def test_the_modrm_operand_reads_and_writes_32_bits(self):
        from mydos import modrm
        cpu = self.start(bytes([0xC1]), ecx=0xDEADBEEF)  # the ModRM byte C1: the r/m operand is register 1, ECX
        m = modrm.decode(cpu)
        self.assertEqual(m.read(cpu, 32), 0xDEADBEEF)
        m.write(cpu, 32, 0x01020304)
        self.assertEqual((cpu.get32(CX), m.read(cpu, 32)), (0x01020304, 0x01020304))
        cpu = self.start(bytes([0x07]))  # [bx]
        m = modrm.decode(cpu)
        m.write(cpu, 32, 0x0A0B0C0D)
        self.assertEqual((cpu.rd(0x2000, 0), m.read(cpu, 32)), (0x0A0B0C0D, 0x0A0B0C0D))


class TestArithmetic(Machine):
    """Every operation with every form that has a 32-bit one, on the values that come at the edges, against the
    definitions."""

    def check(self, code, name, a, b, carry, where, steps=1):
        result, expected = reference(name, a, b, carry)
        self.start(code)
        self.cpu.flags = 0x0002 | flags.DF | flags.IF | (CARRY if carry else 0)
        self.put(0x0100, b if where == "mem" else 0)
        self.cpu.set32(AX, a)
        self.cpu.set32(BX, 0x0100)
        self.cpu.set32(CX, b)
        for _ in range(steps):
            self.cpu.step()
        compared = ("CF", "OF", "ZF", "SF", "PF") + (("AF",) if name in ("add", "adc", "sub", "sbb", "cmp") else ())
        got = self.flag_values()
        for flag in compared:
            self.assertEqual(got[flag], expected[flag], f"{name} {a:08X}, {b:08X} carry {carry}: {flag}")
        self.assertTrue(self.cpu.get_flag(flags.DF) and self.cpu.get_flag(flags.IF))  # the others are not touched
        return result

    def test_register_to_register_both_ways_for_all_operations(self):
        for index, name in enumerate(ALU):
            for carry in (0, 1) if name in ("adc", "sbb") else (0,):
                for a in SAMPLES:
                    for b in SAMPLES:
                        # 66 01 C8: op eax, ecx (the register is the 'reg' field: ecx; r/m is eax)
                        result = self.check(bytes([0x66, index << 3 | 1, 0xC8]), name, a, b, carry, "reg")
                        self.assertEqual(self.register("eax"), a if name == "cmp" else result, (name, a, b))
                        self.assertEqual(self.register("ecx"), b)
                        # 66 03 C1: op eax, ecx (the register is eax: the 'reg' field; r/m is ecx)
                        result = self.check(bytes([0x66, index << 3 | 3, 0xC1]), name, a, b, carry, "reg")
                        self.assertEqual(self.register("eax"), a if name == "cmp" else result, (name, a, b))

    def test_register_and_memory_both_ways(self):
        for index, name in enumerate(ALU):
            for a in SAMPLES:
                for b in SAMPLES[::2]:
                    # 66 03 07: op eax, [bx]
                    result = self.check(bytes([0x66, index << 3 | 3, 0x07]), name, a, b, 0, "mem")
                    self.assertEqual(self.register("eax"), a if name == "cmp" else result, (name, a, b))
                    # 66 01 07: op [bx], eax: the memory has b, the register a, the result goes to the memory
                    self.start(bytes([0x66, index << 3 | 1, 0x07]))
                    self.cpu.set32(AX, a)
                    self.cpu.set32(BX, 0x0100)
                    self.put(0x0100, b)
                    self.cpu.flags = 0x0002
                    self.cpu.step()
                    result, _ = reference(name, b, a)
                    self.assertEqual(self.get(0x0100), b if name == "cmp" else result, (name, b, a))
                    self.assertEqual(self.register("eax"), a)

    def test_eax_and_a_constant_of_32_bits(self):
        for index, name in enumerate(ALU):
            for a in SAMPLES:
                for b in SAMPLES[::2]:
                    # 66 05 imm32: op eax, imm32
                    result = self.check(bytes([0x66, index << 3 | 5]) + le32(b), name, a, b, 0, "reg")
                    self.assertEqual(self.register("eax"), a if name == "cmp" else result, (name, a, b))
                    self.assertEqual(self.cpu.ip, 6)

    def test_a_register_and_a_constant_of_32_bits_or_of_8_bits_widened(self):
        for index, name in enumerate(ALU):
            for a in SAMPLES:
                # 66 81 /n imm32 on ecx (ModRM C0 | n << 3 | 1)
                for b in (0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x12345678):
                    self.start(bytes([0x66, 0x81, 0xC0 | index << 3 | 1]) + le32(b), ecx=a)
                    self.cpu.step()
                    result, expected = reference(name, a, b)
                    self.assertEqual(self.register("ecx"), a if name == "cmp" else result, (name, a, b))
                    self.assertEqual(self.cpu.ip, 7)
                # 66 83 /n imm8 on ecx: the byte is a signed number, widened to 32 bits
                for byte in (0, 1, 0x7F, 0x80, 0xFF, 0xFE):
                    widened = byte | 0xFFFFFF00 if byte & 0x80 else byte
                    self.start(bytes([0x66, 0x83, 0xC0 | index << 3 | 1, byte]), ecx=a)
                    self.cpu.step()
                    result, expected = reference(name, a, widened)
                    self.assertEqual(self.register("ecx"), a if name == "cmp" else result, (name, a, byte))
                    self.assertEqual(self.cpu.ip, 4)

    def test_a_memory_operand_and_a_constant_of_32_bits(self):
        for index, name in enumerate(ALU):
            for a in (0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x12345678):
                for b in (0, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF):
                    self.start(bytes([0x66, 0x81, index << 3 | 7]) + le32(b), ebx=0x0100)  # op dword [bx], imm32
                    self.put(0x0100, a)
                    self.cpu.step()
                    result, _ = reference(name, a, b)
                    self.assertEqual(self.get(0x0100), a if name == "cmp" else result, (name, a, b))
                    self.assertEqual(self.get(0x0104), 0)  # nothing is written beyond the doubleword

    def test_the_flags_of_the_five_that_every_operation_sets_are_what_the_definitions_say_for_cmp_of_equals(self):
        cpu = self.run_code(bytes([0x66, 0x3D]) + le32(0x12345678), eax=0x12345678)
        self.assertTrue(cpu.get_flag(ZERO) and not cpu.get_flag(CARRY) and not cpu.get_flag(SIGN))

    def test_the_8_bit_forms_and_the_prefix_66_are_the_same_as_without_it(self):
        for code in (bytes([0x66, 0x00, 0xC8]), bytes([0x66, 0x02, 0xC1]), bytes([0x66, 0x04, 0x05]),
                     bytes([0x66, 0x80, 0xC1, 0x05])):
            with_prefix = self.run_code(code, eax=0x11223344, ecx=0x55667788)
            first = (list(with_prefix.r), list(with_prefix.rh), with_prefix.flags)
            plain = self.run_code(code[1:], eax=0x11223344, ecx=0x55667788)
            self.assertEqual(first, (list(plain.r), list(plain.rh), plain.flags), code.hex())

    def test_the_other_registers_stay_and_the_flags_that_are_not_the_six_stay(self):
        cpu = self.run_code(bytes([0x66, 0x01, 0xC8]), eax=1, ecx=2, edx=0xAAAAAAAA, ebx=0xBBBBBBBB, esi=0xCCCCCCCC,
                            edi=0xDDDDDDDD, ebp=0xEEEEEEEE)
        for name, value in (("edx", 0xAAAAAAAA), ("ebx", 0xBBBBBBBB), ("esi", 0xCCCCCCCC), ("edi", 0xDDDDDDDD),
                            ("ebp", 0xEEEEEEEE), ("ecx", 2)):
            self.assertEqual(self.register(name), value, name)
        self.assertEqual(self.register("eax"), 3)

    def test_adc_and_sbb_use_the_carry_the_instruction_before_left(self):
        # add eax, eax ; adc edx, 0   a 64-bit number doubled, as it is done with 32-bit halves
        cpu = self.run_code(bytes([0x66, 0x01, 0xC0, 0x66, 0x81, 0xD2]) + le32(0), steps=2, eax=0x80000000, edx=5)
        self.assertEqual((self.register("eax"), self.register("edx")), (0, 6))


class TestMoves(Machine):
    def test_a_register_to_a_register_and_the_other_way(self):
        cpu = self.run_code(bytes([0x66, 0x89, 0xC8]), eax=1, ecx=0x89ABCDEF)  # mov eax, ecx
        self.assertEqual((self.register("eax"), self.register("ecx")), (0x89ABCDEF, 0x89ABCDEF))
        cpu = self.run_code(bytes([0x66, 0x8B, 0xC8]), eax=0x13572468, ecx=7)  # mov ecx, eax
        self.assertEqual((self.register("eax"), self.register("ecx")), (0x13572468, 0x13572468))

    def test_every_register_to_every_register(self):
        for source in range(8):
            for target in range(8):
                cpu = self.start(bytes([0x66, 0x89, 0xC0 | source << 3 | target]))  # mov target, source
                values = [0x01010101 * (i + 1) for i in range(8)]
                for i, value in enumerate(values):
                    cpu.set32(i, value)
                cpu.step()
                expected = list(values)
                expected[target] = values[source]
                self.assertEqual([cpu.get32(i) for i in range(8)], expected, (source, target))

    def test_to_memory_and_from_memory(self):
        self.run_code(bytes([0x66, 0x89, 0x07]), eax=0xCAFEBABE, ebx=0x0100)  # mov [bx], eax
        self.assertEqual(self.get(0x0100), 0xCAFEBABE)
        self.assertEqual(self.get(0x0104), 0)
        self.start(bytes([0x66, 0x8B, 0x0F]), ebx=0x0100)  # mov ecx, [bx]
        self.put(0x0100, 0x0BADF00D)
        self.cpu.step()
        self.assertEqual(self.register("ecx"), 0x0BADF00D)

    def test_the_sixteen_bit_address_with_a_displacement_and_two_registers(self):
        self.start(bytes([0x66, 0x8B, 0x40, 0x10]), ebx=0x0080, esi=0x0070)  # mov eax, [bx+si+10]
        self.cpu.set32(SI, 0x0070)
        self.cpu.set32(BX, 0x0060)
        self.cpu.mem[0x1000 * 16:0x1000 * 16 + 4] = bytes([0x66, 0x8B, 0x40, 0x10])
        self.put(0x0060 + 0x0070 + 0x10, 0x55AA55AA)
        self.cpu.step()
        self.assertEqual(self.register("eax"), 0x55AA55AA)

    def test_a_constant_into_every_register(self):
        for reg in range(8):
            cpu = self.start(bytes([0x66, 0xB8 + reg]) + le32(0xA1B2C3D4))
            cpu.step()
            self.assertEqual(cpu.get32(reg), 0xA1B2C3D4)
            self.assertEqual(cpu.ip, 6)
            others = [cpu.get32(i) for i in range(8) if i != reg and i != SP]  # SP has the stack pointer in it
            self.assertEqual(others, [0] * len(others))

    def test_a_constant_into_memory_and_into_a_register_with_c7(self):
        self.start(bytes([0x66, 0xC7, 0x07]) + le32(0x01020304), ebx=0x0100)  # mov dword [bx], 01020304
        self.cpu.step()
        self.assertEqual((self.get(0x0100), self.cpu.ip), (0x01020304, 7))
        cpu = self.run_code(bytes([0x66, 0xC7, 0xC1]) + le32(0xFFFF0001))  # mov ecx, FFFF0001
        self.assertEqual(self.register("ecx"), 0xFFFF0001)

    def test_c7_with_another_reg_field_is_not_defined(self):
        with self.assertRaisesRegex(NotImplementedError, "opcode 66 C7 /1"):
            self.run_code(bytes([0x66, 0xC7, 0xC9]) + le32(0))

    def test_eax_from_and_to_a_fixed_address(self):
        self.start(bytes([0x66, 0xA1, 0x20, 0x01]))  # mov eax, [0120]
        self.put(0x0120, 0xFEEDFACE)
        self.cpu.step()
        self.assertEqual((self.register("eax"), self.cpu.ip), (0xFEEDFACE, 4))
        self.run_code(bytes([0x66, 0xA3, 0x30, 0x01]), eax=0x0A0B0C0D)  # mov [0130], eax
        self.assertEqual(self.get(0x0130), 0x0A0B0C0D)

    def test_the_prefix_of_a_segment_goes_before_the_66_or_after_it(self):
        for code in (bytes([0x26, 0x66, 0xA1, 0x20, 0x01]), bytes([0x66, 0x26, 0xA1, 0x20, 0x01])):  # es:
            self.start(code)
            self.cpu.s[CPU.ES] = 0x3000
            self.put(0x0120, 0x11111111, seg=0x2000)
            self.put(0x0120, 0x22222222, seg=0x3000)
            self.cpu.step()
            self.assertEqual(self.register("eax"), 0x22222222, code.hex())

    def test_a_prefix_66_twice_is_one(self):
        cpu = self.run_code(bytes([0x66, 0x66, 0xB8]) + le32(0x01020304))
        self.assertEqual((self.register("eax"), cpu.ip), (0x01020304, 7))

    def test_lea_with_a_16_bit_address_is_widened_with_zeros(self):
        cpu = self.run_code(bytes([0x66, 0x8D, 0x40, 0x10]), eax=0xFFFFFFFF, ebx=0x00123456, esi=0xFFFF0020)
        # lea eax, [bx+si+10]: the address is 16 bits, computed from the low halves
        self.assertEqual(self.register("eax"), (0x3456 + 0x0020 + 0x10) & 0xFFFF)

    def test_lea_of_a_register_is_not_defined(self):
        with self.assertRaisesRegex(NotImplementedError, "LEA with a register"):
            self.run_code(bytes([0x66, 0x8D, 0xC1]))

    def test_xchg_with_a_register_and_with_memory(self):
        cpu = self.run_code(bytes([0x66, 0x87, 0xCB]), ebx=0x11111111, ecx=0x22222222)  # xchg ebx, ecx
        self.assertEqual((self.register("ebx"), self.register("ecx")), (0x22222222, 0x11111111))
        self.start(bytes([0x66, 0x87, 0x07]), eax=0xAAAA5555, ebx=0x0100)  # xchg [bx], eax
        self.put(0x0100, 0x12345678)
        self.cpu.step()
        self.assertEqual((self.get(0x0100), self.register("eax")), (0xAAAA5555, 0x12345678))

    def test_xchg_with_eax_in_one_byte(self):
        for reg in range(1, 8):
            cpu = self.start(bytes([0x66, 0x90 + reg]), eax=0xAAAAAAAA)
            cpu.set32(reg, 0xBBBBBBBB)
            cpu.step()
            self.assertEqual((cpu.get32(AX), cpu.get32(reg)), (0xBBBBBBBB, 0xAAAAAAAA), reg)

    def test_66_90_is_a_nop(self):
        cpu = self.run_code(bytes([0x66, 0x90]), eax=0x12345678, ecx=7)
        self.assertEqual((self.register("eax"), self.register("ecx"), cpu.ip), (0x12345678, 7, 2))

    def test_test_and_the_flags_of_a_logic_operation(self):
        for a, b, zero, sign in ((0xF0F0F0F0, 0x0F0F0F0F, True, False), (0x80000000, 0x80000000, False, True),
                                 (0xFFFFFFFF, 1, False, False), (0, 0xFFFFFFFF, True, False)):
            self.run_code(bytes([0x66, 0x85, 0xC8]), eax=b, ecx=a)  # test eax, ecx
            self.assertEqual((self.cpu.get_flag(ZERO), self.cpu.get_flag(SIGN)), (zero, sign), (a, b))
            self.assertFalse(self.cpu.get_flag(CARRY) or self.cpu.get_flag(OVERFLOW))
            self.assertEqual((self.register("eax"), self.register("ecx")), (b, a))
            self.run_code(bytes([0x66, 0xA9]) + le32(b), eax=a)  # test eax, imm32
            self.assertEqual((self.cpu.get_flag(ZERO), self.cpu.get_flag(SIGN)), (zero, sign), (a, b))

    def test_the_moves_do_not_touch_the_flags(self):
        cpu = self.run_code(bytes([0x66, 0xB8]) + le32(0))
        self.assertEqual(cpu.flags, 0x0002 | flags.DF | flags.IF)


class TestIncAndDec(Machine):
    def test_every_register_by_one_in_both_directions(self):
        for reg in range(8):
            for value, up, down in ((0, 1, 0xFFFFFFFF), (0x7FFFFFFF, 0x80000000, 0x7FFFFFFE),
                                    (0xFFFFFFFF, 0, 0xFFFFFFFE), (0x12345678, 0x12345679, 0x12345677)):
                cpu = self.start(bytes([0x66, 0x40 + reg]))
                cpu.set32(reg, value)
                cpu.step()
                self.assertEqual(cpu.get32(reg), up, (reg, value))
                cpu = self.start(bytes([0x66, 0x48 + reg]))
                cpu.set32(reg, value)
                cpu.step()
                self.assertEqual(cpu.get32(reg), down, (reg, value))

    def test_the_flags_are_those_of_add_and_sub_but_the_carry_stays(self):
        for carry in (False, True):
            for value, flag_name, increment in ((0x7FFFFFFF, "OF", True), (0x80000000, "OF", False),
                                                (0xFFFFFFFF, "ZF", True), (1, "ZF", False), (0x0F, "AF", True)):
                cpu = self.start(bytes([0x66, 0x40 if increment else 0x48]), eax=value)
                cpu.flags |= CARRY if carry else 0
                cpu.step()
                self.assertTrue(self.flag_values()[flag_name], (value, flag_name, increment))
                self.assertEqual(cpu.get_flag(CARRY), carry, (value, increment))

    def test_the_sign_and_parity_of_the_result(self):
        self.run_code(bytes([0x66, 0x40]), eax=0x7FFFFFFF)  # 80000000: negative, low byte 00 has even parity
        self.assertTrue(self.cpu.get_flag(SIGN) and self.cpu.get_flag(PARITY) and not self.cpu.get_flag(ZERO))


class TestTheStack(Machine):
    def test_push_puts_four_bytes_and_pop_takes_them_back(self):
        for reg in range(8):
            if reg == SP:
                continue
            cpu = self.start(bytes([0x66, 0x50 + reg, 0x66, 0x58 + reg]))
            cpu.set32(reg, 0xA1B2C3D4)
            cpu.step()
            self.assertEqual((cpu.r[SP], cpu.rd(0x4000, 0x01FC)), (0x01FC, 0xA1B2C3D4), reg)
            self.assertEqual(bytes(cpu.rb(0x4000, 0x01FC + i) for i in range(4)), bytes([0xD4, 0xC3, 0xB2, 0xA1]))
            cpu.set32(reg, 0)
            cpu.step()
            self.assertEqual((cpu.get32(reg), cpu.r[SP]), (0xA1B2C3D4, 0x0200), reg)

    def test_pop_goes_into_the_register_it_names_and_leaves_the_others(self):
        cpu = self.start(bytes([0x66, 0x5B]))  # pop ebx
        cpu.wd(0x4000, 0x0200, 0x01234567)
        cpu.set32(CX, 0x77777777)
        cpu.step()
        self.assertEqual((cpu.get32(BX), cpu.get32(CX)), (0x01234567, 0x77777777))

    def test_push_esp_pushes_what_it_was_before_the_push(self):
        cpu = self.run_code(bytes([0x66, 0x54]), esp=0x00010200)  # push esp
        self.assertEqual(cpu.rd(0x4000, 0x01FC), 0x00010200)
        self.assertEqual(cpu.r[SP], 0x01FC)

    def test_pop_esp_takes_the_value_that_was_on_the_stack(self):
        cpu = self.start(bytes([0x66, 0x5C]))  # pop esp
        cpu.wd(0x4000, 0x0200, 0x00020340)
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x00020340)

    def test_the_stack_pointer_goes_round_in_the_segment(self):
        cpu = self.start(bytes([0x66, 0x50]), eax=0x01020304)
        cpu.r[SP] = 0x0002
        cpu.step()
        self.assertEqual(cpu.r[SP], 0xFFFE)
        cpu = self.start(bytes([0x66, 0x58]))
        cpu.r[SP] = 0xFFFE
        cpu.step()
        self.assertEqual(cpu.r[SP], 0x0002)

    def test_the_flags_stay_with_push_and_pop(self):
        cpu = self.run_code(bytes([0x66, 0x50]), eax=1)
        self.assertEqual(cpu.flags, 0x0002 | flags.DF | flags.IF)

    def test_pushfd_puts_the_flags_in_the_low_word_and_zeros_above(self):
        cpu = self.run_code(bytes([0x66, 0x9C]))
        self.assertEqual(cpu.rd(0x4000, 0x01FC), 0x0002 | flags.DF | flags.IF)
        self.assertEqual(cpu.r[SP], 0x01FC)

    def test_popfd_takes_the_16_flags_and_iopl_and_nt_and_bit_1_is_always_set(self):
        for value in (0x0000, 0xFFFF, 0x0FD7, 0x7000, 0x8000, 0x1234, 0xAAAA):
            cpu = self.start(bytes([0x66, 0x9D]))
            cpu.wd(0x4000, 0x0200, value)
            cpu.step()
            self.assertEqual(cpu.flags, (value & 0x7FD5) | 0x0002, f"{value:04X}")
            self.assertEqual(cpu.r[SP], 0x0204)

    def test_the_alignment_check_flag_can_be_set_and_cleared_and_goes_through_pushfd(self):
        cpu = self.start(bytes([0x66, 0x9D, 0x66, 0x9C]))
        cpu.wd(0x4000, 0x0200, 0x00040002)
        cpu.step()
        self.assertEqual(cpu.eflags_high, 0x4)
        cpu.step()
        self.assertEqual(cpu.rd(0x4000, 0x0200) & 0xFFFF0000, 0x00040000)
        cpu = self.start(bytes([0x66, 0x9D]))
        cpu.eflags_high = 0x4
        cpu.wd(0x4000, 0x0200, 0x00000002)
        cpu.step()
        self.assertEqual(cpu.eflags_high, 0)

    def test_the_id_flag_and_the_other_high_bits_cannot_be_set_so_there_is_no_cpuid(self):
        for value in (0x00200002, 0x00030002, 0xFFFB0002, 0x00180002, 0x80000002):
            cpu = self.start(bytes([0x66, 0x9D, 0x66, 0x9C]))
            cpu.wd(0x4000, 0x0200, value)
            cpu.step()
            cpu.step()
            self.assertEqual(cpu.rd(0x4000, 0x0200) & 0xFFFF0000, value & 0x00040000, hex(value))

    def test_16_bit_popf_and_pushf_leave_the_high_bits_of_eflags_alone(self):
        cpu = self.start(bytes([0x9D, 0x9C]))
        cpu.eflags_high = 0x4
        cpu.wd(0x4000, 0x0200, 0xFFFF7FFF)
        cpu.step()
        cpu.step()
        self.assertEqual(cpu.eflags_high, 0x4)
        self.assertEqual(cpu.rw(0x4000, 0x0200), 0x7FD5 | 0x0002)


class TestSignExtensions(Machine):
    def test_cwde_widens_the_sign_of_ax_into_eax(self):
        for ax, high in ((0, 0), (0x7FFF, 0), (0x8000, 0xFFFF), (0xFFFF, 0xFFFF), (0x1234, 0)):
            cpu = self.run_code(bytes([0x66, 0x98]), eax=0x5A5A0000 | ax)
            self.assertEqual(cpu.get32(AX), high << 16 | ax, hex(ax))

    def test_cdq_widens_the_sign_of_eax_into_edx(self):
        for eax, edx in ((0, 0), (0x7FFFFFFF, 0), (0x80000000, 0xFFFFFFFF), (0xFFFFFFFF, 0xFFFFFFFF), (1, 0)):
            cpu = self.run_code(bytes([0x66, 0x99]), eax=eax, edx=0x12345678)
            self.assertEqual((cpu.get32(DX), cpu.get32(AX)), (edx, eax), hex(eax))

    def test_without_the_prefix_cbw_and_cwd_are_what_they_were(self):
        cpu = self.run_code(bytes([0x98]), eax=0x12340080)  # cbw: AL is 80h, AX becomes FF80
        self.assertEqual(cpu.get32(AX), 0x1234FF80)
        cpu = self.run_code(bytes([0x99]), eax=0x12348000, edx=0x56781111)  # cwd: DX = FFFF, the high half of EDX stays
        self.assertEqual(cpu.get32(DX), 0x5678FFFF)


class TestWhatIsNotDoneYet(Machine):
    def test_an_instruction_with_the_prefix_66_that_has_no_32_bit_form_yet_stops_the_emulator(self):
        for opcode in (0x0F, 0x8C, 0x8E, 0x06, 0x07, 0x1E, 0x1F, 0x9A, 0xEA, 0xC4, 0xC5, 0xCA, 0xCB, 0xCF, 0xE5, 0xE7,
                       0xED, 0xEF, 0x6D, 0x6F, 0xD8):
            cpu = self.start(bytes([0x66, opcode, 0xC0, 0, 0, 0, 0]))
            with self.assertRaisesRegex(NotImplementedError, f"opcode 66 {opcode:02X}, the 32-bit form, at 1000:0000"):
                cpu.step()

    def test_the_message_has_the_bytes_from_the_prefix(self):
        cpu = self.start(bytes([0x66, 0x8C, 0xD8, 0x90]))
        with self.assertRaisesRegex(NotImplementedError, "66 8c d8 90 00 00 00 00$"):  # eight bytes
            cpu.step()

    def test_a_16_bit_form_that_is_not_there_for_the_32_bit_one_is_not_taken_instead(self):
        # 8C /3 is mov ax, ds: with 66 it is mov eax, ds, which is not done: it must not do the 16-bit one
        cpu = self.start(bytes([0x66, 0x8C, 0xD8]), eax=0x12345678)
        with self.assertRaises(NotImplementedError):
            cpu.step()
        self.assertEqual(cpu.get32(AX), 0x12345678)

    def test_the_table_of_the_32_bit_forms_is_not_the_16_bit_table(self):
        cpu = CPU()
        self.assertIsNot(cpu.ops32, cpu.ops)
        self.assertLess(len(cpu.ops32), len(cpu.ops) + 40)


class TestTheTestOfTheInstaller(Machine):
    """The routine of install.exe that asks what the processor is: it needs the 32-bit flags, and said 286 before."""

    def run_routine(self):
        cpu = self.start(INSTALLER_CPU_TEST)
        cpu.mem[0x10400] = 0xF4  # hlt, where the routine returns to
        cpu.r[SP] = 0x0100
        cpu.push16(0x1000)  # the return address of a far call: CS, then IP
        cpu.push16(0x0400)
        cpu.s[CPU.DS] = 0x1234
        for _ in range(500):
            try:
                cpu.step()
            except Exit:
                return cpu
        self.fail("the routine did not return")

    def test_the_machine_is_told_to_be_a_486_without_cpuid(self):
        cpu = self.run_routine()
        self.assertEqual(cpu.get8(0), 4)  # AL: 0 is an 8086, 2 a 286, 3 a 386, 4 a 486

    def test_the_stack_is_as_it_was_and_the_registers_the_routine_saved_are_back(self):
        cpu = self.start(INSTALLER_CPU_TEST)
        cpu.mem[0x10400] = 0xF4
        cpu.r[SP] = 0x0100
        cpu.push16(0x1000)
        cpu.push16(0x0400)
        cpu.r[SI], cpu.r[DI], cpu.s[CPU.DS] = 0x1111, 0x2222, 0x3456
        for _ in range(500):
            try:
                cpu.step()
            except Exit:
                break
        self.assertEqual((cpu.r[SI], cpu.r[DI], cpu.s[CPU.DS], cpu.r[SP]), (0x1111, 0x2222, 0x3456, 0x0100))

    def test_it_stops_at_the_286_when_the_flags_of_the_high_bits_do_not_stay(self):
        # the test of the 286: bits 12-15 written to the flags come back as 0. Make it so, and the routine says 2.
        cpu = self.start(INSTALLER_CPU_TEST)
        cpu.mem[0x10400] = 0xF4
        cpu.r[SP] = 0x0100
        cpu.push16(0x1000)
        cpu.push16(0x0400)
        real_popf = cpu.ops[0x9D]
        cpu.ops[0x9D] = lambda op: (real_popf(op), setattr(cpu, "flags", cpu.flags & ~0xF000))
        for _ in range(500):
            try:
                cpu.step()
            except Exit:
                break
        self.assertEqual(cpu.get8(0), 2)

    def test_it_says_a_386_when_the_alignment_check_flag_cannot_be_changed(self):
        cpu = self.start(INSTALLER_CPU_TEST)
        cpu.mem[0x10400] = 0xF4
        cpu.r[SP] = 0x0100
        cpu.push16(0x1000)
        cpu.push16(0x0400)
        real_popfd = cpu.ops32[0x9D]
        cpu.ops32[0x9D] = lambda op: (real_popfd(op), setattr(cpu, "eflags_high", 0))
        for _ in range(500):
            try:
                cpu.step()
            except Exit:
                break
        self.assertEqual(cpu.get8(0), 3)


class TestAddresses32(Machine):
    """The prefix 67h: the memory operand has 32-bit registers and the SIB byte. The data is at DS = 2000h."""

    def put(self, offset, value, size=4):
        self.cpu.mem[0x20000 + offset:0x20000 + offset + size] = value.to_bytes(size, "little")

    def test_every_base_register_with_no_displacement(self):
        for rm in (0, 1, 2, 3, 6, 7):  # 4 is the SIB byte, 5 is the bare address
            base = NAMES[rm]
            cpu = self.start(bytes([0x67, 0x8B, 0x00 | rm]), **{base: 0x00001234})
            seg = 0x4000 if rm in (4, 5) else 0x2000
            cpu.mem[seg * 16 + 0x1234:seg * 16 + 0x1238] = bytes([0x78, 0x56, 0x34, 0x12])
            cpu.step()
            self.assertEqual(cpu.r[AX], 0x5678, base)  # the 16-bit mov ax, [base]
            self.assertEqual(cpu.ip, 3, base)

    def test_the_high_half_of_the_register_counts(self):
        cpu = self.start(bytes([0x67, 0x8B, 0x03]), ebx=0x00010010)  # 2000:10010h is 30010h
        cpu.mem[0x30010:0x30012] = bytes([0xCD, 0xAB])
        cpu.step()
        self.assertEqual(cpu.r[AX], 0xABCD)

    def test_the_displacement_of_8_bits_is_signed_and_the_one_of_32_bits_is_whole(self):
        cpu = self.start(bytes([0x67, 0x8B, 0x43, 0xFE]), ebx=0x100)  # mov ax, [ebx-2]
        self.put(0xFE, 0x1111, 2)
        cpu.step()
        self.assertEqual((cpu.r[AX], cpu.ip), (0x1111, 4))
        cpu = self.start(bytes([0x67, 0x8B, 0x43, 0x7F]), ebx=0x100)  # mov ax, [ebx+7Fh]
        self.put(0x17F, 0x2222, 2)
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x2222)
        cpu = self.start(bytes([0x67, 0x8B, 0x83]) + le32(0x00010004), ebx=0x10)  # mov ax, [ebx+10004h]
        cpu.mem[0x30014:0x30016] = bytes([0x33, 0x33])
        cpu.step()
        self.assertEqual((cpu.r[AX], cpu.ip), (0x3333, 7))
        cpu = self.start(bytes([0x67, 0x8B, 0x83]) + le32(-4), ebx=0x104)  # a negative one
        self.put(0x100, 0x4444, 2)
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x4444)

    def test_a_bare_32_bit_address(self):
        cpu = self.start(bytes([0x67, 0x8B, 0x05]) + le32(0x00000120))  # mov ax, [00000120]
        self.put(0x120, 0x5555, 2)
        cpu.step()
        self.assertEqual((cpu.r[AX], cpu.ip), (0x5555, 7))

    def test_the_sib_byte_adds_the_index_times_the_scale(self):
        for scale in range(4):
            cpu = self.start(bytes([0x67, 0x8B, 0x04, scale << 6 | 1 << 3 | 3]), ebx=0x100, ecx=0x10)  # [ebx+ecx*s]
            self.put(0x100 + 0x10 * (1 << scale), 0x6000 + scale, 2)
            cpu.step()
            self.assertEqual((cpu.r[AX], cpu.ip), (0x6000 + scale, 4), scale)

    def test_the_index_4_is_no_register_not_esp(self):
        cpu = self.start(bytes([0x67, 0x8B, 0x04, 0x23]), ebx=0x100, esp=0x1000)  # SIB 00 100 011: [ebx]
        self.put(0x100, 0x7777, 2)
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x7777)

    def test_the_base_5_with_mod_0_is_a_32_bit_displacement_and_no_base(self):
        code = bytes([0x67, 0x8B, 0x04, 0x8D]) + le32(0x200)  # SIB 10 001 101: [ecx*4+200h]
        cpu = self.start(code, ecx=0x10, ebp=0x5000)
        self.put(0x240, 0x8888, 2)
        cpu.step()
        self.assertEqual((cpu.r[AX], cpu.ip), (0x8888, 8))
        cpu = self.start(bytes([0x67, 0x8B, 0x04, 0x25]) + le32(0x300))  # SIB 00 100 101: the index is none too
        self.put(0x300, 0x9999, 2)
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x9999)

    def test_the_base_5_with_a_displacement_is_ebp_and_goes_to_the_stack_segment(self):
        cpu = self.start(bytes([0x67, 0x8B, 0x44, 0x0D, 0x04]), ebp=0x100, ecx=0x10)  # [ebp+ecx+4], SIB 00 001 101
        cpu.mem[0x40000 + 0x114:0x40000 + 0x116] = bytes([0xAA, 0xBB])
        cpu.step()
        self.assertEqual((cpu.r[AX], cpu.ip), (0xBBAA, 5))

    def test_esp_as_the_base_goes_to_the_stack_segment_and_ebp_alone_too(self):
        cpu = self.start(bytes([0x67, 0x8B, 0x04, 0x24]), esp=0x120)  # [esp]
        cpu.mem[0x40120:0x40122] = bytes([0x12, 0x34])
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x3412)
        cpu = self.start(bytes([0x67, 0x8B, 0x45, 0x00]), ebp=0x130)  # [ebp+0]: mod 01, since [ebp] is a bare address
        cpu.mem[0x40130:0x40132] = bytes([0x56, 0x78])
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x7856)

    def test_the_other_bases_go_to_the_data_segment_even_with_an_index(self):
        cpu = self.start(bytes([0x67, 0x8B, 0x04, 0xA3]), ebx=0x100, esp=0x1000, esi=0)  # SIB 10 100 011: no index
        self.put(0x100, 0x4321, 2)
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x4321)

    def test_a_segment_prefix_decides_the_segment(self):
        cpu = self.start(bytes([0x26, 0x67, 0x8B, 0x03]), ebx=0x40)  # es:
        cpu.s[CPU.ES] = 0x3000
        cpu.mem[0x30040:0x30042] = bytes([0xEF, 0xBE])
        cpu.step()
        self.assertEqual(cpu.r[AX], 0xBEEF)

    def test_the_address_wraps_at_32_bits(self):
        cpu = self.start(bytes([0x67, 0x8B, 0x43, 0x02]), ebx=0xFFFFFFFF)  # [ebx+2] is [1]
        self.put(1, 0x2468, 2)
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x2468)

    def test_the_offset_is_cut_to_32_bits_and_not_to_16(self):
        for code, registers, offset in ((bytes([0x43, 0x02]), {"ebx": 0xFFFFFFFF}, 1),
                                        (bytes([0x43, 0xFE]), {"ebx": 1}, 0xFFFFFFFF),
                                        (bytes([0x83]) + le32(0x00010000), {"ebx": 0x00020005}, 0x00030005)):
            cpu = self.start(code, **registers)
            cpu.asize32 = True
            self.assertEqual(modrm.decode(cpu, None).off, offset)

    def test_a_register_operand_is_not_an_address_and_the_prefix_changes_nothing(self):
        cpu = self.start(bytes([0x67, 0x8B, 0xC3]), ebx=0x1234)  # mov ax, bx
        cpu.step()
        self.assertEqual((cpu.r[AX], cpu.ip), (0x1234, 3))

    def test_it_works_with_a_32_bit_operand_and_with_a_store(self):
        cpu = self.start(bytes([0x66, 0x67, 0x89, 0x04, 0x8B]), eax=0xDEADBEEF, ebx=0x100, ecx=2)  # [ebx+ecx*4]
        cpu.step()
        self.assertEqual(bytes(cpu.mem[0x20108:0x2010C]), bytes([0xEF, 0xBE, 0xAD, 0xDE]))
        self.assertEqual(cpu.ip, 5)

    def test_lea_gives_the_offset_of_32_bits_or_of_16(self):
        cpu = self.start(bytes([0x66, 0x67, 0x8D, 0x04, 0x8B]), ebx=0x00010000, ecx=0x40000000)  # lea eax, [ebx+ecx*4]
        cpu.step()
        self.assertEqual(cpu.get32(AX), 0x00010000)  # 4 * 40000000h is 100000000h: 32 bits drop it
        cpu = self.start(bytes([0x67, 0x8D, 0x04, 0x8B]), eax=0xAAAA0000, ebx=0x00012345, ecx=1)  # lea ax, [..]
        cpu.step()
        self.assertEqual(cpu.get32(AX), 0xAAAA2349)  # only AX is written, with the low 16 bits of the offset

    def test_the_address_of_a_move_with_a_fixed_address_is_of_32_bits(self):
        cpu = self.start(bytes([0x67, 0xA1]) + le32(0x00010002))  # mov ax, [10002h]
        cpu.mem[0x30002:0x30004] = bytes([0xCA, 0xFE])
        cpu.step()
        self.assertEqual((cpu.r[AX], cpu.ip), (0xFECA, 6))
        cpu = self.start(bytes([0x66, 0x67, 0xA3]) + le32(0x40), eax=0x01020304)  # mov [40h], eax
        cpu.step()
        self.assertEqual((bytes(cpu.mem[0x20040:0x20044]), cpu.ip), (bytes([4, 3, 2, 1]), 7))
        cpu = self.start(bytes([0x67, 0xA0]) + le32(0x50))  # mov al, [50h]
        self.put(0x50, 0x99, 1)
        cpu.step()
        self.assertEqual((cpu.get8(0), cpu.ip), (0x99, 6))
        cpu = self.start(bytes([0xA1, 0x50, 0x00]))  # and without the prefix the address is of 16 bits
        self.put(0x50, 0x1357, 2)
        cpu.step()
        self.assertEqual((cpu.r[AX], cpu.ip), (0x1357, 3))

    def test_the_prefix_is_for_one_instruction_and_twice_is_once(self):
        cpu = self.start(bytes([0x67, 0x67, 0x8B, 0x43, 0x02, 0x8B, 0x07]), ebx=0x00010000, edi=0x20)
        cpu.mem[0x30002:0x30004] = bytes([1, 2])
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x0201)
        cpu.step()  # mov ax, [bx]: 16 bits again: bx is 0, so DS:0
        self.assertEqual((cpu.r[AX], cpu.asize32), (0, False))

    def test_ins_and_outs_do_not_take_32_bit_addresses_yet(self):
        for code in ([0x67, 0x6C], [0x67, 0x6D], [0x67, 0x6E], [0x67, 0x6F]):
            cpu = self.start(bytes(code))
            with self.assertRaisesRegex(NotImplementedError, "with 32-bit addresses at 1000:0000"):
                cpu.step()
            self.assertEqual((cpu.r[CX], cpu.r[SI], cpu.r[DI]), (0, 0, 0), code)

    def test_without_the_prefix_the_string_instructions_are_as_they_were(self):
        cpu = self.start(bytes([0xAA]), eax=0x41)  # stosb
        cpu.step()
        self.assertEqual(cpu.mem[0x20000], 0x41)


class TestCode32(Machine):
    """A 32-bit code segment: the sizes are of 32 bits, and the prefixes 66h and 67h make them 16."""

    def run32(self, code, **registers):
        cpu = self.start(code, **registers)
        cpu.code32 = True
        cpu.step()
        return cpu

    def test_a_mov_without_prefixes_has_a_32_bit_operand_and_a_32_bit_address(self):
        cpu = self.start(bytes([0x8B, 0x04, 0x8B]), ebx=0x00010000, ecx=2)  # mov eax, [ebx+ecx*4]
        cpu.code32 = True
        cpu.mem[0x30008:0x3000C] = bytes([1, 2, 3, 4])
        cpu.step()
        self.assertEqual((cpu.get32(AX), cpu.ip), (0x04030201, 3))

    def test_66_makes_the_operand_16_and_67_makes_the_address_16(self):
        cpu = self.start(bytes([0x66, 0x8B, 0x03]), ebx=0x100, eax=0xAAAA0000)
        cpu.code32 = True
        self.put(cpu, 0x100, bytes([0x34, 0x12, 0x78, 0x56]))
        cpu.step()
        self.assertEqual(cpu.get32(AX), 0xAAAA1234)  # mov ax, [ebx]
        cpu = self.start(bytes([0x67, 0x8B, 0x05]), edi=0x00050100)
        cpu.code32 = True
        self.put(cpu, 0x100, bytes([0x78, 0x56, 0x34, 0x12]))
        cpu.step()
        self.assertEqual((cpu.get32(AX), cpu.ip), (0x12345678, 3))  # mov eax, [di]: only 16 bits of the address

    def test_both_prefixes_and_the_sizes_come_back_for_the_next_instruction(self):
        cpu = self.start(bytes([0x66, 0x67, 0x8B, 0x05, 0x8B, 0x43, 0x04]), edi=0x100, ebx=0x10)
        cpu.code32 = True
        self.put(cpu, 0x100, bytes([0x11, 0x22, 0x33, 0x44]))
        self.put(cpu, 0x14, bytes([0x55, 0x66, 0x77, 0x88]))
        cpu.step()
        self.assertEqual((cpu.get32(AX), cpu.osize32, cpu.asize32), (0x2211, False, False))
        cpu.step()
        self.assertEqual((cpu.get32(AX), cpu.osize32, cpu.asize32), (0x88776655, True, True))

    def test_the_two_prefixes_twice_are_not_a_toggle(self):
        cpu = self.start(bytes([0x66, 0x66, 0x8B, 0xC3]), ebx=0x12345678, eax=0xFFFF0000)
        cpu.code32 = True
        cpu.step()
        self.assertEqual(cpu.get32(AX), 0xFFFF5678)

    def put(self, cpu, offset, data):
        cpu.mem[0x20000 + offset:0x20000 + offset + len(data)] = data


if __name__ == "__main__":
    unittest.main()
