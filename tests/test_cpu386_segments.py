"""FS and GS (step 2d), and the other segment registers with the size of 32 bits: the prefixes 64h and 65h, MOV with
a segment register, PUSH and POP of one, LES, LDS, LSS, LFS and LGS."""
import unittest

from dosloth.cpu import CPU
from dosloth.regs import AX, BX, CX, DI, SI, SP
from tests.test_cpu386 import Machine, le32

FS, GS = CPU.FS, CPU.GS


class TestPrefixes(Machine):
    def test_fs_and_gs_choose_the_segment_of_the_memory_operand(self):
        for prefix, register in ((0x64, FS), (0x65, GS)):
            cpu = self.start(bytes([prefix, 0x8B, 0x07]), ebx=0x0100)  # mov ax, fs:[bx]
            cpu.s[register] = 0x3000
            cpu.wd(0x3000, 0x0100, 0x1234)
            cpu.wd(0x2000, 0x0100, 0xFFFF)  # DS has another
            cpu.step()
            self.assertEqual(cpu.r[AX], 0x1234, hex(prefix))

    def test_a_32_bit_operand_and_a_32_bit_address_with_a_prefix_of_fs(self):
        cpu = self.start(bytes([0x64, 0x66, 0x67, 0x8B, 0x04, 0x8B]), ebx=0x00010000, ecx=2)  # mov eax, fs:[ebx+ecx*4]
        cpu.s[FS] = 0x3000
        cpu.wd(0x3000, 0x10008, 0xCAFEBABE)
        cpu.step()
        self.assertEqual(cpu.get32(AX), 0xCAFEBABE)

    def test_a_store_goes_to_gs(self):
        cpu = self.start(bytes([0x65, 0x89, 0x1E, 0x00, 0x01]), ebx=0xBEEF)  # mov gs:[0100], bx
        cpu.s[GS] = 0x3000
        cpu.step()
        self.assertEqual((cpu.rw(0x3000, 0x0100), cpu.rw(0x2000, 0x0100)), (0xBEEF, 0))

    def test_a_fixed_address_goes_to_the_segment_of_the_prefix(self):
        cpu = self.start(bytes([0x64, 0xA1, 0x00, 0x01]))  # mov ax, fs:[0100]
        cpu.s[FS] = 0x3000
        cpu.ww(0x3000, 0x0100, 0x4321)
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x4321)

    def test_the_prefix_is_for_one_instruction(self):
        cpu = self.start(bytes([0x64, 0x90, 0x8B, 0x07]), ebx=0x0100)
        cpu.s[FS] = 0x3000
        cpu.wd(0x3000, 0x0100, 0x1111)
        cpu.wd(0x2000, 0x0100, 0x2222)
        cpu.step()
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x2222)  # DS again

    def test_the_last_prefix_of_a_kind_counts(self):
        cpu = self.start(bytes([0x64, 0x65, 0x8B, 0x07]), ebx=0x0100)
        cpu.s[FS], cpu.s[GS] = 0x3000, 0x3800
        cpu.wd(0x3000, 0x0100, 0x1111)
        cpu.wd(0x3800, 0x0100, 0x3333)
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x3333)

    def test_the_lea_does_not_look_at_the_segment(self):
        cpu = self.start(bytes([0x64, 0x8D, 0x07]), ebx=0x0100)
        cpu.s[FS] = 0x3000
        cpu.step()
        self.assertEqual(cpu.r[AX], 0x0100)

    def test_the_instruction_that_follows_the_prefixes_is_the_one_of_the_message_when_it_is_not_known(self):
        cpu = self.start(bytes([0x64, 0x0F, 0xA2]))
        with self.assertRaisesRegex(NotImplementedError, "opcode 0F A2 at 1000:0000: 64 0f a2"):
            cpu.step()


class TestMovWithSegmentRegisters(Machine):
    def test_fs_and_gs_are_loaded_from_a_register_and_from_memory(self):
        for register, reg_field in ((FS, 4), (GS, 5)):
            cpu = self.start(bytes([0x8E, 0xC0 | reg_field << 3]), eax=0xAAAA1234)  # mov fs, ax
            cpu.step()
            self.assertEqual(cpu.s[register], 0x1234)
            cpu = self.start(bytes([0x8E, 0x06 | reg_field << 3, 0x00, 0x01]))  # mov fs, [0100]
            cpu.ww(0x2000, 0x0100, 0x4321)
            cpu.step()
            self.assertEqual(cpu.s[register], 0x4321)

    def test_fs_and_gs_are_stored_in_a_register_and_in_memory(self):
        for register, reg_field in ((FS, 4), (GS, 5)):
            cpu = self.start(bytes([0x8C, 0xC0 | reg_field << 3]), eax=0xAAAA0000)  # mov ax, fs
            cpu.s[register] = 0x1357
            cpu.step()
            self.assertEqual(cpu.get32(AX), 0xAAAA1357)
            cpu = self.start(bytes([0x8C, 0x06 | reg_field << 3, 0x00, 0x01]))  # mov [0100], fs
            cpu.s[register] = 0x2468
            cpu.step()
            self.assertEqual(cpu.rw(0x2000, 0x0100), 0x2468)

    def test_with_the_size_of_32_bits_a_register_gets_zeros_in_the_high_half_and_memory_only_a_word(self):
        cpu = self.start(bytes([0x66, 0x8C, 0xE0]), eax=0xAAAA0000)  # mov eax, fs
        cpu.s[FS] = 0x1357
        cpu.step()
        self.assertEqual(cpu.get32(AX), 0x00001357)
        cpu = self.start(bytes([0x66, 0x8C, 0x1E, 0x00, 0x01]))  # mov [0100], ds
        cpu.wd(0x2000, 0x0100, 0xFFFFFFFF)
        cpu.step()
        self.assertEqual(cpu.rd(0x2000, 0x0100), 0xFFFF2000)

    def test_the_load_with_the_size_of_32_bits_takes_a_word(self):
        cpu = self.start(bytes([0x66, 0x8E, 0xE0]), eax=0xAAAA1234)  # mov fs, ax
        cpu.step()
        self.assertEqual(cpu.s[FS], 0x1234)
        cpu = self.start(bytes([0x66, 0x8E, 0x2E, 0x00, 0x01]))  # mov gs, [0100]
        cpu.wd(0x2000, 0x0100, 0x99998888)
        cpu.step()
        self.assertEqual((cpu.s[GS], cpu.ip), (0x8888, 5))

    def test_the_other_numbers_stop_the_emulator_and_cs_cannot_be_loaded(self):
        for second in (6, 7):
            for opcode in (0x8C, 0x8E):
                cpu = self.start(bytes([opcode, 0xC0 | second << 3]))
                with self.assertRaises(NotImplementedError):
                    cpu.step()
        cpu = self.start(bytes([0x8E, 0xC8]))  # mov cs, ax
        with self.assertRaises(NotImplementedError):
            cpu.step()
        for second in (6, 7):
            cpu = self.start(bytes([0x66, 0x8C, 0xC0 | second << 3]))  # mov eax, sreg 6
            with self.assertRaises(NotImplementedError):
                cpu.step()


class TestPushAndPop(Machine):
    def test_push_and_pop_of_fs_and_gs_as_words(self):
        for register, push, pop in ((FS, 0xA0, 0xA1), (GS, 0xA8, 0xA9)):
            cpu = self.start(bytes([0x0F, push, 0x0F, pop]))
            cpu.s[register] = 0x1234
            cpu.step()
            self.assertEqual((cpu.rw(0x4000, 0x01FE), cpu.r[SP]), (0x1234, 0x01FE))
            cpu.s[register] = 0
            cpu.step()
            self.assertEqual((cpu.s[register], cpu.r[SP]), (0x1234, 0x0200))

    def test_push_and_pop_with_the_size_of_32_bits_move_four_bytes(self):
        for register, push, pop in ((FS, 0xA0, 0xA1), (GS, 0xA8, 0xA9)):
            cpu = self.start(bytes([0x66, 0x0F, push, 0x66, 0x0F, pop]))
            cpu.s[register] = 0x1234
            cpu.wd(0x4000, 0x01FC, 0xFFFFFFFF)
            cpu.step()
            self.assertEqual((cpu.rw(0x4000, 0x01FC), cpu.r[SP]), (0x1234, 0x01FC))
            cpu.wd(0x4000, 0x01FC, 0xABCD5678)  # a pop takes the low 16 bits
            cpu.step()
            self.assertEqual((cpu.s[register], cpu.r[SP]), (0x5678, 0x0200))

    def test_the_segments_of_the_386_with_the_size_of_32_bits(self):
        for opcode, register in ((0x06, CPU.ES), (0x0E, CPU.CS), (0x16, CPU.SS), (0x1E, CPU.DS)):
            cpu = self.start(bytes([0x66, opcode]))
            cpu.step()
            self.assertEqual((cpu.rd(0x4000, 0x01FC), cpu.r[SP]), (cpu.s[register], 0x01FC), hex(opcode))
        for opcode, register in ((0x07, CPU.ES), (0x17, CPU.SS), (0x1F, CPU.DS)):
            cpu = self.start(bytes([0x66, opcode]))
            cpu.push32(0xAAAA3456)
            cpu.step()
            self.assertEqual((cpu.s[register], cpu.r[SP]), (0x3456, 0x0200), hex(opcode))


class TestFarPointers(Machine):
    def test_lfs_lgs_and_lss_load_a_register_and_the_segment_from_a_pointer_of_16_16(self):
        for second, register in ((0xB4, FS), (0xB5, GS), (0xB2, CPU.SS)):
            cpu = self.start(bytes([0x0F, second, 0x0E, 0x00, 0x01]))  # lfs cx, [0100]
            cpu.wd(0x2000, 0x0100, 0x3000_5678)  # the offset 5678 and the segment 3000
            cpu.step()
            self.assertEqual((cpu.r[CX], cpu.s[register], cpu.ip), (0x5678, 0x3000, 5), hex(second))

    def test_with_the_size_of_32_bits_the_pointer_is_of_16_32(self):
        for second, register in ((0xB4, FS), (0xB5, GS), (0xB2, CPU.SS)):
            cpu = self.start(bytes([0x66, 0x0F, second, 0x0E, 0x00, 0x01]))  # lfs ecx, [0100]
            cpu.wd(0x2000, 0x0100, 0x89ABCDEF)
            cpu.ww(0x2000, 0x0104, 0x3456)
            cpu.step()
            self.assertEqual((cpu.get32(CX), cpu.s[register]), (0x89ABCDEF, 0x3456), hex(second))

    def test_les_and_lds_with_the_size_of_32_bits(self):
        for opcode, register in ((0xC4, CPU.ES), (0xC5, CPU.DS)):
            cpu = self.start(bytes([0x66, opcode, 0x0E, 0x00, 0x01]))
            cpu.wd(0x2000, 0x0100, 0x12345678)
            cpu.ww(0x2000, 0x0104, 0x3456)
            cpu.step()
            self.assertEqual((cpu.get32(CX), cpu.s[register]), (0x12345678, 0x3456), hex(opcode))
            self.assertEqual(cpu.ip, 5)

    def test_the_pointer_is_read_before_anything_is_written(self):
        cpu = self.start(bytes([0x66, 0x0F, 0xB4, 0x1F]), ebx=0x0100)  # lfs ebx, [bx]
        cpu.wd(0x2000, 0x0100, 0x00000200)
        cpu.ww(0x2000, 0x0104, 0x3777)
        cpu.step()
        self.assertEqual((cpu.get32(BX), cpu.s[FS]), (0x200, 0x3777))

    def test_a_register_operand_stops_the_emulator(self):
        for code in (bytes([0x0F, 0xB4, 0xC3]), bytes([0x66, 0xC5, 0xC3])):
            cpu = self.start(code)
            with self.assertRaisesRegex(NotImplementedError, "with a register"):
                cpu.step()

    def test_a_32_bit_address_for_the_pointer(self):
        cpu = self.start(bytes([0x67, 0x66, 0x0F, 0xB5, 0x0B]), ebx=0x00010000)  # lgs ecx, [ebx]
        cpu.wd(0x2000, 0x10000, 0x0BADF00D)
        cpu.ww(0x2000, 0x10004, 0x3999)
        cpu.step()
        self.assertEqual((cpu.get32(CX), cpu.s[GS]), (0x0BADF00D, 0x3999))


class TestCode32(Machine):
    def test_in_a_code_segment_of_32_bits_the_sizes_are_32_and_the_prefixes_make_them_16(self):
        cpu = self.start(bytes([0x0F, 0xA0]))  # push fs: a doubleword
        cpu.code32 = True
        cpu.s[FS] = 0x1234
        cpu.step()
        self.assertEqual(cpu.r[SP], 0x01FC)
        cpu = self.start(bytes([0x66, 0x0F, 0xA0]))
        cpu.code32 = True
        cpu.step()
        self.assertEqual(cpu.r[SP], 0x01FE)
        cpu = self.start(bytes([0x0F, 0xB4, 0x0D]) + le32(0x100))  # lfs ecx, [00000100]: addresses of 32 bits too
        cpu.code32 = True
        cpu.wd(0x2000, 0x0100, 0x76543210)
        cpu.ww(0x2000, 0x0104, 0x3333)
        cpu.step()
        self.assertEqual((cpu.get32(CX), cpu.s[FS]), (0x76543210, 0x3333))


if __name__ == "__main__":
    unittest.main()
