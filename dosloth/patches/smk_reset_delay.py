"""The wait of the reset of the Sound Blaster in the driver of the sound of SMKP.EXE."""

from ..cpu import CPU
from ._base import LatePatch
from ._common import DWORD, MASK


# The reset of the Sound Blaster in the driver of the sound waits for the card by a loop that does nothing: ECX rounds,
# and in each round the inner loop (`nop`, `loop`) goes 16 times: 36 instructions in a round, 2.2 million in all, when
# ECX is FFFFh. It is the same time for the program, and no work for us, if the instruction count goes on by what the
# loop would have run, up to the next event of the devices (a tick of the timer must come when it is due, to within a
# round or two: a round is not cut).
DELAY_ROUND = 36  # push, mov, 16 times (nop, loop), pop, loop
DELAY_START, DELAY_END = 0x2FB, 0x307  # the offsets of the outer loop and of what comes after it, in the code of the driver


def reset_delay(cpu):
    """What the delay of the reset does: ECX rounds of 36 instructions that change nothing but ECX (it is 0 at the end),
    and the stack below the pointer (the last ECX that was pushed). The rounds that go before the next event of the
    devices are made at once, and the code is started again at the loop for the rest."""
    rounds_left = cpu.wide(CPU.CX) or 1 << 32  # (ECX = 0 is a loop of 2**32 rounds)
    room = cpu.next_event - cpu.instructions + 1  # the instructions that go before the event is looked at
    rounds = max(1, min(rounds_left, room // DELAY_ROUND))
    cpu.instructions += rounds * DELAY_ROUND - 1  # (the INT stands for the first one)
    left = rounds_left - rounds
    DWORD.pack_into(cpu.mem, cpu.bases[cpu.s[CPU.SS]] + cpu.wide(CPU.SP) - 4, left + 1 & MASK)  # the last `push ecx`
    cpu.set32(CPU.CX, left & MASK)
    cpu.ip = DELAY_START if left else DELAY_END


LATE_PATCHES = [LatePatch("the wait of the reset of the Sound Blaster",
                          "a25f010000c6055e01000062c6053307000003a05e010000a230070000a05f01",
                          0x72, "8b3007afa47d4324eddd4fe8218557408cb01581eaf66fd5f1bbc8ff40c34f36",
                          [(0x3A, bytes.fromhex("51b9"), reset_delay)])]
