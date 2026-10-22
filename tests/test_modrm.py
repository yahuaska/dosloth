import unittest

from mydos import modrm, regs
from mydos.cpu import CPU
from mydos.modrm import ModRM


def machine(code):
    cpu = CPU()
    cpu.s[CPU.CS] = 0x1000
    cpu.s[CPU.ES], cpu.s[CPU.CS], cpu.s[CPU.SS], cpu.s[CPU.DS] = 0x3000, 0x1000, 0x4000, 0x2000
    cpu.mem[0x10000:0x10000 + len(code)] = code
    cpu.r[CPU.BX], cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.r[CPU.BP] = 0x1000, 0x0200, 0x0030, 0x0500
    return cpu


# the eight ways to say where an operand in memory is, the plain way: the registers added up, and the segment
# that goes with them (the stack segment when BP is in it, the data segment when not)
WAYS = [
    ("bx+si", lambda r: r["bx"] + r["si"], "DS"), ("bx+di", lambda r: r["bx"] + r["di"], "DS"),
    ("bp+si", lambda r: r["bp"] + r["si"], "SS"), ("bp+di", lambda r: r["bp"] + r["di"], "SS"),
    ("si", lambda r: r["si"], "DS"), ("di", lambda r: r["di"], "DS"),
    ("bp", lambda r: r["bp"], "SS"), ("bx", lambda r: r["bx"], "DS"),
]
SEGMENTS = {"ES": 0x3000, "CS": 0x1000, "SS": 0x4000, "DS": 0x2000}


class TestDecode(unittest.TestCase):
    def registers(self, cpu):
        return {"bx": cpu.r[CPU.BX], "si": cpu.r[CPU.SI], "di": cpu.r[CPU.DI], "bp": cpu.r[CPU.BP]}

    def test_every_way_of_naming_memory_with_no_displacement(self):
        for rm, (name, offset, segment) in enumerate(WAYS):
            if rm == 6:
                continue  # mod 0 with r/m 6 is a bare address
            cpu = machine(bytes([0b00_101_000 | rm]))
            result = modrm.decode(cpu)
            self.assertEqual((result.is_reg, result.reg, result.seg, result.off),
                             (False, 5, SEGMENTS[segment], offset(self.registers(cpu)) & 0xFFFF), name)
            self.assertEqual(cpu.ip, 1, name)

    def test_the_direct_address_is_a_word_after_the_byte_and_goes_with_ds(self):
        cpu = machine(bytes([0b00_011_110, 0x34, 0x12]))
        result = modrm.decode(cpu)
        self.assertEqual((result.is_reg, result.reg, result.seg, result.off), (False, 3, 0x2000, 0x1234))
        self.assertEqual(cpu.ip, 3)

    def test_an_8_bit_displacement_is_signed(self):
        for displacement, signed_value in ((0x00, 0), (0x01, 1), (0x7F, 127), (0x80, -128), (0xFF, -1), (0xFE, -2)):
            for rm, (name, offset, segment) in enumerate(WAYS):
                cpu = machine(bytes([0b01_000_000 | rm, displacement]))
                result = modrm.decode(cpu)
                expected = (offset(self.registers(cpu)) + signed_value) & 0xFFFF
                self.assertEqual((result.seg, result.off), (SEGMENTS[segment], expected), (name, displacement))
                self.assertEqual(cpu.ip, 2, name)

    def test_a_16_bit_displacement_is_taken_as_it_is_and_the_sum_goes_round_65536(self):
        for displacement in (0x0000, 0x0001, 0x7FFF, 0x8000, 0xFFFF, 0xFF00):
            for rm, (name, offset, segment) in enumerate(WAYS):
                cpu = machine(bytes([0b10_000_000 | rm, displacement & 0xFF, displacement >> 8]))
                result = modrm.decode(cpu)
                expected = (offset(self.registers(cpu)) + displacement) & 0xFFFF
                self.assertEqual((result.seg, result.off), (SEGMENTS[segment], expected), (name, hex(displacement)))
                self.assertEqual(cpu.ip, 3, name)

    def test_the_sum_of_two_registers_goes_round_65536_too(self):
        cpu = machine(bytes([0b00_000_000]))  # [bx+si]
        cpu.r[CPU.BX], cpu.r[CPU.SI] = 0xFFFF, 0x0002
        self.assertEqual(modrm.decode(cpu).off, 0x0001)
        cpu = machine(bytes([0b01_000_011, 0x10]))  # [bp+di+10h]
        cpu.r[CPU.BP], cpu.r[CPU.DI] = 0xFFF0, 0x0008
        self.assertEqual(modrm.decode(cpu).off, 0x0008)  # FFF0 + 8 + 10 = 10008

    def test_a_prefix_names_the_segment_in_place_of_the_one_that_goes_with_the_registers(self):
        for override, name in ((CPU.ES, "ES"), (CPU.CS, "CS"), (CPU.SS, "SS"), (CPU.DS, "DS")):
            for rm in range(8):
                cpu = machine(bytes([0b00_000_000 | rm, 0x00, 0x00]))
                self.assertEqual(modrm.decode(cpu, override).seg, SEGMENTS[name], (name, rm))

    def test_the_direct_address_takes_a_prefix_too(self):
        cpu = machine(bytes([0b00_000_110, 0x10, 0x00]))
        self.assertEqual(modrm.decode(cpu, CPU.ES).seg, 0x3000)

    def test_a_register_operand_has_no_memory_and_takes_one_byte(self):
        for rm in range(8):
            for reg in range(8):
                cpu = machine(bytes([0b11_000_000 | reg << 3 | rm, 0x55, 0x66]))
                result = modrm.decode(cpu)
                self.assertEqual((result.is_reg, result.reg, result.n), (True, reg, rm))
                self.assertEqual(cpu.ip, 1)

    def test_the_reg_field_is_returned_for_every_value_and_every_way(self):
        for reg in range(8):
            for mod, extra in ((0, b""), (1, b"\x01"), (2, b"\x01\x00")):
                cpu = machine(bytes([mod << 6 | reg << 3 | 7]) + extra)  # [bx]
                self.assertEqual(modrm.decode(cpu).reg, reg)

    def test_decoding_changes_no_register_and_no_flag(self):
        cpu = machine(bytes([0b10_010_011, 0x34, 0x12]))
        cpu.flags = 0x0FD7
        before = (list(cpu.r), list(cpu.s), cpu.flags)
        modrm.decode(cpu)
        self.assertEqual((cpu.r, cpu.s, cpu.flags), before)


class TestTheDecodedOperand(unittest.TestCase):
    def memory(self, seg=0x2000, off=0x0100):
        return ModRM(reg=2, is_reg=False, seg=seg, off=off)

    def test_the_fields_are_where_the_constructor_puts_them(self):
        operand = ModRM(reg=3, is_reg=True, n=5)
        self.assertEqual((operand.reg, operand.is_reg, operand.n, operand.seg, operand.off), (3, True, 5, 0, 0))
        operand = ModRM(1, False, seg=0x1234, off=0x5678)
        self.assertEqual((operand.reg, operand.is_reg, operand.seg, operand.off), (1, False, 0x1234, 0x5678))
        self.assertEqual(operand.n, 0)  # what is not given is 0
        self.assertEqual((ModRM(2, True).n, ModRM(2, True).seg, ModRM(2, True).off), (0, 0, 0))

    def test_there_is_no_dictionary_in_each_one_so_it_is_cheap_to_make(self):
        self.assertFalse(hasattr(ModRM(0, True), "__dict__"))
        with self.assertRaises(AttributeError):
            ModRM(0, True).extra = 1

    def test_reading_and_writing_memory(self):
        cpu = machine(b"")
        operand = self.memory()
        operand.write16(cpu, 0xBEEF)
        self.assertEqual((operand.read16(cpu), operand.read8(cpu)), (0xBEEF, 0xEF))
        operand.write8(cpu, 0x12)
        self.assertEqual(operand.read16(cpu), 0xBE12)
        self.assertEqual(cpu.rw(0x2000, 0x0100), 0xBE12)

    def test_reading_and_writing_a_register_of_16_and_of_8_bits(self):
        cpu = machine(b"")
        operand = ModRM(0, True, n=3)  # BX as a word, BL as a byte
        operand.write16(cpu, 0x12345)
        self.assertEqual((cpu.r[CPU.BX], operand.read16(cpu), operand.read8(cpu)), (0x2345, 0x2345, 0x45))
        operand.write8(cpu, 0x99)
        self.assertEqual(cpu.r[CPU.BX], 0x2399)

    def test_the_width_is_an_argument_for_code_that_does_both(self):
        cpu = machine(b"")
        operand = self.memory()
        operand.write(cpu, 16, 0xABCD)
        self.assertEqual((operand.read(cpu, 16), operand.read(cpu, 8)), (0xABCD, 0xCD))
        operand.write(cpu, 8, 0x01)
        self.assertEqual(operand.read(cpu, 16), 0xAB01)

    def test_the_two_words_of_a_far_pointer(self):
        cpu = machine(b"")
        cpu.ww(0x2000, 0x0100, 0x1111)
        cpu.ww(0x2000, 0x0102, 0x2222)
        self.assertEqual(self.memory().rw_pair(cpu), (0x1111, 0x2222))

    def test_the_second_word_of_a_far_pointer_at_the_end_of_the_segment_is_at_its_start(self):
        cpu = machine(b"")
        cpu.ww(0x2000, 0xFFFE, 0x3333)
        cpu.ww(0x2000, 0x0000, 0x4444)
        self.assertEqual(self.memory(off=0xFFFE).rw_pair(cpu), (0x3333, 0x4444))


class TestTheRegisterNumbersAreTheOnesOfTheCpu(unittest.TestCase):
    def test_the_names_regs_and_cpu_agree(self):
        for name in ("AX", "CX", "DX", "BX", "SP", "BP", "SI", "DI", "ES", "CS", "SS", "DS"):
            self.assertEqual(getattr(regs, name), getattr(CPU, name), name)


if __name__ == "__main__":
    unittest.main()
