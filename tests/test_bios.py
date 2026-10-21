import unittest

from mydos import bios, flags, vga
from mydos.cpu import CPU


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
        self.cpu.mem[0xBBFFE:0xBC000] = b"\x41\x42"  # the end of the four pages
        self.cpu.mem[0xBC000] = 0x99  # the next byte is not cleared
        self.call(0x0003)
        self.assertEqual(bytes(self.cpu.mem[0xB8000:0xB8006]), b" \x07 \x07 \x07")
        self.assertEqual(bytes(self.cpu.mem[0xBBFFE:0xBC000]), b" \x07")
        self.assertEqual(self.cpu.mem[0xBC000], 0x99)
        self.assertEqual(sum(1 for i in range(0xB8000, 0xBC000, 2) if self.cpu.mem[i] != 0x20), 0)
        self.assertEqual(sum(1 for i in range(0xB8001, 0xBC000, 2) if self.cpu.mem[i] != 0x07), 0)

    def test_other_modes_stop_the_emulator_and_change_nothing(self):
        self.call(0x0012)
        for mode in (0x00, 0x01, 0x02, 0x04, 0x05, 0x06, 0x07, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x13, 0x6A, 0x7F):
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


if __name__ == "__main__":
    unittest.main()
