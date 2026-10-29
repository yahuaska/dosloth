"""What the extender does for a 32-bit program that the machine does not do itself: DPMI (INT 31h), the descriptors
of the program's own, and the questions to INT 2Fh that ask which mode it runs in.

A program of DOS/4G does not know that there is no extender under it: it asks (INT 21h AX=FF00h, INT 2Fh AX=1686h) and
goes on with what the answers are. The table of selectors is the dictionary `cpu.bases` (see flat.py); the descriptors
that the program asks for (AX=0000h) are put in it, and what the program does to them (the base, AX=0007h) is seen
by the CPU at once, because it is the same table. The limits and the rights of a descriptor are taken and not kept:
nothing looks at them.

The selectors that the program is given look like those of a local table: the number of the descriptor shifted by 3, and
the bits 111 (the local table, the privilege 3), as 0Fh and 17h of the loader are. The first free number is 3.
"""
from . import flags
from .cpu import CPU

SELECTOR_BITS = 0x07  # the local descriptor table, privilege level 3
FIRST_INDEX = 3  # 1 and 2 are the code and the data of the program (see loader.py), 0 is no selector
LAST_INDEX = 8191  # a table of descriptors has 8192 of them
INCREMENT = 8  # between the selectors of descriptors that were got together

UNAVAILABLE_DESCRIPTOR = 0x8011
INVALID_VALUE = 0x8021
INVALID_SELECTOR = 0x8022

VERSION = 0x0009  # DPMI 0.9: the major number in AH, the minor one in AL
PIC_BASES = 0x0870  # where the interrupts of the two interrupt controllers begin: 08h and 70h
PROCESSOR_486 = 4
FLAGS_386 = 0x0001  # bit 0: a DPMI of 32-bit programs; no virtual memory


def new_descriptors(bases, count):
    """Put `count` descriptors with the base 0 in the table `bases`, in a row. Returns the first selector, or None if
    there is not a row of that many free ones."""
    row = 0
    for index in range(FIRST_INDEX, LAST_INDEX + 1):
        row = row + 1 if index << 3 | SELECTOR_BITS not in bases else 0
        if row == count:
            first = index - count + 1
            for i in range(count):
                bases[(first + i) << 3 | SELECTOR_BITS] = 0
            return first << 3 | SELECTOR_BITS
    return None


def zero_based_selector(cpu):
    """The selector that GS has if it is one with the base 0 (it names the whole memory by the linear address), else
    a new one, which is put in GS."""
    selector = cpu.s[CPU.GS]
    if selector in cpu.bases and cpu.bases[selector] == 0:
        return selector
    selector = new_descriptors(cpu.bases, 1)
    if selector is None:
        raise NotImplementedError("there is no descriptor left for GS")
    cpu.s[CPU.GS] = selector
    return selector


def refuse(cpu, code):
    cpu.r[CPU.AX] = code
    cpu.set_flag(flags.CF, True)


def int31(cpu):
    """INT 31h: the calls to the DPMI host. AX picks the call, and CF is 1 when it failed, and AX is the error.

    The descriptors: 0000h (CX of them, from the selector in AX), 0001h (free the one in BX), 0006h and 0007h (the
    base of BX in CX:DX, get and set), 0008h and 0009h (the limit in CX:DX and the rights in CX: taken and not kept);
    and 0400h, the version. A call that is not here stops the emulator.
    """
    if not cpu.flat:
        raise NotImplementedError("INT 31h from real mode")
    ax = cpu.r[CPU.AX]
    selector = cpu.r[CPU.BX]
    if ax == 0x0000:
        count = cpu.r[CPU.CX]
        if count == 0:
            return refuse(cpu, INVALID_VALUE)
        first = new_descriptors(cpu.bases, count)
        if first is None:
            return refuse(cpu, UNAVAILABLE_DESCRIPTOR)
        cpu.r[CPU.AX] = first
    elif ax == 0x0400:
        cpu.r[CPU.AX], cpu.r[CPU.BX] = VERSION, FLAGS_386
        cpu.r[CPU.CX], cpu.r[CPU.DX] = PROCESSOR_486, PIC_BASES
    elif ax in (0x0001, 0x0006, 0x0007, 0x0008, 0x0009):
        if selector not in cpu.bases or (ax == 0x0001 and selector & 7 != SELECTOR_BITS):  # only one of ours is freed
            return refuse(cpu, INVALID_SELECTOR)
        if ax == 0x0001:
            del cpu.bases[selector]
        elif ax == 0x0006:
            cpu.r[CPU.CX], cpu.r[CPU.DX] = cpu.bases[selector] >> 16, cpu.bases[selector] & 0xFFFF
        elif ax == 0x0007:
            cpu.bases[selector] = cpu.r[CPU.CX] << 16 | cpu.r[CPU.DX]
    else:
        raise NotImplementedError(f"INT 31h AX={ax:04X}")
    cpu.set_flag(flags.CF, False)


def int2f(cpu, state, real):
    """INT 2Fh of a program in the flat mode: 1686h asks if the program runs in protected mode, and it does, so AX
    comes back 0 (in real mode it stays as it was). The others are answered as in real mode, by `real(cpu, state)`."""
    if cpu.r[CPU.AX] == 0x1686:
        cpu.r[CPU.AX] = 0
    else:
        real(cpu, state)
