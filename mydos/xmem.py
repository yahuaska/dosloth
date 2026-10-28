"""The memory above the first megabyte, where the 32-bit programs of the flat mode live.

DOS cannot give it out (it is not in the chain of its blocks), the extender does: an LE program gets its objects here,
and later the DPMI calls that ask for memory will get theirs from the same place. Naive: a mark that goes up as blocks
are given out, and goes back down to the address of a block when it and everything after it are no longer wanted (the
programs end in the order they were started, the last one first).
"""
from .cpu import MEMORY_SIZE

START = 0x100000  # the first byte above the first megabyte
PAGE = 4096


class ExtendedMemory:
    """Blocks of whole pages, from `start` up to `end`, given out one after another."""

    def __init__(self, start=START, end=MEMORY_SIZE):
        self.start, self.end = start, end
        self.top = start  # the first byte that is not given out

    def allocate(self, size):
        """The address of a block of `size` bytes (it takes whole pages), or None if there is not room for it."""
        pages = (size + PAGE - 1) // PAGE * PAGE
        if self.top + pages > self.end:
            return None
        address, self.top = self.top, self.top + pages
        return address

    def release_from(self, address):
        """Take back the block at `address` and every block given out after it."""
        if not self.start <= address <= self.top:
            raise ValueError(f"{address:X} is not an address of a block that is given out")
        self.top = address
