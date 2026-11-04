"""The memory of the machine is 256 MB that are mostly empty: what is saved of it is only the parts that are not.

The memory is cut in pages of 4 KB. A page of zeros is not saved, and the pages that follow each other that are not
zeros make one run, so that a memory with a program in the first megabyte is a few runs and not 256 MB.
"""
PAGE = 4096
CHUNK = 1024 * 1024  # a part that is all zeros is told with one comparison, and only the others are looked at by pages
EMPTY_CHUNK = bytes(CHUNK)
EMPTY_PAGE = bytes(PAGE)


def spans(mem):
    """The parts of `mem` that are not all zeros, as (address, length): the pages that follow each other make one.
    The size of `mem` must be a whole number of megabytes. Looking at a page that was never written costs nothing, if
    the memory is a private anonymous mmap, as the memory of the machine is (a shared one gets a real page for it)."""
    found, start = [], None
    for base in range(0, len(mem), CHUNK):
        if mem[base:base + CHUNK] == EMPTY_CHUNK:  # a slice of the memory is bytes, and bytes are compared fast
            if start is not None:
                found.append((start, base - start))
                start = None
            continue
        for at in range(base, base + CHUNK, PAGE):
            if mem[at:at + PAGE] != EMPTY_PAGE:
                if start is None:
                    start = at
            elif start is not None:
                found.append((start, at - start))
                start = None
    if start is not None:
        found.append((start, len(mem) - start))
    return found


def runs(mem):
    """The same parts as `spans`, with what is in them: (address, bytes)."""
    return [(address, bytes(mem[address:address + length])) for address, length in spans(mem)]


def erase(mem):
    """Put zeros in all of `mem`, touching only the pages that are not empty (a page that was never written to costs
    nothing, and writing zeros to it all would make the system give it)."""
    for address, length in spans(mem):
        mem[address:address + length] = bytes(length)
