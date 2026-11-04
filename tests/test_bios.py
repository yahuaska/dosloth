import tomllib
import unittest
from pathlib import Path

from dosloth import bios, config, flags, vga
from dosloth.cpu import CPU


class TestKeyboard(unittest.TestCase):
    """INT 16h AH=00h (wait for a key), 01h (is there a key?), 02h (shift keys)."""

    def setUp(self):
        self.cpu = CPU()
        self.keyboard = bios.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x16])

    def call(self, ax, bx=0x3333, cx=0x2222, dx=0x1111, zf=False):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.set_flag(flags.ZF, zf)
        cpu.ip = 0
        cpu.step()

    def test_with_no_key_pressed_ah_01_says_so_with_zf_and_leaves_ax_alone(self):
        self.call(0x0100)
        self.assertTrue(self.cpu.get_flag(flags.ZF))
        self.assertEqual(self.cpu.r[CPU.AX], 0x0100)

    def test_with_a_key_waiting_ah_01_clears_zf_and_gives_it_without_taking_it(self):
        self.keyboard.press(0x1E, 0x61)  # the A key
        for _ in range(2):  # asking twice gives the same answer: the key stays
            self.call(0x0100, zf=True)
            self.assertFalse(self.cpu.get_flag(flags.ZF))
            self.assertEqual(self.cpu.r[CPU.AX], 0x1E61)

    def test_ah_01_shows_the_oldest_of_several_keys(self):
        self.keyboard.press(0x1E, 0x61)
        self.keyboard.press(0x30, 0x62)
        self.call(0x0100)
        self.assertEqual(self.cpu.r[CPU.AX], 0x1E61)

    def test_ah_00_takes_the_oldest_key_and_the_next_one_comes_after(self):
        self.keyboard.press(0x1E, 0x61)
        self.keyboard.press(0x1C, 0x0D)  # Enter
        self.keyboard.press(0x48, 0x00)  # an arrow: no ASCII code, only the scan code
        for key in (0x1E61, 0x1C0D, 0x4800):
            self.call(0x0000)
            self.assertEqual(self.cpu.r[CPU.AX], key)
        self.call(0x0100)
        self.assertTrue(self.cpu.get_flag(flags.ZF))  # all taken

    def test_after_ah_00_the_key_is_no_longer_there(self):
        self.keyboard.press(0x1E, 0x61)
        self.call(0x0000)
        self.call(0x0100, zf=False)
        self.assertTrue(self.cpu.get_flag(flags.ZF))

    def test_the_buffer_holds_15_keys_and_loses_the_ones_after_them(self):
        for number in range(20):
            self.keyboard.press(0x02, 0x30 + number)
        self.assertEqual(len(self.keyboard.buffer), 15)
        self.call(0x0000)
        self.assertEqual(self.cpu.r[CPU.AX], 0x0230)  # the first is the first
        for number in range(1, 15):
            self.call(0x0000)
        self.assertEqual(self.cpu.r[CPU.AX], 0x0230 + 14)  # the 15th; the five after it are gone
        self.call(0x0100)
        self.assertTrue(self.cpu.get_flag(flags.ZF))

    def test_a_freed_place_takes_the_next_key(self):
        for _ in range(15):
            self.keyboard.press(0x1E, 0x61)
        self.call(0x0000)
        self.keyboard.press(0x30, 0x62)
        self.assertEqual(len(self.keyboard.buffer), 15)
        self.assertEqual(self.keyboard.buffer[-1], 0x3062)

    def test_ah_00_waits_through_the_waiter_when_there_is_one(self):
        calls = []

        def waiter():
            calls.append(len(self.keyboard.buffer))
            self.keyboard.press(0x1C, 0x0D)  # the key comes while it waits

        self.keyboard.waiter = waiter
        self.call(0x0000)
        self.assertEqual((calls, self.cpu.r[CPU.AX]), ([0], 0x1C0D))

    def test_the_waiter_is_not_called_when_a_key_is_already_there(self):
        self.keyboard.waiter = lambda: self.fail("it should not wait")
        self.keyboard.press(0x1E, 0x61)
        self.call(0x0000)
        self.assertEqual(self.cpu.r[CPU.AX], 0x1E61)

    def test_ah_01_never_waits(self):
        self.keyboard.waiter = lambda: self.fail("it should not wait")
        self.call(0x0100)
        self.assertTrue(self.cpu.get_flag(flags.ZF))

    def test_ah_00_with_no_key_stops_the_emulator_instead_of_waiting_for_ever(self):
        with self.assertRaises(NotImplementedError):
            self.call(0x0000)

    def test_ah_02_says_no_shift_key_is_held_and_changes_only_al(self):
        self.call(0x02FF)
        self.assertEqual(self.cpu.r[CPU.AX], 0x0200)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x3333, 0x2222, 0x1111))

    def test_the_other_registers_are_left_alone_by_ah_01(self):
        self.keyboard.press(0x1E, 0x61)
        self.call(0x0100)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x3333, 0x2222, 0x1111))

    def test_the_other_services_are_not_done_yet(self):
        for ah in (0x03, 0x05, 0x10, 0x11, 0x12, 0xFF):
            with self.assertRaises(NotImplementedError):
                self.call(ah << 8)

    def test_dos_reaches_the_keyboard_through_the_cpu(self):
        self.assertIs(self.cpu.keyboard, self.keyboard)
        self.assertIsNone(CPU().keyboard)

    def test_each_cpu_has_its_own_keys(self):
        other = CPU()
        bios.install(other)
        self.keyboard.press(0x1E, 0x61)
        other.s[CPU.CS] = 0x1000
        other.mem[0x10000:0x10002] = bytes([0xCD, 0x16])
        other.r[CPU.AX] = 0x0100
        other.step()
        self.assertTrue(other.get_flag(flags.ZF))


class TestVideoBios(unittest.TestCase):
    """INT 10h AH=1Ah AL=00h: what video cards and monitors are there?"""

    def setUp(self):
        self.cpu = CPU()
        bios.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x10])

    def call(self, ax, bx=0x3333, cx=0x2222, dx=0x1111):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.ip = 0
        cpu.step()

    def test_it_says_the_call_is_known_and_the_display_is_a_vga_with_a_color_monitor(self):
        self.call(0x1A00)
        self.assertEqual(self.cpu.get8(0), 0x1A)  # AL: the function is there
        self.assertEqual(self.cpu.get8(3), 0x08)  # BL: VGA, color
        self.assertEqual(self.cpu.get8(7), 0x00)  # BH: no second display

    def test_nothing_else_changes(self):
        self.cpu.set_flag(flags.CF, True)
        self.cpu.set_flag(flags.ZF, False)
        self.call(0x1A00)
        cpu = self.cpu
        self.assertEqual((cpu.get8(4), cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x1A, 0x2222, 0x1111))  # AH stays too
        self.assertTrue(cpu.get_flag(flags.CF))
        self.assertFalse(cpu.get_flag(flags.ZF))
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_the_old_values_of_al_bl_and_bh_do_not_matter(self):
        for ax, bx in ((0x1A00, 0x0000), (0x1A00, 0xFFFF), (0x1A00, 0x5A5A)):
            self.call(ax, bx=bx)
            self.assertEqual((self.cpu.get8(0), self.cpu.r[CPU.BX]), (0x1A, 0x0008))

    def test_the_other_functions_are_not_done_yet(self):
        for ax in (0x1A01, 0x1AFF, 0x1200, 0x1100, 0x0200, 0x0900, 0xFF00):
            with self.assertRaises(NotImplementedError, msg=hex(ax)):
                self.call(ax)


class TestVideoMode(unittest.TestCase):
    """INT 10h AH=0Fh (what mode?) and AH=00h (set the mode)."""

    def setUp(self):
        self.cpu = CPU()
        bios.install(self.cpu)
        self.card = vga.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x10])

    def call(self, ax, bx=0x3333, cx=0x2222, dx=0x1111):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.ip = 0
        cpu.step()

    def mode(self):
        self.call(0x0F00)
        return self.cpu.get8(0), self.cpu.get8(4), self.cpu.get8(7)

    def test_the_machine_starts_in_text_mode_80_columns_page_0(self):
        self.assertEqual(self.mode(), (0x03, 80, 0))

    def test_the_state_is_in_the_bios_data_area_where_programs_look(self):
        self.assertEqual(self.cpu.rb(0x40, 0x49), 0x03)
        self.assertEqual(self.cpu.rw(0x40, 0x4A), 80)
        self.assertEqual(self.cpu.rb(0x40, 0x62), 0)

    def test_get_mode_changes_only_al_ah_and_bh(self):
        self.cpu.set_flag(flags.CF, True)
        self.call(0x0F00, bx=0x3377)
        cpu = self.cpu
        self.assertEqual((cpu.get8(3), cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x77, 0x2222, 0x1111))  # BL stays
        self.assertTrue(cpu.get_flag(flags.CF))
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_the_old_al_does_not_matter_to_get_mode(self):
        for ax in (0x0F00, 0x0F08, 0x0FFF):
            self.call(ax)
            self.assertEqual(self.cpu.get8(0), 0x03)

    def test_mode_12h_is_the_640_by_480_graphics_mode_and_is_remembered(self):
        self.call(0x0012)
        self.assertEqual(self.mode(), (0x12, 80, 0))
        self.assertEqual(self.cpu.rb(0x40, 0x49), 0x12)

    def test_a_mode_set_puts_the_columns_and_the_page_right(self):
        self.cpu.mem[0x44A:0x44C] = (40).to_bytes(2, "little")  # as if some other mode had changed them
        self.cpu.mem[0x462] = 3
        self.call(0x0012)
        self.assertEqual(self.mode(), (0x12, 80, 0))
        self.assertEqual(self.cpu.rw(0x40, 0x4A), 80)

    def test_mode_13h_is_40_columns_of_8_scan_lines_with_the_card_in_the_chain_4_mode(self):
        self.call(0x0013)
        self.assertEqual(self.mode(), (0x13, 40, 0))
        self.assertEqual(self.cpu.rw(0x40, 0x4A), 40)
        self.assertEqual((self.cpu.rb(0x40, 0x84), self.cpu.rw(0x40, 0x85)), (24, 8))
        self.assertTrue(self.card.mode_256() and self.card.chain_4())
        self.assertEqual(self.card.get_dac(15), (63, 63, 63))

    def test_mode_13h_clears_the_planes_and_bit_7_keeps_them(self):
        self.call(0x0013)
        self.card.write_memory(1234, 0x77)
        self.call(0x0093)
        self.assertEqual(self.card.read_memory(1234), 0x77)
        self.assertTrue(self.card.chain_4())
        self.call(0x0013)
        self.assertEqual(self.card.read_memory(1234), 0)

    def test_text_mode_after_mode_13h_takes_the_card_back(self):
        self.call(0x0013)
        self.call(0x0003)
        self.assertFalse(self.card.mode_256() or self.card.chain_4())
        self.assertEqual(self.mode(), (0x03, 80, 0))
        self.assertEqual(self.cpu.rw(0x40, 0x85), 16)

    def test_setting_the_mode_back_to_text_is_remembered_too(self):
        self.call(0x0012)
        self.call(0x0003)
        self.assertEqual(self.mode()[0], 0x03)

    def test_a_mode_set_clears_the_planes_and_sets_up_the_registers(self):
        for plane in self.card.planes:
            plane[100] = 0x5A
        self.card.write(0x3C4, 2)
        self.card.write(0x3C5, 0x01)  # a map mask that is not the one the BIOS leaves
        self.card.latches = [1, 2, 3, 4]
        self.card.changed = False
        self.call(0x0012)
        self.assertEqual([plane[100] for plane in self.card.planes], [0, 0, 0, 0])
        self.assertEqual(self.card.sequencer(2), 0x0F)
        self.assertEqual(self.card.latches, [0, 0, 0, 0])
        self.assertTrue(self.card.changed)  # the screen is to be shown again

    def test_the_whole_of_every_plane_is_cleared(self):
        for plane in self.card.planes:
            plane[0] = plane[-1] = 0xFF
        self.call(0x0012)
        self.assertEqual([(plane[0], plane[-1], any(plane)) for plane in self.card.planes], [(0, 0, False)] * 4)

    def test_bit_7_keeps_the_picture_but_not_the_registers(self):
        for plane in self.card.planes:
            plane[100] = 0x5A
        self.cpu.mem[0xB8000] = 0x41
        self.card.write(0x3C4, 2)
        self.card.write(0x3C5, 0x01)
        self.call(0x0092)  # 12h with bit 7
        self.assertEqual(self.mode()[0], 0x12)  # the mode is 12h, not 92h
        self.assertEqual([plane[100] for plane in self.card.planes], [0x5A] * 4)
        self.assertEqual(self.cpu.mem[0xB8000], 0x41)
        self.assertEqual(self.card.sequencer(2), 0x0F)

    def test_a_mode_set_fills_the_text_screen_with_light_grey_spaces(self):
        self.cpu.mem[0xB8000:0xB8010] = bytes(range(1, 17))
        self.cpu.mem[0xBFFFE:0xC0000] = b"\x41\x42"  # the end of the eight pages
        self.cpu.mem[0xC0000] = 0x99  # the next byte is not cleared
        self.call(0x0003)
        self.assertEqual(bytes(self.cpu.mem[0xB8000:0xB8006]), b" \x07 \x07 \x07")
        self.assertEqual(bytes(self.cpu.mem[0xBFFFE:0xC0000]), b" \x07")
        self.assertEqual(self.cpu.mem[0xC0000], 0x99)
        self.assertEqual(sum(1 for i in range(0xB8000, 0xC0000, 2) if self.cpu.mem[i] != 0x20), 0)
        self.assertEqual(sum(1 for i in range(0xB8001, 0xC0000, 2) if self.cpu.mem[i] != 0x07), 0)

    def test_other_modes_stop_the_emulator_and_change_nothing(self):
        self.call(0x0012)
        for mode in (0x00, 0x01, 0x02, 0x04, 0x05, 0x06, 0x07, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x14, 0x6A, 0x7F):
            with self.assertRaises(NotImplementedError, msg=hex(mode)):
                self.call(mode)
            with self.assertRaises(NotImplementedError, msg=hex(mode | 0x80)):
                self.call(mode | 0x80)
        self.assertEqual(self.mode()[0], 0x12)

    def test_without_a_video_card_a_mode_can_still_be_set(self):
        cpu = CPU()
        bios.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x10])
        cpu.r[CPU.AX] = 0x0012
        cpu.step()
        self.assertEqual(cpu.mem[0x449], 0x12)

    def test_set_mode_changes_no_register(self):
        self.cpu.set_flag(flags.CF, True)
        self.call(0x0012, bx=0x3377)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x0012, 0x3377, 0x2222, 0x1111))
        self.assertTrue(cpu.get_flag(flags.CF))

    def test_each_cpu_has_its_own_state(self):
        other = CPU()
        bios.install(other)
        self.call(0x0012)
        self.assertEqual(other.mem[0x449], 0x03)


class TestPaletteFunctions(unittest.TestCase):
    """INT 10h AH=10h: the colors of the screen."""

    def setUp(self):
        self.cpu = CPU()
        bios.install(self.cpu)
        self.card = vga.install(self.cpu)
        self.cpu.s[CPU.CS], self.cpu.s[CPU.ES] = 0x1000, 0x3000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x10])

    def call(self, al, bx=0x3333, cx=0x2222, dx=0x1111):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x1000 | al, bx, cx, dx
        cpu.ip = 0
        cpu.step()

    def table(self, offset, data):
        self.cpu.mem[0x30000 + offset:0x30000 + offset + len(data)] = bytes(data)

    def test_00_sets_one_palette_register_from_bl_and_bh(self):
        self.call(0x00, bx=0x2A03)
        self.assertEqual(self.card.palette_register(3), 0x2A)
        self.assertEqual(self.card.palette_register(2), 2)

    def test_00_marks_the_picture_as_changed(self):
        self.card.changed = False
        self.call(0x00, bx=0x2A03)
        self.assertTrue(self.card.changed)

    def test_00_takes_the_registers_0_to_15_only(self):
        for number in (0x10, 0x11, 0x14, 0xFF):
            with self.assertRaises(NotImplementedError, msg=hex(number)):
                self.call(0x00, bx=0x2A00 | number)
        self.call(0x00, bx=0x2A0F)
        self.assertEqual(self.card.palette_register(15), 0x2A)

    def test_01_sets_the_overscan_color_from_bh(self):
        self.call(0x01, bx=0x2B00)
        self.assertEqual(self.card.palette_register(0x11), 0x2B)

    def test_02_sets_all_16_and_the_overscan_from_17_bytes(self):
        self.table(0x0200, list(range(0x20, 0x31)))  # 17 bytes
        self.call(0x02, dx=0x0200)
        self.assertEqual([self.card.palette_register(n) for n in range(16)], list(range(0x20, 0x30)))
        self.assertEqual(self.card.palette_register(0x11), 0x30)
        self.assertEqual(self.card.palette_register(0x10), 0x01)  # the mode control register is not in the table

    def test_07_reads_one_palette_register_into_bh(self):
        self.card.set_palette_register(4, 0x2C)
        self.call(0x07, bx=0x9904)
        self.assertEqual((self.cpu.get8(7), self.cpu.get8(3)), (0x2C, 0x04))

    def test_08_reads_the_overscan_color_into_bh(self):
        self.card.set_palette_register(0x11, 0x2D)
        self.call(0x08, bx=0x9900)
        self.assertEqual(self.cpu.get8(7), 0x2D)

    def test_09_writes_the_16_registers_and_the_overscan_to_es_dx(self):
        self.card.set_palette_register(0x11, 0x2E)
        self.call(0x09, dx=0x0300)
        self.assertEqual(list(self.cpu.mem[0x30300:0x30311]), [0, 1, 2, 3, 4, 5, 0x14, 7] + list(range(0x38, 0x40)) +
                         [0x2E])
        self.assertEqual(self.cpu.mem[0x30311], 0)  # and nothing after them

    def test_10_sets_one_dac_color_from_dh_ch_cl(self):
        self.call(0x10, bx=0x0007, cx=0x2211, dx=0x3300 | 0x44)  # DH=33h, CH=22h, CL=11h; DL stays
        self.assertEqual(self.card.get_dac(7), (0x33, 0x22, 0x11))
        self.assertEqual(self.cpu.get8(2), 0x44)

    def test_10_takes_the_color_from_bl_and_wraps_a_bx_over_255(self):
        self.call(0x10, bx=0x0107, cx=0x0201, dx=0x0300)
        self.assertEqual(self.card.get_dac(7), (3, 2, 1))

    def test_10_keeps_only_6_bits(self):
        self.call(0x10, bx=7, cx=0x4080, dx=0xFF00)
        self.assertEqual(self.card.get_dac(7), (0x3F, 0x00, 0x00))

    def test_12_sets_a_block_from_a_table_of_red_green_blue(self):
        self.table(0x0400, [1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.call(0x12, bx=20, cx=3, dx=0x0400)
        self.assertEqual([self.card.get_dac(n) for n in (19, 20, 21, 22, 23)],
                         [(0, 0, 0), (1, 2, 3), (4, 5, 6), (7, 8, 9), (0, 0, 0)])

    def test_12_goes_round_from_255_to_0(self):
        self.table(0x0400, [1, 2, 3, 4, 5, 6])
        self.call(0x12, bx=255, cx=2, dx=0x0400)
        self.assertEqual((self.card.get_dac(255), self.card.get_dac(0), self.card.get_dac(1)),
                         ((1, 2, 3), (4, 5, 6), (0, 0, 42)))  # the third is the one that was there: blue

    def test_12_with_a_count_of_0_changes_nothing_and_a_count_of_256_changes_all(self):
        before = bytes(self.card.dac)
        self.call(0x12, bx=0, cx=0, dx=0x0400)
        self.assertEqual(bytes(self.card.dac), before)
        self.table(0x0400, [7] * 768)
        self.call(0x12, bx=0, cx=256, dx=0x0400)
        self.assertEqual(bytes(self.card.dac), bytes([7] * 768))

    def test_12_keeps_6_bits_and_reads_the_table_over_the_end_of_the_segment(self):
        self.cpu.mem[0x3FFFE:0x40000] = bytes([0xFF, 0x41])
        self.cpu.mem[0x30000] = 0x82  # the third byte of the color, past the end of the segment: back at 0
        self.call(0x12, bx=9, cx=1, dx=0xFFFE)
        self.assertEqual(self.card.get_dac(9), (0x3F, 0x01, 0x02))

    def test_15_reads_one_dac_color_into_dh_ch_cl(self):
        self.card.set_dac(9, 0x11, 0x22, 0x33)
        self.call(0x15, bx=9, cx=0x9999, dx=0x9999)
        self.assertEqual((self.cpu.get8(6), self.cpu.get8(5), self.cpu.get8(1)), (0x11, 0x22, 0x33))
        self.assertEqual(self.cpu.get8(2), 0x99)  # DL stays

    def test_17_reads_a_block_to_es_dx(self):
        self.card.set_dac(30, 1, 2, 3)
        self.card.set_dac(31, 4, 5, 6)
        self.call(0x17, bx=30, cx=2, dx=0x0500)
        self.assertEqual(list(self.cpu.mem[0x30500:0x30507]), [1, 2, 3, 4, 5, 6, 0])

    def test_17_goes_round_from_255_to_0_and_over_the_end_of_the_segment(self):
        self.card.set_dac(255, 1, 2, 3)
        self.card.set_dac(0, 4, 5, 6)
        self.call(0x17, bx=255, cx=2, dx=0xFFFD)
        self.assertEqual(list(self.cpu.mem[0x3FFFD:0x40000]), [1, 2, 3])
        self.assertEqual(list(self.cpu.mem[0x30000:0x30003]), [4, 5, 6])

    def test_what_is_read_back_is_what_was_written(self):
        data = [(n * 7) & 0x3F for n in range(30)]
        self.table(0x0600, data)
        self.call(0x12, bx=100, cx=10, dx=0x0600)
        self.call(0x17, bx=100, cx=10, dx=0x0700)
        self.assertEqual(list(self.cpu.mem[0x30700:0x3071E]), data)

    def test_the_screen_shows_the_new_colors_and_the_picture_is_marked_as_changed(self):
        self.card.changed = False
        self.call(0x10, bx=0x38 + 7, cx=0x0000, dx=0x3F00)  # the color 15 of a pixel: red
        self.assertEqual(self.card.colors()[15], (255, 0, 0))
        self.assertTrue(self.card.changed)

    def test_no_register_changes_but_the_ones_that_give_an_answer_and_the_flags_stay(self):
        self.cpu.set_flag(flags.CF, True)
        self.call(0x12, bx=5, cx=0, dx=0x0400)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x1012, 5, 0, 0x0400))
        self.assertTrue(cpu.get_flag(flags.CF))
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_the_other_functions_are_not_done_yet(self):
        for al in (0x03, 0x13, 0x1A, 0x1B, 0x20, 0xFF):
            with self.assertRaises(NotImplementedError, msg=hex(al)):
                self.call(al)

    def test_without_a_video_card_there_are_no_colors_to_set(self):
        cpu = CPU()
        bios.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x10])
        cpu.r[CPU.AX] = 0x1012
        with self.assertRaises(NotImplementedError):
            cpu.step()


def machine_with_floppies(count):
    """A configuration with this many floppy drives, a hard disk, a CD-ROM drive and a memory disk to go with them."""
    drives = "".join(f"[disks.{'ABCD'[n]}]\ntype = 'floppy'\n" for n in range(count))
    text = drives + "[disks.E]\ntype = 'hdd'\n[disks.F]\ntype = 'cdrom'\n[mounts]\nE = { type = 'tmpfs' }\n"
    return config.parse(tomllib.loads(text), Path("."))


class TestEquipmentAndMemory(unittest.TestCase):
    """INT 11h: the equipment list. INT 12h: the memory size in KB."""

    def machine(self, configuration=None):
        cpu = CPU()
        bios.install(cpu, configuration)
        cpu.s[CPU.CS] = 0x1000
        return cpu

    def call(self, cpu, number, ax=0x7777):
        cpu.mem[0x10000:0x10002] = bytes([0xCD, number])
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, 0x1111, 0x2222, 0x3333
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, 0x5555
        cpu.ip = 0
        cpu.step()
        return cpu.r[CPU.AX]

    def test_the_word_for_each_number_of_floppy_drives(self):
        for count, word in ((0, 0x0020), (1, 0x0021), (2, 0x0061), (3, 0x00A1), (4, 0x00E1)):
            self.assertEqual(bios.equipment_word(machine_with_floppies(count)), word, count)

    def test_more_than_4_drives_is_still_4_and_other_drives_are_not_floppies(self):
        text = "".join(f"[disks.{letter}]\ntype = 'floppy'\n" for letter in "ABCDEF")
        text += "[disks.G]\ntype = 'cdrom'\n[mounts]\nA = { type = 'tmpfs' }\n"
        self.assertEqual(bios.equipment_word(config.parse(tomllib.loads(text), Path("."))), 0x00E1)
        only_others = "[disks.C]\ntype = 'hdd'\n[disks.D]\ntype = 'cdrom'\n[mounts]\nC = { type = 'tmpfs' }\n"
        self.assertEqual(bios.equipment_word(config.parse(tomllib.loads(only_others), Path("."))), 0x0020)

    def test_without_a_configuration_the_machine_has_two_floppy_drives(self):
        self.assertEqual(bios.equipment_word(), 0x0061)
        self.assertEqual(bios.equipment_word(None), 0x0061)

    def test_the_built_in_machine_has_the_default_word(self):
        self.assertEqual(bios.equipment_word(config.default(Path("."))), 0x0061)

    def test_what_the_word_says_of_the_things_we_do_not_have(self):
        for count in range(5):
            word = bios.equipment_word(machine_with_floppies(count))
            self.assertEqual(word >> 4 & 3, 0b10, "a color display of 80 by 25")
            self.assertEqual(word & 0x0002, 0, "no coprocessor")
            self.assertEqual(word & 0x0100, 0, "DMA is there")
            self.assertEqual(word >> 9 & 7, 0, "no serial ports")
            self.assertEqual(word & 0x1000, 0, "no game port")
            self.assertEqual(word >> 14, 0, "no printers")
            self.assertEqual(word & 0x000C, 0)

    def test_int_11h_gives_the_word_in_ax_whatever_ax_was(self):
        for ax in (0x0000, 0x7777, 0xFFFF):
            self.assertEqual(self.call(self.machine(), 0x11, ax=ax), 0x0061)

    def test_int_11h_tells_of_the_configuration_it_was_installed_with(self):
        for count, word in ((0, 0x0020), (1, 0x0021), (3, 0x00A1)):
            self.assertEqual(self.call(self.machine(machine_with_floppies(count)), 0x11), word)

    def test_the_word_is_in_the_data_area_of_the_bios_and_that_is_where_int_11h_reads_it(self):
        cpu = self.machine(machine_with_floppies(1))
        self.assertEqual(cpu.rw(0x40, 0x10), 0x0021)
        cpu.ww(0x40, 0x10, 0x1234)
        self.assertEqual(self.call(cpu, 0x11), 0x1234)

    def test_int_11h_changes_no_other_register_and_no_flag(self):
        cpu = self.machine()
        cpu.flags = 0x0FD7
        self.call(cpu, 0x11)
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]),
                         (0x1111, 0x2222, 0x3333, 0x4444, 0x5555))
        self.assertEqual((cpu.flags, cpu.s[CPU.CS], cpu.ip), (0x0FD7, 0x1000, 2))

    def test_int_12h_gives_640_kb(self):
        for ax in (0x0000, 0x7777):
            self.assertEqual(self.call(self.machine(), 0x12, ax=ax), 640)

    def test_the_memory_size_is_in_the_data_area_too_and_that_is_where_int_12h_reads_it(self):
        cpu = self.machine()
        self.assertEqual(cpu.rw(0x40, 0x13), 640)
        cpu.ww(0x40, 0x13, 512)
        self.assertEqual(self.call(cpu, 0x12), 512)

    def test_the_640_kb_are_the_memory_dos_gives_to_programs(self):
        from dosloth import dos
        self.assertEqual(bios.CONVENTIONAL_MEMORY_KB * 1024 // 16, dos.MEMORY_TOP_SEG)

    def test_int_12h_changes_no_other_register_and_no_flag(self):
        cpu = self.machine()
        cpu.flags = 0x0FD7
        self.call(cpu, 0x12)
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]),
                         (0x1111, 0x2222, 0x3333, 0x4444, 0x5555))
        self.assertEqual(cpu.flags, 0x0FD7)

    def test_the_words_are_stored_low_byte_first_where_the_bios_keeps_them(self):
        cpu = self.machine(machine_with_floppies(2))
        self.assertEqual((cpu.mem[0x410], cpu.mem[0x411]), (0x61, 0x00))
        self.assertEqual((cpu.mem[0x413], cpu.mem[0x414]), (0x80, 0x02))

    def test_each_machine_has_its_own(self):
        one, other = self.machine(machine_with_floppies(1)), self.machine(machine_with_floppies(3))
        self.assertEqual((self.call(one, 0x11), self.call(other, 0x11)), (0x0021, 0x00A1))


class TestEgaInformation(unittest.TestCase):
    """INT 10h AH=12h BL=10h: what kind of EGA is there?"""

    def setUp(self):
        self.cpu = CPU()
        bios.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x10])

    def call(self, ax=0x1203, bx=0x0010, cx=0x2222, dx=0x1111):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x3333, 0x4444
        cpu.ip = 0
        cpu.step()

    def test_a_color_display_with_256_kb_no_features_and_the_switches_of_a_color_ega(self):
        self.call()
        cpu = self.cpu
        self.assertEqual((cpu.get8(7), cpu.get8(3), cpu.get8(5), cpu.get8(1)), (0, 3, 0, 9))  # BH BL CH CL
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX]), (0x0003, 0x0009))

    def test_the_old_registers_do_not_matter_but_bl(self):
        for bx, cx in ((0x0010, 0x0000), (0xFF10, 0xFFFF), (0x5A10, 0x1234)):
            self.call(bx=bx, cx=cx)
            self.assertEqual((self.cpu.r[CPU.BX], self.cpu.r[CPU.CX]), (0x0003, 0x0009))

    def test_ax_dx_si_di_and_the_flags_stay(self):
        self.cpu.flags = 0x0FD7
        self.call(ax=0x1277)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]), (0x1277, 0x1111, 0x3333, 0x4444))
        self.assertEqual((cpu.flags, cpu.s[CPU.CS], cpu.ip), (0x0FD7, 0x1000, 2))

    def test_the_other_alternate_functions_are_not_done_yet(self):
        for bl in (0x00, 0x0F, 0x11, 0x20, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x7F, 0xFF):
            with self.assertRaises(NotImplementedError, msg=hex(bl)):
                self.call(bx=bl)

    def test_the_error_says_which_function(self):
        with self.assertRaises(NotImplementedError) as caught:
            self.call(bx=0x0032)
        self.assertIn("AH=12 BL=32", str(caught.exception))

    def test_it_does_not_disturb_the_other_functions_of_ah_12h_numbers(self):
        self.call()
        self.cpu.ip = 0
        self.cpu.r[CPU.AX], self.cpu.r[CPU.BX] = 0x0F00, 0
        self.cpu.step()
        self.assertEqual(self.cpu.get8(0), 0x03)  # AH=0Fh still tells the mode


class TestTextScreen(unittest.TestCase):
    """INT 10h AH=01h, 02h, 03h, 08h, 09h and 0Ah: the cursor and the characters at it, in the text mode."""

    def setUp(self):
        self.cpu = CPU()
        bios.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x10])

    def call(self, ah, al=0, bx=0, cx=0x2222, dx=0x1111):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ah << 8 | al, bx, cx, dx
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x3333, 0x4444
        cpu.ip = 0
        cpu.step()

    def put_cursor(self, row, column, page=0):
        self.call(0x02, bx=page << 8, dx=row << 8 | column)

    def cell(self, row, column, page=0):
        address = 0xB8000 + page * 0x1000 + (row * 80 + column) * 2
        return self.cpu.mem[address], self.cpu.mem[address + 1]

    def test_the_machine_starts_with_a_clean_text_screen_on_every_page(self):
        self.assertEqual(bytes(self.cpu.mem[0xB8000:0xC0000]), bytes([0x20, 0x07]) * 0x4000)

    def test_the_state_at_the_start(self):
        cpu = self.cpu
        self.assertEqual(bytes(cpu.mem[0x450:0x460]), bytes(16))  # every cursor at the top left
        self.assertEqual(cpu.rw(0x40, 0x60), 0x0607)
        self.assertEqual((cpu.mem[0x484], cpu.rw(0x40, 0x85)), (24, 16))  # 25 rows, characters of 16 scan lines
        self.call(0x03)
        self.assertEqual((cpu.get8(6), cpu.get8(2), cpu.r[CPU.CX], cpu.r[CPU.AX]), (0, 0, 0x0607, 0))

    def test_set_and_get_the_cursor_of_every_page(self):
        for page in range(8):
            self.put_cursor(page + 1, 10 + page, page)
        for page in range(8):
            self.call(0x03, bx=page << 8)
            self.assertEqual((self.cpu.get8(6), self.cpu.get8(2)), (page + 1, 10 + page), page)

    def test_the_column_is_the_low_byte_and_the_row_the_high_byte_of_the_word_in_the_data_area(self):
        self.put_cursor(7, 33, page=2)
        self.assertEqual((self.cpu.mem[0x454], self.cpu.mem[0x455]), (33, 7))
        self.assertEqual(bytes(self.cpu.mem[0x450:0x454]), bytes(4))

    def test_a_cursor_may_be_put_outside_the_screen_to_hide_it(self):
        self.put_cursor(25, 0)
        self.call(0x03)
        self.assertEqual((self.cpu.get8(6), self.cpu.get8(2)), (25, 0))
        self.put_cursor(200, 255)
        self.call(0x03)
        self.assertEqual((self.cpu.get8(6), self.cpu.get8(2)), (200, 255))

    def test_the_shape_of_the_cursor(self):
        self.call(0x01, cx=0x0F0F)
        self.call(0x03)
        self.assertEqual(self.cpu.r[CPU.CX], 0x0F0F)
        self.call(0x01, cx=0x2000)  # bit 5 of CH hides the cursor on a real card: the word is kept as it is
        self.call(0x03)
        self.assertEqual(self.cpu.r[CPU.CX], 0x2000)

    def test_get_cursor_changes_only_ax_cx_and_dx_and_no_flag(self):
        self.cpu.flags = 0x0FD7
        self.call(0x03, bx=0x0000)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.flags), (0, 0x3333, 0x4444, 0x0FD7))

    def test_set_cursor_and_shape_change_no_register_and_no_flag(self):
        self.cpu.flags = 0x0FD7
        self.call(0x02, bx=0x0100, dx=0x0506)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.flags),
                         (0x0200, 0x0100, 0x2222, 0x0506, 0x0FD7))
        self.call(0x01, cx=0x0506)
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.DX], cpu.flags), (0x0100, 0x1111, 0x0FD7))

    def test_a_page_that_does_not_exist_stops_the_emulator(self):
        for ah in (0x02, 0x03, 0x08, 0x09, 0x0A):
            with self.assertRaises(NotImplementedError, msg=hex(ah)):
                self.call(ah, bx=0x0800)

    def test_read_the_character_and_the_attribute_at_the_cursor(self):
        self.cpu.mem[0xB8000 + (3 * 80 + 7) * 2:0xB8000 + (3 * 80 + 7) * 2 + 2] = bytes([0x41, 0x1F])
        self.put_cursor(3, 7)
        self.call(0x08, al=0x77, bx=0x0000)
        self.assertEqual((self.cpu.get8(0), self.cpu.get8(4)), (0x41, 0x1F))

    def test_reading_goes_by_the_page_and_the_ends_of_the_screen_are_there(self):
        for page, row, column in ((0, 0, 0), (0, 24, 79), (1, 0, 0), (7, 24, 79), (3, 12, 40)):
            address = 0xB8000 + page * 0x1000 + (row * 80 + column) * 2
            self.cpu.mem[address:address + 2] = bytes([0x30 + page, 0x70 + row % 16])
            self.put_cursor(row, column, page)
            self.call(0x08, bx=page << 8)
            self.assertEqual((self.cpu.get8(0), self.cpu.get8(4)), (0x30 + page, 0x70 + row % 16), (page, row, column))

    def test_reading_changes_no_register_but_ax_and_no_flag(self):
        self.cpu.flags = 0x0FD7
        self.call(0x08, bx=0x0000)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.flags), (0, 0x2222, 0x1111, 0x0FD7))

    def test_reading_with_the_cursor_outside_the_screen_stops_the_emulator(self):
        for row, column in ((25, 0), (0, 80), (24, 80), (255, 255)):
            self.put_cursor(row, column)
            with self.assertRaises(NotImplementedError, msg=(row, column)):
                self.call(0x08)

    def test_write_a_character_with_an_attribute_at_the_cursor_and_the_cursor_stays(self):
        self.put_cursor(2, 5)
        self.call(0x09, al=0x58, bx=0x001E, cx=1)
        self.assertEqual(self.cell(2, 5), (0x58, 0x1E))
        self.assertEqual(self.cell(2, 6), (0x20, 0x07))
        self.call(0x03)
        self.assertEqual((self.cpu.get8(6), self.cpu.get8(2)), (2, 5))

    def test_write_it_several_times_in_a_row(self):
        self.put_cursor(2, 5)
        self.call(0x09, al=0x2D, bx=0x0070, cx=6)
        self.assertEqual([self.cell(2, c) for c in range(4, 13)],
                         [(0x20, 0x07)] + [(0x2D, 0x70)] * 6 + [(0x20, 0x07)] * 2)

    def test_it_goes_on_into_the_next_line_and_stops_at_the_end_of_the_page(self):
        self.put_cursor(23, 75)
        self.call(0x09, al=0x23, bx=0x0007, cx=0x3000)  # far more than is left
        self.assertEqual(self.cell(23, 74), (0x20, 0x07))
        self.assertEqual([self.cell(23, c) for c in (75, 79)], [(0x23, 0x07)] * 2)
        self.assertEqual([self.cell(24, c) for c in (0, 79)], [(0x23, 0x07)] * 2)
        self.assertEqual(self.cell(0, 0, page=1), (0x20, 0x07))  # not into the next page: it is blank as it was
        self.assertEqual(self.cpu.mem[0xB8000 + 4000:0xB8000 + 4096], bytes([0x20, 0x07] * 48))  # nor the rest of it

    def test_every_character_code_0_to_255_is_kept_as_it_is(self):
        for code in range(256):
            self.put_cursor(code // 80, code % 80)
            self.call(0x09, al=code, bx=0x0007, cx=1)
            self.assertEqual(self.cell(code // 80, code % 80), (code, 0x07), code)
            self.call(0x08, bx=0x0000)
            self.assertEqual(self.cpu.get8(0), code)

    def test_nothing_is_written_for_a_count_of_0(self):
        self.put_cursor(1, 1)
        self.call(0x09, al=0x41, bx=0x0007, cx=0)
        self.call(0x0A, al=0x41, bx=0x0000, cx=0)
        self.assertEqual(self.cell(1, 1), (0x20, 0x07))

    def test_writing_goes_by_the_page(self):
        self.put_cursor(4, 4, page=3)
        self.call(0x09, al=0x5A, bx=0x0320, cx=2)
        self.assertEqual([self.cell(4, 4, 3), self.cell(4, 5, 3)], [(0x5A, 0x20)] * 2)
        self.assertEqual(self.cell(4, 4, 0), (0x20, 0x07))
        self.assertEqual(self.cell(4, 4, 2), (0x20, 0x07))

    def test_the_other_form_changes_the_character_and_leaves_the_attribute(self):
        self.put_cursor(5, 5)
        self.call(0x09, al=0x41, bx=0x004E, cx=3)
        self.call(0x0A, al=0x42, bx=0x00FF, cx=2)  # BL means nothing here
        self.assertEqual([self.cell(5, c) for c in (5, 6, 7)], [(0x42, 0x4E), (0x42, 0x4E), (0x41, 0x4E)])

    def test_writing_changes_no_register_and_no_flag(self):
        self.cpu.flags = 0x0FD7
        self.put_cursor(0, 0)
        self.call(0x09, al=0x41, bx=0x0007, cx=1)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.flags), (0x0941, 0x0007, 1, 0x0FD7))

    def test_writing_with_the_cursor_outside_the_screen_stops_the_emulator(self):
        self.put_cursor(25, 0)
        with self.assertRaises(NotImplementedError):
            self.call(0x09, al=0x41, bx=0x0007, cx=1)

    def test_in_the_graphics_mode_reading_and_writing_characters_stop_the_emulator(self):
        self.call(0x00, al=0x12)
        for ah in (0x08, 0x09, 0x0A):
            with self.assertRaises(NotImplementedError, msg=hex(ah)):
                self.call(ah)

    def test_a_mode_set_puts_the_cursors_and_the_shape_back(self):
        self.put_cursor(9, 9, page=4)
        self.call(0x01, cx=0x0F0F)
        self.call(0x00, al=0x03)
        self.assertEqual(bytes(self.cpu.mem[0x450:0x460]), bytes(16))
        self.assertEqual(self.cpu.rw(0x40, 0x60), 0x0607)

    def test_the_graphics_mode_has_30_rows_of_16_scan_lines_and_the_text_mode_25(self):
        self.call(0x00, al=0x12)
        self.assertEqual((self.cpu.mem[0x484], self.cpu.rw(0x40, 0x85)), (29, 16))
        self.call(0x00, al=0x03)
        self.assertEqual((self.cpu.mem[0x484], self.cpu.rw(0x40, 0x85)), (24, 16))

    def test_the_cursor_functions_work_in_the_graphics_mode_too_as_they_only_keep_numbers(self):
        self.call(0x00, al=0x12)
        self.put_cursor(10, 20)
        self.call(0x03)
        self.assertEqual((self.cpu.get8(6), self.cpu.get8(2)), (10, 20))


class TestTimeOfDay(unittest.TestCase):
    """INT 1Ah: AH=00h reads the clock; AX=B001h asks for Stacker, which is not there."""

    def setUp(self):
        self.cpu = CPU()
        bios.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x1A])

    def call(self, ax, bx=0x3333, cx=0x2222, dx=0x1111, carry=False):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, 0x5555
        cpu.set_flag(flags.CF, carry)
        cpu.ip = 0
        cpu.step()

    def ticks_at(self, seconds):
        from unittest import mock
        with mock.patch("dosloth.bios._seconds_since_midnight", return_value=seconds):
            self.call(0x0000)
        return self.cpu.r[CPU.CX] << 16 | self.cpu.r[CPU.DX]

    def test_the_clock_counts_18_2_ticks_a_second_in_cx_dx(self):
        self.assertEqual(self.ticks_at(0), 0)
        self.assertEqual(self.ticks_at(1), 18)  # 1.0 s is 18.2 ticks
        self.assertEqual(self.ticks_at(3600), 65543)  # an hour: CX = 1, DX = 7
        self.assertEqual((self.cpu.r[CPU.CX], self.cpu.r[CPU.DX]), (1, 7))
        self.assertEqual(self.ticks_at(86399.99), 1573039)  # the last moment of the day, just under 1,573,040 ticks

    def test_a_big_count_has_its_high_bits_in_dx_and_the_next_day_starts_again_from_0(self):
        self.assertEqual(self.ticks_at(10000), 182064)  # 18.2065 a second
        self.assertEqual((self.cpu.r[CPU.CX], self.cpu.r[CPU.DX]), (2, 0xC730))
        self.assertEqual(self.ticks_at(86400), 0)  # not 1,573,040: the day is over
        self.assertEqual(self.ticks_at(86400.5), 9)

    def test_al_says_that_midnight_did_not_pass_and_the_other_registers_stay(self):
        self.call(0x0000, bx=0x3333)
        cpu = self.cpu
        self.assertEqual((cpu.get8(0), cpu.get8(4)), (0, 0))
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.SI], cpu.r[CPU.DI]), (0x3333, 0x4444, 0x5555))

    def test_b001_changes_nothing_so_that_a_program_waiting_for_stacker_finds_none(self):
        for carry in (False, True):
            self.call(0xB001, cx=0x5354, dx=0x4143, carry=carry)
            cpu = self.cpu
            self.assertEqual(cpu.r[CPU.AX], 0xB001)  # a Stacker would have made it another word
            self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x3333, 0x5354, 0x4143))
            self.assertEqual((cpu.r[CPU.SI], cpu.r[CPU.DI]), (0x4444, 0x5555))
            self.assertEqual(cpu.get_flag(flags.CF), carry)
            self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_the_other_functions_are_still_not_known_and_say_which(self):
        for ax in (0x0100, 0x0200, 0x0400, 0xB101, 0xB000, 0xB002, 0xB0FF, 0xB100):
            with self.assertRaisesRegex(NotImplementedError, f"INT 1Ah AH={ax >> 8:02X}"):
                self.call(ax)


if __name__ == "__main__":
    unittest.main()
