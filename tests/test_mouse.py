import unittest

from mydos import mouse
from mydos.cpu import CPU
from mydos.mouse import Mouse


class TestTheState(unittest.TestCase):
    def test_a_new_mouse_is_in_the_middle_with_no_button_down_and_the_cursor_hidden(self):
        m = Mouse()
        self.assertEqual((m.x, m.y, m.buttons, m.cursor), (320, 240, 0, -1))
        self.assertEqual((m.left, m.right, m.top, m.bottom), (0, 639, 0, 479))
        self.assertEqual((m.presses, m.releases), ([0, 0, 0], [0, 0, 0]))

    def test_update_moves_the_pointer_and_sets_the_buttons(self):
        m = Mouse()
        m.update(100, 50, 0b101)
        self.assertEqual((m.x, m.y, m.buttons), (100, 50, 0b101))

    def test_the_pointer_stays_inside_the_range(self):
        m = Mouse()
        m.update(-5, 9999, 0)
        self.assertEqual((m.x, m.y), (0, 479))
        m.update(700, -1, 0)
        self.assertEqual((m.x, m.y), (639, 0))

    def test_setting_a_range_pulls_the_pointer_in_and_takes_the_ends_in_either_order(self):
        m = Mouse()
        m.update(500, 400, 0)
        m.set_range(True, 300, 100)
        self.assertEqual((m.left, m.right, m.x, m.y), (100, 300, 300, 400))
        m.set_range(False, 10, 20)
        self.assertEqual((m.top, m.bottom, m.y), (10, 20, 20))
        m.update(0, 0, 0)
        self.assertEqual((m.x, m.y), (100, 10))

    def test_a_button_going_down_and_up_is_counted_once_each_with_the_place(self):
        m = Mouse()
        m.update(10, 20, 0b001)
        m.update(11, 21, 0b001)  # the same button still down: not another press
        m.update(30, 40, 0b000)
        self.assertEqual((m.presses, m.releases), ([1, 0, 0], [1, 0, 0]))
        self.assertEqual((m.press_at[0], m.release_at[0]), ((10, 20), (30, 40)))

    def test_every_button_is_counted_on_its_own(self):
        m = Mouse()
        m.update(1, 1, 0b110)  # right and middle down together
        m.update(2, 2, 0b100)  # right up
        m.update(3, 3, 0b101)  # left down
        self.assertEqual(m.presses, [1, 1, 1])
        self.assertEqual(m.releases, [0, 1, 0])
        self.assertEqual((m.press_at[1], m.release_at[1], m.press_at[0]), ((1, 1), (2, 2), (3, 3)))

    def test_a_reset_keeps_the_buttons_that_are_down_and_forgets_the_rest(self):
        m = Mouse()
        m.cursor = 0
        m.set_range(True, 100, 200)
        m.update(150, 60, 0b001)
        m.reset()
        self.assertEqual((m.x, m.y, m.cursor, m.buttons), (320, 240, -1, 0b001))
        self.assertEqual((m.left, m.right), (0, 639))
        self.assertEqual((m.presses, m.releases, m.press_at[0]), ([0, 0, 0], [0, 0, 0], (0, 0)))


class TestTheDriver(unittest.TestCase):
    def setUp(self):
        self.cpu = CPU()
        self.mouse = mouse.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x33])

    def call(self, ax, bx=0x3333, cx=0x2222, dx=0x1111):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.ip = 0
        cpu.step()
        return cpu

    def test_a_reset_says_there_is_a_mouse_with_two_buttons(self):
        cpu = self.call(0x0000, bx=0)
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX]), (0xFFFF, 2))
        self.assertEqual((cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x2222, 0x1111))  # the others stay

    def test_a_reset_also_resets_the_state(self):
        self.mouse.update(5, 6, 0)
        self.mouse.cursor = 0
        self.call(0x0000)
        self.assertEqual((self.mouse.x, self.mouse.y, self.mouse.cursor), (320, 240, -1))

    def test_show_and_hide_count(self):
        self.call(0x0001)
        self.assertEqual(self.mouse.cursor, 0)  # shown
        self.call(0x0001)
        self.assertEqual(self.mouse.cursor, 0)  # not more than shown
        self.call(0x0002)
        self.call(0x0002)
        self.assertEqual(self.mouse.cursor, -2)
        self.call(0x0001)
        self.assertEqual(self.mouse.cursor, -1)  # still hidden: one hide is not undone
        self.call(0x0001)
        self.assertEqual(self.mouse.cursor, 0)

    def test_the_position_and_buttons_come_in_bx_cx_dx(self):
        self.mouse.update(123, 45, 0b011)
        cpu = self.call(0x0003, bx=0)
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0b011, 123, 45))
        self.assertEqual(cpu.r[CPU.AX], 0x0003)

    def test_the_pointer_can_be_put_somewhere_within_the_range(self):
        self.call(0x0004, cx=200, dx=100)
        self.assertEqual((self.mouse.x, self.mouse.y), (200, 100))
        self.call(0x0004, cx=9000, dx=9000)
        self.assertEqual((self.mouse.x, self.mouse.y), (639, 479))

    def test_the_press_count_place_and_state_of_a_button_and_it_starts_again_from_0(self):
        self.mouse.update(10, 20, 0b001)
        self.mouse.update(30, 40, 0b001 | 0b010)  # the right one goes down too
        cpu = self.call(0x0005, bx=0)
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0b011, 1, 10, 20))
        cpu = self.call(0x0005, bx=0)
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0, 10, 20))  # the count only; the place stays
        cpu = self.call(0x0005, bx=1)
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (1, 30, 40))

    def test_the_release_count_is_the_same_for_buttons_that_came_up(self):
        self.mouse.update(10, 20, 0b001)
        self.mouse.update(50, 60, 0)
        cpu = self.call(0x0006, bx=0)
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0, 1, 50, 60))
        cpu = self.call(0x0006, bx=0)
        self.assertEqual(cpu.r[CPU.BX], 0)
        self.assertEqual(self.mouse.presses[0], 1)  # the presses are counted on their own

    def test_a_press_count_is_not_a_release_count(self):
        self.mouse.update(1, 1, 0b100)
        self.assertEqual(self.call(0x0006, bx=2).r[CPU.BX], 0)
        self.assertEqual(self.call(0x0005, bx=2).r[CPU.BX], 1)

    def test_there_are_three_buttons_to_ask_about_and_no_more(self):
        for button in (0, 1, 2):
            self.call(0x0005, bx=button)
        with self.assertRaises(NotImplementedError):
            self.call(0x0005, bx=3)
        with self.assertRaises(NotImplementedError):
            self.call(0x0006, bx=0xFFFF)

    def test_the_ranges_across_and_down(self):
        self.mouse.update(500, 400, 0)
        self.call(0x0007, cx=0, dx=319)
        self.assertEqual((self.mouse.left, self.mouse.right, self.mouse.x), (0, 319, 319))
        self.call(0x0008, cx=100, dx=199)
        self.assertEqual((self.mouse.top, self.mouse.bottom, self.mouse.y), (100, 199, 199))

    def test_the_programs_subroutine_is_remembered_with_its_events_and_address(self):
        self.cpu.s[CPU.ES] = 0x17E4
        self.call(0x000C, cx=0x0001, dx=0x02DC)
        self.assertEqual(self.mouse.handler, (0x0001, 0x17E4, 0x02DC))

    def test_events_0_takes_the_subroutine_away_and_a_new_one_takes_the_place_of_the_old(self):
        self.cpu.s[CPU.ES] = 0x1000
        self.call(0x000C, cx=0x0007, dx=0x0100)
        self.call(0x000C, cx=0x0002, dx=0x0200)
        self.assertEqual(self.mouse.handler, (0x0002, 0x1000, 0x0200))
        self.call(0x000C, cx=0, dx=0x0300)
        self.assertIsNone(self.mouse.handler)

    def test_the_subroutine_is_never_called_for_now_and_a_reset_forgets_it(self):
        self.cpu.s[CPU.ES] = 0x1000
        self.call(0x000C, cx=0x0001, dx=0x0100)
        self.mouse.update(5, 5, 1)
        self.assertEqual((self.cpu.s[CPU.CS], self.cpu.ip), (0x1000, 2))  # nothing jumped there
        self.call(0x0000)
        self.assertIsNone(self.mouse.handler)

    def test_registering_the_subroutine_changes_no_register(self):
        self.cpu.s[CPU.ES] = 0x1000
        cpu = self.call(0x000C, bx=0x3333, cx=0x0001, dx=0x0100)
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x000C, 0x3333, 1, 0x0100))

    def test_the_flags_and_the_return_address_are_the_ones_of_an_int(self):
        self.cpu.set_flag(0x0001, True)
        cpu = self.call(0x0003)
        self.assertTrue(cpu.get_flag(0x0001))
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_the_other_functions_are_not_done_yet(self):
        for ax in (0x000B, 0x000D, 0x0021, 0x0024, 0x0009, 0x00FF, 0xFFFF):
            with self.assertRaises(NotImplementedError, msg=hex(ax)):
                self.call(ax)

    def test_each_cpu_has_its_own_mouse(self):
        other = CPU()
        other_mouse = mouse.install(other)
        self.mouse.update(1, 2, 3)
        self.assertEqual((other_mouse.x, other_mouse.buttons), (320, 0))


if __name__ == "__main__":
    unittest.main()
