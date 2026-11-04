import random
import unittest

from dosloth import vga
from dosloth.cpu import CPU
from dosloth.screen import EGA_COLORS
from dosloth.vga import DEFAULT_ATTRIBUTES, Vga


def machine():
    cpu = CPU()
    vga.install(cpu)
    return cpu


class TestIndexedRegisters(unittest.TestCase):
    """The sequencer, the graphics controller and the CRT controller: an index port and a data port each."""

    PAIRS = ((0x3C4, 0x3C5), (0x3CE, 0x3CF), (0x3D4, 0x3D5))

    def test_a_register_gives_back_what_was_written(self):
        for index_port, data_port in self.PAIRS:
            chip = Vga()
            chip.write(index_port, 5)
            chip.write(data_port, 0xA5)
            self.assertEqual(chip.read(data_port), 0xA5, hex(data_port))

    def test_registers_are_told_apart_by_their_index(self):
        for index_port, data_port in self.PAIRS:
            chip = Vga()
            for index in (0, 1, 5):
                chip.write(index_port, index)
                chip.write(data_port, 0x10 + index)
            for index in (0, 1, 5):
                chip.write(index_port, index)
                self.assertEqual(chip.read(data_port), 0x10 + index, f"{data_port:X} register {index}")

    def test_the_three_pairs_do_not_share_registers(self):
        chip = Vga()
        for number, (index_port, data_port) in enumerate(self.PAIRS):
            chip.write(index_port, 3)
            chip.write(data_port, 0x40 + number)
        for number, (index_port, data_port) in enumerate(self.PAIRS):
            chip.write(index_port, 3)
            self.assertEqual(chip.read(data_port), 0x40 + number)

    def test_a_register_never_written_reads_0_and_a_data_write_does_not_change_the_index(self):
        chip = Vga()
        chip.write(0x3CE, 3)
        self.assertEqual(chip.read(0x3CF), 0)
        chip.write(0x3CF, 9)
        self.assertEqual(chip.read(0x3CE), 3)  # the index port still says which register it is

    def test_the_registers_start_as_the_bios_leaves_them_in_a_graphics_mode(self):
        chip = Vga()
        for index_port, index, value in ((0x3C4, 2, 0x0F), (0x3CE, 7, 0x0F), (0x3CE, 8, 0xFF), (0x3CE, 0, 0),
                                         (0x3CE, 1, 0), (0x3CE, 3, 0), (0x3CE, 4, 0), (0x3CE, 5, 0), (0x3C4, 1, 0)):
            chip.write(index_port, index)
            self.assertEqual(chip.read(index_port + 1), value, f"{index_port:X} register {index}")

    def test_a_word_out_sets_the_index_from_al_and_the_register_from_ah(self):
        cpu = machine()
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000] = 0xEF  # out dx, ax
        cpu.r[CPU.DX], cpu.r[CPU.AX] = 0x3CE, 0x0305  # register 5 gets 03h
        cpu.step()
        cpu.port_out(0x3CE, 5)
        self.assertEqual(cpu.port_in(0x3CF), 0x03)


class TestOtherPorts(unittest.TestCase):
    def test_a_port_gives_back_the_last_byte_written(self):
        chip = Vga()
        for port in (0x3C2, 0x3CA, 0x3CC, 0x3CD, 0x3D0, 0x3DB):
            chip.write(port, 0x11)
            chip.write(port, 0x22)
            self.assertEqual(chip.read(port), 0x22, hex(port))

    def test_a_port_never_written_reads_0(self):
        chip = Vga()
        self.assertEqual([chip.read(port) for port in (0x3C2, 0x3CA, 0x3CC)], [0, 0, 0])

    def test_the_ports_are_kept_apart(self):
        chip = Vga()
        chip.write(0x3C2, 1)
        chip.write(0x3CA, 2)
        self.assertEqual((chip.read(0x3C2), chip.read(0x3CA)), (1, 2))

    def test_an_index_write_is_not_taken_for_a_port_write_of_the_same_value(self):
        chip = Vga()
        chip.write(0x3C4, 1)
        self.assertEqual(chip.read(0x3C5), 0)


class TestStatus(unittest.TestCase):
    def test_both_bits_a_program_waits_for_change_on_every_read(self):
        chip = Vga()
        seen = [chip.read(0x3DA) for _ in range(6)]
        self.assertEqual(seen, [0x09, 0x00, 0x09, 0x00, 0x09, 0x00])
        for before, after in zip(seen, seen[1:]):
            self.assertEqual((before ^ after) & 0x09, 0x09)

    def test_a_wait_for_the_bit_to_be_set_and_then_clear_comes_to_an_end(self):
        chip = Vga()
        reads = 0
        while not chip.read(0x3DA) & 0x08:
            reads += 1
        while chip.read(0x3DA) & 0x08:
            reads += 1
        self.assertLess(reads, 4)

    def test_writing_the_port_does_not_change_what_it_reads(self):
        chip = Vga()
        chip.read(0x3DA)
        chip.write(0x3DA, 0xFF)
        self.assertEqual(chip.read(0x3DA), 0x00)


class TestInstall(unittest.TestCase):
    def test_all_the_ports_from_3c0_to_3df_are_there_and_no_others(self):
        cpu = machine()
        for port in range(0x3C0, 0x3E0):
            cpu.port_out(port, 0)
            cpu.port_in(port)
        for port in (0x3BF, 0x3E0, 0x3B4, 0x3BA):
            with self.assertRaises(NotImplementedError):
                cpu.port_in(port)
            with self.assertRaises(NotImplementedError):
                cpu.port_out(port, 0)

    def test_the_cpu_reads_and_writes_through_the_card(self):
        cpu = machine()
        cpu.port_out(0x3C4, 2)
        cpu.port_out(0x3C5, 0x0F)
        self.assertEqual(cpu.port_in(0x3C5), 0x0F)

    def test_with_the_registers_as_they_start_the_video_memory_gives_back_what_was_written(self):
        cpu = machine()
        cpu.wb(0xA000, 0x1234, 0x5A)
        self.assertEqual(cpu.rb(0xA000, 0x1234), 0x5A)

    def test_each_cpu_has_its_own_card(self):
        first, second = machine(), machine()
        first.port_out(0x3C8, 7)
        self.assertEqual(second.port_in(0x3C8), 0)

def bit(byte, number):
    return byte >> number & 1


class Reference:
    """The card as the manual tells it, one pixel at a time: bit n of a byte is one pixel of its eight.

    It is written differently from vga.py on purpose, with single bits and no masks of whole bytes, so that a
    slip in one is not copied into the other.
    """

    def __init__(self, planes, latches, registers):
        self.planes = [list(plane) for plane in planes]
        self.latches = list(latches)
        self.r = registers  # {"map_mask", "set_reset", "enable", "compare", "rotate", "function", "read_map",
        # "write_mode", "read_mode", "care", "bit_mask"}

    def read(self, offset):
        r = self.r
        self.latches = [self.planes[p][offset] for p in range(4)]
        result = 0
        for n in range(8):
            if r["read_mode"] == 0:
                out = bit(self.planes[r["read_map"]][offset], n)
            else:
                out = 1
                for p in range(4):
                    if bit(r["care"], p) and bit(self.planes[p][offset], n) != bit(r["compare"], p):
                        out = 0
            result |= out << n
        return result

    def write(self, offset, value):
        r = self.r
        for p in range(4):
            if not bit(r["map_mask"], p):
                continue
            result = 0
            for n in range(8):
                latch_bit = bit(self.latches[p], n)
                if r["write_mode"] == 1:
                    out = latch_bit
                else:
                    cpu_bit = bit(value, (n + r["rotate"]) % 8)  # rotated right: bit n comes from bit n + rotate
                    if r["write_mode"] == 0:
                        data = bit(r["set_reset"], p) if bit(r["enable"], p) else cpu_bit
                        mask_bit = bit(r["bit_mask"], n)
                    elif r["write_mode"] == 2:
                        data = bit(value, p)
                        mask_bit = bit(r["bit_mask"], n)
                    else:
                        data = bit(r["set_reset"], p)
                        mask_bit = bit(r["bit_mask"], n) & cpu_bit
                    data = (data, data & latch_bit, data | latch_bit, data ^ latch_bit)[r["function"]]
                    out = data if mask_bit else latch_bit
                result |= out << n
            self.planes[p][offset] = result


def configure(chip, **values):
    """Set the registers the way a program would: through the ports."""
    for name, (port, index) in REGISTER_PORTS.items():
        if name in values:
            chip.write(port, index)
            chip.write(port + 1, values[name])


REGISTER_PORTS = {"map_mask": (0x3C4, 2), "set_reset": (0x3CE, 0), "enable": (0x3CE, 1), "compare": (0x3CE, 2),
                  "rotate_function": (0x3CE, 3), "read_map": (0x3CE, 4), "mode": (0x3CE, 5), "care": (0x3CE, 7),
                  "bit_mask": (0x3CE, 8)}


class TestWriteModes(unittest.TestCase):
    """What a byte written to the window does to the four planes, worked out by hand."""

    def setUp(self):
        self.chip = Vga()

    def planes_at(self, offset):
        return [plane[offset] for plane in self.chip.planes]

    def test_mode_0_as_the_registers_start_puts_the_byte_in_every_plane(self):
        self.chip.write_memory(5, 0xA5)
        self.assertEqual(self.planes_at(5), [0xA5] * 4)
        self.assertEqual(self.planes_at(4), [0] * 4)

    def test_the_map_mask_says_which_planes_take_it(self):
        configure(self.chip, map_mask=0b0101)
        self.chip.write_memory(5, 0xA5)
        self.assertEqual(self.planes_at(5), [0xA5, 0, 0xA5, 0])

    def test_with_set_reset_a_plane_is_filled_with_ones_or_zeros_whatever_the_byte(self):
        configure(self.chip, enable=0x0F, set_reset=0b0101)
        for value in (0x00, 0x37, 0xFF):
            self.chip.write_memory(9, value)
            self.assertEqual(self.planes_at(9), [0xFF, 0, 0xFF, 0], hex(value))

    def test_a_plane_with_set_reset_off_gets_the_byte_and_the_others_the_fill(self):
        configure(self.chip, enable=0b0011, set_reset=0b0010)
        self.chip.write_memory(9, 0x3C)
        self.assertEqual(self.planes_at(9), [0x00, 0xFF, 0x3C, 0x3C])

    def test_the_bit_mask_keeps_the_other_bits_from_the_latch(self):
        self.chip.planes[0][3], self.chip.planes[1][3], self.chip.planes[2][3], self.chip.planes[3][3] = (
            0x0F, 0xF0, 0xFF, 0x00)
        self.chip.read_memory(3)  # fills the latches with the four bytes there
        configure(self.chip, enable=0x0F, set_reset=0x0F, bit_mask=0xF0)
        self.chip.write_memory(3, 0x00)
        # the top half is set in every plane, the bottom half is as it was
        self.assertEqual(self.planes_at(3), [0xFF, 0xF0, 0xFF, 0xF0])

    def test_rotate_turns_the_byte_right_before_it_is_used(self):
        configure(self.chip, rotate_function=3)
        self.chip.write_memory(1, 0b00010110)
        self.assertEqual(self.planes_at(1), [0b11000010] * 4)

    def test_the_function_meets_the_byte_with_the_latch(self):
        for function, expected in ((0, 0b10100110), (1, 0b00100100), (2, 0b10110110), (3, 0b10010010)):
            chip = Vga()
            chip.planes[0][2] = 0b00110100
            chip.read_memory(2)
            configure(chip, rotate_function=function << 3, map_mask=1)
            chip.write_memory(2, 0b10100110)
            self.assertEqual(chip.planes[0][2], expected, f"function {function}")

    def test_mode_1_writes_the_latches_and_ignores_the_byte_and_the_bit_mask(self):
        for plane, value in enumerate((0x11, 0x22, 0x33, 0x44)):
            self.chip.planes[plane][6] = value
        self.chip.read_memory(6)
        configure(self.chip, mode=1, bit_mask=0x00, enable=0x0F, set_reset=0x0F)
        self.chip.write_memory(100, 0xFF)
        self.assertEqual(self.planes_at(100), [0x11, 0x22, 0x33, 0x44])

    def test_mode_1_only_fills_the_planes_the_map_mask_allows(self):
        for plane in range(4):
            self.chip.planes[plane][6] = 0xF0 + plane
        self.chip.read_memory(6)
        configure(self.chip, mode=1, map_mask=0b1010)
        self.chip.write_memory(7, 0)
        self.assertEqual(self.planes_at(7), [0, 0xF1, 0, 0xF3])

    def test_mode_2_makes_each_plane_all_ones_or_all_zeros_from_the_low_bits_of_the_byte(self):
        configure(self.chip, mode=2)
        self.chip.write_memory(4, 0b00001010)
        self.assertEqual(self.planes_at(4), [0x00, 0xFF, 0x00, 0xFF])
        self.chip.write_memory(5, 0xF5)  # the high bits of the byte mean nothing
        self.assertEqual(self.planes_at(5), [0xFF, 0x00, 0xFF, 0x00])

    def test_mode_2_with_a_bit_mask_changes_only_the_bits_of_the_mask(self):
        self.chip.planes[0][4] = self.chip.planes[1][4] = 0x00
        self.chip.read_memory(4)
        configure(self.chip, mode=2, bit_mask=0x81, map_mask=0b0011)
        self.chip.write_memory(4, 0b0001)
        self.assertEqual(self.planes_at(4)[:2], [0x81, 0x00])

    def test_mode_3_uses_set_reset_as_the_color_and_the_byte_as_the_mask(self):
        configure(self.chip, mode=3, set_reset=0b0110)
        self.chip.write_memory(8, 0b11110000)
        self.assertEqual(self.planes_at(8), [0x00, 0xF0, 0xF0, 0x00])

    def test_mode_3_ands_the_rotated_byte_with_the_bit_mask(self):
        configure(self.chip, mode=3, set_reset=0x0F, bit_mask=0b00111100, rotate_function=4)
        self.chip.write_memory(8, 0b00110000)  # rotated right by 4: 00000011; and the mask: 00000000
        self.assertEqual(self.planes_at(8), [0] * 4)
        self.chip.write_memory(9, 0b11000011)  # rotated: 00111100
        self.assertEqual(self.planes_at(9), [0b00111100] * 4)

    def test_a_write_does_not_change_the_latches(self):
        self.chip.planes[0][1] = 0x77
        self.chip.read_memory(1)
        self.chip.write_memory(2, 0xFF)
        self.assertEqual(self.chip.latches, [0x77, 0, 0, 0])


class TestChanged(unittest.TestCase):
    """The card remembers that it was drawn on, for whoever shows the screen."""

    def test_a_new_card_has_not_been_drawn_on(self):
        self.assertFalse(Vga().changed)

    def test_a_write_in_any_mode_marks_it(self):
        for mode in (0, 1, 2, 3):
            chip = Vga()
            configure(chip, mode=mode)
            chip.write_memory(0, 0)
            self.assertTrue(chip.changed, f"write mode {mode}")

    def test_a_read_or_a_port_write_does_not(self):
        chip = Vga()
        chip.read_memory(0)
        chip.write(0x3C8, 5)
        configure(chip, mode=2, map_mask=1)
        self.assertFalse(chip.changed)

    def test_whoever_looked_clears_it_and_the_next_write_sets_it_again(self):
        chip = Vga()
        chip.write_memory(0, 0)
        chip.changed = False
        chip.write_memory(0, 0)
        self.assertTrue(chip.changed)


class TestClear(unittest.TestCase):
    """What a change of video mode does to the card."""

    def dirty_card(self):
        chip = Vga()
        for plane in chip.planes:
            plane[0] = plane[-1] = plane[1234] = 0xAA
        configure(chip, map_mask=1, bit_mask=3, mode=2, set_reset=5)
        chip.latches = [9, 8, 7, 6]
        chip.changed = False
        return chip

    def test_the_planes_the_registers_and_the_latches_start_again(self):
        chip = self.dirty_card()
        chip.clear()
        self.assertEqual([(plane[0], plane[-1], plane[1234]) for plane in chip.planes], [(0, 0, 0)] * 4)
        self.assertEqual((chip.latches, chip.changed), ([0, 0, 0, 0], True))
        for index_port, index, value in ((0x3C4, 2, 0x0F), (0x3CE, 8, 0xFF), (0x3CE, 7, 0x0F), (0x3CE, 5, 0),
                                         (0x3CE, 0, 0)):
            chip.write(index_port, index)
            self.assertEqual(chip.read(index_port + 1), value, f"{index_port:X} register {index}")

    def test_keeping_the_planes_clears_the_rest_only(self):
        chip = self.dirty_card()
        chip.clear(keep_planes=True)
        self.assertEqual([plane[1234] for plane in chip.planes], [0xAA] * 4)
        self.assertEqual((chip.latches, chip.changed, chip.sequencer(2)), ([0, 0, 0, 0], True, 0x0F))

    def test_the_planes_stay_the_same_objects_so_whoever_holds_them_sees_the_change(self):
        chip = Vga()
        held = chip.planes[0]
        chip.write_memory(0, 0xFF)
        chip.clear()
        self.assertIs(chip.planes[0], held)
        self.assertEqual(held[0], 0)
        self.assertEqual(len(held), 0x10000)


def set_color(chip, index, red, green, blue):
    """What a program does to the ports to set a color of the DAC."""
    chip.write(0x3C8, index)
    for part in (red, green, blue):
        chip.write(0x3C9, part)


class TestTheDac(unittest.TestCase):
    def test_the_colors_start_as_the_16_of_the_ega(self):
        self.assertEqual(Vga().colors(), EGA_COLORS)

    def test_three_bytes_make_a_color_and_the_address_goes_up_by_itself(self):
        chip = Vga()
        chip.write(0x3C8, 100)
        for part in (10, 20, 30, 11, 21, 31):
            chip.write(0x3C9, part)
        self.assertEqual((chip.get_dac(100), chip.get_dac(101)), ((10, 20, 30), (11, 21, 31)))
        self.assertEqual(chip.read(0x3C8), 102)

    def test_a_color_is_not_there_until_its_third_byte(self):
        chip = Vga()
        chip.write(0x3C8, 100)
        chip.write(0x3C9, 1)
        chip.write(0x3C9, 2)
        self.assertEqual(chip.get_dac(100), (0, 0, 0))

    def test_a_new_address_forgets_the_bytes_of_a_color_that_was_not_finished(self):
        chip = Vga()
        chip.write(0x3C8, 100)
        chip.write(0x3C9, 1)
        chip.write(0x3C9, 2)
        chip.write(0x3C8, 100)
        for part in (7, 8, 9):
            chip.write(0x3C9, part)
        self.assertEqual((chip.get_dac(100), chip.get_dac(101)), ((7, 8, 9), (0, 0, 0)))

    def test_only_6_bits_of_a_byte_are_kept(self):
        chip = Vga()
        set_color(chip, 5, 0xFF, 0x40, 0x7F)
        self.assertEqual(chip.get_dac(5), (0x3F, 0x00, 0x3F))

    def test_the_address_comes_round_after_255(self):
        chip = Vga()
        set_color(chip, 255, 1, 2, 3)
        for part in (4, 5, 6):
            chip.write(0x3C9, part)
        self.assertEqual((chip.get_dac(255), chip.get_dac(0)), ((1, 2, 3), (4, 5, 6)))
        self.assertEqual(chip.read(0x3C8), 1)

    def test_a_color_is_read_back_red_green_blue_and_then_the_next(self):
        chip = Vga()
        set_color(chip, 40, 1, 2, 3)
        set_color(chip, 41, 4, 5, 6)
        chip.write(0x3C7, 40)
        self.assertEqual([chip.read(0x3C9) for _ in range(6)], [1, 2, 3, 4, 5, 6])

    def test_reading_goes_round_after_255_and_a_new_read_address_starts_at_red_again(self):
        chip = Vga()
        set_color(chip, 255, 9, 8, 7)
        chip.write(0x3C7, 255)
        self.assertEqual([chip.read(0x3C9) for _ in range(3)], [9, 8, 7])
        self.assertEqual(chip.read(0x3C9), chip.get_dac(0)[0])
        chip.write(0x3C7, 255)
        chip.read(0x3C9)
        chip.write(0x3C7, 255)  # in the middle of a color: start it again
        self.assertEqual(chip.read(0x3C9), 9)

    def test_reading_does_not_move_the_write_address_and_writing_does_not_move_the_read_address(self):
        chip = Vga()
        chip.write(0x3C7, 10)
        set_color(chip, 20, 1, 2, 3)
        self.assertEqual(chip.read(0x3C9), chip.get_dac(10)[0])
        self.assertEqual(chip.read(0x3C8), 21)

    def test_port_3c7_says_whether_the_dac_was_last_told_to_read_or_to_write(self):
        chip = Vga()
        self.assertEqual(chip.read(0x3C7), 0)
        chip.write(0x3C7, 5)
        self.assertEqual(chip.read(0x3C7), 3)
        chip.write(0x3C8, 5)
        self.assertEqual(chip.read(0x3C7), 0)

    def test_the_pel_mask_starts_at_ff_and_is_read_back(self):
        chip = Vga()
        self.assertEqual(chip.read(0x3C6), 0xFF)
        chip.write(0x3C6, 0x0F)
        self.assertEqual(chip.read(0x3C6), 0x0F)

    def test_the_pel_mask_cuts_the_index_of_a_color_before_it_is_looked_up(self):
        chip = Vga()
        set_color(chip, 0x08, 10, 20, 30)
        chip.write(0x3C6, 0x0F)  # the color 0x38 is now the color 0x08
        self.assertEqual(chip.colors()[8], (10 << 2 | 10 >> 4, 20 << 2 | 20 >> 4, 30 << 2 | 30 >> 4))

    def test_a_color_is_widened_from_6_bits_to_8(self):
        chip = Vga()
        for six, eight in ((0, 0), (1, 4), (2, 8), (32, 130), (62, 251), (63, 255)):
            set_color(chip, 0x38 + 7, six, six, six)  # the color 15 of a pixel
            self.assertEqual(chip.colors()[15], (eight,) * 3, six)

    def test_the_parts_of_a_color_keep_their_places_in_the_color(self):
        chip = Vga()
        set_color(chip, 0x38 + 7, 63, 0, 32)
        self.assertEqual(chip.colors()[15], (255, 0, 130))

    def test_a_dac_change_marks_the_picture_as_changed_and_an_address_change_does_not(self):
        chip = Vga()
        chip.write(0x3C8, 5)
        chip.write(0x3C7, 5)
        chip.write(0x3C9, 1)
        chip.write(0x3C9, 2)
        self.assertFalse(chip.changed)
        chip.write(0x3C9, 3)
        self.assertTrue(chip.changed)

    def test_the_mask_marks_the_picture_as_changed(self):
        chip = Vga()
        chip.write(0x3C6, 0x7F)
        self.assertTrue(chip.changed)


class TestThePaletteRegisters(unittest.TestCase):
    def test_the_16_registers_start_as_the_bios_leaves_them(self):
        chip = Vga()
        self.assertEqual([chip.palette_register(n) for n in range(16)],
                         [0, 1, 2, 3, 4, 5, 0x14, 7, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E, 0x3F])

    def test_the_port_takes_an_index_and_then_a_value_and_then_an_index_again(self):
        chip = Vga()
        chip.write(0x3C0, 3)
        chip.write(0x3C0, 0x2A)
        chip.write(0x3C0, 4)
        chip.write(0x3C0, 0x15)
        self.assertEqual((chip.palette_register(3), chip.palette_register(4)), (0x2A, 0x15))

    def test_reading_the_status_port_sets_it_back_to_an_index(self):
        chip = Vga()
        chip.write(0x3C0, 3)  # an index: the next byte would be its value
        chip.read(0x3DA)
        chip.write(0x3C0, 5)  # so this is an index again
        chip.write(0x3C0, 0x2A)
        self.assertEqual((chip.palette_register(3), chip.palette_register(5)), (3, 0x2A))

    def test_the_index_has_5_bits_and_bit_5_is_not_part_of_it(self):
        chip = Vga()
        chip.write(0x3C0, 0x20 | 3)  # the palette is the screen's: bit 5 set, as a program does when it is done
        chip.write(0x3C0, 0x2A)
        self.assertEqual(chip.palette_register(3), 0x2A)
        self.assertEqual(chip.read(0x3C0), 0x03)

    def test_the_value_is_read_back_through_3c1(self):
        chip = Vga()
        chip.write(0x3C0, 6)
        self.assertEqual(chip.read(0x3C1), 0x14)
        chip.read(0x3DA)
        chip.write(0x3C0, 6)
        chip.write(0x3C0, 0x2A)
        chip.read(0x3DA)
        chip.write(0x3C0, 6)
        self.assertEqual(chip.read(0x3C1), 0x2A)

    def test_registers_past_the_last_are_ignored_and_read_0(self):
        chip = Vga()
        for index in (0x15, 0x1F):
            chip.write(0x3C0, index)
            chip.write(0x3C0, 0x55)
            self.assertEqual(chip.read(0x3C1), 0)
        self.assertEqual([chip.palette_register(n) for n in range(21)], DEFAULT_ATTRIBUTES)

    def test_a_palette_register_says_which_color_of_the_dac_a_pixel_color_is(self):
        chip = Vga()
        set_color(chip, 0x20, 63, 0, 0)
        chip.set_palette_register(1, 0x20)
        self.assertEqual(chip.colors()[1], (255, 0, 0))
        self.assertEqual(chip.colors()[2], EGA_COLORS[2])  # the others are as they were

    def test_the_color_select_register_gives_bits_6_and_7_of_the_color_of_the_dac(self):
        chip = Vga()
        set_color(chip, 0x45, 63, 63, 0)
        chip.set_palette_register(1, 0x05)
        chip.set_palette_register(0x14, 0x04)  # bit 2 of it is bit 6 of the number
        self.assertEqual(chip.colors()[1], (255, 255, 0))
        chip.set_palette_register(0x14, 0x03)  # bits 0 and 1 of it are not used here
        self.assertEqual(chip.colors()[1], EGA_COLORS[5])

    def test_with_bit_7_of_the_mode_control_register_the_color_select_gives_bits_4_to_7(self):
        chip = Vga()
        set_color(chip, 0x25, 0, 63, 63)
        chip.set_palette_register(1, 0x35)  # bits 4 and 5 of this one are not used now
        chip.set_palette_register(0x10, 0x81)
        chip.set_palette_register(0x14, 0x02)  # bit 1 of it is bit 5 of the number
        self.assertEqual(chip.colors()[1], (0, 255, 255))

    def test_with_bit_7_of_the_mode_control_register_all_four_bits_of_the_color_select_count(self):
        chip = Vga()
        set_color(chip, 0xC5, 63, 0, 63)
        chip.set_palette_register(1, 0x05)
        chip.set_palette_register(0x10, 0x81)
        chip.set_palette_register(0x14, 0x0C)  # bits 2 and 3 of it are bits 6 and 7 of the number
        self.assertEqual(chip.colors()[1], (255, 0, 255))

    def test_the_other_attribute_registers_start_as_the_bios_leaves_them(self):
        chip = Vga()
        self.assertEqual([chip.palette_register(n) for n in range(0x10, 0x15)], [0x01, 0x00, 0x0F, 0x00, 0x00])

    def test_setting_a_palette_register_marks_the_picture_as_changed(self):
        chip = Vga()
        chip.set_palette_register(2, 0x30)
        self.assertTrue(chip.changed)

    def test_the_overscan_register_is_kept_and_does_nothing_to_the_colors(self):
        chip = Vga()
        chip.set_palette_register(0x11, 0x2A)
        self.assertEqual((chip.palette_register(0x11), chip.colors()), (0x2A, EGA_COLORS))

    def test_a_write_of_a_value_marks_the_picture_as_changed_and_an_index_does_not(self):
        chip = Vga()
        chip.write(0x3C0, 3)
        self.assertFalse(chip.changed)
        chip.write(0x3C0, 7)
        self.assertTrue(chip.changed)

    def test_a_mode_set_brings_all_the_colors_back(self):
        chip = Vga()
        set_color(chip, 0x38, 1, 2, 3)
        chip.write(0x3C6, 0x0F)
        chip.set_palette_register(2, 0x30)
        chip.write(0x3C0, 3)  # in the middle of an index and a value
        chip.write(0x3C7, 9)
        chip.read(0x3C9)
        chip.clear()
        self.assertEqual(chip.colors(), EGA_COLORS)
        self.assertEqual((chip.read(0x3C6), chip.read(0x3C7), chip.read(0x3C8)), (0xFF, 0, 0))
        self.assertEqual(chip.read(0x3C9), chip.get_dac(0)[0])  # the read address is 0 again, at red
        chip.write(0x3C0, 5)  # an index, not the value of the 3
        self.assertEqual(chip.read(0x3C0), 5)  # and port 3C0h gives the index back
        chip.write(0x3C0, 5)
        self.assertEqual(chip.palette_register(5), 5)
        self.assertEqual([chip.palette_register(n) for n in (0, 3)], [0, 3])

    def test_a_mode_set_that_keeps_the_picture_brings_the_colors_back_too(self):
        chip = Vga()
        chip.set_palette_register(2, 0x30)
        chip.clear(keep_planes=True)
        self.assertEqual(chip.colors(), EGA_COLORS)


class TestReadModes(unittest.TestCase):
    def setUp(self):
        self.chip = Vga()
        for plane, value in enumerate((0b11110000, 0b11001100, 0b10101010, 0b00001111)):
            self.chip.planes[plane][7] = value

    def test_mode_0_reads_the_plane_the_read_map_select_names(self):
        for plane, value in enumerate((0b11110000, 0b11001100, 0b10101010, 0b00001111)):
            configure(self.chip, read_map=plane)
            self.assertEqual(self.chip.read_memory(7), value)

    def test_every_read_fills_the_latches_from_all_four_planes(self):
        configure(self.chip, read_map=2)
        self.chip.read_memory(7)
        self.assertEqual(self.chip.latches, [0b11110000, 0b11001100, 0b10101010, 0b00001111])
        self.chip.read_memory(8)
        self.assertEqual(self.chip.latches, [0, 0, 0, 0])

    def test_mode_1_says_which_pixels_are_of_the_color_asked_for(self):
        configure(self.chip, mode=8, care=0x0F)
        for color in range(16):
            configure(self.chip, compare=color)
            expected = 0
            for pixel in range(8):  # pixel n is bit n of every plane
                pixel_color = sum(bit(self.chip.planes[p][7], pixel) << p for p in range(4))
                expected |= (pixel_color == color) << pixel
            self.assertEqual(self.chip.read_memory(7), expected, f"color {color}")

    def test_mode_1_skips_the_planes_the_color_dont_care_leaves_out(self):
        configure(self.chip, mode=8, care=0b0001, compare=0b0001)  # only plane 0 counts: its set bits
        self.assertEqual(self.chip.read_memory(7), 0b11110000)
        configure(self.chip, compare=0b0000)
        self.assertEqual(self.chip.read_memory(7), 0b00001111)
        configure(self.chip, care=0)  # nothing counts: every pixel matches
        self.assertEqual(self.chip.read_memory(7), 0xFF)


class TestAgainstTheReference(unittest.TestCase):
    """Random registers and memory: the card and the one-pixel-at-a-time model must always agree."""

    def test_writes_and_reads_agree_in_every_mode(self):
        rng = random.Random(1997)
        for trial in range(2000):
            chip = Vga()
            for plane in chip.planes:
                plane[10] = rng.randrange(256)
                plane[11] = rng.randrange(256)
            registers = {"map_mask": rng.randrange(16), "set_reset": rng.randrange(16), "enable": rng.randrange(16),
                         "compare": rng.randrange(16), "rotate": rng.randrange(8), "function": rng.randrange(4),
                         "read_map": rng.randrange(4), "write_mode": rng.randrange(4), "read_mode": rng.randrange(2),
                         "care": rng.randrange(16), "bit_mask": rng.randrange(256)}
            configure(chip, map_mask=registers["map_mask"], set_reset=registers["set_reset"],
                      enable=registers["enable"], compare=registers["compare"],
                      rotate_function=registers["rotate"] | registers["function"] << 3,
                      read_map=registers["read_map"], mode=registers["write_mode"] | registers["read_mode"] << 3,
                      care=registers["care"], bit_mask=registers["bit_mask"])
            reference = Reference(chip.planes, [0, 0, 0, 0], registers)
            for step in range(6):
                offset = rng.choice((10, 11))
                if rng.random() < 0.4:
                    self.assertEqual(chip.read_memory(offset), reference.read(offset), f"trial {trial} step {step}")
                else:
                    value = rng.randrange(256)
                    chip.write_memory(offset, value)
                    reference.write(offset, value)
                self.assertEqual([list(plane[10:12]) for plane in chip.planes],
                                 [plane[10:12] for plane in reference.planes], f"trial {trial} step {step}")
                self.assertEqual(chip.latches, reference.latches, f"trial {trial} step {step}")


class TestTheWindowInTheCpu(unittest.TestCase):
    def test_the_window_is_exactly_a0000_to_affff(self):
        cpu = machine()
        chip = cpu.video
        cpu.wb(0x9FFF, 0x000F, 0x11)  # the last byte below: plain memory
        cpu.wb(0xA000, 0x0000, 0x22)  # the first inside
        cpu.wb(0xA000, 0xFFFF, 0x33)  # the last inside
        cpu.wb(0xB000, 0x0000, 0x44)  # the first above: plain memory
        self.assertEqual((cpu.mem[0x9FFFF], cpu.mem[0xB0000]), (0x11, 0x44))
        # the plain memory behind the window is left alone
        self.assertEqual((cpu.mem[0xA0000], cpu.mem[0xAFFFF]), (0, 0))
        self.assertEqual((chip.planes[0][0], chip.planes[3][0xFFFF]), (0x22, 0x33))
        self.assertEqual((cpu.rb(0x9FFF, 0x000F), cpu.rb(0xB000, 0x0000)), (0x11, 0x44))

    def test_any_segment_and_offset_that_make_the_same_address_reach_the_same_byte(self):
        cpu = machine()
        cpu.wb(0xA000, 0x0123, 0x5A)
        self.assertEqual(cpu.rb(0x9FF0, 0x0123 + 0x100), 0x5A)

    def test_a_word_is_two_bytes_one_after_the_other(self):
        cpu = machine()
        cpu.ww(0xA000, 0x0010, 0xBEEF)
        self.assertEqual((cpu.video.planes[0][0x10], cpu.video.planes[0][0x11]), (0xEF, 0xBE))
        self.assertEqual(cpu.rw(0xA000, 0x0010), 0xBEEF)

    def test_without_a_card_the_window_is_plain_memory(self):
        cpu = CPU()
        cpu.wb(0xA000, 0x0010, 0x77)
        self.assertEqual((cpu.mem[0xA0010], cpu.rb(0xA000, 0x0010)), (0x77, 0x77))

    def test_rep_movsb_inside_the_window_in_mode_1_copies_all_four_planes(self):
        cpu = machine()
        chip = cpu.video
        for plane in range(4):
            for i in range(16):
                chip.planes[plane][0x100 + i] = plane * 16 + i + 1
        configure(chip, mode=1)
        cpu.mem[0x10000:0x10002] = bytes([0xF3, 0xA4])  # rep movsb
        cpu.s[CPU.CS], cpu.s[CPU.DS], cpu.s[CPU.ES] = 0x1000, 0xA000, 0xA000
        cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.r[CPU.CX] = 0x0100, 0x2000, 16
        cpu.step()
        for plane in range(4):
            self.assertEqual(chip.planes[plane][0x2000:0x2010], chip.planes[plane][0x0100:0x0110], plane)
        self.assertEqual(chip.planes[3][0x2000], 3 * 16 + 1)

    def test_a_read_modify_write_instruction_reads_first_so_the_latches_are_filled(self):
        cpu = machine()
        cpu.video.planes[0][0x40] = 0x0F
        configure(cpu.video, enable=0x0F, set_reset=0x01, bit_mask=0x3C)
        cpu.mem[0x10000:0x10004] = bytes([0x8A, 0x07, 0x88, 0x07])  # mov al,[bx] ; mov [bx],al
        cpu.s[CPU.CS], cpu.s[CPU.DS] = 0x1000, 0xA000
        cpu.r[CPU.BX] = 0x40
        cpu.step()
        cpu.step()
        self.assertEqual(cpu.video.planes[0][0x40], 0x3C | 0x03)  # the mask's bits set, the others as they were
        self.assertEqual(cpu.video.planes[1][0x40], 0x00)


def mode_13h():
    chip = Vga()
    chip.clear()
    chip.set_mode_13h()
    return chip


class TestMode13h(unittest.TestCase):
    """256 colors, a byte a pixel, the planes taken in turn: the chain-4 mode of the BIOS."""

    def test_the_two_low_bits_of_an_address_are_the_plane_and_the_rest_the_place_in_it(self):
        chip = mode_13h()
        for address in (0, 1, 2, 3, 4, 5, 0x1234, 63999, 0xFFFF):
            chip.write_memory(address, address & 0xFF ^ 0x5A)
            self.assertEqual(chip.planes[address & 3][address >> 2], address & 0xFF ^ 0x5A, hex(address))
            self.assertEqual(chip.read_memory(address), address & 0xFF ^ 0x5A, hex(address))

    def test_a_byte_goes_to_one_plane_whatever_the_other_registers_say(self):
        chip = mode_13h()
        chip.registers[vga.SEQUENCER, 2] = 0x01  # a map mask that allows plane 0 only
        chip.registers[vga.GRAPHICS, 3] = 0x18  # XOR with the latch
        chip.registers[vga.GRAPHICS, 8] = 0x0F  # and a bit mask
        chip.latches = [0xFF] * 4
        chip.write_memory(3, 0xC3)
        self.assertEqual([plane[0] for plane in chip.planes], [0, 0, 0, 0xC3])

    def test_a_write_says_the_screen_changed(self):
        chip = mode_13h()
        chip.changed = False
        chip.write_memory(10, 1)
        self.assertTrue(chip.changed)

    def test_the_registers_the_bios_leaves(self):
        chip = mode_13h()
        self.assertTrue(chip.mode_256())
        self.assertTrue(chip.chain_4())
        self.assertEqual(chip.attributes[vga.MODE_CONTROL], 0x41)
        self.assertEqual(chip.sequencer(2), 0x0F)  # the map mask is still the one of every graphics mode

    def test_the_first_32_colors_are_those_of_the_ega_and_the_grays_and_the_rest_are_black(self):
        chip = mode_13h()
        self.assertEqual([chip.get_dac(n) for n in range(16)], [tuple(color) for color in vga.CGA_COLORS])
        self.assertEqual([chip.get_dac(16 + n) for n in range(16)], [(g, g, g) for g in vga.GRAYS])
        self.assertEqual(chip.get_dac(6), (42, 21, 0))
        self.assertEqual({chip.get_dac(n) for n in range(32, 256)}, {(0, 0, 0)})

    def test_the_colors_are_the_dac_in_8_bits_and_the_mask_is_on_the_number(self):
        chip = mode_13h()
        chip.set_dac(0x85, 63, 32, 0)
        colors = chip.colors256()
        self.assertEqual((len(colors), colors[0x85], colors[15]), (256, (255, 130, 0), (255, 255, 255)))
        chip.write(vga.PEL_MASK_PORT, 0x0F)
        self.assertEqual(chip.colors256()[0x85], chip.colors256()[5])  # 85h and 0Fh = 05h

    def test_another_mode_set_takes_the_registers_back(self):
        chip = mode_13h()
        chip.clear()
        self.assertFalse(chip.mode_256() or chip.chain_4())
        self.assertEqual(chip.attributes[vga.MODE_CONTROL], DEFAULT_ATTRIBUTES[vga.MODE_CONTROL])

    def test_without_the_chain_the_planes_and_the_write_modes_work_as_in_16_colors(self):
        chip = mode_13h()
        chip.registers[vga.SEQUENCER, 4] = 0x06  # mode X: no chain-4, no odd/even
        chip.registers[vga.SEQUENCER, 2] = 0x05  # planes 0 and 2
        chip.write_memory(7, 0x99)
        self.assertEqual([plane[7] for plane in chip.planes], [0x99, 0, 0x99, 0])
        self.assertEqual(chip.read_memory(7), 0x99)
        self.assertTrue(chip.mode_256())  # and the screen is still of 8-bit pixels

    def test_the_graphics_controller_bit_alone_does_not_stop_the_card(self):
        chip = Vga()
        chip.registers[vga.GRAPHICS, 5] = 0x40
        chip.read_memory(0)
        chip.write_memory(0, 1)


class TestWhatTheCardDoesNotKnow(unittest.TestCase):
    def test_modes_with_odd_even_or_interleaved_shift_stop_the_emulator(self):
        for mode in (0x10, 0x20, 0x30, 0x50, 0x70):
            chip = Vga()
            configure(chip, mode=mode)
            with self.assertRaises(NotImplementedError, msg=hex(mode)):
                chip.read_memory(0)
            with self.assertRaises(NotImplementedError, msg=hex(mode)):
                chip.write_memory(0, 0)

    def test_the_bits_of_the_mode_register_that_are_known_do_not_stop_it(self):
        chip = Vga()
        for mode in (0x00, 0x01, 0x02, 0x03, 0x08, 0x0B, 0x40, 0x43):
            configure(chip, mode=mode)
            chip.read_memory(0)
            chip.write_memory(0, 0)


if __name__ == "__main__":
    unittest.main()
