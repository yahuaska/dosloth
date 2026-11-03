"""INT 21h of a 32-bit program in the flat mode: the registers are of 32 bits and the pointers are of the flat model.

So far the questions that the extender answers by itself (AX=FF00h, who is the extender) and the functions that the
16-bit handler does for both: they take a pointer or a count of 32 bits when the program is of 32 bits (EDX, ECX,
and the answer in all of EAX), see `CPU.wide` and `CPU.address_mask`. Any other function stops the emulator, with its
number, and is done when a program asks for it.
"""
from . import dpmi, flags
from .cpu import CPU

EXTENDER_QUESTION = 0xFF00  # AX, with 0078h in DX: is DOS/4G here?
SHARED_WITH_REAL_MODE = (0x0B, 0x1A, 0x30, 0x3D, 0x3E, 0x3F, 0x40, 0x42, 0x44, 0x4C, 0x4E, 0x4F)


def int21(cpu, state, real):
    """INT 21h for the flat mode. `real(cpu, state)` is the handler of real mode, for what it does as it is."""
    ax = cpu.r[CPU.AX]
    if ax == EXTENDER_QUESTION and cpu.r[CPU.DX] == 0x0078:
        # AL tells that DOS/4G is here (the program asks only if it is 0), and GS is a selector with the base 0: the
        # whole memory, by the linear address
        cpu.r[CPU.AX] = 1
        cpu.set_flag(flags.CF, False)
        dpmi.zero_based_selector(cpu)
    elif ax >> 8 in SHARED_WITH_REAL_MODE:
        real(cpu, state)
    else:
        raise NotImplementedError(f"INT 21h AX={ax:04X} in the flat mode")
