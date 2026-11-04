"""What the patches have in common: the structures of a dword and a word, and the work that most of them end with."""
import struct

from .. import flags

DWORD = struct.Struct("<I")
WORD = struct.Struct("<H")
MASK = 0xFFFFFFFF

ARITHMETIC = flags.CF | flags.PF | flags.AF | flags.ZF | flags.SF | flags.OF


def after_counting(cpu, carry=False):
    """The flags a loop that ends with `dec` of a register that came to 0 leaves: ZF and PF, with the carry that
    the instruction before it left; the others are clear."""
    cpu.flags = (cpu.flags & ~ARITHMETIC) | flags.ZF | flags.PF | (flags.CF if carry else 0)


def give_back(cpu):
    """RET: the address on the stack is where the program goes on."""
    cpu.ip = cpu.pop32() & cpu.ip_mask
