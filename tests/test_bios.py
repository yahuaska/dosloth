import unittest

from mydos import bios, flags
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
        for ax in (0x1A01, 0x1AFF, 0x0003, 0x0F00, 0x1200, 0x1100, 0xFF00):
            with self.assertRaises(NotImplementedError, msg=hex(ax)):
                self.call(ax)


if __name__ == "__main__":
    unittest.main()
