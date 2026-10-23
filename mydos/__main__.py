import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from . import bios, config, disasm, dos, dump, mouse, psp, screen, timer, vga
from .cpu import CPU
from .display import PngFile
from .window import TkWindow, WindowError
from .filesystem import FsError
from .psp import ENV_SEG, PSP_SEG
from .volumes import Volumes

TICK_EVERY = 20_000  # the displays are looked after this often, in instructions: some 20 times a second


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


def load(cpu, data, dos_path):
    """Pick the loader by file content, like DOS: MZ/ZM signature means EXE, else COM.

    `data` is the program file and `dos_path` its full DOS name (`C:\\SETUP\\ESETUP.EXE`), which goes to the program
    in its environment.
    """
    psp.build(cpu, PSP_SEG, ENV_SEG, dos.MEMORY_TOP_SEG, dos_path)
    if data[:2] in (b"MZ", b"ZM"):
        load_exe(cpu, data)
    else:
        load_com(cpu, data)


def make_trace(log):
    """Return a CPU trace hook that writes one line per instruction to the open file `log`.

    A line looks like `1773:0880  8B C3        mov ax, bx`: address, the instruction bytes, the text.
    """
    def trace(cpu):
        seg, off = cpu.s[CPU.CS], cpu.ip
        length, text = disasm.disasm(cpu, seg, off)
        raw = bytes(cpu.rb(seg, off + i) for i in range(length)).hex(" ").upper()
        log.write(f"{seg:04X}:{off:04X}  {raw:<20}  {text}\n")
    return trace


def main():
    """Run the program named on the command line; exit with the program's exit code."""
    parser = argparse.ArgumentParser(prog="python -m mydos", description="Run a DOS .COM or .EXE program.")
    parser.add_argument("-c", metavar="FILE", help="the configuration (TOML): drives, mounts, starting directory; "
                        "without it the current directory is the hard disk C:")
    parser.add_argument("-d", metavar="LOGFILE", help="write every executed instruction to LOGFILE")
    parser.add_argument("-s", metavar="PNGFILE", help="show the screen in this PNG file: written again whenever the "
                        "picture has changed, and at the end, also after a crash")
    parser.add_argument("-w", action="store_true", help="show the screen in a window; the keys typed in it go to "
                        "the program. Closing the window stops the program")
    parser.add_argument("program", help="the .COM or .EXE file to run, named as DOS names it: `ESETUP.EXE`, "
                        "`C:\\SETUP\\ESETUP.EXE`, `setup/esetup.exe`; relative to the current directory of the "
                        "starting drive (the `cwd` of the configuration)")
    args = parser.parse_args()
    try:
        machine = config.load(args.c) if args.c else config.default(Path.cwd())
    except config.ConfigError as error:
        parser.error(str(error))  # prints the message with the usage and exits with status 2, before anything runs

    volumes = Volumes(machine)
    try:
        where = volumes.resolve(args.program)
        with where.filesystem.open(where.parts) as program:
            data = program.read()
    except FsError as error:
        # not found, not a file, or on a drive that is not there or has nothing in it
        parser.error(f"cannot run {args.program}: {error} (the program is named as DOS names a file, from the "
                     "starting directory; to run one that is elsewhere on the host, mount its directory with -c)")
    # the dump of a crash goes beside the program on the host, when the program is there
    host_path = args.program
    if hasattr(where.filesystem, "host_path"):
        host_path = str(where.filesystem.host_path(where.parts))

    cpu = CPU()
    keyboard = bios.install(cpu, machine)
    dos.install(cpu, volumes)
    timer.install(cpu)
    card = vga.install(cpu)
    pointer = mouse.install(cpu)
    displays = []
    if args.s:
        displays.append(PngFile(args.s))
    if args.w:
        try:
            displays.append(TkWindow(keyboard.press, pointer.update))
        except WindowError as error:
            parser.error(f"cannot open a window: {error}")
    monitor = screen.Monitor(card, displays) if displays else None
    if args.w:  # only a window has keys to give, so only with one can a program wait for a key
        keyboard.waiter = lambda: monitor.wait_for_key(keyboard)
    load(cpu, data, where.dos_path())
    log = open(args.d, "w") if args.d else None
    if log:
        cpu.trace = make_trace(log)
    try:
        code = cpu.run(TICK_EVERY, monitor.tick) if monitor else cpu.run()
    except Exception as e:
        dump.write(cpu, host_path, e)  # leave something to debug with, then fail as usual
        raise
    finally:
        if monitor:
            monitor.show()  # the last picture, whether the program ended or crashed
            monitor.close()
        if log:
            log.close()
    sys.exit(code)


main()
