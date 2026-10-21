"""The picture on the screen, worked out from the planes of the video card.

In a 16-color mode the color of a pixel is a number from 0 to 15 whose four bits lie in the four planes: bit 0 in the
first plane, bit 3 in the last, all at the same place. One byte of a plane holds that bit for eight pixels in a row,
the leftmost pixel in the highest bit. A line of the picture is `BYTES_PER_LINE` bytes, one after the other from the
start of the plane.

Only the layout of mode 12h is known here: 640 by 480 pixels, 80 bytes a line, the picture starting at the beginning
of the planes. The start address and the line length that the CRT controller could change are not looked at, and the
colors are the standard 16 of the EGA, whatever palette the program has set up.
"""
from dataclasses import dataclass

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
