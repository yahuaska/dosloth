import unittest

from dosloth import bios, vesa
from dosloth.cpu import CPU


def machine():
    cpu = CPU()
    bios.install(cpu)
    cpu.s[CPU.CS] = 0x1000
    cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x10])
    return cpu


def ask(cpu, ax=0x4F00, es=0x2000, di=0x0100, signature=b"", fill=0xA5, size=600):
    """INT 10h with a buffer at es:di that begins with `signature` and holds `fill` after it."""
    for i in range(size):
        cpu.wb(es, (di + i) & 0xFFFF, fill)
    for i, byte in enumerate(signature):
        cpu.wb(es, (di + i) & 0xFFFF, byte)
    cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, 0x1111, 0x2222, 0x3333
    cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, di
    cpu.s[CPU.ES], cpu.s[CPU.DS] = es, 0x5555
    cpu.ip = 0
    cpu.step()
    return bytes(cpu.rb(es, (di + i) & 0xFFFF) for i in range(size))


def far_pointer(block, offset):
    return int.from_bytes(block[offset + 2:offset + 4], "little"), int.from_bytes(block[offset:offset + 2], "little")


def string_at(cpu, segment, offset):
    out = bytearray()
    while cpu.rb(segment, offset):
        out.append(cpu.rb(segment, offset))
        offset += 1
    return bytes(out)


class TestControllerInformation(unittest.TestCase):
    def test_the_answer_is_4fh_for_the_function_and_00h_for_it_went_well(self):
        cpu = machine()
        ask(cpu)
        self.assertEqual(cpu.r[CPU.AX], 0x004F)

    def test_the_registers_the_call_does_not_answer_with_stay(self):
        cpu = machine()
        cpu.flags = 0x0FD7
        ask(cpu)
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]),
                         (0x1111, 0x2222, 0x3333, 0x4444, 0x0100))
        self.assertEqual((cpu.s[CPU.ES], cpu.s[CPU.DS], cpu.flags), (0x2000, 0x5555, 0x0FD7))
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_the_block_of_a_program_that_does_not_know_vbe_2(self):
        block = ask(machine())
        self.assertEqual(block[0:4], b"VESA")
        self.assertEqual(int.from_bytes(block[4:6], "little"), 0x0200)
        self.assertEqual(block[0x0A:0x0E], bytes(4))
        self.assertEqual(int.from_bytes(block[0x12:0x14], "little"), 16)

    def test_it_is_256_bytes_and_not_a_byte_more_is_written(self):
        block = ask(machine())
        self.assertEqual(block[256:], bytes([0xA5]) * (600 - 256))
        self.assertEqual(block[255], 0x00)  # the last byte of the block was written, with a zero

    def test_the_part_of_the_block_nobody_fills_is_zeros_and_not_what_was_there_before(self):
        block = ask(machine())
        self.assertEqual(block[0x14:256], bytes(256 - 0x14))

    def test_the_manufacturer_string_is_where_the_pointer_says(self):
        cpu = machine()
        block = ask(cpu)
        segment, offset = far_pointer(block, 0x06)
        self.assertEqual((segment, offset), (0xC000, 0x0100))
        self.assertEqual(string_at(cpu, segment, offset), b"dosloth VGA")

    def test_the_list_of_modes_has_0101h_and_ends_with_ffffh_as_the_installer_reads_it(self):
        cpu = machine()
        block = ask(cpu)
        segment, offset = far_pointer(block, 0x0E)
        modes = []
        while True:
            mode = cpu.rw(segment, offset + 2 * len(modes))
            modes.append(mode)
            if mode == 0xFFFF:
                break
        self.assertEqual(modes, [0x0101, 0xFFFF])

    def test_a_program_that_says_vbe2_gets_the_longer_block_with_the_names(self):
        cpu = machine()
        block = ask(cpu, signature=b"VBE2")
        self.assertEqual(block[0:4], b"VESA")  # the signature is changed from VBE2 to VESA
        self.assertEqual(int.from_bytes(block[0x14:0x16], "little"), 0x0100)
        for offset, text in ((0x16, b"dosloth"), (0x1A, b"dosloth VBE"), (0x1E, b"1.0")):
            segment, where = far_pointer(block, offset)
            self.assertEqual((segment, string_at(cpu, segment, where)), (0xC000, text), hex(offset))
        self.assertEqual(block[0x22:512], bytes(512 - 0x22))
        self.assertEqual(block[512:], bytes([0xA5]) * (600 - 512))

    def test_the_short_block_has_no_names_and_the_vbe2_one_has_the_same_first_fields(self):
        short = ask(machine())
        long = ask(machine(), signature=b"VBE2")
        self.assertEqual(short[0:0x14], long[0:0x14])
        self.assertEqual(short[0x14:0x22], bytes(0x22 - 0x14))
        self.assertNotEqual(long[0x14:0x22], bytes(0x22 - 0x14))

    def test_a_signature_that_is_not_vbe2_is_taken_as_none(self):
        for signature in (b"VESA", b"VBE1", b"vbe2", b"VBE"):
            block = ask(machine(), signature=signature)
            self.assertEqual(block[256:], bytes([0xA5]) * (600 - 256), signature)

    def test_the_buffer_may_be_anywhere_and_it_may_wrap_round_the_segment(self):
        cpu = machine()
        for es, di in ((0x3000, 0x0000), (0x9000, 0xFE00), (0x2000, 0xFFC0)):
            block = ask(cpu, es=es, di=di, size=300)
            self.assertEqual(block[0:4], b"VESA", (es, di))
            self.assertEqual(cpu.r[CPU.AX], 0x004F)

    def test_every_string_ends_with_its_own_zero(self):
        for offset, data in (vesa.OEM_STRING, vesa.VENDOR, vesa.PRODUCT, vesa.REVISION):
            self.assertEqual(data[-1], 0, hex(offset))
            self.assertNotIn(0, data[:-1], hex(offset))

    def test_the_block_goes_round_the_end_of_the_segment_as_an_offset_does(self):
        cpu = machine()
        block = ask(cpu, es=0x2000, di=0xFFC0, size=300)
        self.assertEqual(block[0:4], b"VESA")
        self.assertEqual(block[64:256], bytes(192))  # these are past the end of the segment, at its start
        self.assertEqual(block[256:], bytes([0xA5]) * 44)

    def test_the_signature_may_stand_across_the_end_of_the_segment(self):
        cpu = machine()
        block = ask(cpu, es=0x2000, di=0xFFFE, signature=b"VBE2", size=600)
        self.assertEqual(len(block), 600)
        self.assertEqual(block[0:4], b"VESA")
        self.assertEqual(block[0x14:0x16], bytes([0x00, 0x01]))  # only a VBE 2.0 block has the software revision

    def test_the_strings_and_the_modes_do_not_overlap_and_are_in_the_rom_segment(self):
        pieces = [vesa.OEM_STRING, vesa.VENDOR, vesa.PRODUCT, vesa.REVISION, vesa.MODE_LIST]
        spans = sorted((offset, offset + len(data)) for offset, data in pieces)
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertLessEqual(end, start)
        self.assertEqual(vesa.ROM_SEGMENT, 0xC000)
        self.assertTrue(all(0 <= offset and offset + len(data) <= 0x1000 for offset, data in pieces))

    def test_each_machine_has_its_own_rom_data(self):
        one, two = machine(), machine()
        one.mem[0xC0200:0xC0204] = bytes(4)
        block = ask(two)
        segment, offset = far_pointer(block, 0x0E)
        self.assertEqual(two.rw(segment, offset), 0x0101)


def word(block, offset):
    return int.from_bytes(block[offset:offset + 2], "little")


def ask_mode(mode):
    cpu = machine()
    for i in range(300):
        cpu.wb(0x2000, 0x0100 + i, 0xA5)
    cpu.r[CPU.AX], cpu.r[CPU.CX], cpu.r[CPU.DI], cpu.s[CPU.ES] = 0x4F01, mode, 0x0100, 0x2000
    cpu.ip = 0
    cpu.step()
    return cpu, bytes(cpu.rb(0x2000, 0x0100 + i) for i in range(300))


class TestTheInformationOfAMode(unittest.TestCase):
    def test_the_mode_0101_is_640_by_480_in_256_colors_with_the_window_at_a000(self):
        cpu, block = ask_mode(0x0101)
        self.assertEqual(cpu.r[CPU.AX], 0x004F)
        self.assertEqual((word(block, 0x12), word(block, 0x14), word(block, 0x10)), (640, 480, 640))
        self.assertEqual((block[0x18], block[0x19], block[0x1B]), (1, 8, 4))  # one plane, 8 bits, packed pixels
        self.assertEqual((word(block, 0x04), word(block, 0x06), word(block, 0x08)), (64, 64, 0xA000))
        self.assertEqual((block[0x02], block[0x03], word(block, 0x0A)), (0x07, 0, 0))  # the window A, and no B

    def test_there_is_no_function_for_the_window_so_a_program_asks_the_bios(self):
        _, block = ask_mode(0x0101)
        self.assertEqual(block[0x0C:0x10], bytes(4))

    def test_the_attributes_say_graphics_color_a_window_and_no_linear_frame_buffer(self):
        _, block = ask_mode(0x0101)
        attributes = word(block, 0x00)
        self.assertEqual(attributes & 0x01, 0x01)  # the mode can be set
        self.assertEqual(attributes & 0x18, 0x18)  # color, graphics
        self.assertEqual(attributes & 0xC0, 0)  # a window, no linear frame buffer
        self.assertEqual(attributes, 0x001B)

    def test_it_fits_5_banks_of_64_kb_and_the_memory_has_room_for_3_pages(self):
        _, block = ask_mode(0x0101)
        self.assertEqual((block[0x1A], block[0x1C]), (5, 64))
        self.assertEqual(block[0x1D], 2)  # 1 MB holds 3 screens of 300 KB: the byte is the number less one

    def test_the_block_is_256_bytes_and_what_is_after_it_is_left_alone(self):
        _, block = ask_mode(0x0101)
        self.assertEqual(block[256:], bytes([0xA5]) * 44)
        self.assertNotIn(0xA5, block[0x1E:0x28])  # the reserved bytes are cleared, not left as they were

    def test_a_mode_that_is_not_there_is_error_014f_and_the_buffer_is_left_alone(self):
        for mode in (0x0100, 0x0102, 0x0013, 0x4101, 0x0000):
            with self.subTest(mode=mode):
                cpu, block = ask_mode(mode)
                self.assertEqual(cpu.r[CPU.AX], 0x014F)
                self.assertEqual(block, bytes([0xA5]) * 300)

    def test_the_mode_in_the_list_of_the_modes_is_the_one_that_is_answered(self):
        cpu = machine()
        block = ask(cpu)
        segment, offset = far_pointer(block, 0x0E)
        mode = cpu.rw(segment, offset)
        self.assertEqual(ask_mode(mode)[0].r[CPU.AX], 0x004F)


class TestTheOtherVesaFunctions(unittest.TestCase):
    def test_everything_but_the_first_question_stops_the_emulator_and_says_which(self):
        for al in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x10, 0xFF):
            cpu = machine()
            with self.assertRaises(NotImplementedError) as caught:
                ask(cpu, ax=0x4F00 | al)
            self.assertIn(f"AH=4F AL={al:02X}", str(caught.exception))

    def test_the_function_4fh_does_not_get_in_the_way_of_the_others(self):
        cpu = machine()
        cpu.r[CPU.AX] = 0x0F00
        cpu.ip = 0
        cpu.step()
        self.assertEqual(cpu.get8(0), 0x03)


if __name__ == "__main__":
    unittest.main()
