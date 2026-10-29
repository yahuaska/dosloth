"""What the extender does for a 32-bit program that the machine does not do itself: DPMI (INT 31h), the descriptors
of the program's own, and the questions to INT 2Fh that ask which mode it runs in.

A program of DOS/4G does not know that there is no extender under it: it asks (INT 21h AX=FF00h, INT 2Fh AX=1686h) and
goes on with what the answers are. The table of selectors is the dictionary `cpu.bases` (see flat.py); the descriptors
that the program asks for (AX=0000h) are put in it, and what the program does to them (the base, AX=0007h) is seen
by the CPU at once, because it is the same table. The limits and the rights of a descriptor are taken and not kept:
nothing looks at them.

The memory that the program asks for is that above the first megabyte (AX=0501h, with the state of DOS' `extended`,
see xmem.py) and that below it (AX=0100h, from the blocks of DOS, with a selector made for it).

The selectors that the program is given look like those of a local table: the number of the descriptor shifted by 3, and
the bits 111 (the local table, the privilege 3), as 0Fh and 17h of the loader are. The first free number is 3.
"""
from . import flags
from .cpu import CPU
from .xmem import PAGE

SELECTOR_BITS = 0x07  # the local descriptor table, privilege level 3
FIRST_INDEX = 3  # 1 and 2 are the code and the data of the program (see loader.py), 0 is no selector
LAST_INDEX = 8191  # a table of descriptors has 8192 of them
INCREMENT = 8  # between the selectors of descriptors that were got together

UNAVAILABLE_DESCRIPTOR = 0x8011
UNAVAILABLE_MEMORY = 0x8012
INVALID_VALUE = 0x8021
INVALID_SELECTOR = 0x8022
INVALID_HANDLE = 0x8023
INSUFFICIENT_DOS_MEMORY = 0x0008
UNKNOWN = 0xFFFFFFFF  # what the call for the memory says of what it does not know

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


def pair(cpu, high, low):
    """The 32-bit number that a program keeps in two registers, as the high one and the low one."""
    return cpu.r[high] << 16 | cpu.r[low]


def put_pair(cpu, high, low, value):
    cpu.r[high], cpu.r[low] = value >> 16 & 0xFFFF, value & 0xFFFF


def memory_information(cpu, state):
    """AX=0500h: the facts about the memory, in the 48 bytes at ES:EDI. The size of the biggest block is the first;
    then the pages, as many as can be had of one kind and another, and the totals (-1 where it is not known)."""
    extended = state.extended
    pages = extended.free_bytes() // PAGE
    fields = (extended.largest(), extended.largest() // PAGE, extended.largest() // PAGE,
              (extended.end - extended.start) // PAGE, pages, pages, (extended.end - extended.start) // PAGE,
              pages, UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN)
    for i, value in enumerate(fields):
        cpu.wd(cpu.s[CPU.ES], cpu.get32(CPU.DI) + 4 * i, value)


def int31(cpu, state):
    """INT 31h: the calls to the DPMI host. AX picks the call, and CF is 1 when it failed, and AX is the error.

    The descriptors: 0000h (CX of them, from the selector in AX), 0001h (free the one in BX), 0006h and 0007h (the
    base of BX in CX:DX, get and set), 0008h and 0009h (the limit in CX:DX and the rights in CX: taken and not kept);
    0400h, the version; the memory: 0500h (what there is), 0501h and 0502h (a block above 1 MB, and its return),
    0100h and 0101h (a block below it, with its selector, and its return), 0600h and 0601h (locking: nothing to lock),
    0800h (a device's memory: the physical address is the linear one). A call that is not here stops the emulator.
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
    elif ax == 0x0500:
        memory_information(cpu, state)
    elif ax == 0x0501:
        size = pair(cpu, CPU.BX, CPU.CX)
        if size == 0:
            return refuse(cpu, INVALID_VALUE)
        address = state.extended.allocate(size, state.psp_seg)
        if address is None:
            return refuse(cpu, UNAVAILABLE_MEMORY)
        put_pair(cpu, CPU.BX, CPU.CX, address)
        put_pair(cpu, CPU.SI, CPU.DI, address)  # the handle is the address: nothing else is asked of it
    elif ax == 0x0502:
        if not state.extended.free(pair(cpu, CPU.SI, CPU.DI)):
            return refuse(cpu, INVALID_HANDLE)
    elif ax in (0x0600, 0x0601):
        pass
    elif ax == 0x0800:
        put_pair(cpu, CPU.BX, CPU.CX, pair(cpu, CPU.BX, CPU.CX))
    elif ax == 0x0100:
        segment = state.memory.allocate(cpu.r[CPU.BX], state.psp_seg)
        if segment is None:
            cpu.r[CPU.BX] = state.memory.largest()
            return refuse(cpu, INSUFFICIENT_DOS_MEMORY)
        selector = new_descriptors(cpu.bases, 1)
        if selector is None:
            state.memory.free(segment)
            return refuse(cpu, UNAVAILABLE_DESCRIPTOR)
        cpu.bases[selector] = segment * 16
        cpu.r[CPU.AX], cpu.r[CPU.DX] = segment, selector
    elif ax == 0x0101:
        if cpu.r[CPU.DX] not in cpu.bases or not state.memory.free(cpu.bases[cpu.r[CPU.DX]] // 16):
            return refuse(cpu, INVALID_SELECTOR)
        del cpu.bases[cpu.r[CPU.DX]]
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
