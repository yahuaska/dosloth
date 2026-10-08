import struct
import sys
from dataclasses import dataclass

from . import dos, dump
from .cpu import CPU

PSP_SEG = 0x1000


def load_com(cpu, data):
    """Load a .COM image at PSP_SEG:0100 and set up registers the way DOS would."""
    base = PSP_SEG * 16 + 0x100
    cpu.mem[base:base + len(data)] = data
    for i in (CPU.ES, CPU.CS, CPU.SS, CPU.DS):
        cpu.s[i] = PSP_SEG
    cpu.r[CPU.SP] = 0xFFFE
    cpu.ip = 0x100


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


def load_exe(cpu, data):
    """Load an MZ .EXE: copy the image after the PSP, apply relocations, set CS:IP/SS:SP."""
    h = MZHeader.parse(data)
    image = data[h.header_size:h.file_size]
    start_seg = PSP_SEG + 0x10  # the image goes right after the 256-byte PSP
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
    cpu.s[CPU.ES] = cpu.s[CPU.DS] = PSP_SEG
    cpu.s[CPU.CS] = (start_seg + h.cs) & 0xFFFF
    cpu.s[CPU.SS] = (start_seg + h.ss) & 0xFFFF
    cpu.r[CPU.SP] = h.sp
    cpu.ip = h.ip


def load(cpu, path):
    """Pick the loader by file content, like DOS: MZ/ZM signature means EXE, else COM."""
    data = open(path, "rb").read()
    if data[:2] in (b"MZ", b"ZM"):
        load_exe(cpu, data)
    else:
        load_com(cpu, data)


def main():
    """Run the program given as argv[1]; exit with the program's exit code."""
    cpu = CPU()
    dos.install(cpu)
    load(cpu, sys.argv[1])
    try:
        code = cpu.run()
    except Exception as e:
        dump.write(cpu, sys.argv[1], e)  # leave something to debug with, then fail as usual
        raise
    sys.exit(code)


main()
