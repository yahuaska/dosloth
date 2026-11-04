"""A mouse, and the driver that tells a program about it: INT 33h.

The driver keeps what a program can ask about: where the pointer is, which buttons are down, how many times each
button went down and up since it was last asked, where that happened, and the rectangle the pointer may move in.
Whoever has a real mouse, a window, calls `update()` whenever it moves or a button changes; the program asks
through INT 33h.

Positions are in pixels of the screen of the machine, 640 by 480. Buttons are numbered 0 for the left one, 1 for the
right one and 2 for the middle one; in a mask of them the bits are in that order.

A program may also give the driver a subroutine of its own, to be called when the mouse moves or a button changes
(function 0Ch). The driver remembers it, but does not call it yet: that needs a way to call the program's code in the
middle of its running. A program that asks the driver for the state now and then, as esetup does, does not notice.

What is not done (the call stops the emulator, so nothing goes unnoticed): drawing the cursor (the pointer of the
host stays over the window, and it is what the person sees), the speed of the mouse and its mickeys.
"""
from .cpu import CPU

WIDTH = 640
HEIGHT = 480
BUTTONS = 2  # the driver says it has two buttons, as a plain mouse does


class Mouse:
    """The state of the mouse, as the driver knows it."""

    def __init__(self):
        self.buttons = 0  # the buttons that are down now
        self.reset()

    def reset(self):
        """What a reset of the driver does: the pointer to the middle of the screen, the cursor hidden, the whole
        screen for the pointer to move in, and the counts of presses and releases forgotten. The buttons that are
        down are down."""
        self.handler = None  # (the events it wants, the segment, the offset) of the program's subroutine, if any
        self.cursor = -1  # the cursor is shown when this is 0: every show adds 1 (up to 0), every hide takes 1 off
        self.left, self.right, self.top, self.bottom = 0, WIDTH - 1, 0, HEIGHT - 1
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

    def update(self, x, y, buttons):
        """The mouse is now at x, y with these buttons down. Counts every button that went down or up."""
        self.move_to(x, y)
        for button in range(3):
            before, now = self.buttons >> button & 1, buttons >> button & 1
            if now and not before:
                self.presses[button] += 1
                self.press_at[button] = (self.x, self.y)
            elif before and not now:
                self.releases[button] += 1
                self.release_at[button] = (self.x, self.y)
        self.buttons = buttons


def int33(cpu, mouse):
    """INT 33h: the mouse driver, selected by AX. So far 0000h-0008h and 000Ch."""
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
    elif ax == 0x000C:  # CX = the events that call the program's subroutine at ES:DX; 0 takes the subroutine away
        mask = cpu.r[CPU.CX]
        mouse.handler = (mask, cpu.s[CPU.ES], cpu.r[CPU.DX]) if mask else None
    elif ax in (0x0007, 0x0008):  # the range for the pointer, CX to DX: across (7) or down (8)
        mouse.set_range(ax == 0x0007, cpu.r[CPU.CX], cpu.r[CPU.DX])
    else:
        raise NotImplementedError(f"INT 33h AX={ax:04X}")


def install(cpu):
    """Put the driver on INT 33h of the CPU. Returns the mouse, for whoever has events to give it."""
    mouse = Mouse()
    cpu.int_handlers[0x33] = lambda cpu: int33(cpu, mouse)
    return mouse
