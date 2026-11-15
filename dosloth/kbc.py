"""The keyboard controller (the 8042): the scan codes of the keys as the keyboard sends them, through I/O port 60h
and the interrupt of line 1 of the controllers (INT 9).

A key sends its scan code when it goes down (the make code, below 80h) and the same with 80h added when it comes up
(the break code). The keys that the keyboard of the PC/AT did not have, the arrows and the others of the grey block,
the Enter of the keypad and the right Ctrl and Alt, send E0h before each of their codes. A program that takes the
keyboard over (X-COM: Apocalypse does) puts its own handler on INT 9 and reads the byte from port 60h, one byte an
interrupt.

Here the bytes wait in `waiting`. When line 1 is free (not asked for and not in service), the next one goes to the
port and the line is raised; so the next byte comes after the handler has said the end of the interrupt of the one
before, as a real controller lets it. Reading the port gives the last byte that came, as often as it is read. Where
no program has a handler of INT 9, the machine says the end of the interrupt at once (see CPU.interrupt_by) and the
bytes go: a program that reads keys through the BIOS gets them from its buffer, which the window fills apart (see
bios.Keyboard).

Not done: the commands of the controller (port 64h), the lights, the repeat of a key held down (the host repeats it).
"""
from collections import deque

DATA_PORT = 0x60
LINE = 1  # the line of the interrupt controllers: INT 9
EXTENDED = 0xE0  # the prefix of the keys of the grey block
BREAK = 0x80  # added to the scan code when the key comes up
LOOK_EVERY = 1000  # instructions: how often the controller looks whether the line is free for the next byte


class KeyboardController:
    """The bytes the keyboard has sent and the program has not had yet, and the one at the port now."""

    def __init__(self):
        self.waiting = deque()
        self.data = 0

    def key(self, scan_code, down, extended=False):
        """A key went down (`down`) or came up. `extended` keys send E0h first."""
        if extended:
            self.waiting.append(EXTENDED)
        self.waiting.append(scan_code if down else scan_code | BREAK)

    def look(self, pics):
        """If a byte waits and line 1 is free, put it at the port and raise the line."""
        if not self.waiting:
            return
        bit = 1 << LINE
        if pics.master.requests & bit or pics.master.in_service & bit:
            return
        self.data = self.waiting.popleft()
        pics.raise_line(LINE)

    def read(self, port):
        return self.data


def install(cpu, pics):
    """Put the controller on port 60h and among the CPU's event sources. Returns it, for whoever has keys to give."""
    controller = KeyboardController()
    cpu.port_readers[DATA_PORT] = controller.read

    def look(now):
        controller.look(pics)
        return now + LOOK_EVERY
    cpu.event_sources.append(look)
    return controller
