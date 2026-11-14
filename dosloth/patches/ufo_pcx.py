"""The decoding of a line of a PCX picture in UFO2P.EXE (the game): the procedure at 108EB0, which reads the line by
fgetc a byte at a time and undoes the runs of the RLE of PCX.

`pcx_decode_line(line=EAX, file=EDX, count=EBX)`: a byte that has not both top bits set (C0h) is a pixel; one that has
is a run: its low six bits are how many times the next byte is put. It goes on while fewer than `count` bytes are
done (unsigned), and it does one at least; a run may go past `count`, as the code lets it. It gives back EAX = EDX = the
bytes done; EBX is the last byte of a pixel, or FFFFFFFFh after a run; ECX, ESI, EDI and EBP are as they were.

The bytes come from the buffer of the FILE, as the usual way of fgetc takes them (see ufo_stdio.py). When the buffer
has not the bytes of the next pixel or run (the next fgetc would read the file through DOS), the patch stops at the
top of the loop of the code (108EBF), with the registers, the flags and the stack the code has there, and the code
does the rest: its fgetc fills the buffer again.
"""
from ..cpu import CPU
from ._base import Patch
from ._common import DWORD, MASK, give_back
from .ufo_stdio import BINARY, FGETC_AT, READING, USUAL_WAY, PATCHES as FGETC_PATCHES

DECODE_AT = 0x108EB0
LOOP_AT = 0x108EBF  # the top of the loop: `mov eax, edi; call fgetc`
PROLOGUE = 9  # push ecx, push esi, push edi, push ebp, sub esp, mov, mov, mov, xor edx
PIXEL = 2 + USUAL_WAY + 6 + 4 + 2  # mov, call, fgetc; mov, and, and, mov, cmp, jne; mov, mov, inc, mov; cmp, jb
RUN = 2 + USUAL_WAY + 6 + 2 + USUAL_WAY + 3 + 3 + 2  # ...; mov, call, fgetc; mov, and, mov; the last dec, cmp, je; cmp, jb
RUN_BYTE = 7  # dec, cmp, je, mov, inc, mov, jmp: for each byte of a run
EPILOGUE = 7  # mov eax, edx; add esp, 4; 4 pops; ret
FGETC = FGETC_PATCHES[0]


def pcx_decode_line(cpu):
    mem, base = cpu.mem, cpu.bases[cpu.s[CPU.DS]]
    line, stream, count = cpu.wide(CPU.AX), cpu.wide(CPU.DX), cpu.wide(CPU.BX)
    start_pointer = pointer = DWORD.unpack_from(mem, base + stream)[0]
    left = DWORD.unpack_from(mem, base + stream + 4)[0]
    usual = mem[base + stream + 0x0C] & (READING | BINARY) == READING | BINARY and not left & 0x80000000
    executed, done, ebx = PROLOGUE, 0, None
    while True:
        if not usual or left < 1 or (mem[base + pointer] & 0xC0 == 0xC0 and left < 2):
            put_file(cpu, base, stream, start_pointer, pointer, left)
            return to_the_loop(cpu, line, stream, count, executed, done)
        byte = mem[base + pointer]
        if byte & 0xC0 != 0xC0:  # a pixel
            mem[base + (line + done & MASK)] = byte
            ebx = byte
            done = done + 1 & MASK
            pointer, left = pointer + 1, left - 1
            executed += PIXEL
        else:  # a run: the next byte, as many times as the low six bits say
            value, times = mem[base + pointer + 1], byte & 0x3F
            start = base + (line + done & MASK)
            mem[start:start + times] = bytes([value]) * times
            ebx = 0xFFFFFFFF  # (the count, gone down past 0)
            done = done + times & MASK
            pointer, left = pointer + 2, left - 2
            executed += RUN + RUN_BYTE * times
        if done >= count:
            break
    put_file(cpu, base, stream, start_pointer, pointer, left)
    frame = cpu.wide(CPU.SP) - 20 & MASK  # ESP in the body: 4 pushes and the local of 4 bytes below the return
    cpu.add_with_flags(frame, 4, 32)  # the flags of `add esp, 4`, the last of the epilogue that sets them
    cpu.set32(CPU.AX, done)
    cpu.set32(CPU.DX, done)
    cpu.set32(CPU.BX, ebx)
    cpu.instructions += executed + EPILOGUE - 1  # (the INT stands for the first one)
    give_back(cpu)


def put_file(cpu, base, stream, start_pointer, pointer, left):
    """The pointer and the count of the FILE as the calls of fgetc have left them, if they took any byte."""
    if pointer != start_pointer:
        DWORD.pack_into(cpu.mem, base + stream, pointer & MASK)
        DWORD.pack_into(cpu.mem, base + stream + 4, left)


def to_the_loop(cpu, line, stream, count, executed, done):
    """Leave the rest to the code, at the top of its loop: the prologue's pushes and the registers that the loop keeps
    (ESI the line, EDI the FILE, EBP the count, EDX the bytes done). EAX, EBX, CL, the local and the flags are not
    made as the code would have them there: the loop sets each of them before it looks at it, and ECX is popped."""
    for register in (CPU.CX, CPU.SI, CPU.DI, CPU.BP):
        cpu.push32(cpu.wide(register))
    cpu.set32(CPU.SP, cpu.wide(CPU.SP) - 4 & MASK)  # sub esp, 4: the local
    cpu.set32(CPU.SI, line)
    cpu.set32(CPU.DI, stream)
    cpu.set32(CPU.BP, count)
    cpu.set32(CPU.DX, done)
    cpu.instructions += executed - 1
    cpu.ip = LOOP_AT


PATCHES = [Patch("the decoding of a line of PCX of UFO2P.EXE", DECODE_AT, 0x62,
                 "a44d72bda093f12ede2e76e2258cc2252ef65cf18812d9486107cae4073797fd", pcx_decode_line,
                 also=((FGETC_AT, FGETC.length, FGETC.digest),))]
