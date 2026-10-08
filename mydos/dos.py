import sys

from .cpu import CPU, Exit

DOS_VERSION = (7, 1)  # (major, minor) reported by INT 21h AH=30h
OEM_MICROSOFT = 0xFF


def int21(cpu):
    """INT 21h: DOS services, selected by AH. Only 09h, 25h, 30h, 35h and 4Ch so far."""
    ah = cpu.get8(4)
    if ah == 0x09:  # print $-terminated string at DS:DX
        off = cpu.r[CPU.DX]
        out = bytearray()
        while (b := cpu.rb(cpu.s[CPU.DS], off)) != 0x24:
            out.append(b)
            off = (off + 1) & 0xFFFF
        sys.stdout.write(out.decode("cp437"))
        sys.stdout.flush()
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
    elif ah == 0x4C:  # exit with code in AL
        raise Exit(cpu.get8(0))
    else:
        raise NotImplementedError(f"INT 21h AH={ah:02X}")


def install(cpu):
    """Hook DOS interrupt handlers into the CPU."""
    cpu.int_handlers[0x21] = int21
