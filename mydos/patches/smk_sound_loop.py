"""The loop of the sound of Smacker in SMKP.EXE (the procedure at 1109D0, from 11104C)."""

from .. import flags
from ..cpu import CPU
from ._base import Patch
from ._common import ARITHMETIC, DWORD, MASK
from ._smacker import BITS_LEFT


# The loop of the sound of Smacker (16 bits, two channels) in the procedure at 1109D0: four values from four trees of
# Huffman make a dword of two samples, and that is done again and again until the end of what is to be unpacked.
TREE_OF_LEFT_LOW, TREE_OF_LEFT_HIGH, TREE_OF_RIGHT_LOW, TREE_OF_RIGHT_HIGH = 0x114928, 0x11492C, 0x114930, 0x114934
SOUND_END = 0x114A9C  # where the unpacking is to stop: a place in the ring of the buffer
SOUND_EXIT = 0x111166  # the code after the loop


def prefix_table(mem, base, root):
    """For a tree of the sound (a node is the offset of its right side, a leaf has the top bit set; a bit 0 goes to the next
    dword, a bit 1 by the offset), the table by the first 8 bits of the stream (the first one is the lowest): for a leaf that
    is found in 8 bits or fewer it has (how many bits the way to it is, how many instructions the code runs for it, the
    leaf, its place), and None for the others, that are gone down by the bits."""
    table = [None] * 256
    unpack = DWORD.unpack_from
    todo = [(root, 0, 0, 0)]  # the place, the bits that took to it, how many, how many are 0
    while todo:
        at, bits, depth, zeros = todo.pop()
        node = unpack(mem, base + at)[0]
        if node & 0x80000000:
            entry = (depth, 7 + 8 * depth + zeros, node, at)  # (the way, the instructions that it is, the leaf, its place)
            for window in range(bits, 256, 1 << depth):  # every window of the stream that begins with these bits
                table[window] = entry
        elif depth < 8:
            todo.append(((at + 4) & MASK, bits, depth + 1, zeros + 1))
            todo.append(((at + node) & MASK, bits | 1 << depth, depth + 1, zeros))
    return table


def sound_loop(cpu):
    """What the loop at 11104C does. The code of the loop (it begins where the dword is made) goes down the tree of
    TREE_OF_LEFT_LOW to a leaf and adds its byte to CL (the carry to CH), goes down TREE_OF_LEFT_HIGH and adds to CH,
    turns ECX by 16 bits (the right sample), does the same with the two trees of the right, and puts ECX at EDI. Then EDI
    is 4 more, and if it is SOUND_END the loop is over; if it is the end of the ring ([EBP+18]) it goes back to the start
    of the ring ([EBP+14]). A tree is gone down by a table of its first 8 bits (prefix_table), and bit by bit where that is not enough. The code goes on at SOUND_EXIT with the flags of
    the comparison that ended it, and the instructions are counted as the code would have run them."""
    mem = cpu.mem
    base = cpu.bases[cpu.s[CPU.DS]]
    unpack, pack = DWORD.unpack_from, DWORD.pack_into
    frame = cpu.bases[cpu.s[CPU.SS]] + cpu.wide(CPU.BP)
    ring_start, ring_end = unpack(mem, frame + 0x14)[0], unpack(mem, frame + 0x18)[0]
    roots = [unpack(mem, base + at)[0] for at in (TREE_OF_LEFT_LOW, TREE_OF_LEFT_HIGH, TREE_OF_RIGHT_LOW,
                                                  TREE_OF_RIGHT_HIGH)]
    table0, table1, table2, table3 = (prefix_table(mem, base, root) for root in roots)
    root0, root1, root2, root3 = roots
    end = unpack(mem, base + SOUND_END)[0]
    buffer, source, out, both = cpu.wide(CPU.BX), cpu.wide(CPU.SI), cpu.wide(CPU.DI), cpu.wide(CPU.CX)
    left = mem[base + BITS_LEFT]
    executed = 0
    node = node_at = 0

    def by_bits(root):
        """A tree gone down bit by bit from its root: the way is too long for the table, or goes over the end of the dword."""
        nonlocal buffer, source, left, executed, node, node_at
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

    while True:
        # A dword is four values: the low byte of the left sample (it is added to CL, and the carry goes to CH), its
        # high byte (added to CH), and the same for the right sample, which ECX is turned to (ROR ECX, 16) before.
        entry = table0[buffer & 0xFF]
        if entry is not None and entry[0] < left:  # the whole way is in what is left of the dword
            depth, cost, node, node_at = entry
            buffer >>= depth
            left -= depth
            executed += cost
        else:
            by_bits(root0)
        both = both & 0xFFFF0000 | (both + (node & 0xFF)) & 0xFFFF
        entry = table1[buffer & 0xFF]
        if entry is not None and entry[0] < left:
            depth, cost, node, node_at = entry
            buffer >>= depth
            left -= depth
            executed += cost
        else:
            by_bits(root1)
        both = both & 0xFFFF00FF | (both + ((node & 0xFF) << 8)) & 0xFF00
        both = (both >> 16 | both << 16) & MASK
        entry = table2[buffer & 0xFF]
        if entry is not None and entry[0] < left:
            depth, cost, node, node_at = entry
            buffer >>= depth
            left -= depth
            executed += cost
        else:
            by_bits(root2)
        both = both & 0xFFFF0000 | (both + (node & 0xFF)) & 0xFFFF
        entry = table3[buffer & 0xFF]
        if entry is not None and entry[0] < left:
            depth, cost, node, node_at = entry
            buffer >>= depth
            left -= depth
            executed += cost
        else:
            by_bits(root3)
        both = both & 0xFFFF00FF | (both + ((node & 0xFF) << 8)) & 0xFF00
        both = (both >> 16 | both << 16) & MASK
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


PATCHES = [Patch("the loop of the sound of Smacker", 0x11104C, 0x11A,
                 "f33af4d073d49beb7890a7ef6ec4d5abe3744b8b7d92b8205f6b7e8bd3a22d10", sound_loop)]
