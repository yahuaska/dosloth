"""INT 21h of a 32-bit program in the flat mode: the registers are of 32 bits and the pointers are of the flat model.

So far the questions that the extender answers by itself (AX=FF00h, who is the extender) and the functions that the
16-bit handler does for both: they take a pointer or a count of 32 bits when the program is of 32 bits (EDX, ECX,
and the answer in all of EAX), see `CPU.wide` and `CPU.address_mask`. Any other function stops the emulator, with its
number, and is done when a program asks for it.
"""
from . import dpmi, flags
from .cpu import CPU, NEVER

# A program that asks whether a key was pressed (INT 21h AH=0Bh) again and again, with next to nothing done between the
# questions, has nothing to do but wait for the time to come: a player that waits for the next frame, a game that waits
# for a tick. The machine is as slow as the emulator is, and the time that the wait takes is only the count of
# instructions (see timer.py), so the wait would take as long as the emulator is slow. When the questions come closer than
# this many instructions, the time goes on at once to the next thing that is to happen (a tick of the timer, the end of a
# block of sound), and no further: nothing is jumped over, and the program that is at work between its questions has its time.
TIGHT_LOOP = 1000
EXTENDER_QUESTION = 0xFF00  # AX, with 0078h in DX: is DOS/4G here?
SHARED_WITH_REAL_MODE = (0x08, 0x0B, 0x1A, 0x2C, 0x30, 0x3D, 0x3E, 0x3F, 0x40, 0x42, 0x44, 0x4C, 0x4E, 0x4F)


def skip_idle_time(cpu):
    """A question whether a key waits: if the one before it was a few instructions ago (see TIGHT_LOOP), the program is
    waiting, and the time goes on to the next event of the devices, if there is one that is not past already."""
    if cpu.last_poll is not None and cpu.instructions - cpu.last_poll < TIGHT_LOOP and cpu.next_event != NEVER:
        cpu.instructions = max(cpu.instructions, cpu.next_event)
    cpu.last_poll = cpu.instructions


def int21(cpu, state, real):
    """INT 21h for the flat mode. `real(cpu, state)` is the handler of real mode, for what it does as it is."""
    ax = cpu.r[CPU.AX]
    if ax == EXTENDER_QUESTION and cpu.r[CPU.DX] == 0x0078:
        # AL tells that DOS/4G is here (the program asks only if it is 0), and GS is a selector with the base 0: the
        # whole memory, by the linear address
        cpu.r[CPU.AX] = 1
        cpu.set_flag(flags.CF, False)
        dpmi.zero_based_selector(cpu)
    elif ax >> 8 == 0x25:  # set the handler of interrupt AL to DS:EDX
        cpu.pm_vectors[cpu.get8(0)] = (cpu.s[CPU.DS], cpu.get32(CPU.DX))
        cpu.set_flag(flags.CF, False)
    elif ax >> 8 == 0x35:  # get it into ES:EBX
        cpu.s[CPU.ES], offset = dpmi.get_vector(cpu, cpu.get8(0))
        cpu.set32(CPU.BX, offset)
        cpu.set_flag(flags.CF, False)
    elif ax >> 8 in SHARED_WITH_REAL_MODE:
        if ax >> 8 == 0x0B:
            skip_idle_time(cpu)
        real(cpu, state)
    else:
        raise NotImplementedError(f"INT 21h AX={ax:04X} in the flat mode")
