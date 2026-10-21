"""The BIOS interrupts, written in Python: the services DOS and programs expect below DOS itself.

Like dos.py, each handler gets the CPU, reads its arguments from the registers and puts its answer
back into them.
"""
import time
from collections import deque

from . import flags
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


class Keyboard:
    """The keys that were pressed and not yet read, oldest first.

    Each is the word the BIOS gives a program: the scan code of the key in the high byte, the ASCII code in the
    low byte. Nothing fills it yet, so no key is ever pressed; a test, or later a real source of keys, calls press().
    """

    def __init__(self):
        self.buffer = deque()

    def press(self, scan_code, ascii_code):
        self.buffer.append(scan_code << 8 | ascii_code)


def int16(cpu, keyboard):
    """INT 16h: the BIOS keyboard, selected by AH. So far 00h, 01h and 02h."""
    ah = cpu.get8(4)
    if ah == 0x00:  # wait for a key and take it: AH = scan code, AL = ASCII code
        if not keyboard.buffer:
            # A real BIOS waits here until somebody types. Nobody can yet, and waiting for ever without a word
            # would be worse than stopping.
            raise NotImplementedError("INT 16h AH=00: the program waits for a key, and no key can be pressed yet")
        cpu.r[CPU.AX] = keyboard.buffer.popleft()
    elif ah == 0x01:  # is there a key? ZF = 1 when not; else ZF = 0 and AX is the key, which stays in the buffer
        cpu.set_flag(flags.ZF, not keyboard.buffer)
        if keyboard.buffer:
            cpu.r[CPU.AX] = keyboard.buffer[0]
    elif ah == 0x02:  # the state of Shift, Ctrl, Alt and the lock keys into AL: no bit set, nothing is held down
        cpu.set8(0, 0)
    else:
        raise NotImplementedError(f"INT 16h AH={ah:02X}")


def install(cpu):
    """Hook the BIOS interrupt handlers into the CPU. Returns the keyboard, for whoever wants to press keys on it."""
    keyboard = Keyboard()
    cpu.int_handlers[0x1A] = int1a
    cpu.int_handlers[0x16] = lambda cpu: int16(cpu, keyboard)
    return keyboard
