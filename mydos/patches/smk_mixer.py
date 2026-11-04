"""The mixer of the driver of the sound that SMKP.EXE reads from a file (the procedure at 2144)."""

from .. import flags
from ..cpu import CPU
from ._base import LatePatch
from ._common import ARITHMETIC, DWORD, MASK


def mix_into_ring(counter, after):
    """The handler for the loop of the mixing of the sound (two samples for each round of it): the loop at the
    offset of the code `after` less its length, that counts the rounds in the dword at FS:`counter`."""
    def handler(cpu):
        mix_samples(cpu, counter, after)
    return handler


def mix_samples(cpu, counter, after):
    """What the loop of the mixer does: for every sample (a byte, the upper of a 16-bit sample at DS:ESI + 2 * ECX) it
    makes the volume (BH, a signed byte) of it, and adds that to the byte of the ring at ES:EDI (a byte with 80h taken
    off for the sign), and when the sum does not fit in a signed byte it is the biggest or the least. A round is two of
    them; the dword at FS:`counter` is the rounds that are left, and the code goes on at the offset `after` when it is
    0. The registers and the flags are as the code leaves them, and the instructions are counted as it would run."""
    mem = cpu.mem
    source = cpu.bases[cpu.s[CPU.DS]]
    ring = cpu.bases[cpu.s[CPU.ES]]
    counter_at = cpu.bases[cpu.s[CPU.FS]] + counter
    esi, ecx, edi, eax = cpu.wide(CPU.SI), cpu.wide(CPU.CX), cpu.wide(CPU.DI), cpu.wide(CPU.AX)
    volume = cpu.r[CPU.BX] >> 8 & 0xFF
    volume -= 256 if volume > 127 else 0
    rounds = DWORD.unpack_from(mem, counter_at)[0]
    executed = 0
    while True:
        for _ in range(2):
            sample = mem[source + (esi + 2 * ecx + 1 & MASK)]
            ecx = (ecx + 1) & MASK
            product = (sample - 256 if sample > 127 else sample) * volume & 0xFFFF  # imul bh: AX
            eax = ((eax & 0xFFFF0000 | product) >> 7) & 0xFFFF00FF | mem[ring + edi] << 8  # shr eax, 7; mov ah, es:[edi]
            eax ^= 0x8000  # xor ah, 80h
            ah = eax >> 8 & 0xFF
            total = (eax & 0xFF) + ah
            low = total & 0xFF
            executed += 8
            if (low ^ eax) & (low ^ ah) & 0x80:  # the sum overflows: the biggest, or the least
                low = 0xFF if low & 0x80 else 0  # (0x7F, with 80h taken off again, or 0)
                executed += 6
            else:
                low ^= 0x80
                executed += 3
            eax = eax & 0xFFFFFF00 | low
            mem[ring + edi] = low
            edi = (edi + 1) & MASK
        rounds -= 1
        executed += 2
        if rounds == 0:
            break
    DWORD.pack_into(mem, counter_at, 0)
    for number, value in ((CPU.AX, eax), (CPU.CX, ecx), (CPU.DI, edi)):
        cpu.set32(number, value)
    cpu.flags = (cpu.flags & ~ARITHMETIC) | flags.ZF | flags.PF  # sub dword fs:[counter], 1: the result is 0
    cpu.instructions += executed - 1  # (the INT stands for the first one)
    cpu.ip = after



# The mixer of the driver of the sound: the procedure at 2144 of its code, that mixes what is to be played into the
# ring of the buffer; it has two loops, one up to the end of the ring, and one from its beginning.


LATE_PATCHES = [LatePatch("the mixer of the sound",
                          "1e568b0d10130000890d141300000fb7463266f72dfc12000003d28bda668e05",
                          0x118, "5976e86960d7b54a2c51eb978f2d05c6875e674c7c48ba6af1b8a1d34ab75f8d",
                          [(0x46, bytes.fromhex("8a44"), mix_into_ring(0x130C, 0x21E4)),
                           (0xBB, bytes.fromhex("8a44"), mix_into_ring(0x1314, 0x2259))])]
