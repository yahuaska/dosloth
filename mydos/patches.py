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


PATCHES = [Patch("the tree of Smacker", 0x10DD90, 0x2F9,
                 "8d771c093903600f4bb962e1538d789d36301287d0d5a52b6167c23bb01a9989", big_tree),
           Patch("the table of the tree", 0x10E090, 0x6C,
                 "d85b40c6adebd006c283931730a7fb4c4a6f7735c9dd7f1f320dd016ddb65ca3", lookup_table),
           Patch("the values of the tree marked", 0x10E1A0, 0x30,
                 "96d70c96ea368a455b0986a8c6756eaf5d3028b6297ef6871a1f566b18d07bc0", mark_values),
           Patch("the values of the tree filled in", 0x10E1D0, 0x30,
                 "b160ac45fcf6b0b914c88ece8e012c2e89dbb6575e02034d613af8cbff3523e5", fill_values)]


def apply(cpu, patches=None):
    """Patch the code in memory that is one of the known pieces (see Patch.matches). Returns the names of what was
    patched. Call it when a program is loaded."""
    done = []
    for patch in PATCHES if patches is None else patches:
        if patch.matches(cpu):
            cpu.mem[patch.address:patch.address + len(PATCH_BYTES)] = PATCH_BYTES
            done.append(patch.name)
    return done


def install(cpu, patches=None):
    """Teach the machine the meaning of `INT F0h`: do the work of the patch whose first instruction it stands in for."""
    table = {patch.address: patch.handler for patch in (PATCHES if patches is None else patches)}

    def hook(cpu):
        handler = table.get(cpu.ip - len(PATCH_BYTES))
        if handler is None:
            raise NotImplementedError(f"INT {HOOK:02X} at {cpu.s[CPU.CS]:04X}:{cpu.start_ip:X}: no patch there")
        handler(cpu)
    cpu.int_handlers[HOOK] = hook
