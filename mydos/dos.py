import sys

from . import flags
from .cpu import CPU, Exit

DOS_VERSION = (7, 1)  # (major, minor) reported by INT 21h AH=30h
OEM_MICROSOFT = 0xFF
CONSOLE_INFO = 0x80D3  # IOCTL answer for the console: a character device, standard input and output
STANDARD_HANDLES = (0, 1, 2)  # standard input, standard output, standard error: all three are the console
DEFAULT_DRIVE = 2  # the current drive for INT 21h AH=19h: 0 is A:, 1 is B:, 2 is C: (as the environment says)
MEMORY_TOP_SEG = 0xA000  # the end of the 640 KB of ordinary memory; DOS hands out nothing above it


def int21(cpu):
    """INT 21h: DOS services, selected by AH. Only 09h, 19h, 25h, 30h, 35h, 40h, 44h, 4Ah and 4Ch so far."""
    ah = cpu.get8(4)
    if ah == 0x09:  # print $-terminated string at DS:DX
        off = cpu.r[CPU.DX]
        out = bytearray()
        while (b := cpu.rb(cpu.s[CPU.DS], off)) != 0x24:
            out.append(b)
            off = (off + 1) & 0xFFFF
        sys.stdout.write(out.decode("cp437"))
        sys.stdout.flush()
    elif ah == 0x19:  # get the current drive into AL: 0 for A:, 1 for B:, 2 for C:, ...
        cpu.set8(0, DEFAULT_DRIVE)
    elif ah == 0x25:  # set interrupt vector AL to DS:DX
        # The vector table is real memory at 0000:0000, 4 bytes per interrupt: offset, segment.
        # Nothing jumps through it yet: INT still calls the Python handlers.
        n = cpu.get8(0)
        cpu.ww(0, n * 4, cpu.r[CPU.DX])
        cpu.ww(0, n * 4 + 2, cpu.s[CPU.DS])
    elif ah == 0x35:  # get interrupt vector AL into ES:BX
        n = cpu.get8(0)
        cpu.r[CPU.BX] = cpu.rw(0, n * 4)
        cpu.s[CPU.ES] = cpu.rw(0, n * 4 + 2)
    elif ah == 0x30:  # get DOS version: AL=major, AH=minor, BH=OEM, BL:CX=serial number
        cpu.set8(0, DOS_VERSION[0])
        cpu.set8(4, DOS_VERSION[1])
        cpu.set8(7, OEM_MICROSOFT)
        cpu.set8(3, 0)
        cpu.r[CPU.CX] = 0
    elif ah == 0x40:  # write CX bytes from DS:DX to the file handle in BX; AX = how many were written
        handle, count = cpu.r[CPU.BX], cpu.r[CPU.CX]
        if handle in STANDARD_HANDLES:  # the console; error output goes to our own error output
            data = bytes(cpu.rb(cpu.s[CPU.DS], (cpu.r[CPU.DX] + i) & 0xFFFF) for i in range(count))
            stream = sys.stderr if handle == 2 else sys.stdout
            stream.write(data.decode("cp437"))
            stream.flush()
            cpu.r[CPU.AX] = count
            cpu.set_flag(flags.CF, False)
        else:  # error 06: invalid handle (nothing else is open yet)
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 6
    elif ah == 0x44:  # IOCTL: device control through a file handle in BX, subfunction in AL
        al = cpu.get8(0)
        if al != 0x00:
            raise NotImplementedError(f"INT 21h AH=44 AL={al:02X}")
        # Subfunction 0: get device information into DX. C libraries use it to ask "is this handle a
        # terminal or a file?", which decides whether output is buffered. Bit 7 of DX set means a device.
        if cpu.r[CPU.BX] in STANDARD_HANDLES:
            cpu.r[CPU.DX] = CONSOLE_INFO
            cpu.set_flag(flags.CF, False)
        else:  # error 06: invalid handle (nothing else is open yet)
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 6
    elif ah == 0x4A:  # resize the memory block that starts at segment ES to BX paragraphs
        # Naive: we keep no list of blocks, only the top of memory. A block can grow or shrink as
        # long as it ends below that top. (DOS error 09, "not a block", cannot be told apart yet.)
        available = max(MEMORY_TOP_SEG - cpu.s[CPU.ES], 0)
        if cpu.r[CPU.BX] <= available:
            cpu.set_flag(flags.CF, False)
        else:  # error 08: not enough memory; BX says how much there is
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 8
            cpu.r[CPU.BX] = available
    elif ah == 0x4C:  # exit with code in AL
        raise Exit(cpu.get8(0))
    else:
        raise NotImplementedError(f"INT 21h AH={ah:02X}")


def install(cpu):
    """Hook DOS interrupt handlers into the CPU."""
    cpu.int_handlers[0x21] = int21
