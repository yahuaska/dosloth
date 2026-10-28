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


class TestAnLeProgramIsRefused(unittest.TestCase):
    """A program in the LE format, bound to DOS/4G or not, is stopped, and the message says what it is."""

    def programs(self):
        from tests.le_builder import Obj, RW, RX, build_bound, build_program
        objects = [Obj(b"\x90" * 5000, size=0x2100, flags=RX), Obj(b"data", size=0x1800, base=0x30000, flags=RW)]
        plain = build_program(objects, entry=(1, 0x20))
        return {"plain": plain, "bound": build_bound(plain)}

    def test_memory_needed_stops_for_both_with_what_the_program_is(self):
        for kind, data in self.programs().items():
            with self.assertRaises(NotImplementedError) as caught:
                loader.memory_needed(data)
            message = str(caught.exception)
            self.assertIn("LE program", message, kind)
            self.assertIn("2 objects, 5 pages, entry in the object 1 at 0x20", message, kind)
            self.assertIn("32-bit mode is not done yet", message, kind)

    def test_the_message_says_whether_it_is_bound_to_the_extender(self):
        programs = self.programs()
        with self.assertRaisesRegex(NotImplementedError, "bound to the extender of DOS/4G"):
            loader.memory_needed(programs["bound"])
        with self.assertRaisesRegex(NotImplementedError, "plain"):
            loader.memory_needed(programs["plain"])

    def test_load_stops_too_and_puts_nothing_in_memory(self):
        for data in self.programs().values():
            cpu = CPU()
            with self.assertRaises(NotImplementedError):
                loader.load(cpu, data, 0x1000)
            self.assertFalse(any(cpu.mem[0x10000:0x20000]))

    def test_a_program_that_looks_like_one_but_cannot_be_loaded_is_said_to_be_one_that_cannot(self):
        data = bytearray(self.programs()["plain"])
        header = int.from_bytes(data[0x3C:0x40], "little")
        data[header + 8:header + 10] = (1).to_bytes(2, "little")  # for the 286
        with self.assertRaisesRegex(NotImplementedError, "one that cannot be loaded: the program is for the cpu 1"):
            loader.memory_needed(bytes(data))

    def test_the_other_programs_are_not_touched_by_it(self):
        self.assertEqual(loader.memory_needed(b"\xB8\x00\x4C\xCD\x21"), (0x1000, 0xFFFF))
        self.assertEqual(loader.memory_needed(exe(b"", minalloc=0x10, maxalloc=0x20)), (0x12 + 0x10, 0x12 + 0x20))


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
