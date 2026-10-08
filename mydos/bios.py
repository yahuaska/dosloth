"""The BIOS interrupts, written in Python: the services DOS and programs expect below DOS itself.

Like dos.py, each handler gets the CPU, reads its arguments from the registers and puts its answer
back into them.
"""
import time

from .cpu import CPU

TICKS_PER_DAY = 0x1800B0  # the BIOS timer ticks 18.2065 times a second: 1,573,040 ticks make a day


def _seconds_since_midnight():
    """Local time of the host, as seconds since midnight (a function of its own so tests can fake it)."""
    now = time.localtime()
    return now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec + (time.time() % 1)


def int1a(cpu):
    """INT 1Ah: BIOS time of day, selected by AH. Only 00h so far."""
    ah = cpu.get8(4)
    if ah == 0x00:  # read the clock: CX:DX = timer ticks since midnight, AL = "midnight passed" flag
        ticks = int(_seconds_since_midnight() * TICKS_PER_DAY / 86400) % TICKS_PER_DAY
        cpu.r[CPU.CX] = ticks >> 16
        cpu.r[CPU.DX] = ticks & 0xFFFF
        cpu.set8(0, 0)  # the real BIOS says 1 if midnight went by since the last read; nobody checks
    else:
        raise NotImplementedError(f"INT 1Ah AH={ah:02X}")


def install(cpu):
    """Hook the BIOS interrupt handlers into the CPU."""
    cpu.int_handlers[0x1A] = int1a
