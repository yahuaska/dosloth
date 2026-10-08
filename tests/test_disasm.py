import unittest

from mydos.cpu import CPU
from mydos.disasm import disasm


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
            (b"\xE2\xFE", "loop 0100"),
            (b"\xF2\xAE", "repne scasb"),
            (b"\x1E", "push ds"),
            (b"\x50", "push ax"),
            (b"\x5D", "pop bp"),
            (b"\x43", "inc bx"),
            (b"\x4D", "dec bp"),
            (b"\xFC", "cld"),
            (b"\xC3", "ret"),
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
                code = bytes([op, 0xC0, 0, 0, 0, 0, 0, 0])
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
