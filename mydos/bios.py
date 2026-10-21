"""The BIOS interrupts, written in Python: the services DOS and programs expect below DOS itself.

Like dos.py, each handler gets the CPU, reads its arguments from the registers and puts its answer
back into them.
"""
import time
from collections import deque

from . import flags
from .cpu import CPU

TICKS_PER_DAY = 0x1800B0  # the BIOS timer ticks 18.2065 times a second: 1,573,040 ticks make a day
# where the BIOS keeps the state of the video, in its data area at 0040:0000
BDA_VIDEO_MODE = 0x449  # a byte
BDA_COLUMNS = 0x44A  # a word: the characters in a line
BDA_PAGE = 0x462  # a byte: the page of the screen that is shown
TEXT_MODE = 0x03  # 80 by 25 characters in 16 colors: what DOS is in before a program starts
GRAPHICS_MODE = 0x12  # 640 by 480 pixels in 16 colors
TEXT_BUFFER = 0xB8000  # where the characters of the text modes are, a pair of bytes each: the character, its color
TEXT_CLEARED = 0x4000  # how much of it a mode set clears: the four pages of 80 by 25
BLANK_CELL = bytes([0x20, 0x07])  # a space, light grey on black
OVERSCAN = 0x11  # the number of the attribute controller register of the border color
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


def set_video_mode(cpu, mode, keep_picture):
    """Put the machine in a video mode, as the BIOS does: the state in the data area, and a clean screen unless the
    picture is to be kept (the registers of the card are set up either way)."""
    if mode not in (TEXT_MODE, GRAPHICS_MODE):
        raise NotImplementedError(f"INT 10h AH=00 AL={mode:02X}: video mode {mode:02X}")
    cpu.mem[BDA_VIDEO_MODE] = mode
    cpu.mem[BDA_COLUMNS:BDA_COLUMNS + 2] = (80).to_bytes(2, "little")
    cpu.mem[BDA_PAGE] = 0
    if cpu.video is not None:
        cpu.video.clear(keep_planes=keep_picture)
    if not keep_picture:
        cpu.mem[TEXT_BUFFER:TEXT_BUFFER + TEXT_CLEARED] = BLANK_CELL * (TEXT_CLEARED // 2)


def palette_function(cpu, al):
    """INT 10h AH=10h: the colors of the screen, selected by AL.

    The 16 colors a pixel can have each name one of the 256 colors of the DAC, through the palette registers: 00h
    sets register BL to BH, 01h sets the overscan color to BH, 02h sets all 16 and the overscan from the 17 bytes at
    ES:DX; 07h, 08h and 09h read them back. The 256 colors of the DAC are 6 bits each of red, green and blue: 10h
    sets color BX to DH, CH, CL (red, green, blue), 12h sets CX colors from BX on out of the table at ES:DX, 3 bytes
    each; 15h and 17h read them back the same way.
    """
    card = cpu.video
    if card is None:
        raise NotImplementedError(f"INT 10h AH=10 AL={al:02X}: there is no video card to set the colors of")
    number = cpu.get8(3)
    segment, offset = cpu.s[CPU.ES], cpu.r[CPU.DX]
    color = cpu.r[CPU.BX] & 0xFF
    if al == 0x00 and number <= 0x0F:
        card.set_palette_register(number, cpu.get8(7))
    elif al == 0x01:
        card.set_palette_register(OVERSCAN, cpu.get8(7))
    elif al == 0x02:
        for n in range(17):
            card.set_palette_register(n if n < 16 else OVERSCAN, cpu.rb(segment, offset + n))
    elif al == 0x07 and number <= 0x0F:
        cpu.set8(7, card.palette_register(number))
    elif al == 0x08:
        cpu.set8(7, card.palette_register(OVERSCAN))
    elif al == 0x09:
        for n in range(17):
            cpu.wb(segment, offset + n, card.palette_register(n if n < 16 else OVERSCAN))
    elif al == 0x10:
        card.set_dac(color, cpu.get8(6), cpu.get8(5), cpu.get8(1))
    elif al == 0x12:
        for n in range(cpu.r[CPU.CX]):
            card.set_dac((color + n) & 0xFF, *(cpu.rb(segment, (offset + 3 * n + part) & 0xFFFF) for part in range(3)))
    elif al == 0x15:
        red, green, blue = card.get_dac(color)
        cpu.set8(6, red)
        cpu.set8(5, green)
        cpu.set8(1, blue)
    elif al == 0x17:
        for n in range(cpu.r[CPU.CX]):
            for part, value in enumerate(card.get_dac((color + n) & 0xFF)):
                cpu.wb(segment, (offset + 3 * n + part) & 0xFFFF, value)
    else:
        raise NotImplementedError(f"INT 10h AH=10 AL={al:02X}")


def int10(cpu):
    """INT 10h: the BIOS video services, selected by AH. So far 00h (modes 03h and 12h only), 0Fh, 10h (the colors,
    but not their page or gray sums) and 1Ah AL=00h."""
    ah, al = cpu.get8(4), cpu.get8(0)
    if ah == 0x10:
        palette_function(cpu, al)
    elif ah == 0x00:  # set the video mode AL; bit 7 of AL says to keep what is on the screen
        set_video_mode(cpu, al & 0x7F, bool(al & 0x80))
    elif ah == 0x0F:  # get the video mode: AL = the mode, AH = characters in a line, BH = the page shown
        cpu.set8(0, cpu.mem[BDA_VIDEO_MODE])
        cpu.set8(4, cpu.mem[BDA_COLUMNS])
        cpu.set8(7, cpu.mem[BDA_PAGE])
    elif ah == 0x1A and al == 0x00:
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
    cpu.mem[BDA_VIDEO_MODE] = TEXT_MODE  # the machine starts in text mode, as it is at the DOS prompt
    cpu.mem[BDA_COLUMNS:BDA_COLUMNS + 2] = (80).to_bytes(2, "little")
    cpu.mem[BDA_PAGE] = 0
    cpu.int_handlers[0x10] = int10
    cpu.int_handlers[0x1A] = int1a
    cpu.int_handlers[0x16] = lambda cpu: int16(cpu, keyboard)
    return keyboard
