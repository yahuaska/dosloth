"""Putting a program into memory: the .COM and .EXE loaders, the same for the first program and for one started by EXEC.

The program goes right after its PSP (at `psp_seg:0100`, in the segment `psp_seg + 10h` for a .EXE); the PSP itself is
not written here (see psp.py). The registers are set the way DOS leaves them at the start of a program.
"""
import struct
from dataclasses import dataclass

from . import le
from .cpu import CPU

PSP_PARAGRAPHS = 0x10  # the PSP is 256 bytes
COM_PARAGRAPHS = 0x1000  # a .COM program wants a whole 64 KB segment, the PSP included
MAX_PARAGRAPHS = 0xFFFF  # the most a block can be asked for


@dataclass
class MZHeader:
    """The 28-byte header at the start of a DOS .EXE. All fields are little-endian words."""
    magic: bytes     # 00: b"MZ" (old files sometimes b"ZM")
    cblp: int        # 02: bytes used in the last 512-byte page (0 = the page is full)
    cp: int          # 04: number of 512-byte pages in the file, header included
    crlc: int        # 06: number of entries in the relocation table
    cparhdr: int     # 08: header size in 16-byte paragraphs
    minalloc: int    # 0A: minimum extra paragraphs needed above the image
    maxalloc: int    # 0C: maximum extra paragraphs wanted (FFFF = "give me all")
    ss: int          # 0E: initial SS, relative to the load segment
    sp: int          # 10: initial SP
    csum: int        # 12: checksum, practically always ignored
    ip: int          # 14: initial IP
    cs: int          # 16: initial CS, relative to the load segment
    lfarlc: int      # 18: file offset of the relocation table
    ovno: int        # 1A: overlay number (0 = main program)

    @classmethod
    def parse(cls, data):
        return cls(*struct.unpack_from("<2s13H", data))

    @property
    def file_size(self):
        """Size in bytes of header + image, as declared by cp/cblp."""
        return (self.cp - 1) * 512 + self.cblp if self.cblp else self.cp * 512

    @property
    def header_size(self):
        """Size in bytes of the header; the image starts right after it."""
        return self.cparhdr * 16


def is_exe(data):
    """DOS tells a .EXE from a .COM by the first two bytes of the file, not by its name."""
    return data[:2] in (b"MZ", b"ZM")


def _image_paragraphs(data, header):
    return (len(data[header.header_size:header.file_size]) + 15) // 16


def refuse_le_program(data):
    """A program in the LE format, bound to the extender of DOS/4G or not, is for a 32-bit mode that is not done yet
    (see PLAN.md). If `data` is one, stop with a message that says what it is, and does not let it run its DOS
    stub: that would be the extender, which says it needs a 386."""
    start = le.locate(data)
    if start is None:
        return
    try:
        image = le.LeImage(data, start)
        what = (f"{len(image.objects)} objects, {image.page_count} pages, entry in the object {image.entry_object} "
                f"at {image.entry_offset:#x}")
    except le.LeError as error:
        what = f"one that cannot be loaded: {error}"
    kind = "bound to the extender of DOS/4G" if start else "plain"
    raise NotImplementedError(f"this is an LE program ({kind}; {what}): the 32-bit mode is not done yet")


def memory_needed(data):
    """How many paragraphs a program wants, as (the least it can run in, the most it asks for), the PSP counted.

    A .COM takes what there is, 64 KB at the least. A .EXE has its image and the `minalloc` and `maxalloc` of its header
    on top of that. Raises ValueError for a .EXE whose header is not all there, and NotImplementedError for an LE
    program (see refuse_le_program).
    """
    refuse_le_program(data)
    if not is_exe(data):
        return COM_PARAGRAPHS, MAX_PARAGRAPHS
    if len(data) < struct.calcsize("<2s13H"):
        raise ValueError("the header of the .EXE is cut short")
    header = MZHeader.parse(data)
    base = PSP_PARAGRAPHS + _image_paragraphs(data, header)
    return base + header.minalloc, min(base + max(header.maxalloc, header.minalloc), MAX_PARAGRAPHS)


def load_com(cpu, data, psp_seg):
    """Load a .COM image at psp_seg:0100 and set up registers the way DOS would."""
    base = psp_seg * 16 + 0x100
    cpu.mem[base:base + len(data)] = data
    for i in (CPU.ES, CPU.CS, CPU.SS, CPU.DS):
        cpu.s[i] = psp_seg
    cpu.r[CPU.SP] = 0xFFFE
    cpu.ip = 0x100


def load_exe(cpu, data, psp_seg):
    """Load an MZ .EXE: copy the image after the PSP, apply relocations, set CS:IP/SS:SP."""
    h = MZHeader.parse(data)
    image = data[h.header_size:h.file_size]
    start_seg = psp_seg + PSP_PARAGRAPHS  # the image goes right after the 256-byte PSP
    base = start_seg * 16
    cpu.mem[base:base + len(image)] = image
    for i in range(h.crlc):
        # each entry is off:seg (image-relative) of a word holding a segment value to fix up
        off, seg = struct.unpack_from("<2H", data, h.lfarlc + i * 4)
        a = base + seg * 16 + off
        word = cpu.mem[a] | cpu.mem[a + 1] << 8
        word = (word + start_seg) & 0xFFFF
        cpu.mem[a] = word & 0xFF
        cpu.mem[a + 1] = word >> 8
    cpu.s[CPU.ES] = cpu.s[CPU.DS] = psp_seg
    cpu.s[CPU.CS] = (start_seg + h.cs) & 0xFFFF
    cpu.s[CPU.SS] = (start_seg + h.ss) & 0xFFFF
    cpu.r[CPU.SP] = h.sp
    cpu.ip = h.ip


def load(cpu, data, psp_seg):
    """Pick the loader by file content, like DOS: MZ/ZM signature means EXE, else COM. An LE program is refused."""
    refuse_le_program(data)
    cpu.rh[:] = [0] * 8  # the high halves of the 32-bit registers: a program starts with nothing in them
    cpu.s[CPU.FS] = cpu.s[CPU.GS] = 0  # nor in FS and GS
    if is_exe(data):
        load_exe(cpu, data, psp_seg)
    else:
        load_com(cpu, data, psp_seg)
