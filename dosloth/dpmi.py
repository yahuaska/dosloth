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
from .loader import CODE_SELECTOR
from .rom import default_handler
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
PROGRAM_PARAGRAPHS = 0x100  # what a program of 32 bits keeps of the memory below 1 MB that DOS gave it: its PSP, and room
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


def get_vector(cpu, n):
    """The (selector, offset) of the handler of interrupt `n` of the flat mode: what a program has set, else the one of
    the ROM (see rom.py), reached through the code selector of the program, whose base is 0. An interrupt that is one of
    ours (a Python function: DOS, the BIOS) has no address to give: the program that asks stops the emulator."""
    if n in cpu.pm_vectors:
        return cpu.pm_vectors[n]
    if n in cpu.int_handlers:
        raise NotImplementedError(f"the handler of interrupt {n:02X} is a function of the emulator: it has no address")
    segment, offset = default_handler(n)
    return CODE_SELECTOR, segment * 16 + offset


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


REAL_CALL_SIZE = 0x32  # the structure of AX=0300h: the registers of the real mode, 50 bytes
REAL_CALL_REGISTERS = ((0x00, CPU.DI), (0x04, CPU.SI), (0x08, CPU.BP), (0x10, CPU.BX), (0x14, CPU.DX),
                       (0x18, CPU.CX), (0x1C, CPU.AX))  # the offset in the structure, and the register
REAL_CALL_SEGMENTS = ((0x22, CPU.ES), (0x24, CPU.DS), (0x26, CPU.FS), (0x28, CPU.GS))
REAL_CALL_FLAGS = 0x20
REAL_CALLS = (0x10,)  # the interrupts that a program may call so: the video BIOS, and no other so far


def real_mode_interrupt(cpu):
    """AX=0300h: call the interrupt BL of the real mode, with the registers that the structure at ES:EDI has, and
    put the registers that it left back into the structure. The interrupts are ours (Python), so the handler runs
    with the machine put in real mode for the time of the call, and the registers of the program are as they were."""
    number = cpu.get8(3)
    if number not in REAL_CALLS:
        raise NotImplementedError(f"INT 31h AX=0300 of the interrupt {number:02X}")
    structure = cpu.wide(CPU.DI)
    selector = cpu.s[CPU.ES]
    data = bytes(cpu.rb(selector, structure + i) for i in range(REAL_CALL_SIZE))
    saved = (list(cpu.r), list(cpu.rh), list(cpu.s), cpu.flags, cpu.get_mode())
    cpu.use_real_memory()
    try:
        for offset, n in REAL_CALL_REGISTERS:
            cpu.set32(n, int.from_bytes(data[offset:offset + 4], "little"))
        for offset, n in REAL_CALL_SEGMENTS:
            cpu.s[n] = int.from_bytes(data[offset:offset + 2], "little")
        cpu.flags = int.from_bytes(data[REAL_CALL_FLAGS:REAL_CALL_FLAGS + 2], "little") | 0x0002
        cpu.int_handlers[number](cpu)
        out = bytearray(data)
        for offset, n in REAL_CALL_REGISTERS:
            out[offset:offset + 4] = cpu.get32(n).to_bytes(4, "little")
        out[REAL_CALL_FLAGS:REAL_CALL_FLAGS + 2] = (cpu.flags & 0xFFFF).to_bytes(2, "little")
    finally:
        cpu.r[:], cpu.rh[:], cpu.s[:], cpu.flags = saved[:4]
        cpu.set_mode(saved[4])
    for i, value in enumerate(out):
        cpu.wb(selector, structure + i, value)


def int31(cpu, state):
    """INT 31h: the calls to the DPMI host. AX picks the call, and CF is 1 when it failed, and AX is the error.

    The descriptors: 0000h (CX of them, from the selector in AX), 0001h (free the one in BX), 0006h and 0007h (the
    base of BX in CX:DX, get and set), 0008h and 0009h (the limit in CX:DX and the rights in CX: taken and not kept);
    0400h, the version; the memory: 0500h (what there is), 0501h and 0502h (a block above 1 MB, and its return),
    0100h and 0101h (a block below it, with its selector, and its return), 0600h and 0601h (locking: nothing to lock),
    0300h (an interrupt of the real mode: the video BIOS only), 0800h (a device's memory: the physical address is the linear one), 0200h and 0201h (the handlers of the interrupts of the real mode, the table at address 0), 0204h and 0205h
    (those of the protected mode: get, set). A call that is not here stops the emulator.
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
        if segment is None and state.memory.blocks.get(state.psp_seg, 0) > PROGRAM_PARAGRAPHS \
                and state.memory.resize(state.psp_seg, PROGRAM_PARAGRAPHS):
            # DOS gave all the memory to the program as it does to any program, but a program of 32 bits has its
            # code and data above 1 MB, and the memory below is for what it asks of DPMI: it gives it back
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
    elif ax == 0x0200:  # the handler of the interrupt BL of the real mode: CX:DX, from the table at address 0
        n = cpu.get8(3)
        vector = int.from_bytes(cpu.mem[n * 4:n * 4 + 4], "little")  # the table is at the physical address 0
        cpu.r[CPU.DX], cpu.r[CPU.CX] = vector & 0xFFFF, vector >> 16
        if not (cpu.r[CPU.CX] or cpu.r[CPU.DX]) and n not in cpu.int_handlers:
            cpu.r[CPU.CX], cpu.r[CPU.DX] = default_handler(n)  # none: the one of the ROM, as INT 21h AH=35h gives
    elif ax == 0x0201:  # set it, to CX:DX
        n = cpu.get8(3)
        cpu.mem[n * 4:n * 4 + 4] = (cpu.r[CPU.CX] << 16 | cpu.r[CPU.DX]).to_bytes(4, "little")
    elif ax == 0x0204:  # the handler of the interrupt BL of the protected mode: CX:EDX
        cpu.r[CPU.CX], selector_offset = get_vector(cpu, cpu.get8(3))
        cpu.set32(CPU.DX, selector_offset)
    elif ax == 0x0205:  # set it, to CX:EDX
        cpu.pm_vectors[cpu.get8(3)] = (cpu.r[CPU.CX], cpu.get32(CPU.DX))
    elif ax == 0x0300:
        real_mode_interrupt(cpu)
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
