"""The flat memory (step 3a2): selectors and their bases, the reads and writes, the fetch of the program, and the same
tests as for the instructions of the 386 run on it (with the base of a selector being the selector times 16, as in
real mode, so that every one of them must give the same answer)."""
import random
import struct
import unittest

from mydos import flags
from mydos.cpu import CPU, MEMORY_SIZE, Exit
from mydos.flat import Bases, RealModeBases
from tests import test_cpu386, test_cpu386_0f, test_cpu386_ops, test_cpu386_segments, test_cpu386_widths

# Every test of a class made of test_cpu386.Machine, once more with FLAT set: the classes below are those, as
# TestSomethingFlat, found by unittest in this module.
for module in (test_cpu386, test_cpu386_ops, test_cpu386_0f, test_cpu386_segments, test_cpu386_widths):
    for name, cls in list(vars(module).items()):
        if isinstance(cls, type) and issubclass(cls, test_cpu386.Machine) and cls is not test_cpu386.Machine:
            globals()[name + "Flat"] = type(name + "Flat", (cls,), {"FLAT": True, "__module__": __name__})


def flat_cpu(bases=None):
    cpu = CPU()
    cpu.use_flat_memory(Bases({0x10: 0, 0x18: 0x30000, 0x20: 0x1000}) if bases is None else bases)
    return cpu


class DescriptorQuestions(unittest.TestCase):
    """LAR and LSL: what a 32-bit program asks of a selector it has, to make another like it."""

    def lar(self, selector, code=b"\x0F\x02\xC9", ecx=None):
        """Run `lar ecx, ecx` (or the code given) with the selector in CX, with 32-bit code."""
        cpu = flat_cpu()
        cpu.enter_flat(Bases({0x10: 0, 0x18: 0x30000, 0x20: 0x1000}))
        cpu.s[CPU.CS] = 0x10
        cpu.mem[0:len(code)] = code
        cpu.ip = 0
        cpu.set32(CPU.CX, selector if ecx is None else ecx)
        cpu.step()
        return cpu


class TestLoadAccessRights(DescriptorQuestions):
    def test_a_data_selector_has_the_rights_of_a_32_bit_data_segment_and_zf_says_so(self):
        cpu = self.lar(0x18)
        self.assertEqual(cpu.get32(CPU.CX), 0x00C09200)
        self.assertTrue(cpu.get_flag(flags.ZF))

    def test_the_code_selector_is_a_readable_code_segment(self):
        self.assertEqual(self.lar(0x10).get32(CPU.CX), 0x00C09A00)

    def test_a_selector_that_is_not_there_clears_zf_and_leaves_the_register(self):
        cpu = self.lar(0x47, ecx=0x12340047)
        self.assertEqual(cpu.get32(CPU.CX), 0x12340047)
        self.assertFalse(cpu.get_flag(flags.ZF))

    def test_the_selector_is_the_word_of_the_operand_and_not_all_of_ecx(self):
        self.assertEqual(self.lar(0x18, ecx=0xABCD0018).get32(CPU.CX), 0x00C09200)

    def test_the_form_with_a_16_bit_register_changes_only_that_register(self):
        cpu = self.lar(0x18, code=b"\x66\x0F\x02\xC9", ecx=0xABCD0018)
        self.assertEqual(cpu.get32(CPU.CX), 0xABCD9200)

    def test_in_the_real_mode_it_stops_the_emulator(self):
        cpu = CPU()
        cpu.mem[0:3] = b"\x0F\x02\xC9"
        with self.assertRaisesRegex(NotImplementedError, "real mode"):
            cpu.step()

    def test_the_events_of_the_devices_are_looked_at_in_the_flat_mode_too(self):
        cpu = self.lar(0x18)
        seen = []
        cpu.event_sources.append(lambda now: seen.append(now))
        cpu.wake()
        cpu.ip = 0
        cpu.step()
        self.assertEqual(len(seen), 1)


class TestLoadSegmentLimit(DescriptorQuestions):
    """LSL (0F 03): the limit, which is all of the address space for every selector there is."""

    def test_a_selector_that_is_there_gives_the_whole_address_space_and_zf(self):
        cpu = self.lar(0x18, code=b"\x0F\x03\xC9")
        self.assertEqual(cpu.get32(CPU.CX), 0xFFFFFFFF)
        self.assertTrue(cpu.get_flag(flags.ZF))

    def test_a_selector_that_is_not_there_clears_zf_and_leaves_the_register(self):
        cpu = self.lar(0x47, code=b"\x0F\x03\xC9", ecx=0x12340047)
        self.assertEqual((cpu.get32(CPU.CX), cpu.get_flag(flags.ZF)), (0x12340047, False))

    def test_the_form_with_a_16_bit_register_changes_only_that_register(self):
        cpu = self.lar(0x18, code=b"\x66\x0F\x03\xC9", ecx=0xABCD0018)
        self.assertEqual(cpu.get32(CPU.CX), 0xABCDFFFF)

    def test_in_the_real_mode_it_stops_the_emulator(self):
        cpu = CPU()
        cpu.mem[0:3] = b"\x0F\x03\xC9"
        with self.assertRaisesRegex(NotImplementedError, "real mode"):
            cpu.step()


class TestTheTableOfSelectors(unittest.TestCase):
    def test_the_base_of_a_selector_is_what_the_table_says(self):
        cpu = flat_cpu()
        cpu.wb(0x18, 5, 0xAB)
        self.assertEqual((cpu.mem[0x30005], cpu.rb(0x18, 5), cpu.rb(0x10, 0x30005)), (0xAB, 0xAB, 0xAB))
        self.assertEqual(cpu.addr(0x18, 5), 0x30005)
        self.assertEqual((cpu.addr(0x10, 0x00400000), cpu.addr(0x18, 0xFFFFFFFF)), (0x00400000, 0x2FFFF))  # 32 bits

    def test_a_selector_that_is_not_in_the_table_stops_the_emulator_and_says_which(self):
        cpu = flat_cpu()
        for use in (lambda: cpu.rb(0x47, 0), lambda: cpu.wb(0x47, 0, 1), lambda: cpu.rw(0x47, 0),
                    lambda: cpu.rd(0x47, 0), lambda: cpu.addr(0x47, 0)):
            with self.assertRaisesRegex(NotImplementedError, "selector 0047 is not in the table"):
                use()

    def test_real_mode_bases_make_a_selector_a_paragraph(self):
        self.assertEqual(RealModeBases()[0x1234], 0x12340)

    def test_the_default_machine_is_not_flat(self):
        cpu = CPU()
        self.assertEqual((cpu.flat, cpu.bases), (False, None))
        cpu.use_flat_memory(Bases({1: 0}))
        self.assertTrue(cpu.flat)
        cpu.use_real_memory()
        self.assertFalse(cpu.flat)


class TestReadsAndWrites(unittest.TestCase):
    def test_a_word_and_a_doubleword_are_little_endian_and_agree_with_the_bytes(self):
        cpu = flat_cpu()
        cpu.wd(0x10, 0x100, 0xDEADBEEF)
        self.assertEqual([cpu.rb(0x10, 0x100 + i) for i in range(4)], [0xEF, 0xBE, 0xAD, 0xDE])
        self.assertEqual((cpu.rw(0x10, 0x100), cpu.rw(0x10, 0x102), cpu.rd(0x10, 0x100)), (0xBEEF, 0xDEAD, 0xDEADBEEF))
        cpu.ww(0x10, 0x200, 0x1234)
        self.assertEqual((cpu.rb(0x10, 0x200), cpu.rb(0x10, 0x201)), (0x34, 0x12))

    def test_the_extra_bits_of_a_value_are_dropped(self):
        cpu = flat_cpu()
        cpu.wb(0x10, 0x100, 0x1FF)
        cpu.ww(0x10, 0x110, 0x12345)
        cpu.wd(0x10, 0x120, 0x123456789)
        self.assertEqual((cpu.rb(0x10, 0x100), cpu.rb(0x10, 0x101), cpu.rw(0x10, 0x110), cpu.rb(0x10, 0x112),
                          cpu.rd(0x10, 0x120), cpu.rb(0x10, 0x124)), (0xFF, 0, 0x2345, 0, 0x23456789, 0))

    def test_the_offset_is_32_bits_and_wraps_there(self):
        cpu = flat_cpu(Bases({0x10: 0x100}))
        cpu.wb(0x10, 0xFFFFFFFF, 0x77)  # 100h - 1 + 100h... the sum wraps at 4 GB
        self.assertEqual(cpu.mem[0xFF], 0x77)
        cpu.wd(0x10, 0x00400000, 0x11223344)  # 4 MB further: above the first megabyte, which real mode cannot reach
        self.assertEqual((cpu.mem[0x400100], cpu.rd(0x10, 0x00400000)), (0x44, 0x11223344))

    def test_memory_above_one_megabyte_is_there_to_use(self):
        cpu = flat_cpu()
        cpu.wd(0x10, 0x00F00000, 0xCAFEBABE)
        cpu.wd(0x10, MEMORY_SIZE - 4, 0xFEEDFACE)
        self.assertEqual((cpu.rd(0x10, 0x00F00000), cpu.rd(0x10, MEMORY_SIZE - 4)), (0xCAFEBABE, 0xFEEDFACE))

    def test_an_address_beyond_the_memory_stops_the_emulator(self):
        cpu = flat_cpu()
        with self.assertRaises(IndexError):
            cpu.rb(0x10, MEMORY_SIZE)
        with self.assertRaises(IndexError):
            cpu.wb(0x10, MEMORY_SIZE, 1)
        for use in (lambda: cpu.rw(0x10, MEMORY_SIZE - 1), lambda: cpu.ww(0x10, MEMORY_SIZE - 1, 1),
                    lambda: cpu.rd(0x10, MEMORY_SIZE - 3), lambda: cpu.wd(0x10, MEMORY_SIZE - 3, 1),
                    lambda: cpu.rd(0x10, MEMORY_SIZE)):
            with self.assertRaises(struct.error):
                use()

    def test_what_the_real_mode_methods_see_is_the_same_memory(self):
        cpu = flat_cpu()
        cpu.wd(0x18, 0x10, 0x01020304)  # linear 30010h
        cpu.use_real_memory()
        self.assertEqual(cpu.rd(0x3000, 0x10), 0x01020304)


class FakeCard:
    """A video card with a window: the reads and writes in it go to the card and not to the memory."""

    def __init__(self):
        self.cells = {}

    def read_memory(self, offset):
        return self.cells.get(offset, 0x55)

    def write_memory(self, offset, value):
        self.cells[offset] = value


class TestTheWindowOfTheVideoCard(unittest.TestCase):
    def machine(self):
        cpu = flat_cpu()
        cpu.video = FakeCard()
        cpu.video_start, cpu.video_end = 0xA0000, 0xB0000
        return cpu

    def test_a_byte_a_word_and_a_doubleword_in_the_window_go_to_the_card(self):
        cpu = self.machine()
        cpu.wb(0x10, 0xA0010, 1)
        cpu.ww(0x10, 0xA0020, 0x0302)
        cpu.wd(0x10, 0xA0030, 0x07060504)
        self.assertEqual(cpu.video.cells, {0x10: 1, 0x20: 2, 0x21: 3, 0x30: 4, 0x31: 5, 0x32: 6, 0x33: 7})
        self.assertEqual(cpu.mem[0xA0010], 0)
        cpu.video.cells = {0x40: 0x11, 0x41: 0x22, 0x42: 0x33, 0x43: 0x44}
        self.assertEqual((cpu.rb(0x10, 0xA0040), cpu.rw(0x10, 0xA0040), cpu.rd(0x10, 0xA0040)),
                         (0x11, 0x2211, 0x44332211))

    def test_an_access_that_goes_over_an_edge_of_the_window_is_split_between_the_card_and_the_memory(self):
        cpu = self.machine()
        cpu.mem[0x9FFFE:0xA0000] = bytes([0xAA, 0xBB])
        cpu.video.cells = {0: 0xCC, 1: 0xDD}
        self.assertEqual(cpu.rd(0x10, 0x9FFFE), 0xDDCCBBAA)  # two bytes of memory and two of the card
        cpu.wd(0x10, 0x9FFFE, 0x44332211)
        self.assertEqual((bytes(cpu.mem[0x9FFFE:0xA0000]), cpu.video.cells[0], cpu.video.cells[1]),
                         (bytes([0x11, 0x22]), 0x33, 0x44))
        cpu.mem[0xAFFFF] = 0x66
        cpu.mem[0xB0000] = 0x77
        cpu.video.cells[0xFFFF] = 0x88
        self.assertEqual(cpu.rw(0x10, 0xAFFFF), 0x7788)  # the last byte of the card and the first one after it
        self.assertEqual(cpu.rw(0x10, 0x9FFFF), 0x3322)  # the memory byte 9FFFF (22h), then the card byte 0 (33h)

    def test_a_doubleword_that_has_only_its_last_byte_in_the_window_is_split_too(self):
        cpu = self.machine()
        cpu.mem[0x9FFFD:0xA0000] = bytes([1, 2, 3])
        cpu.video.cells = {0: 4}
        self.assertEqual(cpu.rd(0x10, 0x9FFFD), 0x04030201)
        cpu.wd(0x10, 0x9FFFD, 0x08070605)
        self.assertEqual((bytes(cpu.mem[0x9FFFD:0xA0000]), cpu.video.cells[0]), (bytes([5, 6, 7]), 8))
        cpu.video.cells = {0xFFFF: 0x0A}  # and the first byte only of a word or a doubleword, at the other end
        cpu.mem[0xB0000:0xB0003] = bytes([0x0B, 0x0C, 0x0D])
        self.assertEqual(cpu.rd(0x10, 0xAFFFF), 0x0D0C0B0A)

    def test_memory_outside_the_window_is_memory(self):
        cpu = self.machine()
        cpu.wd(0x10, 0xB0000, 0x01020304)
        cpu.wd(0x10, 0x9FFFC, 0x05060708)
        self.assertEqual((cpu.rd(0x10, 0xB0000), cpu.rd(0x10, 0x9FFFC), cpu.video.cells), (0x01020304, 0x05060708, {}))


class TestTheProgram(unittest.TestCase):
    def machine(self, code, at=0, ip_mask=0xFFFFFFFF):
        cpu = flat_cpu()
        cpu.s[CPU.CS] = 0x18  # base 30000h
        cpu.mem[0x30000 + at:0x30000 + at + len(code)] = code
        cpu.ip = at
        cpu.ip_mask = ip_mask
        return cpu

    def test_fetch_reads_at_the_base_of_cs_and_moves_eip(self):
        cpu = self.machine(bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]))
        self.assertEqual((cpu.fetch8(), cpu.ip), (1, 1))
        self.assertEqual((cpu.fetch16(), cpu.ip), (0x0302, 3))
        self.assertEqual((cpu.fetch32(), cpu.ip), (0x07060504, 7))
        self.assertEqual((cpu.fetch8(), cpu.fetch16(), cpu.ip), (8, 0x0A09, 10))

    def test_eip_goes_over_64_kb_and_wraps_at_32_bits_with_a_wide_mask(self):
        cpu = self.machine(bytes([0xAA, 0xBB, 0xCC, 0xDD]), at=0x0001FFFE)
        self.assertEqual((cpu.fetch16(), cpu.ip), (0xBBAA, 0x00020000))
        cpu.mem[0x30000 + 0x00020000] = 0x42
        self.assertEqual((cpu.fetch8(), cpu.ip), (0x42, 0x00020001))
        cpu = self.machine(b"", at=0xFFFFFFFF)
        cpu.mem[0x30000 - 1] = 0x99
        self.assertEqual((cpu.fetch8(), cpu.ip), (0x99, 0))

    def test_the_mask_of_eip_is_the_mask_of_the_machine(self):
        cpu = self.machine(bytes([1, 2, 3, 4, 5, 6]), at=0xFFFE, ip_mask=0xFFFF)
        cpu.mem[0x30000 + 0x10000] = 0x77
        self.assertEqual((cpu.fetch16(), cpu.ip), (0x0201, 0x0000))
        cpu.ip = 0xFFFF
        self.assertEqual((cpu.fetch8(), cpu.ip), (cpu.mem[0x3FFFF], 0))  # EIP FFFFh wraps to 0 with the mask of 16 bits
        cpu.ip = 0xFFFC
        cpu.mem[0x3FFFC:0x40000] = bytes([9, 8, 7, 6])
        self.assertEqual((cpu.fetch32(), cpu.ip), (0x06070809, 0))

    def test_a_fetch_in_the_window_of_the_video_card_comes_from_the_card(self):
        cpu = self.machine(b"")
        cpu.video = FakeCard()
        cpu.video_start, cpu.video_end = 0xA0000, 0xB0000
        cpu.video.cells = {0: 0x11, 1: 0x22, 2: 0x33, 3: 0x44}
        cpu.s[CPU.CS] = 0x10  # base 0
        cpu.ip = 0xA0000
        self.assertEqual((cpu.fetch8(), cpu.fetch16(), cpu.fetch8()), (0x11, 0x3322, 0x44))
        cpu.ip = 0xA0000
        self.assertEqual(cpu.fetch32(), 0x44332211)

    def test_the_last_byte_of_a_word_or_a_doubleword_of_the_program_may_be_the_first_of_the_card(self):
        cpu = self.machine(b"")
        cpu.video = FakeCard()
        cpu.video_start, cpu.video_end = 0xA0000, 0xB0000
        cpu.s[CPU.CS] = 0x10  # base 0
        cpu.mem[0x9FFFF] = 0x11
        cpu.video.cells = {0: 0x22}
        cpu.ip = 0x9FFFF
        self.assertEqual((cpu.fetch16(), cpu.ip), (0x2211, 0xA0001))
        cpu.mem[0x9FFFD:0xA0000] = bytes([1, 2, 3])
        cpu.ip = 0x9FFFD
        self.assertEqual((cpu.fetch32(), cpu.ip), (0x22030201, 0xA0001))

    def test_eip_goes_over_64_kb_in_a_fetch_of_a_doubleword_with_a_wide_mask(self):
        cpu = self.machine(bytes([1, 2, 3, 4]), at=0x0001FFFC)
        self.assertEqual((cpu.fetch32(), cpu.ip), (0x04030201, 0x00020000))

    def test_step_takes_the_opcode_from_the_card_when_the_program_is_in_its_window(self):
        cpu = self.machine(b"")
        cpu.video = FakeCard()
        cpu.video_start, cpu.video_end = 0xA0000, 0xB0000
        cpu.s[CPU.CS] = 0x10
        cpu.video.cells = {0: 0x90}  # nop
        cpu.mem[0xA0000] = 0x06  # in memory it would be push es
        cpu.ip = 0xA0000
        sp = cpu.r[CPU.SP]
        cpu.step()
        self.assertEqual((cpu.ip, cpu.r[CPU.SP]), (0xA0001, sp))

    def test_step_runs_an_instruction_at_the_base_of_cs(self):
        cpu = self.machine(bytes([0x66, 0xB8]) + struct.pack("<I", 0x12345678))  # mov eax, 12345678h
        cpu.step()
        self.assertEqual((cpu.get32(CPU.AX), cpu.ip, cpu.start_ip, cpu.instructions), (0x12345678, 6, 0, 1))

    def test_step_takes_the_prefixes_and_says_what_is_not_there_with_the_selector(self):
        cpu = self.machine(bytes([0x26, 0x66, 0x0F, 0xA2]), at=4)  # es: 66 0F A2 (cpuid)
        cpu.mem[0x30004:0x30008] = bytes([0x26, 0x66, 0x0F, 0xA2])
        with self.assertRaisesRegex(NotImplementedError, "opcode 0F A2 at 0018:0004: 26 66 0f a2"):
            cpu.step()
        cpu = self.machine(bytes([0x66, 0x9A]))
        with self.assertRaisesRegex(NotImplementedError, "opcode 66 9A, the 32-bit form, at 0018:0000"):
            cpu.step()
        cpu = self.machine(bytes([0x67, 0x6C]))
        with self.assertRaisesRegex(NotImplementedError, "opcode 6C with 32-bit addresses at 0018:0000"):
            cpu.step()

    def test_the_trace_and_the_count_are_kept(self):
        cpu = self.machine(bytes([0x90, 0x90]))
        seen = []
        cpu.trace = lambda c: seen.append((c.ip, c.instructions))
        cpu.step()
        cpu.step()
        self.assertEqual((seen, cpu.instructions), ([(0, 1), (1, 2)], 2))  # counted first, as in real mode

    def test_run_uses_the_flat_step(self):
        cpu = self.machine(bytes([0xB8, 0x00, 0x4C, 0xCD, 0x21]))  # mov ax, 4C00h ; int 21h
        cpu.int_handlers[0x21] = lambda c: (_ for _ in ()).throw(Exit(3))
        self.assertEqual(cpu.run(), 3)
        self.assertEqual((cpu.ip, cpu.instructions), (5, 2))


class TestTheMode(unittest.TestCase):
    def test_the_mode_is_put_back_as_it_was(self):
        cpu = flat_cpu()
        cpu.ip_mask, cpu.stack32, cpu.code32 = 0xFFFFFFFF, True, True
        mode = cpu.get_mode()
        self.assertEqual(mode, {"flat": True, "bases": {0x10: 0, 0x18: 0x30000, 0x20: 0x1000}, "ip_mask": 0xFFFFFFFF,
                                "stack32": True, "code32": True})
        other = CPU()
        other.set_mode(mode)
        self.assertEqual(other.get_mode(), mode)
        self.assertTrue(other.flat)
        other.wb(0x18, 1, 5)
        self.assertEqual(other.mem[0x30001], 5)
        with self.assertRaisesRegex(NotImplementedError, "selector 0047"):
            other.rb(0x47, 0)  # the table that comes back is one that says what is not in it

    def test_every_one_of_the_widths_and_sizes_is_kept_apart(self):
        for ip_mask, stack32, code32 in ((0xFFFF, True, False), (0xFFFF, False, True), (0xFFFFFFFF, False, False),
                                         (0xFFFFFFFF, True, False)):
            cpu = flat_cpu()
            cpu.ip_mask, cpu.stack32, cpu.code32 = ip_mask, stack32, code32
            other = CPU()
            other.set_mode(cpu.get_mode())
            self.assertEqual((other.ip_mask, other.stack32, other.code32), (ip_mask, stack32, code32))

    def test_the_mode_of_real_mode_takes_the_flat_one_out(self):
        cpu = flat_cpu()
        cpu.ip_mask, cpu.stack32, cpu.code32 = 0xFFFFFFFF, True, True
        cpu.set_mode(CPU().get_mode())
        self.assertEqual((cpu.flat, cpu.ip_mask, cpu.stack32, cpu.code32), (False, 0xFFFF, False, False))
        cpu.mem[0x20010] = 7
        self.assertEqual(cpu.rb(0x2000, 0x10), 7)  # a selector is a paragraph again
        self.assertNotIn("rb", cpu.__dict__)

    def test_the_mode_of_a_new_machine(self):
        self.assertEqual(CPU().get_mode(), {"flat": False, "bases": {}, "ip_mask": 0xFFFF, "stack32": False,
                                            "code32": False})


if __name__ == "__main__":
    unittest.main()


class TestWriteBytesOfTheFlatMemory(unittest.TestCase):
    """CPU.write_bytes with selectors: the same as writing the bytes one by one."""

    def test_a_run_goes_to_the_base_of_the_selector_and_the_offset(self):
        cpu = flat_cpu()
        cpu.write_bytes(0x18, 0x10, b"hello")
        self.assertEqual(bytes(cpu.mem[0x30010:0x30015]), b"hello")

    def test_a_run_in_the_window_of_the_card_and_over_its_edges_goes_as_bytes_do(self):
        cpu = TestTheWindowOfTheVideoCard().machine()
        cpu.write_bytes(0x10, 0xA0010, b"\x01\x02")
        cpu.write_bytes(0x10, 0x9FFFE, b"\x11\x22\x33\x44")
        self.assertEqual(cpu.video.cells, {0x10: 1, 0x11: 2, 0: 0x33, 1: 0x44})
        self.assertEqual(bytes(cpu.mem[0x9FFFE:0xA0000]), b"\x11\x22")
        self.assertEqual(cpu.mem[0xA0010], 0)

    def test_a_run_that_goes_past_the_end_of_the_memory_stops_the_emulator_as_a_byte_does(self):
        cpu = flat_cpu()
        with self.assertRaises(IndexError):
            cpu.write_bytes(0x10, MEMORY_SIZE - 1, b"ab")

    def test_the_offset_wraps_at_4_gb(self):
        cpu = flat_cpu(Bases({0x10: 0x100}))
        cpu.write_bytes(0x10, 0xFFFFFFFE, b"abcd")  # the sum is 0x100000000FE... it wraps: 0xFE, 0xFF, then 0x100, 0x101
        self.assertEqual(bytes(cpu.mem[0xFE:0x102]), b"abcd")


class TestRepMovsAndStosAsOneBlock(unittest.TestCase):
    """REP MOVS and REP STOS of the flat mode: when the run can be done as one copy or one fill, the registers, the flags
    and the memory are what the loop of single elements gives (it is run beside, with the block turned off)."""

    REGION = 0x20000

    def machine(self, bases=None, block=True):
        cpu = CPU()
        cpu.enter_flat(Bases({0x10: 0, 0x18: self.REGION, 0x20: 0x80000}) if bases is None else bases)
        cpu.s[CPU.CS], cpu.s[CPU.DS], cpu.s[CPU.ES], cpu.s[CPU.SS] = 0x10, 0x18, 0x18, 0x10
        rng = random.Random(9)
        cpu.mem[self.REGION:self.REGION + 0x2000] = bytes(rng.randrange(256) for _ in range(0x2000))
        if not block:
            cpu.rep_block = lambda kind, bits: False
        return cpu

    def execute(self, code, esi=0, edi=0, ecx=0, eax=0, df=False, block=True, bases=None, card=False):
        cpu = self.machine(bases, block)
        if card:
            cpu.video = FakeCard()
            cpu.video_start, cpu.video_end = 0xA0000, 0xB0000
        cpu.mem[0x1000:0x1000 + len(code)] = code
        cpu.ip = 0x1000
        for number, value in ((CPU.SI, esi), (CPU.DI, edi), (CPU.CX, ecx), (CPU.AX, eax)):
            cpu.set32(number, value)
        cpu.set_flag(flags.DF, df)
        try:
            cpu.step()
            outcome = None
        except Exception as error:  # an address out of the memory: the same error
            outcome = type(error)
        return (cpu.r[:], cpu.rh[:], cpu.flags, outcome, bytes(cpu.mem[self.REGION:self.REGION + 0x2000]),
                bytes(cpu.mem[0x80000:0x80100]), dict(cpu.video.cells) if card else None)

    def same(self, code, **registers):
        self.assertEqual(self.execute(code, block=True, **registers), self.execute(code, block=False, **registers))

    def test_a_copy_of_dwords_words_and_bytes_that_does_not_touch_itself(self):
        for code in (b"\xF3\xA5", b"\x66\xF3\xA5", b"\xF3\xA4"):
            self.same(code, esi=0x100, edi=0x900, ecx=37)

    def test_a_copy_that_goes_down_over_its_source_is_the_copy_of_a_block(self):
        self.same(b"\xF3\xA5", esi=0x200, edi=0x100, ecx=60)

    def test_a_copy_that_goes_up_over_its_source_repeats_it(self):
        self.same(b"\xF3\xA4", esi=0x100, edi=0x104, ecx=0x40)  # the first four bytes, again and again
        self.same(b"\xF3\xA5", esi=0x100, edi=0x108, ecx=0x40)

    def test_a_fill_with_a_byte_a_word_and_a_dword(self):
        for code in (b"\xF3\xAA", b"\x66\xF3\xAB", b"\xF3\xAB"):
            self.same(code, edi=0x300, ecx=50, eax=0x8899AABB)

    def test_nothing_is_done_with_a_count_of_zero_and_the_flags_stay(self):
        self.same(b"\xF3\xA5", esi=1, edi=2, ecx=0)
        self.same(b"\xF3\xAB", edi=2, ecx=0, eax=7)

    def test_the_direction_down_is_the_loop_of_elements(self):
        self.same(b"\xF3\xA5", esi=0x400, edi=0x800, ecx=20, df=True)
        self.same(b"\xF3\xAB", edi=0x800, ecx=20, eax=0x01020304, df=True)

    def test_a_fill_with_a_count_that_has_nothing_in_the_high_half(self):
        self.same(b"\xF3\xAB", edi=0, ecx=0x30, eax=5)

    def test_a_run_in_the_window_of_the_card_goes_to_the_card(self):
        self.same(b"\xF3\xAB", edi=0xA0000 - self.REGION + 0x10, ecx=12, eax=0x11223344, card=True)
        self.same(b"\xF3\xA5", esi=0x100, edi=0xA0000 - self.REGION, ecx=8, card=True)
        self.same(b"\xF3\xA5", esi=0xA0000 - self.REGION, edi=0x100, ecx=8, card=True)  # the card is read
        self.same(b"\xF3\xAB", edi=0xA0000 - self.REGION - 8, ecx=6, eax=0x55667788, card=True)  # over its edge

    def test_a_run_that_goes_over_the_end_of_the_memory_or_wraps_at_4_gb_is_as_the_loop_has_it(self):
        self.same(b"\xF3\xAB", edi=MEMORY_SIZE - self.REGION - 8, ecx=4, eax=1)
        self.same(b"\xF3\xAB", edi=MEMORY_SIZE - self.REGION - 8, ecx=2, eax=1)
        bases = Bases({0x10: 0, 0x18: 0x20000, 0x20: 0x80000})
        self.same(b"\xF3\xAA", edi=0xFFFFFFF0, ecx=0x20, eax=0x99, bases=bases)  # the offset wraps: 0x20000 - 0x10 on

    def test_the_block_is_made_when_it_can_be(self):
        cpu = self.machine()
        seen = []
        real = cpu.rep_block
        cpu.rep_block = lambda kind, bits: seen.append(real(kind, bits)) or seen[-1]
        cpu.mem[0x1000:0x1002] = b"\xF3\xA5"
        cpu.ip = 0x1000
        cpu.set32(CPU.CX, 100)
        cpu.set32(CPU.DI, 0x800)
        cpu.step()
        self.assertEqual(seen, [True])
