"""Places in a program where Python does the work of the code, for the code to be fast enough.

The emulation of an instruction costs some microseconds, and a few loops of the game take nearly all the instructions
of a run (see the profile, `-p`). A patch is made for such a loop: the first two bytes of the code are replaced by
`INT F0h`, and the handler of that interrupt does what the code would have done, in Python, and then sets the
instruction pointer where the code goes on. A patch is made for one exact piece of code: the bytes that it replaces
are checked against a hash, so that another version of the program, or another program, is left as it is.

The interrupt is ours alone: no table of vectors is touched, and the handler is found by the address of the `INT`.
The memory of a patched program keeps the `INT`, so a saved state has it too, and `install` has to be made in every
machine, patched or not, to know what the `INT` means.
"""
import hashlib
import struct

from . import flags
from .cpu import CPU

HOOK = 0xF0  # the number of the interrupt that stands in for the first instruction of a patched piece of code
PATCH_BYTES = bytes((0xCD, HOOK))

DWORD = struct.Struct("<I")
WORD = struct.Struct("<H")
MASK = 0xFFFFFFFF


class Patch:
    """One piece of code: where it is (a linear address, which is the offset in the flat mode), how long it is, the
    SHA-256 of its bytes, and the function (`handler(cpu)`) that does its work."""

    def __init__(self, name, address, length, digest, handler):
        self.name, self.address, self.length, self.digest, self.handler = name, address, length, digest, handler

    def matches(self, cpu):
        """Whether the memory holds the very code that this patch is for."""
        return hashlib.sha256(cpu.mem[self.address:self.address + self.length]).hexdigest() == self.digest


# The code of SMKP.EXE (the Smacker player) that reads a Huffman tree from the stream of bits.
BITS_LEFT = 0x114960  # a byte: how many bits are left in the buffer, and one more
LOW_TREE = 0x114928  # the pointers to the trees of the low and the high byte of a value
HIGH_TREE = 0x11492C
ESCAPES = 0x114A80  # three 16-bit values that are in the stream first
TREE_END = 0x10E067  # where the code goes on after the tree is read: its last few instructions, up to RET


def big_tree(cpu):
    """What the code at 10DD90 does: reads the six bytes of the three escape values, clears the 16 bytes at EDX, and
    then the tree itself: a bit 1 is a node that waits for its right side, a bit 0 is a leaf whose value is made of a
    byte from the tree of the low byte and one from the tree of the high byte (each is read from the bits by going
    down the tree: a bit 1 goes by the offset in the node, a bit 0 to the next dword; a leaf is a dword with its
    top bit set and the byte in it). A leaf is put at EDI, and the node it completes gets the offset of what follows. A
    value that is an escape value is put as 0, and the place of the leaf is remembered at EDX+4, +8 or +0Ch. A zero
    bit after all this ends it. The registers are left as the code leaves them at TREE_END."""
    mem = cpu.mem
    base = cpu.bases[cpu.s[CPU.DS]]
    unpack, pack = DWORD.unpack_from, DWORD.pack_into
    source, buffer, out, cache = (cpu.wide(CPU.SI), cpu.wide(CPU.BX), cpu.wide(CPU.DI), cpu.wide(CPU.DX))
    left = mem[base + BITS_LEFT]
    low, high = unpack(mem, base + LOW_TREE)[0], unpack(mem, base + HIGH_TREE)[0]

    escapes = []
    for _ in range(3):
        pair = []
        for _ in range(2):  # a byte: from the buffer, or when it holds less than 8 bits, the rest from the next dword
            byte = buffer & 0xFF
            if left >= 9:
                buffer >>= 8
                left -= 8
            else:
                need = 9 - left
                buffer = unpack(mem, base + source)[0]
                source = (source + 4) & MASK
                byte |= buffer << (left - 1) & 0xFF
                buffer >>= need
                left = 33 - need
            pair.append(byte)
        escapes.append(pair[0] | pair[1] << 8)
        WORD.pack_into(mem, base + ESCAPES + 2 * (len(escapes) - 1), escapes[-1])
    mem[base + cache:base + cache + 16] = bytes(16)

    stack = [MASK, MASK - 1]  # the bottom, and the mark that is above it
    eax = 0
    while True:
        left -= 1
        if left == 0:
            buffer = unpack(mem, base + source)[0]
            source = (source + 4) & MASK
            left = 0x20
        bit = buffer & 1
        buffer >>= 1
        if bit:
            stack.append(out)  # a node: its place waits for the offset
            out = (out + 4) & MASK
            continue
        waiting = stack.pop()
        if waiting == MASK:
            break
        values = []
        for root in (low, high):
            at = root
            node = unpack(mem, base + at)[0]
            while not node & 0x80000000:
                left -= 1
                if left == 0:
                    buffer = unpack(mem, base + source)[0]
                    source = (source + 4) & MASK
                    left = 0x20
                bit = buffer & 1
                buffer >>= 1
                at = (at + (node if bit else 4)) & MASK
                node = unpack(mem, base + at)[0]
            values.append(node)
        eax = (values[1] & 0xFF) << 8 | values[0] & 0xFF  # (what the code has above these 16 bits is shifted out)
        for number, escape in enumerate(escapes):
            if eax & 0xFFFF == escape:
                eax = 0
                pack(mem, base + cache + 4 + 4 * number, out)
                break
        eax = (eax << 16 & MASK) | 1
        pack(mem, base + out, eax)
        out = (out + 4) & MASK
        if waiting != MASK - 1:
            eax = (out - waiting) << 13 & MASK
            pack(mem, base + waiting, eax)

    mem[base + BITS_LEFT] = left
    for number, value in ((CPU.AX, eax), (CPU.CX, MASK), (CPU.DX, cache), (CPU.BX, buffer), (CPU.SI, source),
                          (CPU.DI, out)):
        cpu.set32(number, value)
    cpu.ip = TREE_END


# The three procedures that the code of the player calls when the trees are read, to make of them the tables it looks
# up (10E090), and to note which values are in them (10E1A0) and give the values that are not a default (10E1D0).
TABLE_CODE = 0x114940  # the start of the area of the trees
TABLE_DEPTH = 0x114950  # how many bits a table is looked up by
TABLE_SIZE = 0x114954  # how many entries it has
TABLE_ROOT = 0x114910  # where the code remembers the root
DEFAULT = 0x114970  # the 16-bit value of an entry that is not a leaf
ARITHMETIC = flags.CF | flags.PF | flags.AF | flags.ZF | flags.SF | flags.OF


def after_counting(cpu, carry=False):
    """The flags a loop that ends with `dec` of a register that came to 0 leaves: ZF and PF, with the carry that
    the instruction before it left; the others are clear."""
    cpu.flags = (cpu.flags & ~ARITHMETIC) | flags.ZF | flags.PF | (flags.CF if carry else 0)


def give_back(cpu):
    """RET: the address on the stack is where the program goes on."""
    cpu.ip = cpu.pop32() & cpu.ip_mask


def lookup_table(cpu):
    """What 10E090 does: for each of the numbers from TABLE_SIZE-1 down to 0 goes down the tree that EDX points at
    (the nodes are what big_tree writes: a leaf has something in the low 16 bits, a node has the offset of its right
    side in the bits above the 13th), by the bits of the number from the lowest, as far as TABLE_DEPTH bits or to a
    leaf, and puts where it came (counted from TABLE_CODE, in the bits above the 8th) and how many bits it took (in
    the low byte) at EDI + 4 * the number."""
    mem = cpu.mem
    base = cpu.bases[cpu.s[CPU.DS]]
    unpack, pack = DWORD.unpack_from, DWORD.pack_into
    root, table, count = cpu.wide(CPU.DX), cpu.wide(CPU.DI), unpack(mem, base + TABLE_SIZE)[0]
    depth, area = mem[base + TABLE_DEPTH], unpack(mem, base + TABLE_CODE)[0]
    pack(mem, base + TABLE_ROOT, root)
    counter, edx, used = count, root, 0
    while counter:
        number, left, edx = (counter - 1) & 0xFFFF, depth, root
        node = unpack(mem, base + edx)[0]
        while not node & 0xFFFF and left:  # a node, and not at the depth yet
            left -= 1
            edx = (edx + ((node >> 13) if number & 1 else 4)) & MASK
            number >>= 1
            node = unpack(mem, base + edx)[0]
        used = (depth - left) & 0xFF
        edx = ((edx - area) << 8 & MASK) | used
        pack(mem, base + table + 4 * counter - 4, edx)
        counter -= 1
    cpu.set32(CPU.AX, cpu.wide(CPU.AX) & 0xFFFF0000)  # the last number is 0: what is left of it in AX
    cpu.r[CPU.CX] = cpu.r[CPU.CX] & 0xFF00 | used
    cpu.set32(CPU.DX, edx)
    after_counting(cpu, carry=used != 0)  # the carry is NEG's of the bits that were taken
    give_back(cpu)


def entries_of(cpu):
    """The place and the number of entries of the lists that 10E1A0 and 10E1D0 go through: ESI plus 7468h, and EAX / 4
    dwords."""
    return (cpu.wide(CPU.SI) + 0x7468) & MASK, cpu.wide(CPU.AX) >> 2


def mark_values(cpu):
    """What 10E1A0 does: for each dword of the list that is a leaf (the lowest bit set) sets the bit that its upper
    16 bits number in the bitmap at EDI."""
    mem = cpu.mem
    base = cpu.bases[cpu.s[CPU.DS]]
    start, count = entries_of(cpu)
    count = count or 1 << 32  # (a count of 0 is a loop that goes round 2**32 times)
    bitmap, last = cpu.wide(CPU.DI), None
    for at in range(count):
        entry = DWORD.unpack_from(mem, base + start + 4 * at)[0]
        if entry & 1:
            last = entry >> 16
            mem[base + bitmap + (last >> 3)] |= 1 << (last & 7)
    if last is not None:
        cpu.set32(CPU.DX, last >> 3)
        cpu.r[CPU.CX] = (1 << (last & 7)) << 8 | last & 7
    cpu.set32(CPU.SI, start + 4 * count)
    cpu.set32(CPU.AX, 0)
    after_counting(cpu)
    give_back(cpu)


def fill_values(cpu):
    """What 10E1D0 does: for each dword of the list puts in its low 16 bits the upper 16 if it is a leaf, and the
    default (DEFAULT) if not."""
    mem = cpu.mem
    base = cpu.bases[cpu.s[CPU.DS]]
    start, count = entries_of(cpu)
    default = WORD.unpack_from(mem, base + DEFAULT)[0]
    last = None
    for at in range(count or 1 << 32):
        where = base + start + 4 * at
        if mem[where] & 1:
            last = WORD.unpack_from(mem, where + 2)[0]
            WORD.pack_into(mem, where, last)
        else:
            WORD.pack_into(mem, where, default)
    if last is not None:
        cpu.r[CPU.DX] = last
    cpu.set32(CPU.SI, start + 4 * (count or 1 << 32))
    cpu.set32(CPU.AX, 0)
    after_counting(cpu)
    give_back(cpu)

# The loops of the same code that go down a tree of Huffman, bit by bit, to a leaf. The code has three of them, one
# for each of the three trees of a value (the low byte, the high byte, and the third); each is eight instructions
# for a bit, and they are four of every ten instructions of the player.
def down_the_tree(cpu, leaf_at):
    """What the loop does (it is at the first instruction of the loop, EDX is the node and EAX what is in it, which
    is not a leaf): takes a bit from EBX (when BITS_LEFT is counted out, the next dword from ESI first), goes by it
    (a bit 1 goes by the offset in EAX, a bit 0 to the next dword), and goes on until the dword is a leaf (its top bit
    set). The program goes on at LEAF_AT with the flags of the `test` that found the leaf. The instructions that the
    loop would have run are put in the count of them, so that the time of the program is what it was."""
    mem = cpu.mem
    base = cpu.bases[cpu.s[CPU.DS]]
    unpack = DWORD.unpack_from
    buffer, source, node_at, node = cpu.wide(CPU.BX), cpu.wide(CPU.SI), cpu.wide(CPU.DX), cpu.wide(CPU.AX)
    left = mem[base + BITS_LEFT]
    executed = 0
    while True:
        left = (left - 1) & 0xFF
        executed += 8  # dec, jne, shr, jb, add, mov, test, je
        if left == 0:
            buffer = unpack(mem, base + source)[0]
            source = (source + 4) & MASK
            left = 0x20
            executed += 3  # mov, add, mov
        bit = buffer & 1
        buffer >>= 1
        if not bit:
            node = 4
            executed += 1  # mov eax, 4
        carry = (node_at ^ node ^ (node_at + node)) & 0x10  # the AF of the `add`, which `test` leaves as it is
        node_at = (node_at + node) & MASK
        node = unpack(mem, base + node_at)[0]
        if node & 0x80000000:
            break
    mem[base + BITS_LEFT] = left
    for number, value in ((CPU.AX, node), (CPU.DX, node_at), (CPU.BX, buffer), (CPU.SI, source)):
        cpu.set32(number, value)
    cpu.flags = (cpu.flags & ~ARITHMETIC) | flags.SF | flags.PF | carry  # CF, OF, ZF clear
    cpu.instructions += executed - 1  # (the INT stands for the first one)
    cpu.ip = leaf_at


def down_the_low_tree(cpu):
    down_the_tree(cpu, 0x111083)


def down_the_third_tree(cpu):
    down_the_tree(cpu, 0x111101)


# The loop of the sound of Smacker (16 bits, two channels) in the procedure at 1109D0: four values from four trees of
# Huffman make a dword of two samples, and that is done again and again until the end of what is to be unpacked.
TREE_OF_LEFT_LOW, TREE_OF_LEFT_HIGH, TREE_OF_RIGHT_LOW, TREE_OF_RIGHT_HIGH = 0x114928, 0x11492C, 0x114930, 0x114934
SOUND_END = 0x114A9C  # where the unpacking is to stop: a place in the ring of the buffer
SOUND_EXIT = 0x111166  # the code after the loop


def sound_loop(cpu):
    """What the loop at 11104C does. The code of the loop (it begins where the dword is made) goes down the tree of
    TREE_OF_LEFT_LOW to a leaf and adds its byte to CL (the carry to CH), goes down TREE_OF_LEFT_HIGH and adds to CH,
    turns ECX by 16 bits (the right sample), does the same with the two trees of the right, and puts ECX at EDI. Then EDI
    is 4 more, and if it is SOUND_END the loop is over; if it is the end of the ring ([EBP+18]) it goes back to the start
    of the ring ([EBP+14]). Each tree is gone down as down_the_tree does. The code goes on at SOUND_EXIT with the flags of
    the comparison that ended it, and the instructions are counted as the code would have run them."""
    mem = cpu.mem
    base = cpu.bases[cpu.s[CPU.DS]]
    unpack, pack = DWORD.unpack_from, DWORD.pack_into
    frame = cpu.bases[cpu.s[CPU.SS]] + cpu.wide(CPU.BP)
    ring_start, ring_end = unpack(mem, frame + 0x14)[0], unpack(mem, frame + 0x18)[0]
    roots = [unpack(mem, base + at)[0] for at in (TREE_OF_LEFT_LOW, TREE_OF_LEFT_HIGH, TREE_OF_RIGHT_LOW,
                                                  TREE_OF_RIGHT_HIGH)]
    end = unpack(mem, base + SOUND_END)[0]
    buffer, source, out, both = cpu.wide(CPU.BX), cpu.wide(CPU.SI), cpu.wide(CPU.DI), cpu.wide(CPU.CX)
    left = mem[base + BITS_LEFT]
    executed = 0
    while True:
        for number, root in enumerate(roots):
            node_at = root
            node = unpack(mem, base + node_at)[0]
            executed += 4 + 3  # the three instructions that take the root and test it, the `jne`, and those that use the leaf
            while not node & 0x80000000:
                left = (left - 1) & 0xFF
                executed += 8
                if left == 0:
                    buffer = unpack(mem, base + source)[0]
                    source = (source + 4) & MASK
                    left = 0x20
                    executed += 3
                bit = buffer & 1
                buffer >>= 1
                if not bit:
                    node = 4
                    executed += 1
                node_at = (node_at + node) & MASK
                node = unpack(mem, base + node_at)[0]
            if number & 1:  # the second byte of a sample: added to CH, and the other sample is turned to the low half
                both = (both & 0xFFFF00FF) | ((both + ((node & 0xFF) << 8)) & 0xFF00)
                both = (both >> 16 | both << 16) & MASK
            else:
                total = (both & 0xFF) + (node & 0xFF)
                both = (both & 0xFFFFFF00) | total & 0xFF
                both = (both & 0xFFFF00FF) | ((both + ((total >> 8) << 8)) & 0xFF00)
        pack(mem, base + out, both)
        out = (out + 4) & MASK
        if out == end:
            executed += 4
            break
        executed += 6
        if out == ring_end:
            out = ring_start
            executed += 2
    mem[base + BITS_LEFT] = left
    for number, value in ((CPU.AX, node), (CPU.DX, node_at), (CPU.BX, buffer), (CPU.SI, source), (CPU.DI, out),
                          (CPU.CX, both)):
        cpu.set32(number, value)
    cpu.flags = (cpu.flags & ~ARITHMETIC) | flags.ZF | flags.PF  # cmp edi, [SOUND_END]: equal
    cpu.instructions += executed - 1
    cpu.ip = SOUND_EXIT


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


def row_of_colors(way, colors):
    """What the way `way` of the code of a row makes of EAX, of the colors in `colors` (EDX)."""
    low, high = colors & 0xFF, colors >> 8 & 0xFF

    def rol(value, count):
        return (value << count | value >> 32 - count) & MASK

    def swap_bytes(value):  # xchg ah, al
        return value & 0xFFFF0000 | (value & 0xFF) << 8 | value >> 8 & 0xFF

    if way == 0:
        return low * 0x01010101
    if way == 15:
        return high * 0x01010101
    if way == 1:
        return rol(colors & 0xFFFF00FF | low << 8, 8)
    if way == 2:
        return rol(colors & 0xFFFF00FF | low << 8, 16)
    if way == 3:
        return rol(swap_bytes(colors), 8)
    if way == 4:
        return rol(colors & 0xFFFF00FF | low << 8, 24)
    if way == 5:
        return rol(colors, 8)
    if way == 6:
        return swap_bytes(rol(colors, 8))
    if way == 7:
        return rol(colors & 0xFFFFFF00 | high, 8)
    if way == 8:
        return colors & 0xFFFF00FF | low << 8
    if way == 9:
        return swap_bytes(colors)
    if way == 10:
        return colors
    if way == 11:
        return colors & 0xFFFFFF00 | high
    if way == 12:
        return rol(swap_bytes(colors), 24)
    if way == 13:
        return rol(colors & 0xFFFFFF00 | high, 24)
    return rol(colors & 0xFFFFFF00 | high, 16)  # 14


def frame_blocks(cpu):
    """What the loop that begins at 10F7B0 does (the patch is in the second instruction of it, for the first one has
    an address that the loading of the program changes, and the INT would have been in the place of that): for every run of blocks that the stream says, reads the blocks and draws them (four
    rows of a dword, EBP apart), goes on to the next row of blocks, and then to the next run, until the strip is over: then
    the code goes on at the place that reads the next strip (STRIP_ENDS, one for each kind of block). The registers, the
    flags and the variables are as the code leaves them, and the instructions are counted as the code would have run them."""
    mem = cpu.mem
    base = cpu.bases[cpu.s[CPU.DS]]
    unpack, pack = DWORD.unpack_from, DWORD.pack_into
    eax, ecx, edx, ebx = cpu.wide(CPU.AX), cpu.wide(CPU.CX), cpu.wide(CPU.DX), cpu.wide(CPU.BX)
    esi, edi, ebp = cpu.wide(CPU.SI), cpu.wide(CPU.DI), cpu.wide(CPU.BP)
    default = WORD.unpack_from(mem, base + TREE_DEFAULT)[0]
    area = unpack(mem, base + TREE_AREA)[0]
    executed = 0

    def rd(address):
        return unpack(mem, base + address)[0]

    def wr(address, value):
        pack(mem, base + (address & MASK), value & MASK)

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

    def value(kind, ror):
        """What the code that reads a value does. The value is in the upper 16 bits of the leaf: with `ror` it goes to AX,
        else the leaf is the whole EAX."""
        nonlocal eax, ecx, edx, ebx, esi, executed
        table, cache0, cache1, cache2, limit, mask = kind
        left = mem[base + BITS_LEFT]
        refill = left <= limit  # fewer bits than the table looks at: 16 more from the stream
        if refill:
            edx = (rd(esi) << ((left - 1) & 31)) & MASK | ebx
            ebx = edx
            esi = (esi + 2) & MASK
            left = (left + 16) & 0xFF
            executed += 20
        else:
            edx = ebx & mask
            executed += 13
        entry = rd(rd(table) + 4 * (edx & mask))
        ebx >>= entry & 31
        left = (left - entry) & 0xFF
        ecx = ((entry >> 8) + area) & MASK
        edx = rd(ecx)
        if edx & 0xFFFF == default:  # not a leaf: down the tree, a bit at a time
            executed += 2 if refill else 1
            while True:
                step = edx >> 13 & 0xFFFF8
                left = (left - 1) & 0xFF
                executed += 10
                if left == 0:
                    ebx = rd(esi)
                    esi = (esi + 4) & MASK
                    left = 0x20
                    executed += 3
                bit = ebx & 1
                ebx >>= 1
                if not bit:
                    step = 4
                    executed += 1
                ecx = (ecx + step) & MASK
                edx = rd(ecx)
                if edx & 0xFFFF != default:
                    break
        else:
            executed += 1
        pointer = rd(cache0)
        mem[base + BITS_LEFT] = left
        if ror:
            eax = eax & 0xFFFF0000 | edx >> 16
            executed += 7
        else:
            eax = edx
            executed += 5
        ecx = pointer
        if rd(ecx) != edx:  # not the first of the cache: it becomes the first, and the others go down
            wr(CACHE_SWAP, eax)
            eax = rd(ecx)
            wr(ecx, edx)
            ecx = rd(cache1)
            edx = rd(ecx)
            wr(ecx, eax)
            ecx = rd(cache2)
            eax = rd(CACHE_SWAP)
            wr(ecx, edx)
            executed += 9

    def leave(label, carry):
        for number, register in ((CPU.AX, eax), (CPU.CX, ecx), (CPU.DX, edx), (CPU.BX, ebx), (CPU.SI, esi),
                                 (CPU.DI, edi), (CPU.BP, ebp)):
            cpu.set32(number, register)
        cpu.flags = (cpu.flags & ~ARITHMETIC) | flags.ZF | flags.PF | (flags.CF if carry else 0)
        cpu.instructions += executed - 2  # (the `mov al` before the INT was run, and the INT stands for the next one)
        cpu.ip = label

    while True:
        value(KIND_TYPE, True)  # what the blocks of the run are, and how many
        edx = rd(RUN_LENGTHS + (eax & 0xFC))
        kind = eax & 3
        ecx = kind
        wr(RUN, edx)
        executed += 7
        if kind <= 1:  # a mono block (0) or a full one (1)
            while True:
                if kind == 0:
                    value(KIND_COLORS, False)
                    value(KIND_MASK, True)
                    ecx = eax
                    edx = edx_mask = eax
                    eax = (eax >> 16 | eax << 16) & MASK
                    edx = edx_mask & 0xFFFF0000 | eax & 0xFFFF  # the colors, in both halves
                    eax = (eax >> 28 | ecx << 4) & MASK
                    way = eax >> 4 & 0xF
                    executed += 8
                    eax = row_of_colors(way, edx)
                    executed += ROW_COUNTS[way]
                    draw(edi, eax)
                    edi = (edi + ebp) & MASK
                    way = ecx >> 4 & 0xF
                    executed += 6
                    eax = row_of_colors(way, edx)
                    executed += ROW_COUNTS[way]
                    draw(edi, eax)
                    way = ecx >> 8 & 0xF
                    executed += 5
                    eax = row_of_colors(way, edx)
                    executed += ROW_COUNTS[way]
                    draw(edi + ebp, eax)
                    way = ecx >> 12 & 0xF
                    executed += 5
                    eax = row_of_colors(way, edx)
                    executed += ROW_COUNTS[way]
                else:
                    for row in range(4):
                        value(KIND_FULL, False)
                        value(KIND_FULL, True)
                        if row == 0:
                            draw(edi, eax)
                            edi = (edi + ebp) & MASK
                            executed += 2
                        elif row == 1:
                            draw(edi, eax)
                            executed += 1
                        elif row == 2:
                            draw(edi + ebp, eax)
                            executed += 1
                draw(edi + 2 * ebp, eax)
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
                    for at in range(ecx):
                        draw(edi + 4 * at, eax)
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


# The procedure at 10DCF0 that reads a small tree of Huffman (the leaves are bytes) from the stream: the trees of the
# sound of Smacker, that are read again for every frame.
SMALL_TREE_RETURN = 0x10DD8A  # its RET


def small_tree(cpu):
    """What the procedure at 10DCF0 does: reads the tree from the bits at ESI (the count of them is in BITS_LEFT, EBX has
    the bits that are left of the dword) and writes it at EDI: a bit 1 is a node, a bit 0 is a leaf (a byte of 8 bits that
    makes a dword with the top bit set), or, when nothing waits, the end. A node is a place of a dword that waits on the
    stack for the offset of what follows its left side: when the left side is a leaf, the node gets the offset at once.
    Markers on the stack, -1 below all and -2 for the first, are as the code puts them, and so is what the code leaves on
    the stack below the pointer. Returns as the code does."""
    mem = cpu.mem
    base = cpu.bases[cpu.s[CPU.DS]]
    stack = cpu.bases[cpu.s[CPU.SS]]
    unpack, pack = DWORD.unpack_from, DWORD.pack_into
    esi, ebx, edi, esp = cpu.wide(CPU.SI), cpu.wide(CPU.BX), cpu.wide(CPU.DI), cpu.wide(CPU.SP)
    left = mem[base + BITS_LEFT]
    executed = 3  # mov eax; push; push
    esp -= 8
    pack(mem, stack + esp + 4, MASK)
    pack(mem, stack + esp, MASK - 1)
    eax, ecx, edx = 0x80000000, cpu.wide(CPU.CX), cpu.wide(CPU.DX)
    while True:
        left = (left - 1) & 0xFF
        executed += 4
        if left == 0:
            ebx = unpack(mem, base + esi)[0]
            esi = (esi + 4) & MASK
            left = 0x20
            executed += 3
        bit = ebx & 1
        ebx >>= 1
        if bit:  # a node: its place waits on the stack (what was on the top goes back: the code pops it and pushes it)
            ecx = unpack(mem, stack + esp)[0]
            pack(mem, stack + esp - 4, edi)
            esp -= 4
            edi = (edi + 4) & MASK
            executed += 3 + 4
            continue
        ecx = unpack(mem, stack + esp)[0]
        esp += 4
        executed += 3
        if ecx == MASK:  # the bottom: the tree is over
            executed += 1
            break
        esp -= 4  # push ecx
        pack(mem, stack + esp, ecx)
        executed += 1 + 2
        if left >= 9:  # a byte from what is in EBX
            eax = eax & 0xFFFFFF00 | ebx & 0xFF
            ebx >>= 8
            left = (left - 8) & 0xFF
            executed += 4
        else:  # a part of it, and the rest from the next dword
            low = left
            eax = eax & 0xFFFFFF00 | ebx & 0xFF
            ebx = unpack(mem, base + esi)[0]
            esi = (esi + 4) & MASK
            need = -(low - 9) & 0xFF
            left = (0x21 - need) & 0xFF
            part = ebx & 0xFF
            ebx >>= need & 31
            shift = -(need - 8) & 0xFF
            part = part << (shift & 31) & 0xFF
            eax |= part
            ecx = ecx & 0xFFFF0000 | (part & 0xFF) << 8 | shift  # (the code uses CL and CH; the pop gives ECX back)
            executed += 14
        mem[base + BITS_LEFT] = left
        ecx = unpack(mem, stack + esp)[0]
        esp += 4
        pack(mem, base + edi, eax)
        edi = (edi + 4) & MASK
        executed += 5
        if ecx != MASK - 1:  # the left side of a node: the node gets the offset of what follows
            edx = (edi - ecx) & MASK
            pack(mem, base + ecx, edx)
            executed += 4
    mem[base + BITS_LEFT] = left
    for number, value in ((CPU.AX, eax), (CPU.CX, ecx), (CPU.DX, edx), (CPU.BX, ebx), (CPU.SI, esi), (CPU.DI, edi),
                          (CPU.SP, esp)):
        cpu.set32(number, value)
    cpu.flags = (cpu.flags & ~ARITHMETIC) | flags.ZF | flags.PF  # cmp ecx, -1: equal
    cpu.instructions += executed - 1  # (the INT stands for the first one)
    give_back(cpu)


PATCHES = [Patch("the tree of Smacker", 0x10DD90, 0x2F9,
                 "8d771c093903600f4bb962e1538d789d36301287d0d5a52b6167c23bb01a9989", big_tree),
           Patch("the table of the tree", 0x10E090, 0x6C,
                 "d85b40c6adebd006c283931730a7fb4c4a6f7735c9dd7f1f320dd016ddb65ca3", lookup_table),
           Patch("the values of the tree marked", 0x10E1A0, 0x30,
                 "96d70c96ea368a455b0986a8c6756eaf5d3028b6297ef6871a1f566b18d07bc0", mark_values),
           Patch("the values of the tree filled in", 0x10E1D0, 0x30,
                 "b160ac45fcf6b0b914c88ece8e012c2e89dbb6575e02034d613af8cbff3523e5", fill_values),
           Patch("the small tree of the sound", 0x10DCF0, 0x9B,
                 "d0416e9a70b0b648a7269eee0e2429b9b23b1835d0d3e42922db45d85e5172c8", small_tree),
           Patch("the blocks of a frame of Smacker", 0x10F7B5, 0x120B,
                 "f0cc5fa2a549d8f52976276926fb9af8ca6c0bc0240fda6daaa61c18a98d1e53", frame_blocks),
           Patch("the loop of the sound of Smacker", 0x11104C, 0x11A,
                 "f33af4d073d49beb7890a7ef6ec4d5abe3744b8b7d92b8205f6b7e8bd3a22d10", sound_loop),
           Patch("the descent of the first tree", 0x11105B, 0x28,
                 "2818b5bcdfd4b42808bdf79e05d761ac3bbb9daf601a628bb6466794d7039f14", down_the_low_tree),
           Patch("the descent of the third tree", 0x1110D9, 0x28,
                 "2818b5bcdfd4b42808bdf79e05d761ac3bbb9daf601a628bb6466794d7039f14", down_the_third_tree)]


# The patches of code that is not in the program when it is loaded, but comes later: the driver of the sound is
# read from a file by the player, and put where there is room. Such a piece is found by its bytes, and patched when it
# is there (and again when a saved machine is put back, for it keeps the INTs, and does not know what they mean).
LOOK_EVERY = 2_000_000  # the instructions between the looks, until all the pieces are found
SEARCH_FROM, SEARCH_TO = 0x100000, 0x800000  # where the program puts such code: above the first megabyte


class LatePatch:
    """A piece of code that comes later. It is `length` bytes, the first of which are `head` (they are what it is
    searched by), and SHA-256 `digest` is that of all of it. `sites` are the places in it (the offsets from its
    beginning) where the patch is: (offset, the two bytes of the code that the INT replaces, the handler)."""

    def __init__(self, name, head, length, digest, sites):
        self.name, self.head, self.length, self.digest, self.sites = name, bytes.fromhex(head), length, digest, sites

    def find(self, cpu):
        """The address of the code in the memory, as it is now (patched or not), or None."""
        mem, start = cpu.mem, SEARCH_FROM
        while True:
            at = mem.find(self.head, start, SEARCH_TO)
            if at < 0:
                return None
            code = bytearray(mem[at:at + self.length])
            for offset, original, _ in self.sites:  # what was there before the INTs
                code[offset:offset + len(PATCH_BYTES)] = original
            if hashlib.sha256(code).hexdigest() == self.digest:
                return at
            start = at + 1

    def apply(self, cpu):
        """Patch the code if it is there; the machine learns where. Returns whether it was found."""
        at = self.find(cpu)
        if at is None:
            return False
        for offset, _, handler in self.sites:
            cpu.mem[at + offset:at + offset + len(PATCH_BYTES)] = PATCH_BYTES
            cpu.patch_sites[at + offset] = handler
        return True


def mix_into_ring(counter, after):
    """The handler for the loop of the mixing of the sound (two samples for each round of it): the loop at the
    offset of the code `after` less its length, that counts the rounds in the dword at FS:`counter`."""
    def handler(cpu):
        mix_samples(cpu, counter, after)
    return handler


def mix_samples(cpu, counter, after):
    """What the loop of the mixer does: for every sample (a byte, the upper of a 16-bit sample at DS:ESI + 2 * ECX) it
    makes the volume (BH, a signed byte) of it, and adds that to the byte of the ring at ES:EDI (a byte with 80h taken
    off for the sign), and when the sum does not fit in a signed byte it is the biggest or the least. A round is two of
    them; the dword at FS:`counter` is the rounds that are left, and the code goes on at the offset `after` when it is
    0. The registers and the flags are as the code leaves them, and the instructions are counted as it would run."""
    mem = cpu.mem
    source = cpu.bases[cpu.s[CPU.DS]]
    ring = cpu.bases[cpu.s[CPU.ES]]
    counter_at = cpu.bases[cpu.s[CPU.FS]] + counter
    esi, ecx, edi, eax = cpu.wide(CPU.SI), cpu.wide(CPU.CX), cpu.wide(CPU.DI), cpu.wide(CPU.AX)
    volume = cpu.r[CPU.BX] >> 8 & 0xFF
    volume -= 256 if volume > 127 else 0
    rounds = DWORD.unpack_from(mem, counter_at)[0]
    executed = 0
    while True:
        for _ in range(2):
            sample = mem[source + (esi + 2 * ecx + 1 & MASK)]
            ecx = (ecx + 1) & MASK
            product = (sample - 256 if sample > 127 else sample) * volume & 0xFFFF  # imul bh: AX
            eax = ((eax & 0xFFFF0000 | product) >> 7) & 0xFFFF00FF | mem[ring + edi] << 8  # shr eax, 7; mov ah, es:[edi]
            eax ^= 0x8000  # xor ah, 80h
            ah = eax >> 8 & 0xFF
            total = (eax & 0xFF) + ah
            low = total & 0xFF
            executed += 8
            if (low ^ eax) & (low ^ ah) & 0x80:  # the sum overflows: the biggest, or the least
                low = 0xFF if low & 0x80 else 0  # (0x7F, with 80h taken off again, or 0)
                executed += 6
            else:
                low ^= 0x80
                executed += 3
            eax = eax & 0xFFFFFF00 | low
            mem[ring + edi] = low
            edi = (edi + 1) & MASK
        rounds -= 1
        executed += 2
        if rounds == 0:
            break
    DWORD.pack_into(mem, counter_at, 0)
    for number, value in ((CPU.AX, eax), (CPU.CX, ecx), (CPU.DI, edi)):
        cpu.set32(number, value)
    cpu.flags = (cpu.flags & ~ARITHMETIC) | flags.ZF | flags.PF  # sub dword fs:[counter], 1: the result is 0
    cpu.instructions += executed - 1  # (the INT stands for the first one)
    cpu.ip = after


# The mixer of the driver of the sound: the procedure at 2144 of its code, that mixes what is to be played into the
# ring of the buffer; it has two loops, one up to the end of the ring, and one from its beginning.
LATE_PATCHES = [LatePatch("the mixer of the sound",
                          "1e568b0d10130000890d141300000fb7463266f72dfc12000003d28bda668e05",
                          0x118, "5976e86960d7b54a2c51eb978f2d05c6875e674c7c48ba6af1b8a1d34ab75f8d",
                          [(0x46, bytes.fromhex("8a44"), mix_into_ring(0x130C, 0x21E4)),
                           (0xBB, bytes.fromhex("8a44"), mix_into_ring(0x1314, 0x2259))])]


def apply(cpu, patches=None):
    """Patch the code in memory that is one of the known pieces (see Patch.matches). Returns the names of what was
    patched. Call it when a program is loaded."""
    done = []
    for patch in PATCHES if patches is None else patches:
        if patch.matches(cpu):
            cpu.mem[patch.address:patch.address + len(PATCH_BYTES)] = PATCH_BYTES
            done.append(patch.name)
    return done


def apply_late(cpu, patches=None):
    """Patch the pieces of code that came after the program was loaded, those of them that are there. Returns the
    patches that are not found yet."""
    return [patch for patch in (LATE_PATCHES if patches is None else patches) if not patch.apply(cpu)]


def install(cpu, patches=None):
    """Teach the machine the meaning of `INT F0h`: do the work of the patch whose first instruction it stands in for.
    The pieces of code that come later are looked for from time to time, until they are found."""
    table = {patch.address: patch.handler for patch in (PATCHES if patches is None else patches)}
    cpu.patch_sites = {}  # the places of the pieces that came later (their linear addresses)

    def hook(cpu):
        address = (cpu.bases or {}).get(cpu.s[CPU.CS], 0) + cpu.ip - len(PATCH_BYTES)
        handler = cpu.patch_sites.get(address) or table.get(cpu.ip - len(PATCH_BYTES))
        if handler is None:
            raise NotImplementedError(f"INT {HOOK:02X} at {cpu.s[CPU.CS]:04X}:{cpu.start_ip:X}: no patch there")
        handler(cpu)
    cpu.int_handlers[HOOK] = hook
    if LATE_PATCHES:
        waiting = list(LATE_PATCHES)

        def look(instructions):
            waiting[:] = apply_late(cpu, waiting)
            return instructions + LOOK_EVERY if waiting else None
        cpu.event_sources.append(look)
        cpu.wake()
