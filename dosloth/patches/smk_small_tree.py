"""The small trees of the sound of Smacker in SMKP.EXE (the procedure at 10DCF0)."""

from .. import flags
from ..cpu import CPU
from ._base import Patch
from ._common import ARITHMETIC, DWORD, give_back, MASK
from ._smacker import BITS_LEFT


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


PATCHES = [Patch("the small tree of the sound", 0x10DCF0, 0x9B,
                 "d0416e9a70b0b648a7269eee0e2429b9b23b1835d0d3e42922db45d85e5172c8", small_tree)]
