"""fgetc of the C library in UFO2P.EXE (the game): the byte from the buffer of a file opened in binary mode.

The title picture (ufodata/titles.pcx) is decoded a byte at a time, each by a call of fgetc: nearly all the
instructions of the start of the game are those calls. A FILE of Watcom C has the pointer to the next byte of its
buffer at +0, the count of the bytes that are left in it at +4, and its flags at +0Ch (01h: open for reading, 40h:
binary, 20h: an error, 10h: the end of the file).

The patch does the usual way: a file open for reading in binary mode with a byte left in the buffer. Any other way
(the buffer is empty and the next piece of the file is read through DOS, the text mode with its CR and Ctrl-Z, a file
not open for reading) is left to the code: the patch does the two instructions that the INT stands in place of (push
ebx, push ecx) and goes on at the third."""

from .. import flags
from ..cpu import CPU
from ._base import Patch
from ._common import ARITHMETIC, DWORD, MASK, give_back

FGETC_AT = 0x1B1238
AFTER_THE_PUSHES = 0x1B123A  # the third instruction: the code goes on here for every way but the usual one
READING, BINARY = 0x01, 0x40  # the flags of a FILE at +0Ch
USUAL_WAY = 24  # 4 pushes, mov, test, jne; mov, dec, mov, test, jge; mov, mov, inc, and, mov; test, jne; 4 pops, ret


def fgetc(cpu):
    mem, base = cpu.mem, cpu.bases[cpu.s[CPU.DS]]
    stream = cpu.wide(CPU.AX)
    pointer, count = DWORD.unpack_from(mem, base + stream)[0], DWORD.unpack_from(mem, base + stream + 4)[0]
    file_flags = mem[base + stream + 0x0C]
    if file_flags & (READING | BINARY) != READING | BINARY or not 1 <= count < 0x80000000:
        cpu.push32(cpu.wide(CPU.BX))  # (the INT stood for this one)
        cpu.push32(cpu.wide(CPU.CX))
        cpu.instructions += 1
        cpu.ip = AFTER_THE_PUSHES
        return
    DWORD.pack_into(mem, base + stream + 4, count - 1)
    DWORD.pack_into(mem, base + stream, pointer + 1 & MASK)
    cpu.set32(CPU.AX, mem[base + pointer])
    # the flags of `test byte [edx+0Ch], 40h` (not zero), with AF of the `inc` of the pointer, which TEST leaves alone
    carry = flags.AF if pointer & 0xF == 0xF else 0
    cpu.flags = cpu.flags & ~ARITHMETIC | carry
    cpu.instructions += USUAL_WAY - 1  # (the INT stands for the first one)
    give_back(cpu)


PATCHES = [Patch("fgetc of UFO2P.EXE", FGETC_AT, 0x88,  # (up to the call of _filbuf at 1B12C1)
                 "8a4988c1292a89b91351de0af4fccfd8671fd6ae6b43bd98bfcebbab20a28c6c", fgetc)]
