import unittest

from mydos.cpu import ADDRESS_SIZED, CPU
from mydos.disasm import disasm


# A ModRM byte that is valid after the opcode: a register operand, and the 'reg' field the opcode needs.
# Most opcodes accept any; the groups below only know some operations (8F, C6 and C7 /0 = POP and MOV,
# FE and FF /0 = INC),
# and LEA, LES and LDS need a memory operand ([bx]); 0F is followed by an opcode of its own, 84 is a jump.
MODRM_FOR = {0x0F: 0x84, 0x8D: 0x1F, 0x8F: 0xC0, 0xC6: 0xC0, 0xC7: 0xC0, 0xFE: 0xC0, 0xFF: 0xC0, 0xC4: 0x1F, 0xC5: 0x1F}


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
            (b"\x9A\x0F\x00\xEC\x14", "call 14EC:000F"),   # from esetup.exe
            (b"\xEA\x00\x01\x00\x20", "jmp 2000:0100"),
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
            (b"\xA9\x01\x00", "test ax, 0001"),   # from esetup.exe
            (b"\xA8\x80", "test al, 80"),
            (b"\x84\xC3", "test bl, al"),
            (b"\x85\x07", "test [bx], ax"),
            (b"\x85\xD9", "test cx, bx"),
            (b"\x97", "xchg ax, di"),
            (b"\x87\xD8", "xchg bx, ax"),   # reg field BX first, r/m AX second
            (b"\x86\x07", "xchg al, [bx]"),
            (b"\x87\x4F\x02", "xchg cx, [bx+2]"),
            (b"\x99", "cwd"),
            (b"\x9B", "wait"),
            (b"\x66\x9C", "pushfd"),   # from install.exe: the test of the processor
            (b"\x66\x9D", "popfd"),
            (b"\x66\x58", "pop eax"),
            (b"\x66\x50", "push eax"),
            (b"\x66\x8B\xC8", "mov ecx, eax"),
            (b"\x66\x35\x00\x00\x04\x00", "xor eax, 00040000"),
            (b"\x66\x33\xC1", "xor eax, ecx"),
            (b"\x66\xB8\x01\x00\x00\x00", "mov eax, 00000001"),
            (b"\x66\xB9\x78\x56\x34\x12", "mov ecx, 12345678"),
            (b"\x66\x83\xF9\x00", "cmp ecx, 0"),
            (b"\x66\x83\xC1\xFF", "add ecx, -1"),
            (b"\x66\x81\xC3\x00\x00\x00\x80", "add ebx, 80000000"),
            (b"\x66\x81\x07\x01\x00\x00\x00", "add dword [bx], 00000001"),
            (b"\x66\xA1\x1B\x00", "mov eax, [001B]"),
            (b"\x66\xA3\x1B\x00", "mov [001B], eax"),
            (b"\x66\x89\x16\x06\x00", "mov [0006], edx"),
            (b"\x66\x8B\x40\x10", "mov eax, [bx+si+10]"),
            (b"\x66\x89\xC8", "mov eax, ecx"),
            (b"\x66\x01\xD8", "add eax, ebx"),
            (b"\x66\x03\x07", "add eax, [bx]"),
            (b"\x66\x05\x01\x00\x00\x00", "add eax, 00000001"),
            (b"\x66\x3D\xFF\xFF\xFF\xFF", "cmp eax, FFFFFFFF"),
            (b"\x66\xC7\x06\x34\x12\x78\x56\x00\x00", "mov dword [1234], 00005678"),
            (b"\x66\xC7\xC1\x01\x00\x00\x00", "mov ecx, 00000001"),
            (b"\x66\x8D\x40\x10", "lea eax, [bx+si+10]"),
            (b"\x66\x40", "inc eax"),
            (b"\x66\x4F", "dec edi"),
            (b"\x66\x55", "push ebp"),
            (b"\x66\x5D", "pop ebp"),
            (b"\x66\x87\xCB", "xchg ecx, ebx"),  # the register of the 'reg' field first, as for 16 bits
            (b"\x66\x93", "xchg eax, ebx"),
            (b"\x66\x85\xC0", "test eax, eax"),
            (b"\x66\xA9\x01\x00\x00\x80", "test eax, 80000001"),
            (b"\x66\x98", "cwde"),
            (b"\x66\x99", "cdq"),
            (b"\x66\x90", "nop"),
            (b"\x66\x04\x05", "add al, 05"),
            (b"\xD4\x0A", "aam"),   # from install.exe
            (b"\xD4\x08", "aam 08"),
            (b"\xD5\x0A", "aad"),
            (b"\xD5\x10", "aad 10"),
            (b"\xDB\xE3", "esc 1C, bx"),   # fninit, from install.exe
            (b"\xDD\x7E\x00", "esc 2F, [bp]"),   # fnstsw [bp]
            (b"\xD9\x06\x34\x12", "esc 08, [1234]"),
            (b"\x60", "pusha"),   # from esetup.exe
            (b"\x6A\x02", "push 2"),   # from esetup.exe
            (b"\x6A\xFE", "push -2"),
            (b"\x68\x34\x12", "push 1234"),
            (b"\x61", "popa"),
            (b"\x9C", "pushf"),
            (b"\x9D", "popf"),
            (b"\x9E", "sahf"),
            (b"\x9F", "lahf"),
            (b"\xC3", "ret"),
            (b"\xCB", "retf"),
            (b"\xC9", "leave"),   # from esetup.exe
            (b"\xF5", "cmc"),   # from xcomapoc.exe
            (b"\xCF", "iret"),
            (b"\x0F\x84\xBA\x00", "je 01BE"),   # from install.exe: 0104 + 0xBA
            (b"\x0F\x80\x00\x00", "jo 0104"),
            (b"\x0F\x8F\xFC\xFF", "jg 0100"),
            (b"\x0F\x85\x00\x80", "jne 8104"),
            (b"\xE6\x43", "out 43, al"),   # from esetup.exe
            (b"\xE4\x40", "in al, 40"),
            (b"\xE5\x61", "in ax, 61"),
            (b"\xE7\x10", "out 10, ax"),
            (b"\xEC", "in al, dx"),
            (b"\xED", "in ax, dx"),
            (b"\xEE", "out dx, al"),
            (b"\xEF", "out dx, ax"),
            (b"\xC4\x5E\x06", "les bx, [bp+6]"),   # from esetup.exe
            (b"\x8D\x46\x80", "lea ax, [bp-80]"),   # from esetup.exe
            (b"\x8D\x40\x05", "lea ax, [bx+si+5]"),
            (b"\x8D\x06\x00\x04", "lea ax, [0400]"),
            (b"\x26\x8D\x07", "lea ax, [bx]"),
            (b"\x8F\x06\x4E\x21", "pop word [214E]"),
            (b"\x8F\x47\x02", "pop word [bx+2]"),
            (b"\x8F\xC3", "pop bx"),
            (b"\xC5\x37", "lds si, [bx]"),
            (b"\xC2\x06\x00", "ret 0006"),
            (b"\xCA\x04\x00", "retf 0004"),
            (b"\xC8\x12\x00\x00", "enter 0012, 00"),   # from esetup.exe
            (b"\xC8\x00\x01\x03", "enter 0100, 03"),
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


class TestAddresses32(unittest.TestCase):
    """The prefix 67h, and the code segments of 32 bits."""

    def test_the_registers_of_the_address_and_the_displacement(self):
        for code, text in (
                ("67 8B 03", "mov ax, [ebx]"),
                ("67 8B 43 FE", "mov ax, [ebx-2]"),
                ("67 8B 43 7F", "mov ax, [ebx+7F]"),
                ("67 8B 83 04 00 01 00", "mov ax, [ebx+10004]"),
                ("67 8B 83 FC FF FF FF", "mov ax, [ebx-4]"),
                ("67 8B 05 20 01 00 00", "mov ax, [00000120]"),
                ("67 8B 45 00", "mov ax, [ebp]"),
                ("26 67 8B 03", "mov ax, es:[ebx]"),
                ("66 67 8B 04 8B", "mov eax, [ebx+ecx*4]"),
                ("67 67 8B 03", "mov ax, [ebx]")):
            with self.subTest(code=code):
                data = bytes.fromhex(code)
                self.assertEqual(dis(data), (len(data), text))

    def test_the_sib_byte(self):
        for code, text in (
                ("67 8B 04 0B", "mov ax, [ebx+ecx]"),
                ("67 8B 04 4B", "mov ax, [ebx+ecx*2]"),
                ("67 8B 04 8B", "mov ax, [ebx+ecx*4]"),
                ("67 8B 04 CB", "mov ax, [ebx+ecx*8]"),
                ("67 8B 04 23", "mov ax, [ebx]"),  # the index 4 is none
                ("67 8B 04 24", "mov ax, [esp]"),
                ("67 8B 04 8D 00 02 00 00", "mov ax, [ecx*4+00000200]"),  # the base 5 with mod 0 is a displacement
                ("67 8B 04 25 00 03 00 00", "mov ax, [00000300]"),
                ("67 8B 44 0D 04", "mov ax, [ebp+ecx+4]"),
                ("67 8B 84 8B 00 01 00 00", "mov ax, [ebx+ecx*4+100]")):
            with self.subTest(code=code):
                data = bytes.fromhex(code)
                self.assertEqual(dis(data), (len(data), text))

    def test_a_fixed_address_of_a_move(self):
        self.assertEqual(dis(bytes.fromhex("67 A1 02 00 01 00")), (6, "mov ax, [00010002]"))
        self.assertEqual(dis(bytes.fromhex("66 67 A3 40 00 00 00")), (7, "mov [00000040], eax"))
        self.assertEqual(dis(bytes.fromhex("67 A0 50 00 00 00")), (6, "mov al, [00000050]"))

    def test_in_a_code_segment_of_32_bits_the_prefixes_make_the_sizes_16(self):
        def dis32(text):
            cpu = machine(bytes.fromhex(text))
            cpu.code32 = True
            return disasm(cpu, 0x1000, 0x100)
        self.assertEqual(dis32("8B 03"), (2, "mov eax, [ebx]"))
        self.assertEqual(dis32("66 8B 03"), (3, "mov ax, [ebx]"))
        self.assertEqual(dis32("67 8B 07"), (3, "mov eax, [bx]"))
        self.assertEqual(dis32("66 67 8B 07"), (4, "mov ax, [bx]"))
        self.assertEqual(dis32("A1 02 00 00 00"), (5, "mov eax, [00000002]"))


class TestAgreesWithCpu32(unittest.TestCase):
    def test_every_opcode_that_has_a_32_bit_form_has_the_same_length_here(self):
        """If you teach the CPU a 32-bit instruction and forget disasm.py, this fails."""
        for op in CPU().ops32:
            with self.subTest(op=f"66 {op:02X}"):
                code = bytes([0x66, op, MODRM_FOR.get(op, 0xD8), 0, 0, 0, 0, 0, 0])
                cpu = machine(code)
                cpu.ip = 0x100
                fetched = []
                real_fetch8 = cpu.fetch8
                cpu.fetch8 = lambda: fetched.append(1) or real_fetch8()
                try:
                    cpu.step()
                except Exception:
                    pass
                length, text = disasm(machine(code), 0x1000, 0x100)
                self.assertFalse(text.startswith("db"), text)
                self.assertEqual(length, len(fetched) + 1, text)  # step() reads the first byte, the prefix, itself

    def test_an_opcode_with_the_prefix_that_the_cpu_does_not_know_is_a_db_of_the_prefix(self):
        self.assertEqual(dis(b"\x66\x0F\x84\x00\x00"), (1, "db 66"))

    def test_the_opcodes_that_the_cpu_takes_with_the_prefix_are_the_ones_that_are_written_with_it(self):
        """Both ways: nothing that the CPU stops at is written as an instruction, and nothing it takes as a db."""
        taken = set(CPU().ops32)
        for op in range(256):
            if op in (0x26, 0x2E, 0x36, 0x3E, 0x66, 0x67, 0xF2, 0xF3):  # the prefixes: another one is read after 66
                continue
            code = bytes([0x66, op, MODRM_FOR.get(op, 0xD8), 0, 0, 0, 0, 0, 0])
            length, text = disasm(machine(code), 0x1000, 0x100)
            self.assertEqual(not text.startswith("db"), op in taken, f"66 {op:02X}: {text}")


class TestAgreesWithCpuOn32BitAddresses(unittest.TestCase):
    def length_on_cpu(self, code):
        cpu = machine(code)
        cpu.ip = 0x100
        fetched = []
        real_fetch8 = cpu.fetch8
        cpu.fetch8 = lambda: fetched.append(1) or real_fetch8()
        try:
            cpu.step()
        except Exception:
            pass
        return len(fetched) + 1  # step() reads the first byte itself

    def test_every_opcode_has_the_same_length_with_a_sib_byte_and_a_32_bit_displacement(self):
        for prefix, ops in ((b"\x67", CPU().ops), (b"\x66\x67", CPU().ops32)):
            for op in ops:
                if op in ADDRESS_SIZED:
                    continue
                with self.subTest(op=f"{prefix.hex()} {op:02X}"):
                    modrm = (MODRM_FOR.get(op, 0xD8) & 0x38) | 0x84  # mod 10, r/m 100: a SIB byte and a 32-bit disp.
                    code = prefix + bytes([op, modrm, 0x88, 1, 0, 0, 0, 0, 0, 0, 0])
                    length, text = disasm(machine(code), 0x1000, 0x100)
                    self.assertFalse(text.startswith("db"), text)
                    self.assertEqual(length, self.length_on_cpu(code), text)


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
                # step() reads the opcode itself, and the rest of the instruction through fetch8
                self.assertEqual(length, len(fetched) + 1, text)


if __name__ == "__main__":
    unittest.main()
