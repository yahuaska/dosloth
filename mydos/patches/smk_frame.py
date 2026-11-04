"""The blocks of a frame of Smacker in SMKP.EXE (the procedure at 10F680, from 10F7B0)."""

from .. import flags
from ..cpu import CPU
from ._base import Patch
from ._common import ARITHMETIC, DWORD, MASK, WORD
from ._smacker import BITS_LEFT


# The loop of the blocks of a frame of Smacker, in the procedure at 10F680. A frame is cut in blocks of 4 by 4 pixels,
# and the stream says, run by run, of what kind the blocks are: a mono block (two colors and a mask of 16 bits), a full
# one (all 16 pixels, as 8 values), a void one (what was there stays) or a solid one (one color). A value is read from
# the stream by a table of its first bits and, if that is not enough, down the tree bit by bit (the trees that the
# procedures above make); the last values are kept in a cache of three.
TREE_DEFAULT = 0x114970  # the 16-bit value of a node that is not a leaf
TREE_AREA = 0x114940  # the start of the trees: the tables give the places counted from it
CACHE_SWAP = 0x114910  # where the code puts a value for a while
RUN = 0x114900  # the blocks that are left of the run
BLOCKS_IN_ROW = 0x114908  # the blocks that are left in the row of blocks of the strip
BLOCKS_PER_ROW = 0x114904  # how many a row of blocks has
ROWS_LEFT = 0x11490C  # the rows of blocks that are left in the strip
ROW_STEP = 0x114920  # what is added to the place, for the next row of blocks
BLOCK_STEP = 0x114924  # what is taken from the place after the four rows of a block, for the next block
SOLID_STEP = 0x11491C
RUN_LENGTHS = 0x114980  # the lengths of a run, by the number that the stream says
# for each kind of value: the pointer to the table of the first bits, the pointers to the three places of the cache,
# how few bits the code wants more for (it is 16 more then), and the mask of the table
KIND_TYPE = (0x1148BC, 0x1148F4, 0x1148F8, 0x1148FC, 8, 0xFF)  # of a run: the kind of its blocks and its length
KIND_COLORS = (0x1148B4, 0x1148D4, 0x1148D8, 0x1148DC, 0x0A, 0x3FF)  # the two colors of a mono block
KIND_MASK = (0x1148B0, 0x1148C4, 0x1148C8, 0x1148CC, 0x0B, 0x7FF)  # the mask of a mono block
KIND_FULL = (0x1148B8, 0x1148E4, 0x1148E8, 0x1148EC, 0x0C, 0xFFF)  # two pixels of a full block
# the code of a row of a mono block has 16 ways, one for each four bits of the mask, that make EAX of the colors in EDX
# (DL and DH): they are at 10FA80 and 16 bytes on; this says how many instructions each is, with its last jump
ROW_COUNTS = (6, 4, 4, 4, 4, 3, 4, 4, 3, 3, 2, 3, 4, 4, 4, 6)
# where the code goes on when the strip is over (it reads the next strip), for each kind of block
STRIP_ENDS = (0x10FF40, 0x1106F0, 0x1107E0, 0x110920)


def _rol(value, count):
    return (value << count | value >> 32 - count) & MASK


def _swap_bytes(value):  # xchg ah, al
    return value & 0xFFFF0000 | (value & 0xFF) << 8 | value >> 8 & 0xFF


# what the 16 ways of the code of a row make of EAX, of the colors in EDX (the first in DL, the second in DH)
ROW_WAYS = (lambda c: (c & 0xFF) * 0x01010101,
            lambda c: _rol(c & 0xFFFF00FF | (c & 0xFF) << 8, 8),
            lambda c: _rol(c & 0xFFFF00FF | (c & 0xFF) << 8, 16),
            lambda c: _rol(_swap_bytes(c), 8),
            lambda c: _rol(c & 0xFFFF00FF | (c & 0xFF) << 8, 24),
            lambda c: _rol(c, 8),
            lambda c: _swap_bytes(_rol(c, 8)),
            lambda c: _rol(c & 0xFFFFFF00 | c >> 8 & 0xFF, 8),
            lambda c: c & 0xFFFF00FF | (c & 0xFF) << 8,
            _swap_bytes,
            lambda c: c,
            lambda c: c & 0xFFFFFF00 | c >> 8 & 0xFF,
            lambda c: _rol(_swap_bytes(c), 24),
            lambda c: _rol(c & 0xFFFFFF00 | c >> 8 & 0xFF, 24),
            lambda c: _rol(c & 0xFFFFFF00 | c >> 8 & 0xFF, 16),
            lambda c: (c >> 8 & 0xFF) * 0x01010101)


def row_of_colors(way, colors):
    """What the way `way` of the code of a row makes of EAX, of the colors in `colors` (EDX)."""
    return ROW_WAYS[way](colors)


def frame_blocks(cpu):
    """What the loop that begins at 10F7B0 does (the patch is in the second instruction of it, for the first one has an
    address that the loading of the program changes, and the INT would have been in the place of that): for every run of
    blocks that the stream says, reads the blocks and draws them (four rows of a dword, EBP apart), goes on to the next
    row of blocks, and then to the next run, until the strip is over: then the code goes on at the place that reads the
    next strip (STRIP_ENDS, one for each kind of block). The registers, the flags and the variables are as the code
    leaves them, and the instructions are counted as the code would have run them. The memory is looked at as dwords
    (the tables, the trees and the cache are dword by dword); only the stream is read from places that are not."""
    with memoryview(cpu.mem) as raw, raw.cast("I") as dwords:
        draw_frame(cpu, dwords)


def draw_frame(cpu, dwords):
    mem = cpu.mem
    base = cpu.bases[cpu.s[CPU.DS]]
    if base & 3:
        raise NotImplementedError("the blocks of a frame in a segment that is not at a dword")
    base4 = base >> 2
    unpack, pack = DWORD.unpack_from, DWORD.pack_into
    eax, ecx, edx, ebx = cpu.wide(CPU.AX), cpu.wide(CPU.CX), cpu.wide(CPU.DX), cpu.wide(CPU.BX)
    esi, edi, ebp = cpu.wide(CPU.SI), cpu.wide(CPU.DI), cpu.wide(CPU.BP)
    default = WORD.unpack_from(mem, base + TREE_DEFAULT)[0]
    area = dwords[base4 + (TREE_AREA >> 2)]
    bits_at = base + BITS_LEFT
    swap_at = base4 + (CACHE_SWAP >> 2)
    executed = 0

    def rd(address):
        return dwords[base4 + (address >> 2)]

    def wr(address, value):
        dwords[base4 + (address >> 2)] = value & MASK

    def resolve(kind):
        """The pointers of a kind of value, as they are now (the code does not change them while the loop goes), and
        the places of the dwords that they point at."""
        table, cache0, cache1, cache2, limit, mask = kind
        places = [rd(cache0), rd(cache1), rd(cache2)]
        if any(place & 3 for place in places) or rd(table) & 3 or area & 3:
            raise NotImplementedError("the tables of the values are not at dwords")
        return (base4 + (rd(table) >> 2), places[0], base4 + (places[0] >> 2), places[1], base4 + (places[1] >> 2),
                places[2], base4 + (places[2] >> 2), limit, mask)

    kind_type, kind_colors, kind_mask, kind_full = (resolve(kind) for kind in (KIND_TYPE, KIND_COLORS, KIND_MASK,
                                                                                KIND_FULL))

    video, window_start, window_end = cpu.video, cpu.video_start, cpu.video_end
    chain_4 = None  # whether the card is in the mode in which a byte is a pixel (it does not change while the loop goes)

    def draw(address, dword):
        """A dword of the picture: in the memory, or in the window of the video card (where the program draws it)."""
        nonlocal chain_4
        address = (base + address) & MASK
        if address + 4 <= window_start or address >= window_end:
            pack(mem, address, dword)
            return
        if chain_4 is None:
            video.check_mode()
            chain_4 = video.chain_4()
        if chain_4 and window_start <= address and address + 4 <= window_end:
            offset = address - window_start
            planes = video.planes
            for number in range(4):
                at = offset + number
                planes[at & 3][at >> 2] = dword >> 8 * number & 0xFF
            video.changed = True
            return
        for number in range(4):
            if window_start <= address + number < window_end:
                video.write_memory(address + number - window_start, dword >> 8 * number & 0xFF)
            else:
                mem[address + number] = dword >> 8 * number & 0xFF

    fast = None  # the planes of the card, if it is in the mode where a byte is a pixel, else False; None: not looked at yet

    def look():
        nonlocal fast
        video.check_mode()
        fast = tuple(video.planes) if video.chain_4() else False

    def draw_block(address, pitch, row0, row1, row2, row3):
        """The four rows of a block: dwords that are `pitch` apart."""
        address = (base + address) & MASK
        if fast is None:
            look()
        if fast and window_start <= address and address + 3 * pitch + 4 <= window_end and not (address | pitch) & 3:
            at = address - window_start >> 2
            step = pitch >> 2
            plane0, plane1, plane2, plane3 = fast
            for row in (row0, row1, row2, row3):
                plane0[at] = row & 0xFF
                plane1[at] = row >> 8 & 0xFF
                plane2[at] = row >> 16 & 0xFF
                plane3[at] = row >> 24
                at += step
            video.changed = True
            return
        for row in (row0, row1, row2, row3):
            draw(address - base, row)
            address = (address + pitch) & MASK

    def fill(address, count, dword):
        """`count` dwords that are the same, in a row."""
        if fast is None:
            look()
        place = (base + address) & MASK
        if fast and count and window_start <= place and place + 4 * count <= window_end and not place & 3:
            at = place - window_start >> 2
            for number, plane in enumerate(fast):
                plane[at:at + count] = bytes((dword >> 8 * number & 0xFF,)) * count
            video.changed = True
            return
        for number in range(count):
            draw(address + 4 * number, dword)

    def value(kind, ror):
        """What the code that reads a value does. The value is in the upper 16 bits of the leaf: with `ror` it goes to AX,
        else the leaf is the whole EAX."""
        nonlocal eax, ecx, edx, ebx, esi, executed
        entries, place0, index0, place1, index1, place2, index2, limit, mask = kind
        left = mem[bits_at]
        if left <= limit:  # fewer bits than the table looks at: 16 more from the stream
            at = base + esi
            ebx = ((dwords[at >> 2] if not at & 3 else unpack(mem, at)[0]) << ((left - 1) & 31)) & MASK | ebx
            esi = (esi + 2) & MASK
            left = (left + 16) & 0xFF
            executed += 20
            refill = 2
        else:
            executed += 13
            refill = 1
        entry = dwords[entries + (ebx & mask)]
        ebx >>= entry & 31
        left = (left - entry) & 0xFF
        ecx = ((entry >> 8) + area) & MASK
        edx = dwords[base4 + (ecx >> 2)]
        if edx & 0xFFFF == default:  # not a leaf: down the tree, a bit at a time
            executed += refill
            while True:
                step = edx >> 13 & 0xFFFF8
                left = (left - 1) & 0xFF
                executed += 10
                if left == 0:
                    at = base + esi
                    ebx = dwords[at >> 2] if not at & 3 else unpack(mem, at)[0]
                    esi = (esi + 4) & MASK
                    left = 0x20
                    executed += 3
                if not ebx & 1:
                    step = 4
                    executed += 1
                ebx >>= 1
                ecx = (ecx + step) & MASK
                edx = dwords[base4 + (ecx >> 2)]
                if edx & 0xFFFF != default:
                    break
        else:
            executed += 1
        mem[bits_at] = left
        if ror:
            eax = eax & 0xFFFF0000 | edx >> 16
            executed += 7
        else:
            eax = edx
            executed += 5
        ecx = place0
        if dwords[index0] != edx:  # not the first of the cache: it becomes the first, and the others go down
            dwords[swap_at] = eax
            eax = dwords[index0]
            dwords[index0] = edx
            edx = dwords[index1]
            dwords[index1] = eax
            ecx = place2
            eax = dwords[swap_at]
            dwords[index2] = edx
            executed += 9

    def leave(label, carry):
        for number, register in ((CPU.AX, eax), (CPU.CX, ecx), (CPU.DX, edx), (CPU.BX, ebx), (CPU.SI, esi),
                                 (CPU.DI, edi), (CPU.BP, ebp)):
            cpu.set32(number, register)
        cpu.flags = (cpu.flags & ~ARITHMETIC) | flags.ZF | flags.PF | (flags.CF if carry else 0)
        cpu.instructions += executed - 2  # (the `mov al` before the INT was run, and the INT stands for the next one)
        cpu.ip = label

    while True:
        value(kind_type, True)  # what the blocks of the run are, and how many
        edx = rd(RUN_LENGTHS + (eax & 0xFC))
        kind = eax & 3
        ecx = kind
        wr(RUN, edx)
        executed += 7
        if kind <= 1:  # a mono block (0) or a full one (1)
            while True:
                if kind == 0:
                    value(kind_colors, False)
                    value(kind_mask, True)
                    ecx = eax
                    edx_mask = eax
                    eax = (eax >> 16 | eax << 16) & MASK
                    edx = edx_mask & 0xFFFF0000 | eax & 0xFFFF  # the colors, in both halves
                    eax = (eax >> 28 | ecx << 4) & MASK
                    way = eax >> 4 & 0xF
                    row0 = ROW_WAYS[way](edx)
                    executed += 8 + ROW_COUNTS[way]
                    way = ecx >> 4 & 0xF
                    row1 = ROW_WAYS[way](edx)
                    executed += 6 + ROW_COUNTS[way]
                    way = ecx >> 8 & 0xF
                    row2 = ROW_WAYS[way](edx)
                    executed += 5 + ROW_COUNTS[way]
                    way = ecx >> 12 & 0xF
                    eax = row3 = ROW_WAYS[way](edx)
                    executed += 5 + ROW_COUNTS[way]
                else:
                    value(kind_full, False)
                    value(kind_full, True)
                    row0 = eax
                    value(kind_full, False)
                    value(kind_full, True)
                    row1 = eax
                    value(kind_full, False)
                    value(kind_full, True)
                    row2 = eax
                    value(kind_full, False)
                    value(kind_full, True)
                    row3 = eax
                    executed += 4  # the stores of the first three rows, and the step of EDI after the first
                draw_block(edi, ebp, row0, row1, row2, row3)
                edi = (edi + ebp) & MASK
                step = rd(BLOCK_STEP)
                carry = edi < step
                edi = (edi - step) & MASK
                executed += 4
                in_row = (rd(BLOCKS_IN_ROW) - 1) & MASK
                wr(BLOCKS_IN_ROW, in_row)
                if in_row == 0:
                    rows = (rd(ROWS_LEFT) - 1) & MASK
                    wr(ROWS_LEFT, rows)
                    executed += 2
                    if rows == 0:
                        return leave(STRIP_ENDS[kind], carry)
                    edi = (edi + rd(ROW_STEP)) & MASK
                    eax = rd(BLOCKS_PER_ROW)
                    wr(BLOCKS_IN_ROW, eax)
                    executed += 3
                run = (rd(RUN) - 1) & MASK
                wr(RUN, run)
                executed += 2
                if run == 0:
                    executed += 1
                    break
        elif kind == 2:  # void blocks: the place goes on, nothing is drawn
            while True:
                ecx = rd(BLOCKS_IN_ROW)
                executed += 3
                if edx < ecx:
                    ecx = edx
                    executed += 1
                edi = (edi + 4 * ecx) & MASK
                in_row = (rd(BLOCKS_IN_ROW) - ecx) & MASK
                wr(BLOCKS_IN_ROW, in_row)
                executed += 3
                if in_row == 0:
                    rows = (rd(ROWS_LEFT) - 1) & MASK
                    wr(ROWS_LEFT, rows)
                    executed += 2
                    if rows == 0:
                        return leave(STRIP_ENDS[2], False)
                    edi = (edi + rd(ROW_STEP)) & MASK
                    eax = rd(BLOCKS_PER_ROW)
                    wr(BLOCKS_IN_ROW, eax)
                    executed += 3
                edx = (edx - ecx) & MASK
                executed += 2
                if edx == 0:
                    executed += 1
                    break
        else:  # solid blocks: a color
            ecx = eax = eax & 0xFFFFFF00 | eax >> 8 & 0xFF
            eax = (eax << 16) & MASK | ecx & 0xFFFF
            executed += 4
            while True:
                ecx = rd(BLOCKS_IN_ROW)
                executed += 3
                if rd(RUN) < ecx:
                    ecx = rd(RUN)
                    executed += 1
                wr(CACHE_SWAP, ebp)
                edx = 4 * ecx & MASK
                ebp = (ebp - edx) & MASK
                edx = ecx
                for row in range(4):
                    fill(edi, ecx, eax)
                    edi = (edi + 4 * ecx) & MASK
                    if row < 3:
                        edi = (edi + ebp) & MASK
                ecx = 0
                edi = (edi - rd(SOLID_STEP)) & MASK
                ebp = rd(CACHE_SWAP)
                executed += 16
                in_row = (rd(BLOCKS_IN_ROW) - edx) & MASK
                wr(BLOCKS_IN_ROW, in_row)
                executed += 2
                if in_row == 0:
                    rows = (rd(ROWS_LEFT) - 1) & MASK
                    wr(ROWS_LEFT, rows)
                    executed += 2
                    if rows == 0:
                        return leave(STRIP_ENDS[3], False)
                    edi = (edi + rd(ROW_STEP)) & MASK
                    run = (rd(RUN) - edx) & MASK
                    wr(RUN, run)
                    ecx = rd(BLOCKS_PER_ROW)
                    wr(BLOCKS_IN_ROW, ecx)
                    executed += 5
                else:
                    run = (rd(RUN) - edx) & MASK
                    wr(RUN, run)
                    executed += 2
                if run == 0:
                    executed += 1
                    break


PATCHES = [Patch("the blocks of a frame of Smacker", 0x10F7B5, 0x120B,
                 "f0cc5fa2a549d8f52976276926fb9af8ca6c0bc0240fda6daaa61c18a98d1e53", frame_blocks)]
