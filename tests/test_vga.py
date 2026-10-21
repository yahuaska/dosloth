import random
import unittest

from mydos import vga
from mydos.cpu import CPU
from mydos.vga import Vga


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
        for port in (0x3C0, 0x3C2, 0x3C6, 0x3C8, 0x3C9):
            chip.write(port, 0x11)
            chip.write(port, 0x22)
            self.assertEqual(chip.read(port), 0x22, hex(port))

    def test_a_port_never_written_reads_0(self):
        chip = Vga()
        self.assertEqual([chip.read(port) for port in (0x3C0, 0x3C9, 0x3CC)], [0, 0, 0])

    def test_the_ports_are_kept_apart(self):
        chip = Vga()
        chip.write(0x3C8, 1)
        chip.write(0x3C9, 2)
        self.assertEqual((chip.read(0x3C8), chip.read(0x3C9)), (1, 2))

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


class TestWhatTheCardDoesNotKnow(unittest.TestCase):
    def test_modes_with_odd_even_interleaved_or_256_colors_stop_the_emulator(self):
        for mode in (0x10, 0x20, 0x40, 0x50):
            chip = Vga()
            configure(chip, mode=mode)
            with self.assertRaises(NotImplementedError, msg=hex(mode)):
                chip.read_memory(0)
            with self.assertRaises(NotImplementedError, msg=hex(mode)):
                chip.write_memory(0, 0)

    def test_the_bits_of_the_mode_register_that_are_known_do_not_stop_it(self):
        chip = Vga()
        for mode in (0x00, 0x01, 0x02, 0x03, 0x08, 0x0B):
            configure(chip, mode=mode)
            chip.read_memory(0)
            chip.write_memory(0, 0)


if __name__ == "__main__":
    unittest.main()
