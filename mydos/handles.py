"""The table of file handles of the running program: what each number it was given stands for.

A program does not hold files, it holds numbers. DOS gives a program 20 of them. The first five are open from the
start: 0, 1 and 2 are the console (input, output, errors), 3 and 4 the auxiliary and printer devices. Opening or
creating a file hands out the lowest number that is free, so the first file gets 5, and a program that closes
handle 0 and then opens a file gets 0 back (the old way to redirect input).
"""
from dataclasses import dataclass

TABLE_SIZE = 20  # as many handles as the table in the PSP has room for


@dataclass(frozen=True)
class Console:
    """Handle 0, 1 or 2. `stream` is where what is written to it goes: our "stdout" or our "stderr"."""
    stream: str


@dataclass(frozen=True)
class NullDevice:
    """Handles 3 and 4, the auxiliary and printer devices. There is nothing behind them: writes are thrown away."""
    name: str


@dataclass(frozen=True)
class OpenFile:
    """A file opened or created by the program. `file` is the file object of the file system; `dos_path` is
    the full name, for messages. `readable` and `writable` are what the program asked for when it opened the file:
    a file opened to be read only is not to be written, and the other way round."""
    file: object
    dos_path: str
    readable: bool = True
    writable: bool = True


class HandleTable:
    """The 20 slots. A slot holds a Console, a NullDevice, an OpenFile, or None when it is free."""

    def __init__(self):
        self.slots = [None] * TABLE_SIZE
        console, error = Console("stdout"), Console("stderr")
        self.slots[0:5] = [console, console, error, NullDevice("AUX"), NullDevice("PRN")]

    def get(self, handle):
        """What the handle stands for, or None if it is not open (never was, was closed, or is out of range)."""
        return self.slots[handle] if 0 <= handle < TABLE_SIZE else None

    def has_free(self):
        return None in self.slots

    def allocate(self, entry):
        """Put `entry` in the lowest free slot and return its number, or None if all 20 are taken."""
        for handle, slot in enumerate(self.slots):
            if slot is None:
                self.slots[handle] = entry
                return handle
        return None

    def close(self, handle):
        """Free the slot, closing the file if it holds one. Returns False if the handle was not open."""
        entry = self.get(handle)
        if entry is None:
            return False
        self.slots[handle] = None
        if isinstance(entry, OpenFile):
            entry.file.close()
        return True

    def close_all(self):
        """Close everything, as DOS does when a program ends."""
        for handle in range(TABLE_SIZE):
            self.close(handle)
