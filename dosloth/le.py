"""Programs in the LE format, which is what a DOS/4G program is: the 32-bit programs of the games of 1995-97.

A program for DOS/4G or DOS/4GW is an LE file (Linear Executable, the format of OS/2 2.x that Watcom's linker makes
for DOS). It is a header, a table of objects (a code object and a data object, as a rule: each a run of 4 KB pages
with a place in a 32-bit address space), a map from the pages of the objects to the pages in the file, the pages, and
a list of fixups: the places in the pages where an address of another place of the program is written, which the
loader has to correct for where it put the objects.

The file that is run is mostly not a plain LE. The programs of the game are bound to the extender: in front of the
program stands the extender itself, a DOS/16M stub and its kernel (`BW`, a 286 protected-mode program), and the
program with its own DOS stub and the LE header comes after it:

    0               MZ stub of the extender (DOS/16M), and its kernel, `BW`, right after the stub
    start           the MZ stub of the program (its e_lfanew, at start+3Ch, is where the LE header is)
    start+e_lfanew  `LE`, the header, the tables of the loader and the fixups
    start+data_pages  the pages

We do not run the extender: it needs a protected mode that we do not have, and everything it does for a program (it
is the DPMI host, and passes the calls of DOS) is done by the emulator itself. What we want out of the file is the
program, as `LeImage`, and a place in the file where the program begins, as `locate` finds it.

Done here: reading the header, the objects, the pages and the fixups, and making an image of the objects as they are
after the fixups, for the bases the loader chooses. Not done, and said at once with a LeError: a page that is not
stored plainly (iterated or compressed), a fixup to another module (an import) or by way of the table of entries, a
program for the 286, a big-endian file. None of the programs seen so far has any of them.
"""
import struct
from dataclasses import dataclass

LE_SIGNATURE = b"LE\x00\x00"  # `LE`, then the byte order and the word order, both 0 for the little-endian
HEADER_SIZE = 0xB0  # the part of the header that is read
KERNEL_SIGNATURE = b"BW"  # what follows the MZ stub of a DOS/16M program: the kernel of the extender

# the flags of an object
READABLE, WRITABLE, EXECUTABLE, RESOURCE, DISCARDABLE, SHARED, PRELOAD, INVALID, ZERO_FILLED = (
    0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80, 0x100)
BIG = 0x2000  # 32-bit: the default size of operands and addresses of its code is 32 bits

# what a page of an object is in the file (the flags of the page map)
PAGE_LEGAL, PAGE_ITERATED, PAGE_INVALID, PAGE_ZERO_FILLED, PAGE_RANGE = 0, 1, 2, 3, 4

# the kinds of a place that a fixup writes to (the low 4 bits of its source type)
SOURCE_BYTE, SOURCE_SELECTOR, SOURCE_POINTER_16_16, SOURCE_OFFSET_16, SOURCE_POINTER_16_32 = 0, 2, 3, 5, 6
SOURCE_OFFSET_32, SOURCE_RELATIVE_32 = 7, 8


class LeError(ValueError):
    """The file is not an LE program that can be loaded. The message says why."""


@dataclass(frozen=True)
class Object:
    """An object of the program: a run of pages that go to one place of the address space."""
    number: int  # from 1, as the fixups and the entry name it
    size: int  # the virtual size in bytes; what the pages do not fill is zeros
    base: int  # the address the linker meant it for; the loader may choose another
    flags: int
    first_page: int  # from 1: the number in the page map of its first page
    page_count: int

    @property
    def executable(self):
        return bool(self.flags & EXECUTABLE)

    @property
    def writable(self):
        return bool(self.flags & WRITABLE)

    @property
    def big(self):
        return bool(self.flags & BIG)


@dataclass(frozen=True)
class Fixup:
    """One place where an address is to be written: in the page `page` (from 0), at `offset` from its start (it can
    be negative, for a place that begins in the page before), what kind (`source`), and the place that it names:
    `target_offset` in the object `target_object`, and `additive` more."""
    page: int
    source: int
    offset: int
    target_object: int
    target_offset: int
    additive: int


class LeImage:
    """The LE program that begins at `start` in `data` (the file): where its DOS stub is; the header comes after it,
    at the e_lfanew of the stub. Raises a LeError if what is there cannot be loaded."""

    def __init__(self, data, start=0):
        self.data = data
        self.start = start
        if start + 0x40 > len(data) or data[start:start + 2] not in (b"MZ", b"ZM"):
            raise LeError("no DOS stub of the program is where it should be")
        self.header_at = start + struct.unpack_from("<I", data, start + 0x3C)[0]
        if self.header_at + HEADER_SIZE > len(data) or data[self.header_at:self.header_at + 4] != LE_SIGNATURE:
            raise LeError("no LE header where the DOS stub says it is (a file of another format, or one that is cut)")
        self._read_header()
        self.objects = self._read_objects()
        self.page_map = self._read_page_map()

    def _word(self, offset):
        return struct.unpack_from("<I", self.data, self.header_at + offset)[0]

    def _read_header(self):
        cpu = struct.unpack_from("<H", self.data, self.header_at + 8)[0]
        if cpu not in (2, 3, 4):
            raise LeError(f"the program is for the cpu {cpu}, and only the 386 and later are done")
        self.cpu = cpu
        self.flags = self._word(0x10)
        self.page_count = self._word(0x14)
        self.entry_object, self.entry_offset = self._word(0x18), self._word(0x1C)
        self.stack_object, self.stack_offset = self._word(0x20), self._word(0x24)
        self.page_size = self._word(0x28)
        self.last_page_size = self._word(0x2C)
        self._object_table, self._object_count = self._word(0x40), self._word(0x44)
        self._page_map_at = self._word(0x48)
        self._iterated_map = self._word(0x4C)
        self._fixup_page_table, self._fixup_records = self._word(0x68), self._word(0x6C)
        self._fixup_size = self._word(0x30)
        self.import_modules = self._word(0x74)
        self._data_pages = self._word(0x80)
        if self.page_size == 0 or self.page_size & (self.page_size - 1):
            raise LeError(f"the size of a page, {self.page_size}, is not a power of two")
        if self.import_modules:
            raise LeError(f"the program imports from {self.import_modules} other modules, which is not done")

    def _read_objects(self):
        objects = []
        for i in range(self._object_count):
            at = self.header_at + self._object_table + 24 * i
            if at + 24 > len(self.data):
                raise LeError("the table of objects goes beyond the end of the file")
            size, base, flags, first, count, _ = struct.unpack_from("<6I", self.data, at)
            objects.append(Object(i + 1, size, base, flags, first, count))
        return objects

    def _read_page_map(self):
        at = self.header_at + self._page_map_at
        if at + 4 * self.page_count > len(self.data):
            raise LeError("the map of the pages goes beyond the end of the file")
        return [(self.data[at + 4 * i] << 16 | self.data[at + 4 * i + 1] << 8 | self.data[at + 4 * i + 2],
                 self.data[at + 4 * i + 3]) for i in range(self.page_count)]

    @property
    def entry_address(self):
        """Where the program begins, as the address the linker meant: the base of the entry object and the offset."""
        return self.object(self.entry_object).base + self.entry_offset

    def object(self, number):
        if not 1 <= number <= len(self.objects):
            raise LeError(f"there is no object {number}")
        return self.objects[number - 1]

    def page_bytes(self, page):
        """The bytes of the page `page` of the program (from 0), a whole page, as they are in the file."""
        number, kind = self.page_map[page]
        if kind == PAGE_ZERO_FILLED:
            return bytes(self.page_size)
        if kind != PAGE_LEGAL:
            raise LeError(f"the page {page + 1} is of a kind that is not done ({kind}: iterated, invalid or a range)")
        at = self.start + self._data_pages + (number - 1) * self.page_size
        length = self.last_page_size if page == self.page_count - 1 else self.page_size
        if at + length > len(self.data):
            raise LeError(f"the page {page + 1} is beyond the end of the file")
        return self.data[at:at + length].ljust(self.page_size, b"\0")

    def object_bytes(self, number):
        """The bytes of an object before the fixups: its pages, cut or filled with zeros to its virtual size."""
        obj = self.object(number)
        pages = range(obj.first_page - 1, obj.first_page - 1 + obj.page_count)
        return b"".join(self.page_bytes(page) for page in pages)[:obj.size].ljust(obj.size, b"\0")

    def fixups(self):
        """Every fixup of the program, as a list of Fixup. Raises a LeError for one that cannot be done."""
        data = self.data
        table = self.header_at + self._fixup_page_table
        records = self.header_at + self._fixup_records
        if table + 4 * (self.page_count + 1) > len(data):
            raise LeError("the table of the fixups of the pages goes beyond the end of the file")
        starts = struct.unpack_from(f"<{self.page_count + 1}I", data, table)
        result = []
        for page in range(self.page_count):
            at, end = records + starts[page], records + starts[page + 1]
            if end > len(data) or at > end:
                raise LeError(f"the fixups of the page {page + 1} are not where they should be")
            while at < end:
                at = self._fixup(page, at, result)
        return result

    def _fixup(self, page, at, result):
        data = self.data
        source, flags = data[at], data[at + 1]
        at += 2
        kind, listed = source & 0x0F, bool(source & 0x20)
        if source & 0x10:
            raise LeError("a fixup to an alias is not done")
        if flags & 0x03:
            raise LeError("a fixup to another module, or by way of the table of entries, is not done")
        if listed:
            count = data[at]
            at += 1
        else:
            offset = struct.unpack_from("<h", data, at)[0]
            at += 2
        if flags & 0x40:  # the number of the object is a word
            target, at = struct.unpack_from("<H", data, at)[0], at + 2
        else:
            target, at = data[at], at + 1
        target_offset = 0
        if kind != SOURCE_SELECTOR:
            if flags & 0x10:
                target_offset, at = struct.unpack_from("<I", data, at)[0], at + 4
            else:
                target_offset, at = struct.unpack_from("<H", data, at)[0], at + 2
        additive = 0
        if flags & 0x04:
            if flags & 0x20:
                additive, at = struct.unpack_from("<I", data, at)[0], at + 4
            else:
                additive, at = struct.unpack_from("<H", data, at)[0], at + 2
        if listed:
            for _ in range(count):
                result.append(Fixup(page, kind, struct.unpack_from("<h", data, at)[0], target, target_offset, additive))
                at += 2
        else:
            result.append(Fixup(page, kind, offset, target, target_offset, additive))
        return at

    def load(self, bases, selector_of=None):
        """The objects as they are in memory once the program is put at `bases` (a dict, the number of an object to
        the address it goes to): {number: bytearray}, with the fixups done. `selector_of(number)` gives the selector
        of an object, for the fixups that write one; without it such a fixup is a LeError."""
        images = {obj.number: bytearray(self.object_bytes(obj.number)) for obj in self.objects}
        owner = {}  # the page -> the object it is in, and where in it the page starts
        for obj in self.objects:
            for index in range(obj.page_count):
                owner[obj.first_page - 1 + index] = (obj, index * self.page_size)
        for fixup in self.fixups():
            if fixup.page not in owner:
                raise LeError(f"a fixup is in the page {fixup.page + 1}, which is in no object")
            obj, page_start = owner[fixup.page]
            where = page_start + fixup.offset
            target = bases[fixup.target_object] + fixup.target_offset + fixup.additive
            image = images[obj.number]
            self._write(image, where, fixup, target, bases[obj.number] + where, selector_of)
        return images

    @staticmethod
    def _write(image, where, fixup, target, linear, selector_of):
        kind = fixup.source
        if kind == SOURCE_OFFSET_32:
            value, size = target, 4
        elif kind == SOURCE_RELATIVE_32:
            value, size = target - (linear + 4), 4
        elif kind == SOURCE_OFFSET_16:
            value, size = target, 2
        elif kind == SOURCE_BYTE:
            value, size = target, 1
        elif kind in (SOURCE_SELECTOR, SOURCE_POINTER_16_16, SOURCE_POINTER_16_32):
            if selector_of is None:
                raise LeError("a fixup writes a selector, and the loader gave no way to know one")
            selector = selector_of(fixup.target_object)
            if kind == SOURCE_SELECTOR:
                value, size = selector, 2
            elif kind == SOURCE_POINTER_16_16:
                value, size = (selector << 16 | target & 0xFFFF), 4
            else:
                value, size = (selector << 32 | target & 0xFFFFFFFF), 6
        else:
            raise LeError(f"a fixup of the kind {kind} is not done")
        if where < 0 or where + size > len(image):  # a place that begins before the object or ends after it
            raise LeError("a fixup goes beyond the object it is in")
        image[where:where + size] = (value & ((1 << (8 * size)) - 1)).to_bytes(size, "little")


def locate(data):
    """Where the LE program of a file is, as the offset of its DOS stub (see LeImage), or None if there is none.

    A plain LE program has its stub at the start of the file. One bound to DOS/4G has the extender first: the DOS/16M
    stub of the file is followed by the kernel (`BW`), and the program is the first MZ stub after them whose e_lfanew
    leads to an `LE`.
    """
    if data[:2] not in (b"MZ", b"ZM") or len(data) < 0x40:
        return None
    if _leads_to_le(data, 0):
        return 0
    stub = struct.unpack_from("<H", data, 2)[0], struct.unpack_from("<H", data, 4)[0]
    end = (stub[1] - 1) * 512 + stub[0] if stub[0] else stub[1] * 512
    if data[end:end + 2] != KERNEL_SIGNATURE:
        return None
    start = data.find(b"MZ", end)
    while start >= 0:
        if _leads_to_le(data, start):
            return start
        start = data.find(b"MZ", start + 1)
    return None


def _leads_to_le(data, start):
    if start + 0x40 > len(data):
        return False
    at = start + struct.unpack_from("<I", data, start + 0x3C)[0]
    return data[at:at + 4] == LE_SIGNATURE


def is_bound(data):
    """Is this file a program that DOS/4G is bound to: the extender first, and the LE program after it?"""
    start = locate(data)
    return start is not None and start > 0


def is_le_program(data):
    """Is there an LE program in this file, bound to the extender or not? DOS cannot run it without one."""
    return locate(data) is not None
