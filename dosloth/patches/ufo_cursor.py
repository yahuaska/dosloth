"""The mouse cursor of UFO2P.EXE (the game), which the game draws itself, straight into the window of the video card:
the loops over the rows of the three procedures that the handler of the mouse calls whenever the mouse moves (and a
frame now and then): 1061E3 puts back what was under the cursor, 10632F keeps what is under its new place, 106493
draws it (its colour 0 is seen through).

Each goes over the rows of the cursor ([1CD7CE] of them, [1CD7CC] pixels each, the counts kept in the words
[ebp-8] and [ebp-6]), a pixel a time, from ESI to EDI: a pixel at x 640 or more (CX) or y 480 or more (BX), compared
with sign, is not put. DX is where the pixel is in the bank of 64 KB of the window; when it goes over the end of the
bank, in a row (DX comes to 0) or between two (at the end of a row EDX is less than [1CD7D4]: the 16-bit adds of the
rest of the line, [1CD7D0], to DI or SI and to DX went round), the code calls 1087B8 to show the next bank, which is
done through DPMI and the BIOS. The patch does the rows, and before any such call, and when the rows are done, it
gives the machine back to the code with its registers and the two counts as the code has them there:

    the row:         the INT stands for `mov ax, [001CD7CC]` at its top (the code jumps there for every row)
    in a row:        at `mov edi/esi, 000A0000`, after `inc dx; jne`
    between rows:    at `mov ax, [001C4CDE]`, after `cmp edx, [001CD7D4]; jge`
    the rows done:   at `mov ax, [ebp-4]`, after `dec word [ebp-8]; jne`

The flags are not made as the code leaves them there: each of the three goes on to the call of 1087B8, whose first
`cmp ax, [001C4CDE]` sets all of them, and nothing looks at them before it.

The window is read and written as the code does it (cpu.rb and cpu.wb through the selectors), so the bank that is
shown is the one the pixels go to.
"""
from dataclasses import dataclass

from ..cpu import CPU
from ._base import Patch
from ._common import MASK

WIDTH, HEIGHT = 0x1CD7CC, 0x1CD7CE  # words: the size of the cursor
REST = 0x1CD7D0  # word: what is added to go from the end of a row to the start of the next (640 - the width)
BANK_LIMIT = 0x1CD7D4  # dword: EDX below it at the end of a row means the bank went round
SCREEN_WIDTH, SCREEN_HEIGHT = 0x280, 0x1E0


@dataclass(frozen=True)
class Loop:
    row_at: int  # the INT: mov ax, [001CD7CC]
    wrap_at: int  # mov edi (or esi), 000A0000: the bank goes round in a row
    switch_at: int  # mov ax, [001C4CDE]: between two rows
    done_at: int  # mov ax, [ebp-4]
    transparent: bool  # the colour 0 is not put (cmp al, 0; je)
    from_video: bool  # the pixels come from the window (SI is in it), else they go to it (DI)
    skipped: int  # the instructions of a pixel that is not put, after the jump: inc edi, and what follows it


RESTORE = Loop(0x1062A8, 0x1062D2, 0x106309, 0x10631C, False, False, 3)  # inc edi; xchg ebx, ebx; nop
SAVE = Loop(0x106410, 0x106435, 0x10646D, 0x106480, False, True, 1)  # inc edi
DRAW = Loop(0x106520, 0x10654A, 0x106581, 0x106594, True, False, 2)  # inc edi; nop


def signed16(value):
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def low16(register, value):
    return register & ~0xFFFF & MASK | value & 0xFFFF


def rows(cpu, loop):
    ds, es, ss = cpu.s[CPU.DS], cpu.s[CPU.ES], cpu.s[CPU.SS]
    width, rest = cpu.rw(ds, WIDTH), cpu.rw(ds, REST)
    limit = cpu.rd(ds, BANK_LIMIT)
    limit = limit - (1 << 32) if limit & 0x80000000 else limit
    eax, ebx, ecx, edx = (cpu.wide(register) for register in (CPU.AX, CPU.BX, CPU.CX, CPU.DX))
    esi, edi, ebp = cpu.wide(CPU.SI), cpu.wide(CPU.DI), cpu.wide(CPU.BP)
    left_rows = cpu.rw(ss, ebp - 8 & MASK)
    executed = 0

    def give_back(at, columns):
        for register, value in ((CPU.AX, eax), (CPU.BX, ebx), (CPU.CX, ecx), (CPU.DX, edx), (CPU.SI, esi),
                                (CPU.DI, edi)):
            cpu.set32(register, value)
        cpu.ww(ss, ebp - 6 & MASK, columns)
        cpu.ww(ss, ebp - 8 & MASK, left_rows)
        cpu.instructions += executed - 1  # (the INT stands for the first one)
        cpu.ip = at

    while True:
        eax = low16(eax, width)  # mov ax, [001CD7CC]; mov [ebp-6], ax; xchg ebx, ebx
        columns = width
        executed += 3
        while True:
            pixel = cpu.rb(ds, esi)  # lodsb
            esi = esi + 1 & MASK
            eax = eax & ~0xFF | pixel
            executed += 1
            put = True
            if loop.transparent:  # cmp al, 0; je
                put = pixel != 0
                executed += 2
            if put:  # cmp cx, 0280; jge
                put = signed16(ecx) < SCREEN_WIDTH
                executed += 2
                if put:  # cmp bx, 01E0; jge
                    put = signed16(ebx) < SCREEN_HEIGHT
                    executed += 2
            if put:  # stosb; jmp
                cpu.wb(es, edi, pixel)
                executed += 2
            else:  # inc edi, ...
                executed += loop.skipped
            edi = edi + 1 & MASK
            ecx = low16(ecx, ecx + 1)  # inc cx; inc dx; jne
            edx = low16(edx, edx + 1)
            executed += 3
            if edx & 0xFFFF == 0:  # the bank goes round in the row
                return give_back(loop.wrap_at, columns)
            columns = columns - 1 & 0xFFFF  # dec word [ebp-6]; jne
            executed += 2
            if columns == 0:
                break
        ebx = low16(ebx, ebx + 1)  # inc bx; sub cx, [001CD7CC]; add di (or si), [001CD7D0]; add dx, [001CD7D0]
        ecx = low16(ecx, ecx - width)
        if loop.from_video:
            esi = low16(esi, esi + rest)
        else:
            edi = low16(edi, edi + rest)
        edx = low16(edx, edx + rest)
        executed += 6  # ... cmp edx, [001CD7D4]; jge
        signed_edx = edx - (1 << 32) if edx & 0x80000000 else edx
        if signed_edx < limit:  # the bank went round between the rows
            return give_back(loop.switch_at, columns)
        left_rows = left_rows - 1 & 0xFFFF  # dec word [ebp-8]; jne
        executed += 2
        if left_rows == 0:
            return give_back(loop.done_at, columns)


PATCHES = [
    Patch("UFO2P.EXE: the cursor, what was under it put back", RESTORE.row_at, 0x74,
          "25e0268a8f55cf54c410f5c062cabe0629309f1f0a25afdc6dddcc18dfe5df30", lambda cpu: rows(cpu, RESTORE)),
    Patch("UFO2P.EXE: the cursor, what is under it kept", SAVE.row_at, 0x70,
          "a6816ca17151a5f449f901483fc2ae0fb3068d5ca2562d512dfa2bd1f5f2f52a", lambda cpu: rows(cpu, SAVE)),
    Patch("UFO2P.EXE: the cursor drawn", DRAW.row_at, 0x74,
          "c2d766cef922bf3127d3e396f7a412a3d3ae2c3925d00ddc0f9505833f9fcf25", lambda cpu: rows(cpu, DRAW)),
]
