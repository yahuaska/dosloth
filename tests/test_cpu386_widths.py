"""The width of the stack pointer and of the instruction pointer (step 3a1): a 32-bit stack (CPU.stack32) works on ESP,
and CPU.ip_mask makes the targets of jumps, calls and returns as wide as the pointer. Real mode has 16 bits of both."""
import unittest

from dosloth.cpu import CPU
from dosloth.regs import AX, BP, BX, CX, SP
from tests.test_cpu386 import Machine, le32

SS_BASE = 0x40000  # SS = 4000h


class TestStack32(Machine):
    """The stack segment is at SS = 4000h, so ESP 0001xxxxh is at 5xxxxh."""

    def stack(self, code, **registers):
        cpu = self.start(code, **registers)
        cpu.stack32 = True
        return cpu

    def test_push_and_pop_of_a_word_go_by_the_whole_of_esp(self):
        cpu = self.stack(bytes([0x50, 0x59]), esp=0x00010200, eax=0xAAAA1234)  # push ax ; pop cx
        cpu.step()
        self.assertEqual((cpu.get32(SP), cpu.rw(0x4000, 0x101FE)), (0x000101FE, 0x1234))
        cpu.step()
        self.assertEqual((cpu.get32(SP), cpu.r[CX]), (0x00010200, 0x1234))

    def test_push_and_pop_of_a_doubleword_go_by_the_whole_of_esp(self):
        cpu = self.stack(bytes([0x66, 0x50, 0x66, 0x59]), esp=0x00010200, eax=0xCAFEBABE)  # push eax ; pop ecx
        cpu.step()
        self.assertEqual((cpu.get32(SP), cpu.rd(0x4000, 0x101FC)), (0x000101FC, 0xCAFEBABE))
        cpu.step()
        self.assertEqual((cpu.get32(SP), cpu.get32(CX)), (0x00010200, 0xCAFEBABE))

    def test_esp_wraps_at_32_bits_and_not_at_16(self):
        cpu = self.stack(bytes([0x66, 0x50]), esp=0x00000002, eax=0x11223344)
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0xFFFFFFFE)  # 2 - 4
        self.assertEqual(cpu.mem[SS_BASE - 2], 0x44)  # the address is cut to 1 MB by the real-mode memory
        cpu = self.stack(bytes([0x66, 0x58]), esp=0xFFFFFFFE)  # pop eax: 4 on
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x00000002)
        cpu = self.stack(bytes([0x58]), esp=0xFFFFFFFF)  # pop ax: 2 on
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x00000001)

    def test_without_the_flag_the_stack_is_sp_and_the_high_half_of_esp_stays(self):
        cpu = self.start(bytes([0x66, 0x50, 0x66, 0x58, 0x50, 0x58]), esp=0x12340002, eax=1)
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x1234FFFE)  # SP wraps at 16 bits
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x12340002)
        cpu.step()
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x12340002)

    def test_get_stack_and_set_stack(self):
        cpu = self.start(b"", esp=0x12345678, ebp=0x00ABCDEF)
        self.assertEqual((cpu.get_stack(SP), cpu.get_stack(BP)), (0x5678, 0xCDEF))
        cpu.set_stack(SP, 0xFFFF0010)
        self.assertEqual(cpu.get32(SP), 0x12340010)  # only the low 16 bits are written, the others stay
        cpu.stack32 = True
        self.assertEqual((cpu.get_stack(SP), cpu.get_stack(BP)), (0x12340010, 0x00ABCDEF))
        cpu.set_stack(BP, 0x80000001)
        self.assertEqual(cpu.get32(BP), 0x80000001)

    def test_pusha_pushes_the_stack_pointer_as_it_was(self):
        cpu = self.stack(bytes([0x60]), esp=0x00010200)
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x00010200 - 16)
        self.assertEqual(cpu.rw(0x4000, 0x10200 - 10), 0x0200)  # the fifth word: SP, the low 16 bits of ESP
        cpu = self.stack(bytes([0x66, 0x60]), esp=0x00010200)
        cpu.step()
        self.assertEqual((cpu.get32(SP), cpu.rd(0x4000, 0x10200 - 20)), (0x00010200 - 32, 0x00010200))

    def test_ret_with_a_count_and_retf_with_a_count_drop_the_arguments_from_esp(self):
        cpu = self.stack(bytes([0xC2, 0x08, 0x00]), esp=0x00010200)
        cpu.ww(0x4000, 0x10200, 0x0300)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.get32(SP)), (0x0300, 0x00010200 + 2 + 8))
        cpu = self.stack(bytes([0xCA, 0x04, 0x00]), esp=0x00010200)
        cpu.ww(0x4000, 0x10200, 0x0300)
        cpu.ww(0x4000, 0x10202, 0x3000)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.s[CPU.CS], cpu.get32(SP)), (0x0300, 0x3000, 0x00010200 + 4 + 4))
        cpu = self.stack(bytes([0x66, 0xC2, 0x08, 0x00]), esp=0x0000FFFC)  # a 32-bit return: past 64 KB
        cpu.wd(0x4000, 0xFFFC, 0x00000300)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.get32(SP)), (0x0300, 0x0000FFFC + 4 + 8))

    def test_enter_and_leave_of_16_bits_with_a_stack_of_32(self):
        cpu = self.stack(bytes([0xC8, 0x10, 0x00, 0x00, 0xC9]), esp=0x00010200, ebp=0x00020000)
        cpu.step()
        self.assertEqual((cpu.get32(SP), cpu.get32(BP)), (0x00010200 - 2 - 0x10, 0x000101FE))
        self.assertEqual(cpu.rw(0x4000, 0x101FE), 0x0000)  # the low half of the old EBP is what a 16-bit push keeps
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x00010200)
        self.assertEqual(cpu.r[BP], 0x0000)

    def test_enter_and_leave_of_32_bits_with_a_stack_of_32_with_every_level(self):
        for level in range(0, 4):
            cpu = self.stack(bytes([0x66, 0xC8, 0x0C, 0x00, level]), esp=0x00010400, ebp=0x00010300)
            for i in range(8):
                cpu.wd(0x4000, 0x10300 - 4 * i, 0x9000 + i)
            cpu.step()
            bp, sp, memory = 0x00010300, 0x00010400 - 4, {}
            memory[sp] = 0x00010300
            frame = sp
            if level:
                for _ in range(level - 1):
                    bp -= 4
                    sp -= 4
                    memory[sp] = 0x9000 + (0x00010300 - bp) // 4
                sp -= 4
                memory[sp] = frame
            self.assertEqual((cpu.get32(BP), cpu.get32(SP)), (frame, sp - 0x0C), f"level {level}")
            for address, value in memory.items():
                self.assertEqual(cpu.rd(0x4000, address), value, f"level {level} at {address:X}")
        cpu = self.stack(bytes([0x66, 0xC9]), esp=0x00010100, ebp=0x00010180)  # leave
        cpu.wd(0x4000, 0x10180, 0xAABBCCDD)
        cpu.step()
        self.assertEqual((cpu.get32(BP), cpu.get32(SP)), (0xAABBCCDD, 0x00010184))

    def test_a_borrow_or_a_carry_crosses_64_kb_in_every_instruction_of_16_bits(self):
        cpu = self.stack(bytes([0x50]), esp=0x00010001)  # push ax: 10001h - 2
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x0000FFFF)
        cpu = self.stack(bytes([0x58]), esp=0x0000FFFF)  # pop ax: FFFFh + 2
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x00010001)
        cpu = self.stack(bytes([0xC2, 0x20, 0x00]), esp=0x0000FFF0)  # ret 20h: the pop and the 20h cross 10000h
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x0000FFF0 + 2 + 0x20)
        cpu = self.stack(bytes([0xCA, 0x20, 0x00]), esp=0x0000FFF0)  # retf 20h
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x0000FFF0 + 4 + 0x20)
        cpu = self.stack(bytes([0xC8, 0x20, 0x00, 0x00]), esp=0x00010010, ebp=0)  # enter 20h, 0: down across 10000h
        cpu.step()
        self.assertEqual((cpu.get32(SP), cpu.get32(BP)), (0x00010010 - 2 - 0x20, 0x0001000E))
        cpu = self.stack(bytes([0xC9]), esp=0x00010100, ebp=0x00030180)  # leave: ESP takes all of EBP
        cpu.wd(0x4000, 0x30180, 0x0000ABCD)
        cpu.step()
        self.assertEqual((cpu.get32(SP), cpu.r[BP]), (0x00030182, 0xABCD))

    def test_a_borrow_or_a_carry_crosses_64_kb_in_every_instruction_of_32_bits(self):
        cpu = self.stack(bytes([0x66, 0xC8, 0x20, 0x00, 0x00]), esp=0x00010020, ebp=0)  # enter 20h, 0
        cpu.step()
        self.assertEqual((cpu.get32(SP), cpu.get32(BP)), (0x00010020 - 4 - 0x20, 0x0001001C))
        cpu = self.stack(bytes([0x66, 0xC9]), esp=0x00010100, ebp=0x00030180)  # leave
        cpu.wd(0x4000, 0x30180, 0x12345678)
        cpu.step()
        self.assertEqual((cpu.get32(SP), cpu.get32(BP)), (0x00030184, 0x12345678))
        cpu = self.stack(bytes([0x66, 0xC2, 0x20, 0x00]), esp=0x0000FFF0)  # ret 20h
        cpu.step()
        self.assertEqual(cpu.get32(SP), 0x0000FFF0 + 4 + 0x20)

    def test_a_call_and_a_return_with_a_stack_of_32(self):
        cpu = self.stack(bytes([0x66, 0xE8]) + le32(0x10) + b"\x90" * 16 + bytes([0x66, 0xC3]), esp=0x00010200)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.get32(SP), cpu.rd(0x4000, 0x101FC)), (0x16, 0x000101FC, 6))
        cpu.step()
        self.assertEqual((cpu.ip, cpu.get32(SP)), (6, 0x00010200))


class TestInstructionPointerWidth(Machine):
    """The code is at CS = 1000h; an instruction at 0FF00h is followed by a target that is past 64 KB."""

    def at(self, ip, code, **registers):
        cpu = self.start(b"", **registers)
        cpu.mem[0x10000 + ip:0x10000 + ip + len(code)] = code
        cpu.ip = ip
        return cpu

    def test_a_short_jump_and_the_loops_go_past_64_kb_with_a_wide_mask(self):
        for code, flag in ((bytes([0xEB, 0x7F]), None), (bytes([0xE2, 0x7F]), None), (bytes([0x75, 0x7F]), 0)):
            cpu = self.at(0xFFF0, code, ecx=5)
            cpu.ip_mask = 0xFFFFFFFF
            cpu.flags = 0x0002
            cpu.step()
            self.assertEqual(cpu.ip, 0xFFF2 + 0x7F, code.hex())
            cpu = self.at(0xFFF0, code, ecx=5)  # and cut to 16 bits with the usual one
            cpu.flags = 0x0002
            cpu.step()
            self.assertEqual(cpu.ip, (0xFFF2 + 0x7F) & 0xFFFF, code.hex())

    def test_a_short_jump_backwards_stays_below_the_start_with_a_wide_mask(self):
        cpu = self.at(0x0002, bytes([0xEB, 0xF0]))
        cpu.ip_mask = 0xFFFFFFFF
        cpu.step()
        self.assertEqual(cpu.ip, 0xFFFFFFF4)  # 4 - 16: below 0 is not a place of the segment, in the flat mode it wraps
        cpu = self.at(0x0002, bytes([0xEB, 0xF0]))
        cpu.step()
        self.assertEqual(cpu.ip, 0xFFF4)

    def test_a_distance_of_32_bits_goes_past_64_kb_with_a_wide_mask(self):
        for opcode in (0xE8, 0xE9):
            cpu = self.at(0xFF00, bytes([0x66, opcode]) + le32(0x200))
            cpu.ip_mask = 0xFFFFFFFF
            cpu.step()
            self.assertEqual(cpu.ip, 0xFF06 + 0x200, f"{opcode:02X}")
        cpu = self.at(0xFF00, bytes([0x66, 0x0F, 0x84]) + le32(0x200))  # je rel32
        cpu.ip_mask = 0xFFFFFFFF
        cpu.flags = 0x0002 | 0x0040
        cpu.step()
        self.assertEqual(cpu.ip, 0xFF07 + 0x200)
        cpu = self.at(0xFF00, bytes([0x66, 0xE9]) + le32(-0x10000))  # backwards by 64 KB: wraps at 32 bits
        cpu.ip_mask = 0xFFFFFFFF
        cpu.step()
        self.assertEqual(cpu.ip, 0xFF06 - 0x10000 & 0xFFFFFFFF)

    def test_a_distance_of_16_bits_is_cut_to_16_bits_with_any_mask(self):
        for opcode in (0xE8, 0xE9):
            cpu = self.at(0xFF00, bytes([opcode, 0x00, 0x02]))
            cpu.ip_mask = 0xFFFFFFFF
            cpu.step()
            self.assertEqual(cpu.ip, (0xFF03 + 0x200) & 0xFFFF, f"{opcode:02X}")
        cpu = self.at(0x0010, bytes([0xE9, 0xFE, 0xFF]))  # -2: it is not 65534 more
        cpu.ip_mask = 0xFFFFFFFF
        cpu.step()
        self.assertEqual(cpu.ip, 0x0013 - 2)

    def test_a_return_and_an_indirect_jump_or_call_go_past_64_kb_with_a_wide_mask(self):
        cpu = self.start(bytes([0x66, 0xC3]))
        cpu.ip_mask = 0xFFFFFFFF
        cpu.push32(0x00020300)
        cpu.step()
        self.assertEqual(cpu.ip, 0x00020300)
        cpu = self.start(bytes([0x66, 0xC2, 0x04, 0x00]))
        cpu.ip_mask = 0xFFFFFFFF
        cpu.push32(0x00030400)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.r[SP]), (0x00030400, 0x0204))
        for code, label in ((bytes([0x66, 0xFF, 0xD3]), "call ebx"), (bytes([0x66, 0xFF, 0xE3]), "jmp ebx")):
            cpu = self.start(code, ebx=0x00040500)
            cpu.ip_mask = 0xFFFFFFFF
            cpu.step()
            self.assertEqual(cpu.ip, 0x00040500, label)
            cpu = self.start(code, ebx=0x00040500)  # cut to 16 bits with the usual mask
            cpu.step()
            self.assertEqual(cpu.ip, 0x0500, label)

    def test_the_usual_mask_is_16_bits_and_the_stack_is_16_bits(self):
        cpu = CPU()
        self.assertEqual((cpu.ip_mask, cpu.stack32), (0xFFFF, False))


if __name__ == "__main__":
    unittest.main()
