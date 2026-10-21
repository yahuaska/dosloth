import unittest
from types import SimpleNamespace

from mydos.display import Display
from mydos.screen import Frame
from mydos.window import zoom_for

try:
    import tkinter
except ImportError:  # a Python without Tk
    tkinter = None


def frame_of(width, height, pixel):
    """A picture of one color, with the pixel (1, 0) in another."""
    rgb = bytearray(bytes(pixel) * (width * height))
    rgb[3:6] = bytes((10, 20, 30))
    return Frame(width, height, bytes(rgb))


@unittest.skipIf(tkinter is None, "this Python has no tkinter")
class TestTkWindow(unittest.TestCase):
    def setUp(self):
        from mydos.window import TkWindow, WindowError
        self.keys = []
        try:
            self.window = TkWindow(lambda scan, ascii_code: self.keys.append((scan, ascii_code)), hidden=True)
        except WindowError as error:
            self.skipTest(f"no window: {error}")
        self.addCleanup(self.window.close)

    def test_it_is_a_display_and_starts_open(self):
        self.assertIsInstance(self.window, Display)
        self.assertFalse(self.window.closed)

    def test_the_picture_shown_is_the_picture_given(self):
        self.window.present(frame_of(6, 4, (200, 100, 50)))
        image = self.window.image
        self.assertEqual((image.width(), image.height()), (6, 4))
        self.assertEqual(image.get(0, 0), (200, 100, 50))
        self.assertEqual(image.get(1, 0), (10, 20, 30))
        self.assertEqual(image.get(5, 3), (200, 100, 50))

    def test_the_next_picture_takes_the_place_of_the_one_before_even_when_the_size_is_another(self):
        self.window.present(frame_of(6, 4, (1, 2, 3)))
        self.window.present(frame_of(8, 2, (7, 8, 9)))
        self.assertEqual((self.window.image.width(), self.window.image.height()), (8, 2))
        self.assertEqual(self.window.image.get(7, 1), (7, 8, 9))

    def test_a_full_screen_picture(self):
        rgb = bytes(range(256)) * (640 * 480 * 3 // 256)
        self.window.present(Frame(640, 480, rgb))
        self.assertEqual((self.window.image.width(), self.window.image.height()), (640, 480))
        self.assertEqual(self.window.image.get(0, 0), (0, 1, 2))
        self.assertEqual(self.window.image.get(1, 0), (3, 4, 5))

    def test_it_opens_at_the_size_of_the_screen_of_the_machine_before_any_picture_comes(self):
        self.assertEqual((self.window.canvas.winfo_reqwidth(), self.window.canvas.winfo_reqheight()), (640, 480))

    def test_it_can_be_made_bigger_and_not_smaller_than_the_screen(self):
        self.assertEqual(self.window.root.resizable(), (True, True))
        self.assertEqual(self.window.root.minsize(), (640, 480))

    def test_a_bigger_window_makes_the_picture_bigger_by_whole_numbers_and_keeps_it_in_the_middle(self):
        self.window.present(frame_of(640, 480, (200, 100, 50)))
        self.assertEqual(self.window.zoom, 1)
        for size, zoom in (((1279, 959), 1), ((1280, 960), 2), ((1280, 1100), 2), ((1920, 1080), 2),
                           ((1920, 1440), 3), ((640, 480), 1), ((2560, 1920), 4)):
            self.window.layout(*size)
            self.assertEqual(self.window.zoom, zoom, size)
            self.assertEqual((self.window.shown.width(), self.window.shown.height()), (640 * zoom, 480 * zoom), size)
            self.assertEqual(self.window.canvas.coords(self.window.item), [size[0] // 2, size[1] // 2], size)
            self.assertEqual(self.window.shown.get(640 * zoom - 1, 480 * zoom - 1), (200, 100, 50), size)

    def test_a_zoomed_pixel_is_a_square_of_the_same_color(self):
        self.window.present(frame_of(640, 480, (0, 0, 0)))  # the pixel (1, 0) is (10, 20, 30)
        self.window.layout(1280, 960)
        for x, y in ((2, 0), (3, 0), (2, 1), (3, 1)):
            self.assertEqual(self.window.shown.get(x, y), (10, 20, 30), (x, y))
        self.assertEqual(self.window.shown.get(4, 0), (0, 0, 0))

    def test_the_next_picture_is_shown_at_the_zoom_the_window_has(self):
        self.window.layout(1280, 960)
        self.window.present(frame_of(640, 480, (1, 2, 3)))
        self.assertEqual((self.window.zoom, self.window.shown.width()), (2, 1280))
        self.window.present(frame_of(640, 480, (4, 5, 6)))
        self.assertEqual(self.window.shown.get(1279, 959), (4, 5, 6))

    def test_a_window_that_has_no_picture_yet_can_be_resized(self):
        self.window.layout(1280, 960)
        self.assertEqual(self.window.zoom, 1)
        self.assertIsNone(self.window.shown)

    def test_a_key_pressed_goes_to_the_program_as_scan_and_ascii_codes(self):
        for keysym, char in (("Return", "\r"), ("a", "a"), ("A", "A"), ("Up", ""), ("Escape", "\x1b")):
            self.window.on_key(SimpleNamespace(keysym=keysym, char=char))
        self.assertEqual(self.keys, [(0x1C, 0x0D), (0x1E, 0x61), (0x1E, 0x41), (0x48, 0), (0x01, 0x1B)])

    def test_keys_the_program_is_not_to_see_are_not_passed_on(self):
        for keysym, char in (("Shift_L", ""), ("Control_L", ""), ("Caps_Lock", ""), ("odiaeresis", "ö")):
            self.window.on_key(SimpleNamespace(keysym=keysym, char=char))
        self.assertEqual(self.keys, [])

    def test_polling_an_open_window_is_harmless_and_does_not_close_it(self):
        for _ in range(3):
            self.window.poll()
        self.assertFalse(self.window.closed)
        self.assertEqual(self.keys, [])

    def test_closing_the_window_from_its_close_button_is_noticed(self):
        self.window.on_delete()
        self.assertTrue(self.window.closed)
        self.window.poll()  # nothing happens, and nothing breaks

    def test_a_window_destroyed_from_outside_is_noticed_by_the_next_poll(self):
        self.window.root.destroy()
        self.window.poll()
        self.assertTrue(self.window.closed)

    def test_close_may_be_called_twice(self):
        self.window.close()
        self.window.close()
        self.assertTrue(self.window.closed)
        self.window.poll()


class TestZoom(unittest.TestCase):
    def test_the_largest_whole_number_that_fits_both_ways(self):
        self.assertEqual(zoom_for(640, 480, 640, 480), 1)
        self.assertEqual(zoom_for(1280, 960, 640, 480), 2)
        self.assertEqual(zoom_for(1279, 960, 640, 480), 1)  # not wide enough for 2
        self.assertEqual(zoom_for(1280, 959, 640, 480), 1)  # not high enough for 2
        self.assertEqual(zoom_for(1920, 1080, 640, 480), 2)  # the height says 2, the width would allow 3
        self.assertEqual(zoom_for(3000, 1500, 640, 480), 3)
        self.assertEqual(zoom_for(1000, 3000, 640, 480), 1)

    def test_never_less_than_1_not_even_in_a_window_that_is_too_small(self):
        self.assertEqual(zoom_for(100, 100, 640, 480), 1)
        self.assertEqual(zoom_for(1, 1, 640, 480), 1)
        self.assertEqual(zoom_for(0, 0, 640, 480), 1)

    def test_another_size_of_picture(self):
        self.assertEqual(zoom_for(1280, 800, 320, 200), 4)
        self.assertEqual(zoom_for(1280, 1000, 320, 200), 4)


class TestWithoutAScreen(unittest.TestCase):
    @unittest.skipIf(tkinter is None, "this Python has no tkinter")
    def test_a_window_cannot_be_opened_where_there_is_no_screen(self):
        import os
        from unittest import mock
        from mydos.window import TkWindow, WindowError
        if os.name != "posix":
            self.skipTest("a screen is always there")
        with mock.patch.dict(os.environ):
            for name in ("DISPLAY", "WAYLAND_DISPLAY"):
                os.environ.pop(name, None)
            with self.assertRaises(WindowError) as caught:
                TkWindow(lambda scan, ascii_code: None, hidden=True)
        self.assertIn("display", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
