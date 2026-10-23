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


class TestLoad(unittest.TestCase):
    def test_load_picks_the_loader_by_the_first_bytes_and_puts_the_program_at_the_given_segment(self):
        cpu = CPU()
        loader.load(cpu, b"\xB8\x00\x4C", 0x3000)
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, bytes(cpu.mem[0x30100:0x30103])), (0x3000, 0x100, b"\xB8\x00\x4C"))
        loader.load(cpu, exe(b"\xB8\x00\x4C"), 0x4000)
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, bytes(cpu.mem[0x40100:0x40103])), (0x4010, 0, b"\xB8\x00\x4C"))


if __name__ == "__main__":
    unittest.main()
