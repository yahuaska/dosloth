"""The picture on the screen, worked out from the planes of the video card.

In a 16-color mode the color of a pixel is a number from 0 to 15 whose four bits lie in the four planes: bit 0 in the
first plane, bit 3 in the last, all at the same place. One byte of a plane holds that bit for eight pixels in a row,
the leftmost pixel in the highest bit. A line of the picture is `BYTES_PER_LINE` bytes, one after the other from the
start of the plane.

Only the layout of mode 12h is known here: 640 by 480 pixels, 80 bytes a line, the picture starting at the beginning
of the planes. The start address and the line length that the CRT controller could change are not looked at, and the
colors are the standard 16 of the EGA, whatever palette the program has set up.
"""
import time
from dataclasses import dataclass

from .cpu import Exit

WIDTH = 640
HEIGHT = 480

EGA_COLORS = [
    (0x00, 0x00, 0x00), (0x00, 0x00, 0xAA), (0x00, 0xAA, 0x00), (0x00, 0xAA, 0xAA),
    (0xAA, 0x00, 0x00), (0xAA, 0x00, 0xAA), (0xAA, 0x55, 0x00), (0xAA, 0xAA, 0xAA),
    (0x55, 0x55, 0x55), (0x55, 0x55, 0xFF), (0x55, 0xFF, 0x55), (0x55, 0xFF, 0xFF),
    (0xFF, 0x55, 0x55), (0xFF, 0x55, 0xFF), (0xFF, 0xFF, 0x55), (0xFF, 0xFF, 0xFF),
]

# for every byte the eight pixels it holds, one number (0 or 1) each, the leftmost first
PIXELS_OF_BYTE = [bytes(byte >> (7 - n) & 1 for n in range(8)) for byte in range(256)]


@dataclass(frozen=True)
class Frame:
    """A picture: `rgb` holds three bytes a pixel (red, green, blue), line after line from the top left."""
    width: int
    height: int
    rgb: bytes


def render(planes, palette=EGA_COLORS, width=WIDTH, height=HEIGHT):
    """Make the Frame the four planes (each a sequence of bytes) show, with `palette` for the 16 colors."""
    if width % 8:
        raise ValueError(f"the width must be a multiple of 8, not {width}")
    size = width // 8 * height
    if any(len(plane) < size for plane in planes):
        raise ValueError(f"{width}x{height} pixels need {size} bytes in each plane")
    count = width * height
    # One number holds the whole picture at once: a byte for each pixel. The planes' bits, 0 or 1 in every byte,
    # are put together as bit 0, 1, 2 and 3 of the color without any carrying from one pixel into the next.
    colors = 0
    for number, plane in enumerate(planes):
        bits = b"".join(map(PIXELS_OF_BYTE.__getitem__, bytes(plane[:size])))
        colors |= int.from_bytes(bits, "big") << number
    indexes = colors.to_bytes(count, "big")
    rgb = bytearray(count * 3)
    for channel in range(3):
        table = bytes(color[channel] for color in palette) + bytes(256 - len(palette))
        rgb[channel::3] = indexes.translate(table)
    return Frame(width, height, bytes(rgb))


class Monitor:
    """Everything that shows the picture of the card, looked after together.

    The emulator calls tick() often, and show() at the end. A tick lets every display look after its window, and
    gives them the picture again if the card has been drawn on since the last time. Closing a window stops the
    program, as turning a monitor off would stop a person.
    """

    def __init__(self, card, displays):
        self.card = card
        self.displays = displays
        self.hook = None  # a function(inside) called at every tick: `inside` is True if a program waits for a key

    def show(self):
        """Give every display the picture as it is now."""
        frame = render(self.card.planes, self.card.colors())
        self.card.changed = False
        for display in self.displays:
            display.present(frame)

    def tick(self, inside=False):
        """Look after the displays. `inside` is True when this is called from a program that waits for a key, in the
        middle of its instruction, and not between two instructions."""
        for display in self.displays:
            display.poll()
        if any(display.closed for display in self.displays):
            raise Exit(1)
        if self.card.changed:
            self.show()
        if self.hook is not None:
            self.hook(inside)

    def wait_for_key(self, keyboard):
        """Let the displays go on working until a key is in the keyboard's buffer: what INT 16h AH=00 waits for."""
        while not keyboard.buffer:
            self.tick(inside=True)
            time.sleep(0.01)  # the person is slow, and nothing else is going on

    def close(self):
        for display in self.displays:
            display.close()
