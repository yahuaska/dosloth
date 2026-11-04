"""The memory blocks DOS hands out: the program's own block, and those it asks for with AH=48h.

Naive: a list of blocks and the first gap that fits. DOS keeps its blocks as a chain of MCBs, small headers
in the memory itself; we keep them here, outside of it, and nothing in the memory shows them. Sizes and
segments are in paragraphs of 16 bytes.
"""


class Blocks:
    """The blocks of memory that are taken, between the first of them and the top of the ordinary memory."""

    def __init__(self, top, first_segment, first_size):
        self.top = top  # the segment where the memory ends: nothing above it can be given
        self.blocks = {first_segment: first_size}  # segment -> size; the first block is the program's
        self.owners = {first_segment: first_segment}  # segment -> the PSP segment of the program that asked for it

    def _end_of_gap_after(self, segment):
        """Where the free space that follows the block at `segment` ends: at the next block, or at the top."""
        following = [start for start in self.blocks if start > segment]
        return min(following, default=self.top)

    def _gaps(self):
        """The free spaces as (segment, size), from the lowest."""
        gaps = []
        for segment in sorted(self.blocks):
            begin = segment + self.blocks[segment]
            gaps.append((begin, self._end_of_gap_after(segment) - begin))
        return gaps

    def largest(self):
        """The biggest block that AH=48h could give now."""
        return max((size for _, size in self._gaps()), default=0)

    def allocate(self, size, owner=0):
        """Take the first gap that holds `size` paragraphs for the program whose PSP is at `owner`.

        Returns the segment of the block, or None if there is no gap that big.

        A block of 0 paragraphs is given one: two blocks at the same segment could not be told apart.
        """
        size = max(size, 1)
        for segment, room in self._gaps():
            if room >= size:
                self.blocks[segment] = size
                self.owners[segment] = owner
                return segment
        return None

    def free(self, segment):
        """Give a block back. False if no block starts at `segment`."""
        self.owners.pop(segment, None)
        return self.blocks.pop(segment, None) is not None

    def own(self, segment, owner):
        """Say that the block at `segment` belongs to the program whose PSP is at `owner`."""
        self.owners[segment] = owner

    def free_owned(self, owner):
        """Give back every block of a program: what DOS does when it ends."""
        for segment in [segment for segment, who in self.owners.items() if who == owner]:
            self.free(segment)

    def room_for(self, segment):
        """How many paragraphs the block at `segment` could have: its own, and the free space after it."""
        return self._end_of_gap_after(segment) - segment

    def resize(self, segment, size):
        """Make the block at `segment` `size` paragraphs long, if it fits before the next block or the top.

        Returns True if done, False if it does not fit, and None if no block starts at `segment`.
        """
        if segment not in self.blocks:
            return None
        if size > self.room_for(segment):
            return False
        self.blocks[segment] = size
        return True
