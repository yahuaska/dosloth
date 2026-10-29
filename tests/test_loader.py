import unittest

from mydos import loader
from mydos.cpu import CPU
from tests.test_process import exe


class TestMemoryNeeded(unittest.TestCase):
    def test_a_com_takes_all_there_is_and_64_kb_at_the_least(self):
        self.assertEqual(loader.memory_needed(b"\xB8\x00\x4C\xCD\x21"), (0x1000, 0xFFFF))

    def test_an_exe_wants_its_psp_its_image_and_what_the_header_adds(self):
        # 32 bytes of image: 2 paragraphs, and the PSP is 10h
        self.assertEqual(loader.memory_needed(exe(b"", minalloc=0x10, maxalloc=0x20)), (0x12 + 0x10, 0x12 + 0x20))

    def test_the_image_is_counted_in_whole_paragraphs(self):
        self.assertEqual(loader.memory_needed(exe(b"", image_size=33, minalloc=0, maxalloc=0))[0], 0x10 + 3)
        self.assertEqual(loader.memory_needed(exe(b"", image_size=48, minalloc=0, maxalloc=0))[0], 0x10 + 3)

    def test_a_maxalloc_below_the_minalloc_does_not_make_the_most_less_than_the_least(self):
        least, most = loader.memory_needed(exe(b"", minalloc=0x30, maxalloc=0x10))
        self.assertEqual(least, most)

    def test_all_the_memory_is_asked_for_with_ffff_and_it_is_not_more_than_a_block_can_be(self):
        self.assertEqual(loader.memory_needed(exe(b"", maxalloc=0xFFFF))[1], 0xFFFF)

    def test_the_zm_signature_is_an_exe_too(self):
        data = bytearray(exe(b""))
        data[0:2] = b"ZM"
        self.assertEqual(loader.memory_needed(bytes(data))[0], 0x12 + 0x10)

    def test_half_a_header_is_not_an_exe(self):
        with self.assertRaises(ValueError):
            loader.memory_needed(b"MZ" + bytes(10))
        with self.assertRaises(ValueError):
            loader.memory_needed(b"MZ" + bytes(25))  # 27 bytes: one short
        loader.memory_needed(b"MZ" + bytes(26))


class TestAnLeProgram(unittest.TestCase):
    """A program in the LE format, bound to DOS/4G or not, is loaded above 1 MB: what it asks of DOS is its PSP."""

    def programs(self):
        from tests.le_builder import Obj, RW, RX, build_bound, build_program
        objects = [Obj(b"\x90" * 5000, size=0x2100, flags=RX), Obj(b"data", size=0x1800, base=0x30000, flags=RW)]
        plain = build_program(objects, entry=(1, 0x20))
        return {"plain": plain, "bound": build_bound(plain)}

    def test_memory_needed_is_the_psp_at_the_least_and_all_there_is_at_the_most(self):
        for kind, data in self.programs().items():
            self.assertEqual(loader.memory_needed(data), (0x10, 0xFFFF), kind)

    def test_one_that_cannot_be_loaded_is_an_error_of_the_format_that_says_why(self):
        data = bytearray(self.programs()["plain"])
        header = int.from_bytes(data[0x3C:0x40], "little")
        data[header + 8:header + 10] = (1).to_bytes(2, "little")  # for the 286
        with self.assertRaisesRegex(ValueError, "the program is for the cpu 1"):
            loader.memory_needed(bytes(data))

    def test_load_puts_it_in_the_extended_memory_and_starts_it_in_the_flat_mode(self):
        from mydos.xmem import START, ExtendedMemory
        for kind, data in self.programs().items():
            cpu, extended = CPU(), ExtendedMemory()
            loader.load(cpu, data, 0x3000, extended)
            self.assertTrue(cpu.flat, kind)
            self.assertEqual((cpu.ip, sum(extended.blocks.values())), (START + 0x20, 5 * 0x1000), kind)
            self.assertEqual(bytes(cpu.mem[START + 0x20:START + 0x23]), b"\x90" * 3, kind)

    def test_load_without_the_extended_memory_to_put_it_in_stops_and_puts_nothing_in_memory(self):
        for data in self.programs().values():
            cpu = CPU()
            with self.assertRaisesRegex(ValueError, "extended memory"):
                loader.load(cpu, data, 0x1000)
            self.assertFalse(any(cpu.mem[0x10000:0x20000]))
            self.assertFalse(cpu.flat)

    def test_the_other_programs_are_not_touched_by_it(self):
        self.assertEqual(loader.memory_needed(b"\xB8\x00\x4C\xCD\x21"), (0x1000, 0xFFFF))
        self.assertEqual(loader.memory_needed(exe(b"", minalloc=0x10, maxalloc=0x20)), (0x12 + 0x10, 0x12 + 0x20))

    def test_a_program_of_real_mode_does_not_need_the_extended_memory_given_nor_uses_it(self):
        from mydos.xmem import ExtendedMemory
        extended = ExtendedMemory()
        loader.load(CPU(), exe(b"\xB8\x00\x4C"), 0x4000, extended)
        self.assertEqual(extended.blocks, {})


class TestLoad(unittest.TestCase):
    def test_a_program_of_real_mode_starts_in_real_mode_whatever_the_machine_was_in(self):
        from mydos.flat import REAL_MODE, Bases
        for data in (b"\xB8\x00\x4C", exe(b"\xB8\x00\x4C")):
            cpu = CPU()
            cpu.use_flat_memory(Bases({0x10: 0}))
            cpu.ip_mask, cpu.stack32, cpu.code32 = 0xFFFFFFFF, True, True
            loader.load(cpu, data, 0x3000)
            self.assertEqual(cpu.get_mode(), REAL_MODE)
            self.assertEqual(cpu.rb(cpu.s[CPU.CS], cpu.ip), 0xB8)  # the first instruction: a selector is a paragraph


    def test_a_program_starts_with_zeros_in_fs_and_gs(self):
        cpu = CPU()
        cpu.s[CPU.FS], cpu.s[CPU.GS] = 0x1111, 0x2222
        loader.load(cpu, b"\xB8\x00\x4C", 0x3000)
        self.assertEqual((cpu.s[CPU.FS], cpu.s[CPU.GS]), (0, 0))
        cpu.s[CPU.FS], cpu.s[CPU.GS] = 0x1111, 0x2222
        loader.load(cpu, exe(b"\xB8\x00\x4C"), 0x4000)
        self.assertEqual((cpu.s[CPU.FS], cpu.s[CPU.GS]), (0, 0))

    def test_load_picks_the_loader_by_the_first_bytes_and_puts_the_program_at_the_given_segment(self):
        cpu = CPU()
        loader.load(cpu, b"\xB8\x00\x4C", 0x3000)
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, bytes(cpu.mem[0x30100:0x30103])), (0x3000, 0x100, b"\xB8\x00\x4C"))
        loader.load(cpu, exe(b"\xB8\x00\x4C"), 0x4000)
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, bytes(cpu.mem[0x40100:0x40103])), (0x4010, 0, b"\xB8\x00\x4C"))


if __name__ == "__main__":
    unittest.main()
