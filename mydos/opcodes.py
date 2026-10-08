"""Names for the one-byte opcodes the CPU understands.

IntEnum members are plain ints, so `op == Op.NOP` and `op - Op.MOV_R8_IMM8` just work.
Opcodes that encode a register in their low 3 bits are listed by their first value.
"""
from enum import IntEnum


class Op(IntEnum):
    # segment-override prefixes: modify the next instruction, not instructions themselves
    ES_PREFIX = 0x26
    CS_PREFIX = 0x2E
    SS_PREFIX = 0x36
    DS_PREFIX = 0x3E

    MOV_RM16_R16 = 0x89  # followed by ModRM: r/m16 <- r16
    MOV_R16_RM16 = 0x8B  # followed by ModRM: r16 <- r/m16
    NOP = 0x90
    MOV_R8_IMM8 = 0xB0   # B0..B7: AL CL DL BL AH CH DH BH <- imm8
    MOV_R16_IMM16 = 0xB8  # B8..BF: AX CX DX BX SP BP SI DI <- imm16
    INT = 0xCD           # followed by the interrupt number
    JMP_SHORT = 0xEB     # followed by a signed 8-bit displacement
    HLT = 0xF4
