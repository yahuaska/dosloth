"""Makes LE programs for the tests: the header, the tables, the pages and the fixups, laid out as Watcom does."""
import struct

PAGE = 4096
RX = 0x1 | 0x4 | 0x40 | 0x2000  # readable, executable, preload, 32-bit
RW = 0x1 | 0x2 | 0x40 | 0x2000
HEADER = 0xC4  # the size of the header and what comes right after it: the table of objects


class Obj:
    """An object to put in the program: `data` is what the file has of it, `size` its virtual size (by default the
    size of the data)."""

    def __init__(self, data=b"", size=None, base=0x10000, flags=RX):
        self.data, self.base, self.flags = data, base, flags
        self.size = len(data) if size is None else size


class F:
    """A fixup. Where: in object `obj` at `at` from its start, or straight in the page `page` at `offset` (which can be
    negative: a place that begins in the page before). What: `kind` is the source type (7 = a 32-bit offset), and
    it points at `target_offset` in the object `target`."""

    def __init__(self, kind, target, target_offset=0, obj=None, at=None, page=None, offset=None, additive=None,
                 wide=True, big_object=False, wide_additive=True, sources=None, source_bits=0, flag_bits=0):
        self.kind, self.target, self.target_offset = kind, target, target_offset
        self.obj, self.at, self.page, self.offset = obj, at, page, offset
        self.additive, self.wide, self.big_object, self.wide_additive = additive, wide, big_object, wide_additive
        self.sources = sources  # a list of offsets in the page: one record that has many places
        self.source_bits, self.flag_bits = source_bits, flag_bits  # to set bits that mean something we do not do


def mz_stub(size, lfanew):
    """The DOS stub of a program: the MZ header of 64 bytes, with e_lfanew, and zeros up to `size` bytes."""
    stub = bytearray(size)
    stub[0:2] = b"MZ"
    stub[2:4] = (size % 512).to_bytes(2, "little")
    stub[4:6] = ((size + 511) // 512).to_bytes(2, "little")
    stub[8:10] = (4).to_bytes(2, "little")  # the header is 4 paragraphs
    stub[0x3C:0x40] = lfanew.to_bytes(4, "little")
    return bytes(stub)


def fixup_record(f):
    """The bytes of one fixup record."""
    flags = 0x10 if f.wide else 0
    if f.big_object:
        flags |= 0x40
    if f.additive is not None:
        flags |= 0x04 | (0x20 if f.wide_additive else 0)
    flags |= f.flag_bits
    record = bytearray([f.kind | (0x20 if f.sources is not None else 0) | f.source_bits, flags])
    if f.sources is not None:
        record.append(len(f.sources))
    else:
        record += struct.pack("<h", f.offset)
    record += struct.pack("<H", f.target) if f.big_object else bytes([f.target])
    if f.kind != 2:
        record += struct.pack("<I" if f.wide else "<H", f.target_offset)
    if f.additive is not None:
        record += struct.pack("<I" if f.wide_additive else "<H", f.additive)
    if f.sources is not None:
        for source in f.sources:
            record += struct.pack("<h", source)
    return bytes(record)


def build_program(objects, entry=(1, 0), stack=(2, 0), fixups=(), cpu=2, page_kinds=None, last_page_size=None,
                  import_modules=0, byte_order=0, lfanew=0x80, stub_size=0x80, page_size=PAGE):
    """A plain LE program: an MZ stub, the header and the tables, and the pages. `page_kinds` is {page (from 0): the
    kind in the page map}; `last_page_size` cuts the last page of the file to that many bytes."""
    counts = [(obj.size + page_size - 1) // page_size for obj in objects]
    pages = sum(counts)
    first, pages_so_far = [], 1
    for count in counts:
        first.append(pages_so_far)
        pages_so_far += count
    object_table = b"".join(struct.pack("<6I", obj.size, obj.base, obj.flags, first[i], counts[i], 0)
                            for i, obj in enumerate(objects))
    page_map = b"".join(bytes([(i + 1) >> 16, ((i + 1) >> 8) & 0xFF, (i + 1) & 0xFF, (page_kinds or {}).get(i, 0)])
                        for i in range(pages))
    by_page = [[] for _ in range(pages)]
    for f in fixups:
        if f.obj is not None:
            f.page, f.offset = first[f.obj - 1] - 1 + f.at // page_size, f.at % page_size
        by_page[f.page].append(f)
    records, starts = b"", []
    for page in range(pages):
        starts.append(len(records))
        records += b"".join(fixup_record(f) for f in by_page[page])
    starts.append(len(records))
    fixup_table = struct.pack(f"<{pages + 1}I", *starts)

    object_table_at = HEADER
    page_map_at = object_table_at + len(object_table)
    resident_names_at = page_map_at + len(page_map)
    entry_table_at = resident_names_at + 1
    fixup_table_at = entry_table_at + 1
    fixup_records_at = fixup_table_at + len(fixup_table)
    imports_at = fixup_records_at + len(records)
    data_pages_at = (lfanew + imports_at + 15) & ~15
    last = last_page_size if last_page_size is not None else ((objects[-1].size % page_size) or page_size)
    header = bytearray(HEADER)
    header[0:4] = b"LE" + bytes([byte_order, 0])
    fields = {0x08: (cpu, 2), 0x0A: (1, 2), 0x10: (0x200, 4), 0x14: (pages, 4), 0x18: (entry[0], 4),
              0x1C: (entry[1], 4), 0x20: (stack[0], 4), 0x24: (stack[1], 4), 0x28: (page_size, 4), 0x2C: (last, 4),
              0x30: (len(fixup_table) + len(records), 4), 0x38: (fixup_table_at - HEADER, 4),
              0x40: (object_table_at, 4), 0x44: (len(objects), 4), 0x48: (page_map_at, 4),
              0x58: (resident_names_at, 4), 0x5C: (entry_table_at, 4), 0x68: (fixup_table_at, 4),
              0x6C: (fixup_records_at, 4), 0x70: (imports_at, 4), 0x74: (import_modules, 4), 0x78: (imports_at, 4),
              0x80: (data_pages_at, 4)}  # from the start of the program, which is where its DOS stub begins
    for offset, (value, size) in fields.items():
        header[offset:offset + size] = value.to_bytes(size, "little")
    body = bytes(header) + object_table + page_map + b"\0" + b"\0" + fixup_table + records
    out = bytearray(mz_stub(stub_size, lfanew).ljust(lfanew, b"\0") + body)
    out = out.ljust(data_pages_at, b"\0")
    stored = []
    for i, obj in enumerate(objects):
        data = obj.data.ljust(counts[i] * page_size, b"\0")
        stored += [data[p * page_size:(p + 1) * page_size] for p in range(counts[i])]
    if stored:
        stored[-1] = stored[-1][:last]  # the file ends where the header says the last page does
    return bytes(out) + b"".join(stored)


def build_bound(program, extender_size=0x200, kernel_size=0x300, decoys=True):
    """The program bound to an extender, as the files of the game are: an MZ stub of `extender_size` bytes, the kernel
    (`BW` and what looks like other things: more MZ and even an LE signature, that are not the program), then the
    program."""
    stub = mz_stub(extender_size, 0x1000000)  # the e_lfanew of the stub leads nowhere
    kernel = bytearray(b"BW" + bytes(kernel_size - 2))
    if decoys:
        kernel[20:24] = b"MZ\0\0"
        kernel[40:44] = b"MZ\x90\0"
        kernel[60:64] = b"LE\0\0"
    return stub + bytes(kernel) + program
