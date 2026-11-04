"""The tables that SMKP.EXE makes of a tree of Smacker (the procedures at 10E090, 10E1A0 and 10E1D0)."""

from ..cpu import CPU
from ._base import Patch
from ._common import after_counting, DWORD, give_back, MASK, WORD


# The three procedures that the code of the player calls when the trees are read, to make of them the tables it looks
# up (10E090), and to note which values are in them (10E1A0) and give the values that are not a default (10E1D0).
TABLE_CODE = 0x114940  # the start of the area of the trees
TABLE_DEPTH = 0x114950  # how many bits a table is looked up by
TABLE_SIZE = 0x114954  # how many entries it has
TABLE_ROOT = 0x114910  # where the code remembers the root
DEFAULT = 0x114970  # the 16-bit value of an entry that is not a leaf
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


PATCHES = [Patch("the table of the tree", 0x10E090, 0x6C,
                 "d85b40c6adebd006c283931730a7fb4c4a6f7735c9dd7f1f320dd016ddb65ca3", lookup_table),
           Patch("the values of the tree marked", 0x10E1A0, 0x30,
                 "96d70c96ea368a455b0986a8c6756eaf5d3028b6297ef6871a1f566b18d07bc0", mark_values),
           Patch("the values of the tree filled in", 0x10E1D0, 0x30,
                 "b160ac45fcf6b0b914c88ece8e012c2e89dbb6575e02034d613af8cbff3523e5", fill_values)]
