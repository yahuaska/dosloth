"""How a patch is made: the code that it stands in the place of is found, and the `INT` that stands for it."""
import hashlib

HOOK = 0xF0  # the number of the interrupt that stands in for the first instruction of a patched piece of code
PATCH_BYTES = bytes((0xCD, HOOK))


class Patch:
    """One piece of code: where it is (a linear address, which is the offset in the flat mode), how long it is, the
    SHA-256 of its bytes, and the function (`handler(cpu)`) that does its work."""

    def __init__(self, name, address, length, digest, handler):
        self.name, self.address, self.length, self.digest, self.handler = name, address, length, digest, handler

    def matches(self, cpu):
        """Whether the memory holds the very code that this patch is for."""
        return hashlib.sha256(cpu.mem[self.address:self.address + self.length]).hexdigest() == self.digest


# The patches of code that is not in the program when it is loaded, but comes later: the driver of the sound is
# read from a file by the player, and put where there is room. Such a piece is found by its bytes, and patched when it
# is there (and again when a saved machine is put back, for it keeps the INTs, and does not know what they mean).
LOOK_EVERY = 50_000  # the instructions between the looks, until all the pieces are found
SEARCH_FROM, SEARCH_TO = 0x100000, 0x800000  # where the program puts such code: above the first megabyte


class LatePatch:
    """A piece of code that comes later. It is `length` bytes, the first of which are `head` (they are what it is
    searched by), and SHA-256 `digest` is that of all of it. `sites` are the places in it (the offsets from its
    beginning) where the patch is: (offset, the two bytes of the code that the INT replaces, the handler)."""

    def __init__(self, name, head, length, digest, sites):
        self.name, self.head, self.length, self.digest, self.sites = name, bytes.fromhex(head), length, digest, sites

    def find(self, cpu):
        """The address of the code in the memory, as it is now (patched or not), or None."""
        mem, start = cpu.mem, SEARCH_FROM
        while True:
            at = mem.find(self.head, start, SEARCH_TO)
            if at < 0:
                return None
            code = bytearray(mem[at:at + self.length])
            for offset, original, _ in self.sites:  # what was there before the INTs
                code[offset:offset + len(PATCH_BYTES)] = original
            if hashlib.sha256(code).hexdigest() == self.digest:
                return at
            start = at + 1

    def apply(self, cpu):
        """Patch the code if it is there; the machine learns where. Returns whether it was found."""
        at = self.find(cpu)
        if at is None:
            return False
        for offset, _, handler in self.sites:
            cpu.mem[at + offset:at + offset + len(PATCH_BYTES)] = PATCH_BYTES
            cpu.patch_sites[at + offset] = handler
        return True
