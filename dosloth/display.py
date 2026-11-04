"""Where the pictures go: the one interface the emulator shows them through, and a first thing behind it.

The emulator only knows a `Display`. It calls `present(frame)` with the picture as it is now, every so often and at
the end, and `close()` when it is done. A window, a file or anything else is a Display, and can be put in its
place without the rest of the emulator knowing.
"""
import os
import struct
import zlib


class Display:
    """What shows pictures. Make one of these by giving it `present`.

    A display that has a window, or anything else that wants to be looked after, also gives `poll`, which the
    emulator calls often, and sets `closed` when the person who watches has closed the window.
    """

    closed = False

    def present(self, frame):
        """Show the picture (a screen.Frame). It replaces the one shown before."""
        raise NotImplementedError

    def poll(self):
        """Look after what the display needs to be looked after: its window's events, for one."""

    def close(self):
        """Nothing more will be shown."""


def png_bytes(frame):
    """The contents of a PNG file that holds the picture, in 8-bit RGB."""
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    line = frame.width * 3
    raw = b"".join(b"\0" + frame.rgb[y * line:(y + 1) * line] for y in range(frame.height))  # filter 0: as it is
    header = struct.pack(">IIBBBBB", frame.width, frame.height, 8, 2, 0, 0, 0)  # 8 bits, truecolor, no interlace
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 1)) +
            chunk(b"IEND", b""))


class PngFile(Display):
    """Every picture goes to one PNG file, over the one before. The file is swapped in whole, so a program that
    looks at it while the emulator runs never sees half a picture."""

    def __init__(self, path):
        self.path = os.fspath(path)

    def present(self, frame):
        temporary = self.path + ".tmp"
        with open(temporary, "wb") as file:
            file.write(png_bytes(frame))
        os.replace(temporary, self.path)
