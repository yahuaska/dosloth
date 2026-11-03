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


PATCHES = [Patch("the tree of Smacker", 0x10DD90, 0x2F9,
                 "8d771c093903600f4bb962e1538d789d36301287d0d5a52b6167c23bb01a9989", big_tree)]


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
