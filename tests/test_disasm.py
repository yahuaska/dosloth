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


class TestInstructions32(unittest.TestCase):
    """The text of the 32-bit forms of step 2b, and of the 186 instructions that came with them."""

    def check(self, cases):
        for code, text in cases:
            with self.subTest(code=code):
                data = bytes.fromhex(code)
                self.assertEqual(dis(data), (len(data), text))

    def test_shifts(self):
        self.check((("66 D1 E3", "shl ebx, 1"), ("66 D3 EB", "shr ebx, cl"), ("66 C1 E3 04", "shl ebx, 04"),
                    ("66 C1 26 00 01 04", "shl dword [0100], 04"), ("C0 EB 03", "shr bl, 03"),
                    ("C1 E3 04", "shl bx, 04"), ("C1 3E 00 01 02", "sar word [0100], 02"), ("66 D1 C3", "rol ebx, 1")))

    def test_group_3(self):
        self.check((("66 F7 C3 78 56 34 12", "test ebx, 12345678"), ("66 F7 D3", "not ebx"), ("66 F7 DB", "neg ebx"),
                    ("66 F7 E3", "mul ebx"), ("66 F7 EB", "imul ebx"), ("66 F7 F3", "div ebx"),
                    ("66 F7 FB", "idiv ebx"), ("66 F7 36 00 01", "div dword [0100]"), ("F7 C3 34 12", "test bx, 1234"),
                    ("F6 C3 80", "test bl, 80")))

    def test_imul_with_a_constant(self):
        self.check((("66 6B CB FE", "imul ecx, ebx, -2"), ("66 69 CB 78 56 34 12", "imul ecx, ebx, 12345678"),
                    ("6B CB FE", "imul cx, bx, -2"), ("69 CB 00 10", "imul cx, bx, 1000"),
                    ("66 6B 0E 00 01 0A", "imul ecx, [0100], A")))

    def test_group_5_and_pop(self):
        self.check((("66 FF C3", "inc ebx"), ("66 FF 06 00 01", "inc dword [0100]"), ("66 FF D3", "call ebx"),
                    ("66 FF E3", "jmp ebx"), ("66 FF F3", "push ebx"), ("66 FF 36 00 01", "push dword [0100]"),
                    ("66 8F C3", "pop ebx"), ("66 8F 06 00 01", "pop dword [0100]"),
                    ("FF 36 00 01", "push word [0100]")))

    def test_group_5_far_forms_have_no_32_bit_text(self):
        self.assertEqual(dis(bytes.fromhex("66 FF 1E 00 01")), (1, "db 66"))
        self.assertEqual(dis(bytes.fromhex("66 8F C8")), (1, "db 66"))

    def test_the_stack(self):
        self.check((("66 68 78 56 34 12", "push 12345678"), ("66 6A FE", "push -2"), ("66 60", "pushad"),
                    ("66 61", "popad"), ("66 C8 0C 00 01", "enter 000C, 01"), ("66 C9", "leave"), ("66 C3", "ret"),
                    ("66 C2 08 00", "ret 0008")))

    def test_near_jumps_and_calls(self):
        self.check((("66 E8 00 01 00 00", "call 0206"), ("66 E9 20 00 00 00", "jmp 0126"),
                    ("66 E8 00 FF FF FF", "call 0006"),  # the target is cut to 16 bits, as the CPU does
                    ("66 E9 00 00 01 00", "jmp 0106")))

    def test_string_instructions(self):
        self.check((("66 A5", "movsd"), ("66 A7", "cmpsd"), ("66 AB", "stosd"), ("66 AD", "lodsd"), ("66 AF", "scasd"),
                    ("66 F3 A5", "rep movsd"), ("66 F3 A7", "repe cmpsd"), ("66 F2 AF", "repne scasd"),
                    ("66 A4", "movsb"), ("67 A4", "movsb"), ("A5", "movsw")))

    def test_loops_and_xlat(self):
        self.check((("67 E2 FD", "loop 0100"), ("67 E3 10", "jcxz 0113"), ("D7", "xlat"), ("67 D7", "xlat"),
                    ("66 E2 FE", "loop 0101")))

    def test_in_a_code_segment_of_32_bits(self):
        def dis32(text):
            cpu = machine(bytes.fromhex(text))
            cpu.code32 = True
            return disasm(cpu, 0x1000, 0x100)
        self.assertEqual(dis32("C1 E3 04"), (3, "shl ebx, 04"))
        self.assertEqual(dis32("66 C1 E3 04"), (4, "shl bx, 04"))
        self.assertEqual(dis32("F7 35 00 01 00 00"), (6, "div dword [00000100]"))
        self.assertEqual(dis32("E8 00 01 00 00"), (5, "call 0205"))
        self.assertEqual(dis32("F3 A5"), (2, "rep movsd"))


class TestTwoByteInstructions(unittest.TestCase):
    """The instructions that start with 0F."""

    def check(self, cases):
        for code, text in cases:
            with self.subTest(code=code):
                data = bytes.fromhex(code)
                self.assertEqual(dis(data), (len(data), text))

    def test_the_long_jumps(self):
        self.check((("0F 84 10 00", "je 0114"), ("66 0F 85 10 00 00 00", "jne 0117"), ("0F 8F F0 FF", "jg 00F4"),
                    ("0F 80 00 00", "jo 0104")))

    def test_setcc(self):
        self.check((("0F 94 C3", "sete bl"), ("0F 9F 06 00 01", "setg byte [0100]"), ("66 0F 92 C0", "setb al"),
                    ("0F 90 C0", "seto al"), ("0F 9B C0", "setnp al")))

    def test_movzx_and_movsx(self):
        self.check((("66 0F B6 C3", "movzx eax, bl"), ("66 0F B7 C3", "movzx eax, bx"), ("0F B6 C3", "movzx ax, bl"),
                    ("66 0F BE 06 00 01", "movsx eax, byte [0100]"), ("66 0F BF 06 00 01", "movsx eax, word [0100]"),
                    ("66 0F B6 CF", "movzx ecx, bh")))

    def test_imul(self):
        self.check((("66 0F AF CB", "imul ecx, ebx"), ("0F AF CB", "imul cx, bx"),
                    ("66 0F AF 0E 00 01", "imul ecx, [0100]")))

    def test_the_double_shifts(self):
        self.check((("66 0F A4 D3 04", "shld ebx, edx, 04"), ("66 0F A5 D3", "shld ebx, edx, cl"),
                    ("0F AC D3 08", "shrd bx, dx, 08"), ("66 0F AD D3", "shrd ebx, edx, cl"),
                    ("66 0F A4 0E 00 01 04", "shld [0100], ecx, 04")))

    def test_the_bit_instructions(self):
        self.check((("66 0F A3 CB", "bt ebx, ecx"), ("66 0F AB CB", "bts ebx, ecx"), ("0F B3 CB", "btr bx, cx"),
                    ("66 0F BB 0E 00 01", "btc [0100], ecx"), ("66 0F BA E3 05", "bt ebx, 05"),
                    ("66 0F BA 2E 00 01 1F", "bts dword [0100], 1F"), ("0F BA F3 03", "btr bx, 03"),
                    ("0F BA 3E 00 01 02", "btc word [0100], 02")))

    def test_bit_scans_and_bswap(self):
        self.check((("66 0F BC CB", "bsf ecx, ebx"), ("0F BD CB", "bsr cx, bx"),
                    ("66 0F BD 0E 00 01", "bsr ecx, [0100]"),
                    ("0F C8", "bswap eax"), ("0F CF", "bswap edi")))

    def test_a_second_byte_that_is_not_done_is_a_db_of_the_first(self):
        for code in ("0F A2", "0F 31", "0F 00 C0", "0F BA C3 01", "0F BA DB 01", "66 0F A2"):
            data = bytes.fromhex(code)
            self.assertEqual(dis(data), (1, f"db {data[0]:02X}"), code)

    def test_in_a_code_segment_of_32_bits(self):
        cpu = machine(bytes.fromhex("0F B6 C3"))
        cpu.code32 = True
        self.assertEqual(disasm(cpu, 0x1000, 0x100), (3, "movzx eax, bl"))
        cpu = machine(bytes.fromhex("0F 84 10 00 00 00"))
        cpu.code32 = True
        self.assertEqual(disasm(cpu, 0x1000, 0x100), (6, "je 0116"))


class TestSegmentRegisters(unittest.TestCase):
    def check(self, cases):
        for code, text in cases:
            with self.subTest(code=code):
                data = bytes.fromhex(code)
                self.assertEqual(dis(data), (len(data), text))

    def test_the_prefixes_and_the_moves(self):
        self.check((("64 8B 07", "mov ax, fs:[bx]"), ("65 8B 07", "mov ax, gs:[bx]"),
                    ("64 A1 00 01", "mov ax, fs:[0100]"),
                    ("66 67 64 8B 04 8B", "mov eax, fs:[ebx+ecx*4]"), ("8E E0", "mov fs, ax"), ("8E E8", "mov gs, ax"),
                    ("8C E0", "mov ax, fs"), ("8C 2E 00 01", "mov [0100], gs"), ("66 8C E0", "mov eax, fs"),
                    ("66 8C 1E 00 01", "mov [0100], ds"), ("66 8E E0", "mov fs, ax"), ("8C D8", "mov ax, ds")))

    def test_a_segment_register_that_is_not_one_is_a_db(self):
        for code in ("8C F0", "8E F0", "8E C8", "66 8C F0"):  # 6 and 7 are nothing, and CS cannot be loaded
            data = bytes.fromhex(code)
            self.assertEqual(dis(data), (1, f"db {data[0]:02X}"), code)

    def test_push_and_pop(self):
        self.check((("0F A0", "push fs"), ("0F A1", "pop fs"), ("0F A8", "push gs"), ("0F A9", "pop gs"),
                    ("66 0F A0", "push fs"), ("66 06", "push es"), ("66 1F", "pop ds"), ("66 0E", "push cs")))

    def test_far_pointers(self):
        self.check((("0F B4 0E 00 01", "lfs cx, [0100]"), ("0F B5 0E 00 01", "lgs cx, [0100]"),
                    ("0F B2 0E 00 01", "lss cx, [0100]"), ("66 0F B4 0E 00 01", "lfs ecx, [0100]"),
                    ("66 C4 0E 00 01", "les ecx, [0100]"), ("66 C5 0E 00 01", "lds ecx, [0100]"),
                    ("C4 0E 00 01", "les cx, [0100]")))

    def test_a_far_pointer_in_a_register_is_a_db(self):
        for code in ("0F B4 C3", "66 C5 C3"):
            data = bytes.fromhex(code)
            self.assertEqual(dis(data), (1, f"db {data[0]:02X}"), code)


class TestTwoByteAgreesWithCpu(unittest.TestCase):
    def test_every_second_byte_is_known_to_both_or_to_neither_and_the_lengths_are_the_same(self):
        for prefix in (b"", b"\x66", b"\x67"):
            for second in range(256):
                code = prefix + bytes([0x0F, second, 0xE0, 1, 0, 0, 0, 0, 0, 0])  # 0xE0: a register, 'reg' 4
                cpu = machine(code)
                cpu.ip = 0x100
                fetched = []
                real_fetch8 = cpu.fetch8
                cpu.fetch8 = lambda: fetched.append(1) or real_fetch8()
                done = True
                try:
                    cpu.step()
                except NotImplementedError:
                    done = False
                except Exception:
                    pass
                length, text = disasm(machine(code), 0x1000, 0x100)
                label = f"{prefix.hex()} 0F {second:02X}: {text}"
                self.assertEqual(not text.startswith("db"), done, label)
                if done:
                    self.assertEqual(length, len(fetched) + 1, label)


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
        self.assertEqual(dis(b"\x66\xCB"), (1, "db 66"))  # retf with a pointer of 16:32: no form yet

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
