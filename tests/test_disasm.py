import unittest

from mydos.cpu import CPU
from mydos.disasm import disasm


# A ModRM byte that is valid after the opcode: a register operand, and the 'reg' field the opcode needs.
# Most opcodes accept any; the groups below only know some operations (C6 and C7 /0 = MOV, FE and FF /0 = INC),
# and LES and LDS need a memory operand ([bx]).
MODRM_FOR = {0xC6: 0xC0, 0xC7: 0xC0, 0xFE: 0xC0, 0xFF: 0xC0, 0xC4: 0x1F, 0xC5: 0x1F}


def machine(code):
    """A CPU with `code` placed at 1000:0100."""
    cpu = CPU()
    cpu.s[CPU.CS] = 0x1000
    cpu.mem[0x10100:0x10100 + len(code)] = code
    return cpu


def dis(code):
    return disasm(machine(code), 0x1000, 0x100)


class TestText(unittest.TestCase):
    def test_known_instructions(self):
        cases = [
            (b"\x8B\xC3", "mov ax, bx"),
            (b"\x89\xD8", "mov ax, bx"),
            (b"\x8A\xCC", "mov cl, ah"),
            (b"\x88\xE1", "mov cl, ah"),
            (b"\x26\x8A\x4F\x01", "mov cl, es:[bx+1]"),   # from esetup.exe
            (b"\x88\x07", "mov [bx], al"),
            (b"\x8B\x07", "mov ax, [bx]"),
            (b"\x8B\x46\xFE", "mov ax, [bp-2]"),
            (b"\x8B\x80\x00\x01", "mov ax, [bx+si+100]"),
            (b"\x8B\x2E\x02\x00", "mov bp, [0002]"),
            (b"\x2E\x89\x16\x91\x02", "mov cs:[0291], dx"),
            (b"\x8E\xD8", "mov ds, ax"),
            (b"\x8C\x06\x7B\x00", "mov [007B], es"),
            (b"\xA3\x7D\x00", "mov [007D], ax"),
            (b"\xB4\x30", "mov ah, 30"),
            (b"\xB8\x34\x12", "mov ax, 1234"),
            (b"\xCD\x21", "int 21"),
            (b"\x33\xC0", "xor ax, ax"),
            (b"\x3C\x05", "cmp al, 05"),
            (b"\x83\xC0\xFF", "add ax, -1"),
            (b"\x83\xEC\x04", "sub sp, 4"),
            (b"\x80\x3F\x00", "cmp byte [bx], 00"),
            (b"\x81\x3E\x10\x00\x99\x99", "cmp word [0010], 9999"),
            (b"\x75\xFD", "jne 00FF"),          # 0102 - 3
            (b"\xE8\x55\x01", "call 0258"),     # 0103 + 0155
            (b"\xE9\x3F\xFF", "jmp 0042"),      # 0103 - 00C1, the bytes from esetup.exe
            (b"\xE9\x00\x00", "jmp 0103"),
            (b"\xE2\xFE", "loop 0100"),
            (b"\xF2\xAE", "repne scasb"),
            (b"\xF3\xAA", "rep stosb"),
            (b"\xF3\xA5", "rep movsw"),
            (b"\xF3\xA6", "repe cmpsb"),
            (b"\xF3\xAE", "repe scasb"),
            (b"\xAC", "lodsb"),
            (b"\xAB", "stosw"),
            (b"\x1E", "push ds"),
            (b"\x50", "push ax"),
            (b"\x5D", "pop bp"),
            (b"\x43", "inc bx"),
            (b"\x4D", "dec bp"),
            (b"\xFC", "cld"),
            (b"\x98", "cbw"),
            (b"\x92", "xchg ax, dx"),   # from esetup.exe
            (b"\x97", "xchg ax, di"),
            (b"\x87\xD8", "xchg bx, ax"),   # reg field BX first, r/m AX second
            (b"\x86\x07", "xchg al, [bx]"),
            (b"\x87\x4F\x02", "xchg cx, [bx+2]"),
            (b"\x99", "cwd"),
            (b"\xC3", "ret"),
            (b"\xCB", "retf"),
            (b"\xC4\x5E\x06", "les bx, [bp+6]"),   # from esetup.exe
            (b"\xC5\x37", "lds si, [bx]"),
            (b"\xC2\x06\x00", "ret 0006"),
            (b"\xCA\x04\x00", "retf 0004"),
            (b"\xF7\xD9", "neg cx"),
            (b"\xF7\xEA", "imul dx"),   # from esetup.exe
            (b"\xF7\xE3", "mul bx"),
            (b"\xF7\x3F", "idiv word [bx]"),
            (b"\xF6\xF1", "div cl"),
            (b"\xF6\x17", "not byte [bx]"),
            (b"\xF6\xC3\x80", "test bl, 80"),
            (b"\xF7\x07\x34\x12", "test word [bx], 1234"),
            (b"\x26\xFF\x57\x02", "call word es:[bx+2]"),   # from esetup.exe
            (b"\xFF\xD0", "call ax"),
            (b"\xFF\x1F", "call far [bx]"),
            (b"\xFF\xE3", "jmp bx"),
            (b"\xFF\x6F\x04", "jmp far [bx+4]"),
            (b"\xFF\x36\x34\x12", "push word [1234]"),
            (b"\xFF\xF1", "push cx"),
            (b"\xFF\xC0", "inc ax"),
            (b"\xFF\x0F", "dec word [bx]"),
            (b"\xFE\xC1", "inc cl"),
            (b"\xFE\x0F", "dec byte [bx]"),
            (b"\x26\xC6\x07\xFF", "mov byte es:[bx], FF"),   # from esetup.exe
            (b"\xC7\x06\x34\x12\x00\x01", "mov word [1234], 0100"),
            (b"\xC6\xC1\x05", "mov cl, 05"),
            (b"\xC7\xC3\x34\x12", "mov bx, 1234"),
            (b"\xD3\xE3", "shl bx, cl"),
            (b"\xD1\xE3", "shl bx, 1"),   # from esetup.exe
            (b"\xD0\x0F", "ror byte [bx], 1"),
            (b"\xD2\xE0", "shl al, cl"),
            (b"\xD2\x3F", "sar byte [bx], cl"),
            (b"\xD3\x2F", "shr word [bx], cl"),
            (b"\xF7\x1F", "neg word [bx]"),
        ]
        for code, text in cases:
            with self.subTest(code=code.hex(" ")):
                length, got = dis(code)
                self.assertEqual(got, text)
                self.assertEqual(length, len(code))

    def test_unknown_opcode_is_one_byte(self):
        self.assertEqual(dis(b"\x0F\x00"), (1, "db 0F"))


class TestAgreesWithCpu(unittest.TestCase):
    def test_every_opcode_the_cpu_knows_has_the_same_length_here(self):
        """If you teach the CPU a new opcode and forget disasm.py, this fails."""
        for op in CPU().ops:
            with self.subTest(op=f"{op:02X}"):
                code = bytes([op, MODRM_FOR.get(op, 0xD8), 0, 0, 0, 0, 0, 0])
                cpu = machine(code)
                cpu.ip = 0x100
                fetched = []
                real_fetch8 = cpu.fetch8
                cpu.fetch8 = lambda: fetched.append(1) or real_fetch8()
                try:
                    cpu.step()
                except Exception:  # INT with no handler, HLT: they still read their operands first
                    pass
                length, text = disasm(machine(code), 0x1000, 0x100)
                self.assertFalse(text.startswith("db"), text)
                self.assertEqual(length, len(fetched), text)


if __name__ == "__main__":
    unittest.main()
