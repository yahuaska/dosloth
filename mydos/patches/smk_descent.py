"""The loops of SMKP.EXE that go down a tree of Huffman to a leaf, bit by bit (11105B and 1110D9)."""

from .. import flags
from ..cpu import CPU
from ._base import Patch
from ._common import ARITHMETIC, DWORD, MASK
from ._smacker import BITS_LEFT


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


PATCHES = [Patch("the descent of the first tree", 0x11105B, 0x28,
                 "2818b5bcdfd4b42808bdf79e05d761ac3bbb9daf601a628bb6466794d7039f14", down_the_low_tree),
           Patch("the descent of the third tree", 0x1110D9, 0x28,
                 "2818b5bcdfd4b42808bdf79e05d761ac3bbb9daf601a628bb6466794d7039f14", down_the_third_tree)]
