import sys

from . import dos
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


def load_exe(cpu, data):
    """Load an MZ .EXE image (not implemented yet)."""
    raise NotImplementedError("EXE loader")


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
    sys.exit(cpu.run())


main()
