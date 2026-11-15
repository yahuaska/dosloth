"""A mouse, and the driver that tells a program about it: INT 33h.

The driver keeps what a program can ask about: where the pointer is, which buttons are down, how many times each
button went down and up since it was last asked, where that happened, and the rectangle the pointer may move in.
Whoever has a real mouse, a window, calls `update()` whenever it moves or a button changes; the program asks
through INT 33h.

Positions are in the units of the driver: pixels of the screen of the machine, 640 by 480, unless a program asks for
finer ones. A real mouse tells only how far it moved, in mickeys, and the driver moves the pointer by mickeys * 8 /
the mickeys for 8 pixels that the program set (function 0Fh); X-COM: Apocalypse sets 1 and 1, and a range of
5120 by 3840, so that its units are eighths of a pixel. The window here tells where the pointer of the host is, a
pixel of the screen: the driver takes a pixel of the host for a mickey and puts the pointer at the units of that
many mickeys from the corner, so that the pointer of the program is where that of the host is. A pixel of the host
is never less than a unit (with the mickeys of a reset, 8 and 16, it is one unit), so that all the screen is
reached. Buttons are numbered 0 for the left one, 1 for the
right one and 2 for the middle one; in a mask of them the bits are in that order.

A program may also give the driver a subroutine of its own, to be called when the mouse moves or a button changes
(function 0Ch). A real driver calls it from the interrupt of the mouse (IRQ 12), between two instructions of the
program; this one looks every LOOK_EVERY instructions whether something the subroutine wants has happened, and if
the program allows interrupts (IF), calls it as the interrupt would: the registers of the program are kept, the
subroutine gets the events in AX, the buttons in BX, the place in CX and DX, and is called far (with doublewords in
the flat mode), with the return address in the ROM (rom.MOUSE_RETURN_AT). Its RETF goes there, to an INT F1h, and
the driver puts the registers of the program back: the program goes on where it was stopped, as after an IRET.
The subroutine is not called again while it runs.

What is not done (the call stops the emulator, so nothing goes unnoticed): drawing the cursor (the pointer of the
host stays over the window, and it is what the person sees), the speed of the mouse and its mickeys.
"""
from . import flags
from .cpu import CPU
from .loader import CODE_SELECTOR
from .rom import MOUSE_RETURN_AT

WIDTH = 640
HEIGHT = 480
BUTTONS = 2  # the driver says it has two buttons, as a plain mouse does
MICKEYS_X, MICKEYS_Y = 8, 16  # the mickeys for 8 pixels that a reset gives: a mickey is the step the mouse itself
# tells, some 1/200 of an inch; the driver moves the pointer by mickeys * 8 / these pixels
MOVED = 0x01  # the events a program's subroutine is called for (function 0Ch): the pointer moved; then for each
PRESSED, RELEASED = 0x02, 0x04  # button, from the left one, two bits: it went down, it went up (shifted 2 a button)
RETURN = 0xF1  # the interrupt where the subroutine returns to (rom.MOUSE_RETURN_AT)
LOOK_EVERY = 1000  # instructions: how often the driver looks whether the subroutine is to be called


class Mouse:
    """The state of the mouse, as the driver knows it."""

    def __init__(self):
        self.buttons = 0  # the buttons that are down now
        self.saved = None  # while the program's subroutine runs: the registers of the program, as call() took them
        self.reset()

    def reset(self):
        """What a reset of the driver does: the pointer to the middle of the screen, the cursor hidden, the whole
        screen for the pointer to move in, and the counts of presses and releases forgotten. The buttons that are
        down are down."""
        self.handler = None  # (the events it wants, the segment, the offset) of the program's subroutine, if any
        self.events = 0  # what has happened since the subroutine was last called (MOVED, PRESSED, RELEASED)
        self.cursor = -1  # the cursor is shown when this is 0: every show adds 1 (up to 0), every hide takes 1 off
        self.left, self.right, self.top, self.bottom = 0, WIDTH - 1, 0, HEIGHT - 1
        self.mickeys_x, self.mickeys_y = MICKEYS_X, MICKEYS_Y  # how many mickeys move the pointer 8 pixels
        self.x, self.y = WIDTH // 2, HEIGHT // 2
        self.presses = [0, 0, 0]
        self.releases = [0, 0, 0]
        self.press_at = [(0, 0)] * 3
        self.release_at = [(0, 0)] * 3

    def clamp(self):
        self.x = min(max(self.x, self.left), self.right)
        self.y = min(max(self.y, self.top), self.bottom)

    def move_to(self, x, y):
        self.x, self.y = x, y
        self.clamp()

    def set_range(self, horizontal, low, high):
        """Allow the pointer only between `low` and `high` (in either order), across if `horizontal`, else down."""
        low, high = min(low, high), max(low, high)
        if horizontal:
            self.left, self.right = low, high
        else:
            self.top, self.bottom = low, high
        self.clamp()

    def units(self, pixels, mickeys):
        """The units of the driver that a place of the host, in pixels, is at, with `mickeys` for 8 of them."""
        return pixels * 8 // mickeys if mickeys < 8 else pixels

    def update(self, x, y, buttons):
        """The pointer of the host is now at the pixel x, y of the screen, with these buttons down. Counts every button
        that went down or up, and keeps what happened in `events`, for the program's subroutine."""
        where = self.x, self.y
        self.move_to(self.units(x, self.mickeys_x), self.units(y, self.mickeys_y))
        if (self.x, self.y) != where:
            self.events |= MOVED
        for button in range(3):
            before, now = self.buttons >> button & 1, buttons >> button & 1
            if now and not before:
                self.presses[button] += 1
                self.press_at[button] = (self.x, self.y)
                self.events |= PRESSED << 2 * button
            elif before and not now:
                self.releases[button] += 1
                self.release_at[button] = (self.x, self.y)
                self.events |= RELEASED << 2 * button
        self.buttons = buttons


def int33(cpu, mouse):
    """INT 33h: the mouse driver, selected by AX. So far 0000h-0008h, 000Ch and 000Fh."""
    ax = cpu.r[CPU.AX]
    if ax == 0x0000:  # reset the driver. AX = FFFFh: there is a mouse and a driver; BX = how many buttons
        mouse.reset()
        cpu.r[CPU.AX] = 0xFFFF
        cpu.r[CPU.BX] = BUTTONS
    elif ax == 0x0001:  # show the cursor
        mouse.cursor = min(mouse.cursor + 1, 0)
    elif ax == 0x0002:  # hide the cursor
        mouse.cursor -= 1
    elif ax == 0x0003:  # BX = the buttons that are down, CX = x, DX = y
        cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = mouse.buttons, mouse.x, mouse.y
    elif ax == 0x0004:  # put the pointer at CX, DX
        mouse.move_to(cpu.r[CPU.CX], cpu.r[CPU.DX])
    elif ax in (0x0005, 0x0006):  # about the button in BX: how many times it went down (5) or up (6) since last asked
        button = cpu.r[CPU.BX]
        if button > 2:
            raise NotImplementedError(f"INT 33h AX={ax:04X} BX={button:04X}: no such button")
        counts, places = (mouse.presses, mouse.press_at) if ax == 0x0005 else (mouse.releases, mouse.release_at)
        cpu.r[CPU.AX] = mouse.buttons  # AX = the buttons that are down now
        cpu.r[CPU.BX] = counts[button]
        cpu.r[CPU.CX], cpu.r[CPU.DX] = places[button]  # where it last happened
        counts[button] = 0
    elif ax == 0x000C:  # CX = the events that call the program's subroutine at ES:DX (ES:EDX for a program in the
        mask = cpu.r[CPU.CX]  # flat mode, as DOS/4G takes it); 0 takes the subroutine away
        mouse.handler = (mask, cpu.s[CPU.ES], cpu.wide(CPU.DX)) if mask else None
        mouse.events = 0  # what happened before is not for this subroutine
    elif ax == 0x000F:  # CX and DX mickeys move the pointer 8 pixels, across and down
        if not (1 <= cpu.r[CPU.CX] < 0x8000 and 1 <= cpu.r[CPU.DX] < 0x8000):
            raise NotImplementedError(f"INT 33h AX=000F CX={cpu.r[CPU.CX]:04X} DX={cpu.r[CPU.DX]:04X}: not 1 to 7FFFh")
        mouse.mickeys_x, mouse.mickeys_y = cpu.r[CPU.CX], cpu.r[CPU.DX]
    elif ax in (0x0007, 0x0008):  # the range for the pointer, CX to DX: across (7) or down (8)
        mouse.set_range(ax == 0x0007, cpu.r[CPU.CX], cpu.r[CPU.DX])
    else:
        raise NotImplementedError(f"INT 33h AX={ax:04X}")


def call(cpu, mouse):
    """Stop the program between two instructions and call its subroutine with the events it wants. The registers of
    the program are kept in `mouse.saved` till the subroutine returns (see returned()). SI and DI, which a real
    driver gives the last movement in mickeys in, are 0."""
    wanted, segment, offset = mouse.handler
    events, mouse.events = mouse.events & wanted, 0
    mouse.saved = [list(cpu.r), list(cpu.rh), list(cpu.s), cpu.flags, cpu.eflags_high, cpu.ip]
    rom_segment, rom_offset = MOUSE_RETURN_AT
    if cpu.flat:  # a 32-bit subroutine: RETF takes EIP and CS as doublewords; the ROM is reached by a selector of base 0
        cpu.push32(CODE_SELECTOR)
        cpu.push32(rom_segment * 16 + rom_offset)
    else:
        cpu.push16(rom_segment)
        cpu.push16(rom_offset)
    for register, value in ((CPU.AX, events), (CPU.BX, mouse.buttons), (CPU.CX, mouse.x), (CPU.DX, mouse.y),
                            (CPU.SI, 0), (CPU.DI, 0)):
        cpu.set32(register, value)
    cpu.flags &= ~(flags.IF | flags.TF)  # as in the handler of an interrupt
    cpu.s[CPU.CS], cpu.ip = segment, offset


def returned(cpu, mouse):
    """INT F1h, where the subroutine returns to: the registers of the program as they were before call()."""
    if mouse.saved is None:
        raise NotImplementedError(f"INT {RETURN:02X} at {cpu.s[CPU.CS]:04X}:{cpu.start_ip:X}: "
                                  "the mouse driver has not called a subroutine")
    r, rh, s, cpu.flags, cpu.eflags_high, cpu.ip = mouse.saved
    cpu.r[:], cpu.rh[:], cpu.s[:] = r, rh, s
    mouse.saved = None


def install(cpu):
    """Put the driver on INT 33h of the CPU, and its look at the events among the CPU's event sources. Returns the
    mouse, for whoever has events to give it."""
    mouse = Mouse()
    cpu.int_handlers[0x33] = lambda cpu: int33(cpu, mouse)
    cpu.int_handlers[RETURN] = lambda cpu: returned(cpu, mouse)

    def look(now):
        if (mouse.handler and mouse.events & mouse.handler[0] and mouse.saved is None
                and cpu.flags & flags.IF):
            call(cpu, mouse)
        return now + LOOK_EVERY
    cpu.event_sources.append(look)
    return mouse
