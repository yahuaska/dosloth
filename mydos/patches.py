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


PATCHES = [Patch("the tree of Smacker", 0x10DD90, 0x2F9,
                 "8d771c093903600f4bb962e1538d789d36301287d0d5a52b6167c23bb01a9989", big_tree),
           Patch("the table of the tree", 0x10E090, 0x6C,
                 "d85b40c6adebd006c283931730a7fb4c4a6f7735c9dd7f1f320dd016ddb65ca3", lookup_table),
           Patch("the values of the tree marked", 0x10E1A0, 0x30,
                 "96d70c96ea368a455b0986a8c6756eaf5d3028b6297ef6871a1f566b18d07bc0", mark_values),
           Patch("the values of the tree filled in", 0x10E1D0, 0x30,
                 "b160ac45fcf6b0b914c88ece8e012c2e89dbb6575e02034d613af8cbff3523e5", fill_values),
           Patch("the loop of the sound of Smacker", 0x11104C, 0x11A,
                 "f33af4d073d49beb7890a7ef6ec4d5abe3744b8b7d92b8205f6b7e8bd3a22d10", sound_loop),
           Patch("the descent of the first tree", 0x11105B, 0x28,
                 "2818b5bcdfd4b42808bdf79e05d761ac3bbb9daf601a628bb6466794d7039f14", down_the_low_tree),
           Patch("the descent of the third tree", 0x1110D9, 0x28,
                 "2818b5bcdfd4b42808bdf79e05d761ac3bbb9daf601a628bb6466794d7039f14", down_the_third_tree)]


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
