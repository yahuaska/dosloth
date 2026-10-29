"""Putting a program into memory: the .COM, .EXE and LE loaders, the same for the first program and for one started by
EXEC.

A .COM or a .EXE goes right after its PSP (at `psp_seg:0100`, in the segment `psp_seg + 10h` for a .EXE); the PSP
itself is not written here (see psp.py). The registers are set the way DOS leaves them at the start of a program. An LE
program (a DOS/4G one) goes to the memory above 1 MB, and starts in the flat mode.
"""
import struct
from dataclasses import dataclass

from . import le
from .cpu import CPU
from .flat import REAL_MODE, Bases

PSP_PARAGRAPHS = 0x10  # the PSP is 256 bytes
COM_PARAGRAPHS = 0x1000  # a .COM program wants a whole 64 KB segment, the PSP included
MAX_PARAGRAPHS = 0xFFFF  # the most a block can be asked for
# the selectors of an LE program: its code and its data (both with base 0, so an offset is an address). The PSP and
# the environment are named by their own segment numbers, which is what the PSP says to the program already (PSP:002C)
CODE_SELECTOR = 0x0F
DATA_SELECTOR = 0x17


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


def memory_needed(data):
    """How many paragraphs a program wants, as (the least it can run in, the most it asks for), the PSP counted.

    A .COM takes what there is, 64 KB at the least. A .EXE has its image and the `minalloc` and `maxalloc` of its header
    on top of that. An LE program wants its PSP and what is left of the memory (the program itself goes above 1 MB,
    see load_le). Raises ValueError for a .EXE whose header is not all there, and a le.LeError (a ValueError too) for
    an LE program that cannot be loaded.
    """
    start = le.locate(data)
    if start is not None:
        le.LeImage(data, start)  # to see that it can be read at all
        return PSP_PARAGRAPHS, MAX_PARAGRAPHS
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


def load_le(cpu, data, psp_seg, extended):
    """Load the LE program in `data` (bound to the extender or not) the way DOS/4G does, and start it in the flat mode.

    Each object goes to a block of `extended` (see xmem.py), which the program owns, and the fixups are made for where
    they went. The code has the selector CODE_SELECTOR and the data DATA_SELECTOR, and both have the base 0:
    what the linker wrote as an offset in an object is the address. ES is the PSP, whose selector is its segment
    (so is the one of the environment, that PSP:002C holds), SS and DS are the data and FS and GS are 0. The program
    starts at its entry, with its stack where the header says, and all the registers but ESP are 0.

    Raises a le.LeError for a program that cannot be loaded, and a MemoryError if there is not room for it in
    `extended`; both leave `extended` and the machine as they were.
    """
    start = le.locate(data)
    if start is None:
        raise le.LeError("this file has no LE program in it")
    image = le.LeImage(data, start)
    bases = {}
    try:
        for obj in image.objects:
            bases[obj.number] = extended.allocate(obj.size, psp_seg)
            if bases[obj.number] is None:
                raise MemoryError(f"no room for the object {obj.number} of {obj.size:X}h bytes in the extended memory")
        code = {obj.number for obj in image.objects if obj.executable}
        loaded = image.load(bases, lambda number: CODE_SELECTOR if number in code else DATA_SELECTOR)
        entry = bases[image.entry_object] + image.entry_offset
        esp = bases[image.stack_object] + image.stack_offset
    except (le.LeError, MemoryError):
        extended.free_owned(psp_seg)
        raise
    for number, bytes_ in loaded.items():
        pages = (len(bytes_) + 4095) // 4096 * 4096
        cpu.mem[bases[number]:bases[number] + pages] = bytes_.ljust(pages, b"\0")
    env_seg = int.from_bytes(cpu.mem[psp_seg * 16 + 0x2C:psp_seg * 16 + 0x2E], "little")
    cpu.enter_flat(Bases({CODE_SELECTOR: 0, DATA_SELECTOR: 0, psp_seg: psp_seg * 16, env_seg: env_seg * 16}))
    cpu.r[:] = [0] * 8
    cpu.rh[:] = [0] * 8
    cpu.s[:] = [0] * 6
    cpu.s[CPU.CS], cpu.s[CPU.SS], cpu.s[CPU.DS], cpu.s[CPU.ES] = CODE_SELECTOR, DATA_SELECTOR, DATA_SELECTOR, psp_seg
    cpu.set32(CPU.SP, esp)
    cpu.ip = entry


def load(cpu, data, psp_seg, extended=None):
    """Pick the loader by file content, like DOS: an LE program (see load_le, it needs the `extended` memory), else
    MZ/ZM signature means EXE, else COM."""
    if le.locate(data) is not None:
        if extended is None:
            raise ValueError("an LE program is loaded into the extended memory, and none was given")
        load_le(cpu, data, psp_seg, extended)
        return
    cpu.set_mode(REAL_MODE)  # a program of real mode runs in it, whatever the one that started it did
    cpu.rh[:] = [0] * 8  # the high halves of the 32-bit registers: a program starts with nothing in them
    cpu.s[CPU.FS] = cpu.s[CPU.GS] = 0  # nor in FS and GS
    if is_exe(data):
        load_exe(cpu, data, psp_seg)
    else:
        load_com(cpu, data, psp_seg)
