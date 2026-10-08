"""Names for the one-byte opcodes the CPU understands.

IntEnum members are plain ints, so `op == Op.NOP` and `op - Op.MOV_R8_IMM8` just work.
Opcodes that encode a register in their low 3 bits are listed by their first value.
"""
from enum import IntEnum


class Op(IntEnum):
    # push/pop of a segment register; the register number is (op >> 3) & 3 (ES CS SS DS)
    PUSH_ES = 0x06
    POP_ES = 0x07
    PUSH_CS = 0x0E       # there is no POP CS
    PUSH_SS = 0x16
    POP_SS = 0x17
    PUSH_DS = 0x1E
    POP_DS = 0x1F

    # segment-override prefixes: modify the next instruction, not instructions themselves
    ES_PREFIX = 0x26
    CS_PREFIX = 0x2E
    SS_PREFIX = 0x36
    DS_PREFIX = 0x3E

    INC_R16 = 0x40       # 40..47: add 1 to AX CX DX BX SP BP SI DI
    DEC_R16 = 0x48       # 48..4F: subtract 1 from AX CX DX BX SP BP SI DI
    PUSH_R16 = 0x50      # 50..57: push AX CX DX BX SP BP SI DI
    POP_R16 = 0x58       # 58..5F: pop into AX CX DX BX SP BP SI DI
    MOV_RM16_R16 = 0x89  # followed by ModRM: r/m16 <- r16
    MOV_R16_RM16 = 0x8B  # followed by ModRM: r16 <- r/m16
    MOV_RM16_SREG = 0x8C  # followed by ModRM (reg = segment register): r/m16 <- sreg
    MOV_SREG_RM16 = 0x8E  # followed by ModRM (reg = segment register): sreg <- r/m16
    NOP = 0x90
    MOV_AL_MOFFS8 = 0xA0    # followed by a 16-bit offset: AL <- byte at [offset]
    MOV_AX_MOFFS16 = 0xA1   # followed by a 16-bit offset: AX <- word at [offset]
    MOV_MOFFS8_AL = 0xA2    # followed by a 16-bit offset: byte at [offset] <- AL
    MOV_MOFFS16_AX = 0xA3   # followed by a 16-bit offset: word at [offset] <- AX
    SCASB = 0xAE         # compare AL with the byte at ES:DI, then step DI
    SCASW = 0xAF         # compare AX with the word at ES:DI, then step DI
    MOV_R8_IMM8 = 0xB0   # B0..B7: AL CL DL BL AH CH DH BH <- imm8
    MOV_R16_IMM16 = 0xB8  # B8..BF: AX CX DX BX SP BP SI DI <- imm16
    RET_NEAR = 0xC3      # pops IP from the stack
    INT = 0xCD           # followed by the interrupt number
    LOOPNE = 0xE0        # followed by a signed 8-bit displacement
    LOOPE = 0xE1         # followed by a signed 8-bit displacement
    LOOP = 0xE2          # followed by a signed 8-bit displacement
    JCXZ = 0xE3          # followed by a signed 8-bit displacement
    CALL_NEAR = 0xE8     # followed by a signed 16-bit displacement; pushes the return IP
    JMP_SHORT = 0xEB     # followed by a signed 8-bit displacement
    REPNE_PREFIX = 0xF2  # modifies the next string instruction: repeat while CX != 0 and ZF == 0
    HLT = 0xF4
    CLC = 0xF8
    STC = 0xF9
    CLI = 0xFA
    STI = 0xFB
    CLD = 0xFC
    STD = 0xFD


class Alu(IntEnum):
    """The eight two-operand arithmetic/logic operations that share opcodes 00-3D.

    Each operation owns a block of 8 opcodes: opcode = (operation << 3) | form, and the forms are
        0: r/m8,  r8        3: r16, r/m16
        1: r/m16, r16       4: AL,  imm8
        2: r8,    r/m8      5: AX,  imm16
    (forms 6 and 7 are other instructions: push/pop of a segment register, prefixes, BCD adjusts).
    For example XOR is 6, so XOR AX, imm16 is (6 << 3) | 5 = 0x35. Only the operations that are
    implemented get an entry in CPU.alu_ops.
    """
    ADD = 0
    OR = 1
    ADC = 2
    SBB = 3
    AND = 4
    SUB = 5
    XOR = 6
    CMP = 7
