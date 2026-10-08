import sys

from .cpu import CPU, Exit


def int21(cpu):
    ah = cpu.get8(4)
    if ah == 0x09:  # print $-terminated string at DS:DX
        off = cpu.r[CPU.DX]
        out = bytearray()
        while (b := cpu.rb(cpu.s[CPU.DS], off)) != 0x24:
            out.append(b)
            off = (off + 1) & 0xFFFF
        sys.stdout.write(out.decode("cp437"))
        sys.stdout.flush()
    elif ah == 0x4C:  # exit with code in AL
        raise Exit(cpu.get8(0))
    else:
        raise NotImplementedError(f"INT 21h AH={ah:02X}")


def install(cpu):
    cpu.int_handlers[0x21] = int21
