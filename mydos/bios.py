"""The BIOS interrupts, written in Python: the services DOS and programs expect below DOS itself.

Like dos.py, each handler gets the CPU, reads its arguments from the registers and puts its answer
back into them.
"""
import time
from collections import deque

from . import flags
from .cpu import CPU

TICKS_PER_DAY = 0x1800B0  # the BIOS timer ticks 18.2065 times a second: 1,573,040 ticks make a day
VGA_COLOR_DISPLAY = 0x08  # the code of a VGA with a color monitor in the answer of INT 10h AH=1Ah
NO_DISPLAY = 0x00  # the code that says there is no second monitor


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


def int10(cpu):
    """INT 10h: the BIOS video services, selected by AH. Only 1Ah AL=00h so far."""
    ah, al = cpu.get8(4), cpu.get8(0)
    if ah == 0x1A and al == 0x00:
        # Which video cards and monitors are there? AL = 1Ah says that the BIOS knows this call, which only a VGA
        # BIOS does: it is the way a program finds out that it is on a VGA. BL = the code of the active display,
        # BH = the code of the other one. Ours is a VGA with a color monitor, and nothing else.
        cpu.set8(0, 0x1A)
        cpu.set8(3, VGA_COLOR_DISPLAY)
        cpu.set8(7, NO_DISPLAY)
    else:
        raise NotImplementedError(f"INT 10h AH={ah:02X} AL={al:02X}")


class Keyboard:
    """The keys that were pressed and not yet read, oldest first.

    Each is the word the BIOS gives a program: the scan code of the key in the high byte, the ASCII code in the
    low byte. Whatever has keys to give calls press(). The buffer holds 15 keys, like the one of the real BIOS; a
    key pressed when it is full is lost (the PC beeps).

    `waiter` is what INT 16h AH=00 calls to wait for a key: a function that returns once the buffer is not empty.
    Without one, nothing could ever press a key, and waiting would be for ever.
    """

    SIZE = 15

    def __init__(self):
        self.buffer = deque()
        self.waiter = None

    def press(self, scan_code, ascii_code):
        if len(self.buffer) < self.SIZE:
            self.buffer.append(scan_code << 8 | ascii_code)


def int16(cpu, keyboard):
    """INT 16h: the BIOS keyboard, selected by AH. So far 00h, 01h and 02h."""
    ah = cpu.get8(4)
    if ah == 0x00:  # wait for a key and take it: AH = scan code, AL = ASCII code
        if not keyboard.buffer:
            if keyboard.waiter is None:  # waiting for ever without a word would be worse than stopping
                raise NotImplementedError("INT 16h AH=00: the program waits for a key, and no key can be pressed")
            keyboard.waiter()
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
    cpu.int_handlers[0x10] = int10
    cpu.int_handlers[0x1A] = int1a
    cpu.int_handlers[0x16] = lambda cpu: int16(cpu, keyboard)
    return keyboard
