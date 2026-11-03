import random
import unittest

from mydos import screen
from mydos.bios import Keyboard
from mydos.cpu import Exit
from mydos.display import Display
from mydos.screen import EGA_COLORS, Frame, Monitor, render, render_256
from mydos.vga import Vga


def reference(planes, palette, width, height):
    """The picture pixel by pixel: the color is built from one bit of each plane, and looked up in the palette."""
    rgb = bytearray()
    for y in range(height):
        for x in range(width):
            offset = y * (width // 8) + x // 8
            color = 0
            for number in range(4):
                color |= (planes[number][offset] >> (7 - x % 8) & 1) << number
            rgb += bytes(palette[color])
    return bytes(rgb)


def blank(width, height, extra=0):
    return [bytearray(width // 8 * height + extra) for _ in range(4)]


class TestRender256(unittest.TestCase):
    PALETTE = [(n, 255 - n, n // 2) for n in range(256)]

    def test_the_bytes_of_the_planes_in_turn_are_the_pixels_in_a_row(self):
        planes = [bytearray(0x10000) for _ in range(4)]
        for address in range(64000):
            planes[address & 3][address >> 2] = address * 7 & 0xFF
        frame = render_256(planes, self.PALETTE)
        self.assertEqual((frame.width, frame.height, len(frame.rgb)), (320, 200, 320 * 200 * 3))
        for address in (0, 1, 2, 3, 4, 319, 320, 321, 12345, 63999):
            number = address * 7 & 0xFF
            self.assertEqual(frame.rgb[3 * address:3 * address + 3], bytes(self.PALETTE[number]), address)

    def test_a_smaller_picture(self):
        planes = [bytearray(2) for _ in range(4)]
        planes[1][0] = 9
        frame = render_256(planes, self.PALETTE, width=4, height=2)
        self.assertEqual((frame.width, frame.height), (4, 2))
        self.assertEqual(frame.rgb[3:6], bytes(self.PALETTE[9]))

    def test_planes_that_are_too_small_are_refused(self):
        with self.assertRaises(ValueError):
            render_256([bytearray(100)] * 4, self.PALETTE)


class TestRender(unittest.TestCase):
    def test_empty_planes_are_a_black_screen_of_the_default_size(self):
        planes = [bytearray(0x10000) for _ in range(4)]  # the whole of what the card has, more than the screen needs
        frame = render(planes)
        self.assertEqual((frame.width, frame.height, len(frame.rgb)), (640, 480, 640 * 480 * 3))
        self.assertEqual(set(frame.rgb), {0})

    def test_the_leftmost_pixel_of_a_byte_is_its_highest_bit(self):
        planes = blank(16, 1)
        planes[0][0] = 0b10000000
        frame = render(planes, width=16, height=1)
        self.assertEqual(frame.rgb[0:3], bytes(EGA_COLORS[1]))
        self.assertEqual(set(frame.rgb[3:]), {0})
        planes = blank(16, 1)
        planes[0][0] = 0b00000001
        frame = render(planes, width=16, height=1)
        self.assertEqual(frame.rgb[7 * 3:8 * 3], bytes(EGA_COLORS[1]))
        self.assertEqual(set(frame.rgb[:7 * 3]) | set(frame.rgb[8 * 3:]), {0})

    def test_the_second_byte_of_a_line_is_the_next_eight_pixels(self):
        planes = blank(16, 1)
        planes[3][1] = 0b10000000
        frame = render(planes, width=16, height=1)
        self.assertEqual(frame.rgb[8 * 3:9 * 3], bytes(EGA_COLORS[8]))
        self.assertEqual(set(frame.rgb[:8 * 3]) | set(frame.rgb[9 * 3:]), {0})

    def test_the_next_line_starts_after_the_bytes_of_the_line(self):
        planes = blank(16, 3)
        planes[1][4] = 0b01000000  # line 2 (two bytes each), the first byte, the second pixel
        frame = render(planes, width=16, height=3)
        start = (2 * 16 + 1) * 3
        self.assertEqual(frame.rgb[start:start + 3], bytes(EGA_COLORS[2]))
        self.assertEqual(set(frame.rgb[:start]) | set(frame.rgb[start + 3:]), {0})

    def test_each_of_the_16_numbers_gives_its_color_with_each_plane_a_bit_of_it(self):
        planes = blank(16, 1)
        for pixel in range(16):
            for number in range(4):
                if pixel >> number & 1:
                    planes[number][pixel // 8] |= 0x80 >> pixel % 8
        frame = render(planes, width=16, height=1)
        for pixel in range(16):
            self.assertEqual(frame.rgb[pixel * 3:pixel * 3 + 3], bytes(EGA_COLORS[pixel]), f"color {pixel}")

    def test_the_standard_colors(self):
        self.assertEqual(len(EGA_COLORS), 16)
        self.assertEqual(EGA_COLORS[0], (0, 0, 0))
        self.assertEqual(EGA_COLORS[15], (255, 255, 255))
        self.assertEqual(EGA_COLORS[6], (0xAA, 0x55, 0x00))  # brown, not dark yellow
        self.assertEqual(EGA_COLORS[7], (0xAA, 0xAA, 0xAA))
        self.assertEqual(EGA_COLORS[8], (0x55, 0x55, 0x55))
        self.assertEqual(len(set(EGA_COLORS)), 16)

    def test_another_palette_is_used_when_given(self):
        palette = [(n, 100 + n, 200 + n) for n in range(16)]
        planes = blank(8, 1)
        for number in range(4):
            planes[number][0] = 0xFF if 0b0101 >> number & 1 else 0
        frame = render(planes, palette, width=8, height=1)
        self.assertEqual(frame.rgb[:3], bytes(palette[5]))
        self.assertEqual(frame.rgb, bytes(palette[5]) * 8)

    def test_the_bytes_of_the_planes_beyond_the_screen_are_not_in_the_picture(self):
        planes = blank(8, 2, extra=100)
        for plane in planes:
            plane[16:] = b"\xFF" * 100
        self.assertEqual(set(render(planes, width=8, height=2).rgb), {0})

    def test_the_planes_are_not_changed(self):
        planes = blank(16, 2)
        planes[2][1] = 0x5A
        before = [bytes(plane) for plane in planes]
        render(planes, width=16, height=2)
        self.assertEqual([bytes(plane) for plane in planes], before)

    def test_planes_that_are_lists_or_bytes_will_do(self):
        planes = blank(16, 1)
        planes[0][0] = 0xF0
        expected = render(planes, width=16, height=1)
        self.assertEqual(render([list(plane) for plane in planes], width=16, height=1), expected)
        self.assertEqual(render([bytes(plane) for plane in planes], width=16, height=1), expected)

    def test_it_agrees_with_the_pixel_by_pixel_reference_on_random_planes(self):
        rng = random.Random(640)
        for width, height in ((8, 1), (24, 5), (64, 7), (16, 16)):
            for _ in range(20):
                planes = [bytearray(rng.randrange(256) for _ in range(width // 8 * height)) for _ in range(4)]
                self.assertEqual(render(planes, width=width, height=height).rgb,
                                 reference(planes, EGA_COLORS, width, height), f"{width}x{height}")

    def test_a_whole_screen_of_random_planes_agrees_with_the_reference(self):
        rng = random.Random(480)
        planes = [bytearray(rng.randrange(256) for _ in range(38400)) for _ in range(4)]
        self.assertEqual(render(planes).rgb, reference(planes, EGA_COLORS, 640, 480))

    def test_a_width_that_is_not_a_multiple_of_8_is_refused(self):
        with self.assertRaises(ValueError):
            render(blank(8, 1), width=12, height=1)

    def test_planes_that_are_too_short_are_refused(self):
        with self.assertRaises(ValueError):
            render([bytearray(79) for _ in range(4)], width=640, height=1)
        planes = blank(8, 2)
        planes[3] = bytearray(1)  # one plane short is enough
        with self.assertRaises(ValueError):
            render(planes, width=8, height=2)

    def test_the_frame_is_a_value(self):
        self.assertEqual(Frame(1, 1, b"abc"), Frame(1, 1, b"abc"))
        with self.assertRaises(AttributeError):
            Frame(1, 1, b"abc").width = 2

    def test_the_constants(self):
        self.assertEqual((screen.WIDTH, screen.HEIGHT), (640, 480))


class Recorder(Display):
    """A display that only writes down what was done to it."""

    def __init__(self, on_poll=None):
        self.calls = []
        self.frames = []
        self.on_poll = on_poll

    def present(self, frame):
        self.calls.append("present")
        self.frames.append(frame)

    def poll(self):
        self.calls.append("poll")
        if self.on_poll:
            self.on_poll(self)

    def close(self):
        self.calls.append("close")


class TestMonitor(unittest.TestCase):
    def setUp(self):
        self.card = Vga()
        self.first, self.second = Recorder(), Recorder()
        self.monitor = Monitor(self.card, [self.first, self.second])

    def test_a_card_nobody_has_drawn_on_is_not_shown_again(self):
        self.monitor.tick()
        self.monitor.tick()
        self.assertEqual(self.first.calls, ["poll", "poll"])
        self.assertEqual(self.second.calls, ["poll", "poll"])

    def test_a_tick_after_drawing_shows_the_picture_on_every_display_once(self):
        self.card.write_memory(0, 0x80)  # all four planes: the first pixel is white
        self.monitor.tick()
        self.monitor.tick()
        for display in (self.first, self.second):
            self.assertEqual(display.calls, ["poll", "present", "poll"])
            self.assertEqual(display.frames[0].rgb[:3], b"\xFF\xFF\xFF")
        self.assertFalse(self.card.changed)

    def test_a_card_in_mode_13h_shows_320_by_200_pixels_with_the_colors_of_the_dac(self):
        self.card.clear()
        self.card.set_mode_13h()
        self.card.set_dac(7, 63, 0, 0)  # red
        self.card.write_memory(2, 7)  # the third pixel of the first row
        self.card.write_memory(321, 15)  # the second of the second
        self.monitor.tick()
        frame = self.first.frames[0]
        self.assertEqual((frame.width, frame.height), (320, 200))
        self.assertEqual(frame.rgb[6:9], b"\xFF\x00\x00")
        self.assertEqual(frame.rgb[3 * 321:3 * 321 + 3], b"\xFF\xFF\xFF")
        self.assertEqual(frame.rgb[:6], bytes(6))

    def test_the_picture_goes_back_to_16_colors_with_the_mode(self):
        self.card.clear()
        self.card.set_mode_13h()
        self.card.clear()
        self.card.write_memory(0, 0x80)
        self.monitor.tick()
        self.assertEqual((self.first.frames[0].width, self.first.frames[0].height), (640, 480))

    def test_every_display_is_polled_before_any_is_shown_the_picture(self):
        order = []
        for name, display in (("first", self.first), ("second", self.second)):
            display.on_poll = lambda display, name=name: order.append(name)
        self.card.write_memory(0, 1)
        self.monitor.tick()
        self.assertEqual(order, ["first", "second"])
        self.assertEqual(self.second.calls, ["poll", "present"])

    def test_show_gives_the_same_picture_to_all_even_when_nothing_changed(self):
        self.monitor.show()
        self.assertEqual(self.first.calls, ["present"])
        self.assertIs(self.first.frames[0], self.second.frames[0])
        self.assertEqual((self.first.frames[0].width, self.first.frames[0].height), (640, 480))

    def test_a_picture_that_is_drawn_again_is_shown_again(self):
        self.card.write_memory(0, 0xFF)
        self.monitor.tick()
        self.card.write_memory(1, 0xFF)
        self.monitor.tick()
        self.assertEqual(self.first.calls, ["poll", "present", "poll", "present"])

    def test_reading_the_planes_is_not_drawing(self):
        self.card.read_memory(0)
        self.monitor.tick()
        self.assertNotIn("present", self.first.calls)

    def test_a_closed_window_stops_the_program_with_status_1(self):
        self.second.closed = True
        self.card.write_memory(0, 1)
        with self.assertRaises(Exit) as caught:
            self.monitor.tick()
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(self.first.calls, ["poll"])  # and nothing is shown to the others

    def test_waiting_for_a_key_goes_on_with_the_displays_until_one_is_there(self):
        keyboard = Keyboard()
        self.first.on_poll = lambda display: keyboard.press(0x1C, 0x0D) if display.calls.count("poll") == 3 else None
        self.monitor.wait_for_key(keyboard)
        self.assertEqual(self.first.calls.count("poll"), 3)
        self.assertEqual(list(keyboard.buffer), [0x1C0D])

    def test_waiting_for_a_key_shows_what_is_drawn_meanwhile(self):
        keyboard = Keyboard()
        self.first.on_poll = lambda display: (self.card.write_memory(0, 0x80) if len(display.calls) == 1 else
                                              keyboard.press(0x01, 0x1B) if len(display.calls) > 3 else None)
        self.monitor.wait_for_key(keyboard)
        self.assertIn("present", self.second.calls)

    def test_it_does_not_wait_when_a_key_is_there(self):
        keyboard = Keyboard()
        keyboard.press(0x1E, 0x61)
        self.monitor.wait_for_key(keyboard)
        self.assertEqual(self.first.calls, [])

    def test_waiting_with_the_window_closed_ends_the_program(self):
        keyboard = Keyboard()
        self.first.on_poll = lambda display: setattr(display, "closed", True)
        with self.assertRaises(Exit):
            self.monitor.wait_for_key(keyboard)

    def test_the_picture_is_shown_in_the_colors_of_the_card(self):
        self.card.set_dac(0x38 + 7, 63, 0, 0)  # the color 15 of a pixel: red
        self.card.write_memory(0, 0x80)  # the first pixel has the color 15
        self.monitor.show()
        self.assertEqual(self.first.frames[0].rgb[:3], b"\xFF\x00\x00")

    def test_a_change_of_colors_alone_shows_the_picture_again(self):
        self.card.write_memory(0, 0x80)
        self.monitor.tick()
        self.monitor.tick()
        self.assertEqual(self.first.calls.count("present"), 1)
        self.card.set_dac(0x38 + 7, 0, 63, 0)
        self.monitor.tick()
        self.assertEqual(self.first.calls.count("present"), 2)
        self.assertEqual(self.first.frames[1].rgb[:3], b"\x00\xFF\x00")

    def test_close_closes_every_display(self):
        self.monitor.close()
        self.assertEqual((self.first.calls, self.second.calls), (["close"], ["close"]))

    def test_the_hook_is_called_at_every_tick_after_the_displays_were_looked_after(self):
        calls = []
        self.monitor.hook = lambda inside: calls.append((inside, list(self.first.calls)))
        self.monitor.tick()
        self.monitor.tick()
        self.assertEqual(calls, [(False, ["poll"]), (False, ["poll", "poll"])])

    def test_a_tick_from_a_program_that_waits_says_so_to_the_hook(self):
        calls = []
        self.monitor.hook = calls.append
        self.monitor.tick(inside=True)
        self.assertEqual(calls, [True])

    def test_waiting_for_a_key_ticks_inside_and_ends_when_there_is_a_key(self):
        calls = []
        keyboard = Keyboard()

        def hook(inside):
            calls.append(inside)
            if len(calls) == 3:
                keyboard.press(0x1E, 0x61)
        self.monitor.hook = hook
        self.monitor.wait_for_key(keyboard)
        self.assertEqual(calls, [True, True, True])

    def test_a_hook_that_is_not_there_is_not_called(self):
        self.assertIsNone(self.monitor.hook)
        self.monitor.tick()

    def test_a_closed_window_stops_the_program_before_the_hook_is_called(self):
        calls = []
        self.monitor.hook = calls.append
        self.first.closed = True
        with self.assertRaises(Exit):
            self.monitor.tick()
        self.assertEqual(calls, [])

    def test_no_displays_at_all_is_fine(self):
        monitor = Monitor(self.card, [])
        self.card.write_memory(0, 1)
        monitor.tick()
        monitor.show()
        monitor.close()
        self.assertFalse(self.card.changed)


if __name__ == "__main__":
    unittest.main()
