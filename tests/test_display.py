import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from dosloth.display import Display, PngFile, png_bytes
from dosloth.screen import Frame


def decode(data):
    """Read a PNG the way a viewer would, as far as these files go: (width, height, rgb bytes).

    It checks everything it can on the way: the signature, the length and checksum of every chunk, the header,
    the order of the chunks, that every line starts with filter type 0.
    """
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    position, chunks = 8, []
    while position < len(data):
        (length,) = struct.unpack(">I", data[position:position + 4])
        kind = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + length]
        (crc,) = struct.unpack(">I", data[position + 8 + length:position + 12 + length])
        assert crc == zlib.crc32(kind + body), f"bad checksum in {kind}"
        chunks.append((kind, body))
        position += 12 + length
    assert position == len(data), "something after the last chunk"
    assert [kind for kind, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"], chunks
    width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    assert (depth, color, compression, filtering, interlace) == (8, 2, 0, 0, 0)
    assert chunks[2][1] == b""
    raw = zlib.decompress(chunks[1][1])
    line = width * 3
    assert len(raw) == height * (line + 1), "the data is not the size of the picture"
    rgb = bytearray()
    for y in range(height):
        row = raw[y * (line + 1):(y + 1) * (line + 1)]
        assert row[0] == 0, "a filter other than 0"
        rgb += row[1:]
    return width, height, bytes(rgb)


def picture(width, height):
    return Frame(width, height, bytes((x * 7 + y * 13 + c * 31) & 0xFF for y in range(height) for x in range(width)
                                      for c in range(3)))


class TestPng(unittest.TestCase):
    def test_the_file_holds_the_picture_exactly(self):
        for width, height in ((1, 1), (3, 2), (17, 5), (64, 3)):
            frame = picture(width, height)
            self.assertEqual(decode(png_bytes(frame)), (width, height, frame.rgb), f"{width}x{height}")

    def test_a_screen_sized_picture(self):
        frame = Frame(640, 480, bytes(range(256)) * (640 * 480 * 3 // 256))
        self.assertEqual(decode(png_bytes(frame)), (640, 480, frame.rgb))

    def test_the_header_names_the_size_with_the_width_first(self):
        data = png_bytes(picture(5, 2))
        self.assertEqual(struct.unpack(">II", data[16:24]), (5, 2))

    def test_the_same_picture_gives_the_same_bytes(self):
        self.assertEqual(png_bytes(picture(9, 9)), png_bytes(picture(9, 9)))
        self.assertNotEqual(png_bytes(picture(9, 9)), png_bytes(picture(9, 8)))


class TestPngFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "screen.png"

    def test_presenting_writes_the_file(self):
        frame = picture(8, 4)
        PngFile(self.path).present(frame)
        self.assertEqual(decode(self.path.read_bytes()), (8, 4, frame.rgb))

    def test_the_next_picture_takes_the_place_of_the_one_before(self):
        display = PngFile(self.path)
        display.present(picture(8, 4))
        second = picture(6, 3)
        display.present(second)
        self.assertEqual(decode(self.path.read_bytes()), (6, 3, second.rgb))

    def test_nothing_else_is_left_in_the_directory(self):
        display = PngFile(self.path)
        display.present(picture(8, 4))
        display.present(picture(8, 4))
        display.close()
        self.assertEqual(os.listdir(self.tmp.name), ["screen.png"])

    def test_the_path_may_be_text_or_a_path_object(self):
        PngFile(str(self.path)).present(picture(2, 2))
        self.assertTrue(self.path.exists())

    def test_a_picture_is_in_the_file_only_when_it_is_whole(self):
        display = PngFile(self.path)
        display.present(picture(8, 4))
        seen = []
        real_replace = os.replace

        def spy(source, target):
            seen.append(self.path.read_bytes())  # what a viewer would see just before the swap
            real_replace(source, target)

        os.replace = spy
        try:
            display.present(picture(6, 3))
        finally:
            os.replace = real_replace
        self.assertEqual(decode(seen[0])[:2], (8, 4))  # still the whole of the old one

    def test_it_is_a_display(self):
        self.assertIsInstance(PngFile(self.path), Display)


class TestDisplayInterface(unittest.TestCase):
    def test_the_base_class_shows_nothing_and_says_so(self):
        with self.assertRaises(NotImplementedError):
            Display().present(picture(1, 1))

    def test_closing_a_display_that_does_nothing_on_close_is_allowed(self):
        self.assertIsNone(Display().close())

    def test_anything_with_present_can_stand_in_for_it(self):
        class Collector(Display):
            def __init__(self):
                self.frames = []

            def present(self, frame):
                self.frames.append(frame)

        display = Collector()
        display.present(picture(2, 2))
        display.present(picture(3, 3))
        display.close()
        self.assertEqual([frame.width for frame in display.frames], [2, 3])


if __name__ == "__main__":
    unittest.main()
