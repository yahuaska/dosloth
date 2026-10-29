"""The memory above the first megabyte, where the 32-bit programs of the flat mode live.

DOS cannot give it out (it is not in the chain of its blocks), the extender does: an LE program gets its objects here,
and the DPMI calls that ask for memory get theirs from the same place. Like the blocks of DOS (see memory.py): a table
of blocks, the first gap that fits, and the owner of each, so that what a program has is taken back when it ends. The
blocks are of whole pages, and what they are has no header in the memory itself.
"""
from .cpu import MEMORY_SIZE

START = 0x100000  # the first byte above the first megabyte
PAGE = 4096


class ExtendedMemory:
    """The blocks that are given out between `start` and `end`."""

    def __init__(self, start=START, end=MEMORY_SIZE):
        self.start, self.end = start, end
        self.blocks = {}  # address -> size in bytes
        self.owners = {}  # address -> the PSP segment of the program that has it

    def gaps(self):
        """The free spaces as (address, size), from the lowest."""
        gaps, at = [], self.start
        for address in sorted(self.blocks):
            if address > at:
                gaps.append((at, address - at))
            at = address + self.blocks[address]
        if self.end > at:
            gaps.append((at, self.end - at))
        return gaps

    def largest(self):
        """The biggest block that could be given now, in bytes."""
        return max((size for _, size in self.gaps()), default=0)

    def free_bytes(self):
        return sum(size for _, size in self.gaps())

    def allocate(self, size, owner=0):
        """Take the first gap that holds `size` bytes (as whole pages) for the program whose PSP is at `owner`.

        Returns the address of the block, or None if there is no gap that big. A block of nothing is given a page: two
        blocks at the same address could not be told apart.
        """
        size = (max(size, 1) + PAGE - 1) // PAGE * PAGE
        for address, room in self.gaps():
            if room >= size:
                self.blocks[address] = size
                self.owners[address] = owner
                return address
        return None

    def free(self, address):
        """Give a block back. False if no block starts at `address`."""
        self.owners.pop(address, None)
        return self.blocks.pop(address, None) is not None

    def free_owned(self, owner):
        """Give back every block of a program: what happens when it ends."""
        for address in [address for address, who in self.owners.items() if who == owner]:
            self.free(address)
