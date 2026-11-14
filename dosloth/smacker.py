"""Smacker video files (`.smk`, the signature SMK2), decoded in Python: no CPU, no memory of the machine.

It is written from what is known of the format, with the table of the lengths of a run taken from the player of the
game (SMKP.EXE; the patches `smk_*` do parts of the same work there, on the machine), and checked against the pictures
that the emulator makes when it runs that player: every frame of the three videos of the game, its pixels and its
colors, is the same (`tests/test_smacker.py` keeps a part of that). A kind of file that the videos of the game are not
(another signature, flags in the header) stops loudly. Some parts that none of them has are done as the format is
known, and checked only by the small videos of the tests, or not at all: a tree that is not there, a last value that a
tree does not say, the flags in the sizes of the frames, a rate in milliseconds.

The file: a header of 68h bytes, the size of every frame (a dword; its two low bits are flags), the type of every frame
(a byte: bit 0 a palette, bits 1-7 the sound tracks that are in it), the Huffman trees of the video, then the frames.
A frame: the palette (if its type says so), the sound tracks (each a dword of its size, the dword counted, then the
data), and the rest is the picture.

The picture is cut in blocks of 4 by 4 pixels. A stream of bits, read from the lowest bit of each byte up, says run by
run what the blocks are: mono (two colors and a mask of 16 bits), full (16 pixels, 2 to a value), void (they stay as
they were) or solid (one color). The values come from four big Huffman trees (the types of the runs, the masks and the
colors of mono blocks, the pixels of full ones); each tree keeps its three last values in three of its leaves, that the
file says, and these are 0 again at the start of every frame.
"""
import struct

SIGNATURE = b"SMK2"
HEADER_SIZE = 0x68
BLOCK = 4
# the lengths of a run of blocks, by the 6 bits that the type of a run has for it (the table at 114980 of SMKP.EXE)
RUN_LENGTHS = tuple(range(1, 60)) + (128, 256, 512, 1024, 2048)
MONO, FULL, VOID, SOLID = range(4)
TABLE_BITS = 12  # the first bits of a value that a table of a tree is made for; longer ways are gone down bit by bit


class SmackerError(Exception):
    """The file is not a Smacker video that this knows how to show."""


class Bits:
    """A stream of bits, read from the lowest bit of each byte up."""

    def __init__(self, data, start=0, end=None):
        self.data = bytes(data[start:end]) + bytes(8)  # (the reads look a few bytes past the end)
        self.pos = 0
        self.end = (len(self.data) - 8) * 8

    def peek(self):
        """At least 57 of the next bits, the next one lowest."""
        pos = self.pos
        return int.from_bytes(self.data[pos >> 3:(pos >> 3) + 8], "little") >> (pos & 7)

    def get(self, count):
        value = self.peek() & ((1 << count) - 1)
        self.pos += count
        return value

    def bit(self):
        pos = self.pos
        self.pos += 1
        return self.data[pos >> 3] >> (pos & 7) & 1


def byte_tree(bits):
    """A tree of 8-bit values in the header of a big tree: {(length, code): value}, or None when it is not there (all its
    values are 0 then). A bit 1 is a node (its left side, the bit 0, comes first), a bit 0 a leaf and 8 bits of it."""
    if not bits.bit():
        return None
    leaves = {}

    def node(code, length):
        if bits.bit():
            node(code, length + 1)
            node(code | 1 << length, length + 1)
        else:
            leaves[length, code] = bits.get(8)
    node(0, 0)
    if bits.bit():
        raise SmackerError("a byte tree that does not end with a bit 0")
    return leaves


def read_by(bits, leaves):
    """A value of a byte tree, going down it bit by bit."""
    if leaves is None:
        return 0
    code = length = 0
    while (length, code) not in leaves:
        code |= bits.bit() << length
        length += 1
        if length > 32:
            raise SmackerError("a way down a byte tree that does not end")
    return leaves[length, code]


class BigTree:
    """A big tree of 16-bit values. `values` are those of its leaves, in the order of the file; three of them (`last`)
    are the three last values that were read, and start at 0 in every frame. A way down the tree is found by a table
    of its first TABLE_BITS bits, or, for a longer one, by the dict of the longer ways."""

    def __init__(self, bits):
        self.values = []
        self.last = [None, None, None]
        if not bits.bit():  # no tree: every value is 0, and no bits are read for one
            self.values.append(0)
            self.table, self.long_ways, self.bits = None, {}, 0
            self.last = [self.add_leaf(0) for _ in range(3)]
            return
        low, high = byte_tree(bits), byte_tree(bits)
        escapes = [bits.get(16) for _ in range(3)]
        ways = []  # (length, code, leaf)

        def node(code, length):
            if bits.bit():
                node(code, length + 1)
                node(code | 1 << length, length + 1)
                return
            value = read_by(bits, low) | read_by(bits, high) << 8
            leaf = len(self.values)
            for number, escape in enumerate(escapes):  # (the first escape that the value is)
                if value == escape:
                    self.last[number] = leaf
                    value = 0
                    break
            self.values.append(value)
            ways.append((length, code, leaf))
        node(0, 0)
        if bits.bit():
            raise SmackerError("a big tree that does not end with a bit 0")
        self.last = [leaf if leaf is not None else self.add_leaf(0) for leaf in self.last]
        self.bits = min(TABLE_BITS, max(length for length, _, _ in ways))
        self.table = [(0, None)] * (1 << self.bits)  # (the first bits of a longer way: it is looked for in long_ways)
        self.long_ways = {}
        for length, code, leaf in ways:
            if length <= self.bits:
                for rest in range(1 << (self.bits - length)):
                    self.table[code | rest << length] = (length, leaf)
            else:
                self.long_ways[length, code] = leaf
        self.longest = max(length for length, _, _ in ways)

    def add_leaf(self, value):
        """A leaf that no way goes to: the place of a last value that the tree does not say."""
        self.values.append(value)
        return len(self.values) - 1

    def reset(self):
        """The start of a frame: the three last values are 0."""
        for leaf in self.last:
            self.values[leaf] = 0

    def read(self, bits):
        """The next value: down the tree by the bits, and it becomes the last value (if it is not that already)."""
        values, last = self.values, self.last
        if self.table is None:
            value = values[0]
        else:
            ahead = bits.peek()
            length, leaf = self.table[ahead & ((1 << self.bits) - 1)]
            if leaf is None:
                length, leaf = self.long_way(ahead)
            bits.pos += length
            value = values[leaf]
        if value != values[last[0]]:
            values[last[2]] = values[last[1]]
            values[last[1]] = values[last[0]]
            values[last[0]] = value
        return value

    def long_way(self, ahead):
        for length in range(self.bits + 1, self.longest + 1):
            leaf = self.long_ways.get((length, ahead & ((1 << length) - 1)))
            if leaf is not None:
                return length, leaf
        raise SmackerError("bits that are no way down a big tree")


class Video:
    """A Smacker video in memory (`data`, the whole file). `frames()` gives every frame as (the pixels, a byte a pixel,
    line after line; the palette, 768 bytes of red, green and blue in 6 bits, as the DAC of the card takes them)."""

    def __init__(self, data):
        if len(data) < HEADER_SIZE or data[:4] != SIGNATURE:
            raise SmackerError(f"not a Smacker video of the kind {SIGNATURE.decode()}: {bytes(data[:4])!r}")
        (self.width, self.height, self.count, rate, flags) = struct.unpack_from("<4x5i", data)
        if flags:
            raise SmackerError(f"a Smacker video with the flags {flags:X} (a ring of frames, lines doubled or "
                               "interlaced): not done")
        if self.width <= 0 or self.height <= 0 or self.width % BLOCK or self.height % BLOCK:
            raise SmackerError(f"a picture of {self.width}x{self.height}: not whole blocks of {BLOCK}")
        # the time of a frame: in milliseconds if it is more than 0, in 1/100000 of a second if less, 10 frames a second
        # if 0
        self.frame_ms = rate if rate > 0 else -rate / 100 if rate < 0 else 100
        tree_size = struct.unpack_from("<I", data, 0x34)[0]
        sizes_at = HEADER_SIZE
        types_at = sizes_at + 4 * self.count
        trees_at = types_at + self.count
        self.sizes = [size & ~3 for size in struct.unpack_from(f"<{self.count}I", data, sizes_at)]
        self.types = data[types_at:types_at + self.count]
        frames_at = trees_at + tree_size
        if frames_at + sum(self.sizes) != len(data):
            raise SmackerError(f"the frames end at {frames_at + sum(self.sizes)}, the file at {len(data)}")
        self.data = data
        self.trees_at, self.tree_size, self.frames_at = trees_at, tree_size, frames_at

    def trees(self):
        bits = Bits(self.data, self.trees_at, self.trees_at + self.tree_size)
        return [BigTree(bits) for _ in range(4)]  # the masks of mono blocks, their colors, full blocks, the types

    def frames(self):
        """Every frame in turn, as (pixels, palette): the same bytearrays, changed for every frame."""
        masks, colors, fulls, types = self.trees()
        pixels = bytearray(self.width * self.height)
        palette = bytearray(768)
        at = self.frames_at
        for number in range(self.count):
            size, kind = self.sizes[number], self.types[number]
            end = at + size
            if kind & 1:
                at = new_palette(self.data, at, palette)
            for track in range(7):
                if kind & 2 << track:
                    length = struct.unpack_from("<I", self.data, at)[0]
                    if length < 4:
                        raise SmackerError(f"frame {number}: sound track {track} of {length} bytes")
                    at += length  # (the sound is not played)
            if at > end:
                raise SmackerError(f"frame {number}: the palette and the sound go past the end of the frame")
            for tree in (masks, colors, fulls, types):
                tree.reset()
            draw(Bits(self.data, at, end), pixels, self.width, self.height, masks, colors, fulls, types)
            at = end
            yield pixels, palette


def new_palette(data, at, palette):
    """The palette that a frame has: a byte of its length in dwords (the byte counted), then for the 256 colors in turn,
    a byte that says: 80h + n, the next n + 1 colors stay as they are; 40h + n and a byte b, the next n + 1 colors are the
    colors from b on of the palette before; else, it is the red of a new color, and green and blue follow (6 bits each).
    Returns where what follows the palette is."""
    end = at + data[at] * 4
    old = bytes(palette)
    color = 0
    at += 1
    while color < 256:
        if at >= end:
            raise SmackerError("a palette that ends before its 256 colors")
        op = data[at]
        if op & 0x80:
            color += (op & 0x7F) + 1
            at += 1
        elif op & 0x40:
            count, start = (op & 0x3F) + 1, data[at + 1]
            if start + count > 256:
                raise SmackerError("a palette that copies colors past the 256")
            count = min(count, 256 - color)
            palette[color * 3:(color + count) * 3] = old[start * 3:(start + count) * 3]
            color += count
            at += 2
        else:
            palette[color * 3:color * 3 + 3] = bytes(part & 0x3F for part in data[at:at + 3])
            color += 1
            at += 3
    if color != 256:
        raise SmackerError("a palette that goes past its 256 colors")
    return end


# the four pixels of a row of a mono block, by four bits of its mask: which of the two colors each is (bit 0 the left)
MONO_ROWS = [tuple(nibble >> pixel & 1 for pixel in range(4)) for nibble in range(16)]


def draw(bits, pixels, width, height, masks, colors, fulls, types):
    """Draw the blocks of a frame on the pixels of the one before."""
    across = width // BLOCK
    blocks = across * (height // BLOCK)
    block = 0
    row4 = width * BLOCK
    rows = [width * row for row in range(BLOCK)]
    read_type, read_mask, read_colors, read_full = types.read, masks.read, colors.read, fulls.read
    while block < blocks:
        value = read_type(bits)
        run = min(RUN_LENGTHS[value >> 2 & 0x3F], blocks - block)
        kind = value & 3
        if kind == VOID:
            block += run
            continue
        for block in range(block, block + run):
            at = block // across * row4 + block % across * BLOCK
            if kind == MONO:
                pair = read_colors(bits)
                lo, hi = pair & 0xFF, pair >> 8
                mask = read_mask(bits)
                for row in rows:
                    a, b, c, d = MONO_ROWS[mask & 15]
                    place = at + row
                    pixels[place] = hi if a else lo
                    pixels[place + 1] = hi if b else lo
                    pixels[place + 2] = hi if c else lo
                    pixels[place + 3] = hi if d else lo
                    mask >>= 4
            elif kind == FULL:
                for row in rows:
                    right = read_full(bits)
                    left = read_full(bits)
                    place = at + row
                    pixels[place:place + 4] = (left | right << 16).to_bytes(4, "little")
            else:  # SOLID
                color = bytes((value >> 8,)) * BLOCK
                for row in rows:
                    pixels[at + row:at + row + BLOCK] = color
        block += 1
    if bits.pos > bits.end:
        raise SmackerError("the blocks of a frame go past its end")
