"""32-bit programs in the flat mode (step 3a3): code, data and stack above the first megabyte, as a program of DOS/4G
has them. The programs were assembled with nasm (the source is above each of them); each is run to its INT 21h and what
it leaves is checked against what Python says it must be."""
import struct
import unittest

from dosloth.cpu import CPU, MEMORY_SIZE, Exit
from dosloth.disasm import disasm
from dosloth.flat import REAL_MODE, Bases

FLAT = 0x10  # the selector of the code, the data and the stack: base 0, so an offset is an address
STACK = 0x00900000

# nasm: bits 32, org 0x400000. A recursive factorial with a frame, as a compiler makes it.
#   start: push 10 / call fact / add esp,4 / mov ebx,eax / mov eax,4C00h / int 21h
#   fact:  push ebp / mov ebp,esp / mov eax,[ebp+8] / cmp eax,1 / jle one / dec eax / push eax / call fact /
#          add esp,4 / imul eax,[ebp+8] / jmp done / one: mov eax,1 / done: leave / ret
FACT = bytes.fromhex("6a0ae80c00000083c40489c3b8004c0000cd215589e58b450883f8017e104850e8eeffffff83c4040faf4508eb05b8"
                     "01000000c9c3")

# Copy 20003h bytes with rep movsd and rep movsb, find the length of a string with repne scasb, fill 1000 doublewords
# with rep stosd (source 500000h, destination 600000h, the string at 700000h, the fill at 710000h).
MEM = bytes.fromhex("be00005000bf00006000b90300020089cac1e902fcf3a589d183e103f3a4bf0000700031c0b9fffffffff2aef7d14989"
                    "cbbf00007100b841414141b9e8030000f3abb8004c0000cd21")

# A bubble sort of 60 doublewords at 500000h (signed), with a scaled index: [esi+edx*4], lea, loop.
SORT = bytes.fromhex("be00005000b93c00000031d28b04968b5c960439d87e07891c9689449604428d79ff39fa7ce6e2e2b8004c0000cd21")

# The digits of 4294967294 with div by 10 (written backwards ending at 500020h), and gcd(1071, 462) with cdq and idiv.
DIVS = bytes.fromhex("b8feffffffbf20005000b90a000000c6070031d2f7f180c2304f881785c075f289fbb82f040000bece01000085f67409"
                     "99f7fe89f089d6ebf389c1b8004c0000cd21")

# movsx, movzx (byte and word, from a bare address), bsr, bsf, bswap, shld, cmp and the four setcc, xchg with memory.
BITS = bytes.fromhex("0fbe05000050000fb61d000050000fbf0d000050000fb71500005000be0000f0000fbdfe0fbceebc78563412"
                     "0fcc0fa4e60839ef0f9405100050000f9f05110050000f9205120050000f970513005000c705200050000403"
                     "020187252000500066b8004ccd21")

# mov eax,12345678h / add eax,11111111h / mov ebx,eax / push ebx / pop ecx / mov eax,4C00h / int 21h
# It is run at 0FFFF0h, where it goes over the megabyte, and at 0FFFFFE0h, at the end of the memory.
TOP = bytes.fromhex("b878563412051111111189c35359b8004c0000cd21")

# A call to a function 12 MB away: caller (org 400000h): mov eax,14 / call 0FF0000h / mov ebx,eax / mov eax,4C00h /
# int 21h; callee (org 0FF0000h): lea eax,[eax+eax*2+1] / ret
CALLER = bytes.fromhex("b80e000000e8f6ffbe0089c3b8004c0000cd21")
CALLEE = bytes.fromhex("8d444001c3")


def exit_with_al(cpu):
    raise Exit(cpu.get8(0))


class FlatProgram(unittest.TestCase):
    def machine(self, *blobs, eip, esp=STACK):
        cpu = CPU()
        cpu.enter_flat(Bases({FLAT: 0}))
        cpu.s[:] = [FLAT] * 6
        for address, code in blobs:
            cpu.mem[address:address + len(code)] = code
        cpu.ip = eip
        cpu.set32(CPU.SP, esp)
        cpu.int_handlers[0x21] = exit_with_al
        return cpu

    def run_to_the_end(self, cpu, limit=300000):
        for _ in range(limit):
            try:
                cpu.step()
            except Exit as stop:
                return stop.code
        self.fail("the program did not end")

    def reg(self, cpu, name):
        return cpu.get32(["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"].index(name))

    def dword(self, cpu, address):
        return struct.unpack_from("<I", cpu.mem, address)[0]


class TestTheFlatModeIsEntered(unittest.TestCase):
    def test_entering_puts_everything_of_32_bits_in_place_and_leaving_takes_it_out(self):
        cpu = CPU()
        cpu.enter_flat(Bases({FLAT: 0}))
        self.assertEqual((cpu.flat, cpu.ip_mask, cpu.stack32, cpu.code32), (True, 0xFFFFFFFF, True, True))
        cpu.leave_flat()
        self.assertEqual(cpu.get_mode(), REAL_MODE)
        self.assertNotIn("rb", cpu.__dict__)


class TestProgramsAboveTheFirstMegabyte(FlatProgram):
    def test_a_recursive_function_with_a_frame(self):
        cpu = self.machine((0x400000, FACT), eip=0x400000)
        self.assertEqual(self.run_to_the_end(cpu), 0)
        self.assertEqual(self.reg(cpu, "ebx"), 3628800)  # 10!
        self.assertEqual(self.reg(cpu, "esp"), STACK)  # every push was taken back
        self.assertEqual(cpu.ip, 0x400000 + 19)  # after the int 21h, which is the last of the 19 bytes of start

    def test_a_block_copy_a_string_length_and_a_fill_across_64_kb_and_1_mb(self):
        cpu = self.machine((0x400000, MEM), eip=0x400000)
        source = bytes((i * 7 + i // 251) & 0xFF for i in range(0x20003))
        cpu.mem[0x500000:0x500000 + len(source)] = source
        cpu.mem[0x700000:0x700012] = b"hello, flat world\0"
        self.run_to_the_end(cpu)
        self.assertEqual(bytes(cpu.mem[0x600000:0x600000 + 0x20003]), source)
        self.assertEqual(cpu.mem[0x600000 + 0x20003], 0)  # nothing after the last byte
        self.assertEqual(self.reg(cpu, "ebx"), 17)
        self.assertEqual(bytes(cpu.mem[0x710000:0x710000 + 4000]), b"AAAA" * 1000)
        self.assertEqual((self.dword(cpu, 0x710000 + 4000), self.reg(cpu, "ecx")), (0, 0))
        self.assertEqual((self.reg(cpu, "esi"), self.reg(cpu, "edi")), (0x500000 + 0x20003, 0x710000 + 4000))

    def test_a_sort_with_scaled_indexes_gives_the_sorted_signed_numbers(self):
        numbers = [(i * 2654435761 + 12345) % (1 << 32) for i in range(60)]
        numbers[5], numbers[17], numbers[40] = 0x80000000, 0xFFFFFFFF, 0  # the extremes
        cpu = self.machine((0x400000, SORT), eip=0x400000)
        cpu.mem[0x500000:0x500000 + 240] = struct.pack("<60I", *numbers)
        self.run_to_the_end(cpu)
        got = struct.unpack_from("<60i", cpu.mem, 0x500000)
        self.assertEqual(list(got), sorted(n - (1 << 32) if n >> 31 else n for n in numbers))

    def test_division_by_10_gives_the_digits_and_idiv_with_cdq_gives_the_gcd(self):
        cpu = self.machine((0x400000, DIVS), eip=0x400000)
        self.run_to_the_end(cpu)
        start = self.reg(cpu, "ebx")
        end = bytes(cpu.mem[start:start + 20]).index(0)
        self.assertEqual(bytes(cpu.mem[start:start + end]), b"4294967294")
        self.assertEqual(start, 0x500020 - 10)
        self.assertEqual(self.reg(cpu, "ecx"), 21)  # gcd(1071, 462)

    def test_widening_bit_scans_a_byte_swap_a_double_shift_and_the_flags_of_a_comparison(self):
        cpu = self.machine((0x400000, BITS), eip=0x400000)
        cpu.mem[0x500000:0x500002] = bytes([0x80, 0xFF])
        self.run_to_the_end(cpu)
        self.assertEqual([self.reg(cpu, r) for r in ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp")],
                         [0xFFFF4C00, 0x80, 0xFFFFFF80, 0xFF80, 0xF0000078, 23, 20, 0x01020304])
        self.assertEqual(list(cpu.mem[0x500010:0x500014]), [0, 1, 0, 1])  # 23 against 20: ne, g, not b, a
        self.assertEqual(self.dword(cpu, 0x500020), 0x78563412)  # what ESP was after the bswap

    def test_code_that_goes_over_the_megabyte_is_read_on_and_does_not_wrap(self):
        cpu = self.machine((0xFFFF0, TOP), eip=0xFFFF0)
        self.run_to_the_end(cpu)
        self.assertEqual((self.reg(cpu, "ebx"), self.reg(cpu, "ecx")), (0x23456789, 0x23456789))
        self.assertEqual(cpu.ip, 0xFFFF0 + len(TOP))  # 100005h
        self.assertEqual(cpu.mem[0], 0)  # and the first bytes of the memory are what they were

    def test_code_and_a_stack_at_the_very_end_of_the_memory(self):
        cpu = self.machine((MEMORY_SIZE - 32, TOP), eip=MEMORY_SIZE - 32, esp=MEMORY_SIZE)
        self.run_to_the_end(cpu)
        self.assertEqual((self.reg(cpu, "ecx"), self.reg(cpu, "esp"), cpu.ip), (0x23456789, MEMORY_SIZE,
                                                                               MEMORY_SIZE - 32 + len(TOP)))

    def test_a_call_to_a_function_12_mb_away_and_back(self):
        cpu = self.machine((0x400000, CALLER), (0xFF0000, CALLEE), eip=0x400000)
        self.run_to_the_end(cpu)
        self.assertEqual((self.reg(cpu, "ebx"), self.reg(cpu, "esp")), (14 * 3 + 1, STACK))

    def test_the_stack_pointer_above_64_kb_is_esp_and_not_sp(self):
        cpu = self.machine((0x400000, FACT), eip=0x400000, esp=0x00123456)
        self.run_to_the_end(cpu)
        self.assertEqual((self.reg(cpu, "ebx"), self.reg(cpu, "esp")), (3628800, 0x00123456))

    def test_the_same_program_run_with_the_instruction_trace_is_the_same_program(self):
        cpu = self.machine((0x400000, FACT), eip=0x400000)
        seen = []
        cpu.trace = lambda c: seen.append(c.ip)
        self.run_to_the_end(cpu)
        self.assertEqual(seen[0], 0x400000)
        self.assertEqual(len(seen), cpu.instructions)
        self.assertGreater(len(seen), 50)  # 10 levels of recursion


class TestTheDisassemblerReadsThem(FlatProgram):
    def test_every_instruction_of_every_program_is_known_and_the_lengths_add_up(self):
        programs = {"fact": (0x400000, FACT), "mem": (0x400000, MEM), "sort": (0x400000, SORT),
                    "divs": (0x400000, DIVS), "bits": (0x400000, BITS), "top": (0xFFFF0, TOP),
                    "caller": (0x400000, CALLER), "callee": (0xFF0000, CALLEE)}
        for name, (address, code) in programs.items():
            cpu = self.machine((address, code), eip=address)
            at, texts = address, []
            while at < address + len(code):
                length, text = disasm(cpu, FLAT, at)
                self.assertFalse(text.startswith("db"), f"{name} at {at:X}: {text}")
                texts.append(text)
                at += length
            self.assertEqual(at, address + len(code), name)

    def test_a_few_of_the_texts_with_8_digits_for_the_targets(self):
        cpu = self.machine((0x400000, FACT), eip=0x400000)
        texts = []
        at = 0x400000
        for _ in range(8):
            length, text = disasm(cpu, FLAT, at)
            texts.append(text)
            at += length
        self.assertEqual(texts[:3], ["push A", "call 00400013", "add esp, 4"])
        self.assertEqual(texts[3], "mov ebx, eax")

    def test_the_text_of_a_scaled_index_and_of_a_bare_address(self):
        cpu = self.machine((0x400000, SORT), eip=0x400000)
        at, texts = 0x400000, []
        for _ in range(8):
            length, text = disasm(cpu, FLAT, at)
            texts.append(text)
            at += length
        self.assertIn("mov eax, [esi+edx*4]", texts)
        self.assertIn("mov ebx, [esi+edx*4+4]", texts)
        cpu = self.machine((0x400000, BITS), eip=0x400000)
        self.assertEqual(disasm(cpu, FLAT, 0x400000), (7, "movsx eax, byte [00500000]"))


if __name__ == "__main__":
    unittest.main()
