"""The choice of the voices that play, in the driver of the sound that SMKP.EXE reads from a file (at 1527)."""

from ..cpu import CPU
from ._base import LatePatch
from ._common import DWORD, MASK, WORD, after_counting


# The service of the driver (the procedure at 14DE of its code, 100 times a second from the timer) first makes a list
# of the voices that play: of the 32 records of a voice (6Ch bytes each, from DS:ESI), the ones whose word of flags (at
# +30h) has the bit 8000h and not the bit 1000h. Their addresses go to the list at DS:EBX, and their number to the
# dword DS:[1270h]. A round is 5 instructions for a voice without 8000h, 7 for one with 1000h too, 10 for one that plays.
PLAYING, PAUSED = 0x8000, 0x1000
RECORD = 0x6C  # the size of the record of a voice
FLAGS = 0x30  # where its word of flags is in it
COUNT = 0x1270  # the dword that counts the voices in the list
AFTER = 0x154A  # the code after the loop


def choose_voices(cpu):
    """What the loop at 1527 does: ECX rounds, one for each record. ESI goes past them all, EBX past the list, ECX is 0,
    and the flags are those of `sub ecx, 1` that came to 0. The memory is read and written in the order of the code."""
    mem, base = cpu.mem, cpu.bases[cpu.s[CPU.DS]]
    esi, ebx = cpu.wide(CPU.SI), cpu.wide(CPU.BX)
    rounds = cpu.wide(CPU.CX) or 1 << 32  # (ECX = 0 is a loop of 2**32 rounds)
    count_at = base + COUNT
    executed = 0
    for _ in range(rounds):
        state = WORD.unpack_from(mem, base + (esi + FLAGS & MASK))[0]
        if not state & PLAYING:
            executed += 5
        elif state & PAUSED:
            executed += 7
        else:
            DWORD.pack_into(mem, base + ebx, esi)
            ebx = (ebx + 4) & MASK
            DWORD.pack_into(mem, count_at, DWORD.unpack_from(mem, count_at)[0] + 1 & MASK)
            executed += 10
        esi = (esi + RECORD) & MASK
    cpu.set32(CPU.SI, esi)
    cpu.set32(CPU.BX, ebx)
    cpu.set32(CPU.CX, 0)
    after_counting(cpu)
    cpu.instructions += executed - 1  # (the INT stands for the first one)
    cpu.ip = AFTER


# The piece begins after `mov ax, <selector>` at 14E5: the driver gets its selector when it is loaded.
LATE_PATCHES = [LatePatch("the choice of the voices that play",
                          "833d2e130000000f85a3050000ff052e130000668c054e1300005657551e0fa1",  # 14EC-150B
                          0x5E, "cfb0b438f41eadabdaad7f3ab11f4b66aa75116f22ba5a4b6d88420c1ce9991e",
                          [(0x3B, bytes.fromhex("66f7"), choose_voices)])]
