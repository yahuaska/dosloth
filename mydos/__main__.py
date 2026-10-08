import sys

from . import dos
from .cpu import CPU

PSP_SEG = 0x1000


def load_com(cpu, path):
    """Load a .COM file at PSP_SEG:0100 and set up registers the way DOS would."""
    data = open(path, "rb").read()
    base = PSP_SEG * 16 + 0x100
    cpu.mem[base:base + len(data)] = data
    for i in (CPU.ES, CPU.CS, CPU.SS, CPU.DS):
        cpu.s[i] = PSP_SEG
    cpu.r[CPU.SP] = 0xFFFE
    cpu.ip = 0x100


def main():
    """Run the .COM file given as argv[1]; exit with the program's exit code."""
    cpu = CPU()
    dos.install(cpu)
    load_com(cpu, sys.argv[1])
    sys.exit(cpu.run())


main()
