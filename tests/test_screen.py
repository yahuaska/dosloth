import random
import unittest

from mydos import screen
from mydos.screen import EGA_COLORS, Frame, render


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


if __name__ == "__main__":
    unittest.main()
