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
    PUSHA = 0x60         # 80186 and later: push AX CX DX BX SP BP SI DI
    POPA = 0x61          # 80186 and later: pop DI SI BP (skip SP) BX DX CX AX
    PUSH_IMM16 = 0x68    # 80186 and later, followed by imm16: push the constant
    PUSH_IMM8 = 0x6A     # 80186 and later, followed by imm8: push the constant, sign-extended to a word
    TWO_BYTE = 0x0F      # 80386 and later: the first byte of an instruction of two; the second one says which
    JCC = 0x70           # 70..7F: conditional short jump, followed by a signed 8-bit displacement
    ALU_RM8_IMM8 = 0x80    # + ModRM + imm8:  the operation is the ModRM 'reg' field (see Alu)
    ALU_RM16_IMM16 = 0x81  # + ModRM + imm16
    ALU_RM16_IMM8 = 0x83   # + ModRM + imm8, sign-extended to 16 bits
    TEST_RM_REG = 0x84   # 84 (byte) and 85 (word) + ModRM: AND a register with r/m, flags only
    XCHG_RM_REG = 0x86   # 86 (byte) and 87 (word) + ModRM: swap a register with a register or memory
    MOV_RM_REG = 0x88    # 88..8B + ModRM: r/m8 <- r8, r/m16 <- r16, r8 <- r/m8, r16 <- r/m16 (see CPU.operands)
    MOV_RM16_SREG = 0x8C  # followed by ModRM (reg = segment register): r/m16 <- sreg
    LEA = 0x8D           # + ModRM (memory): the address of the operand into a register, memory not read
    MOV_SREG_RM16 = 0x8E  # followed by ModRM (reg = segment register): sreg <- r/m16
    POP_RM16 = 0x8F      # + ModRM (reg field 0): pop a word into a register or memory
    NOP = 0x90           # really XCHG AX, AX, which does nothing
    XCHG_AX_R16 = 0x90   # 91..97: swap AX with CX DX BX SP BP SI DI (90 itself is NOP)
    CBW = 0x98           # AL -> AX, the sign copied into AH
    CWD = 0x99           # AX -> DX:AX, the sign copied into DX
    CALL_FAR = 0x9A      # followed by offset and segment (two words): a call to another segment
    WAIT = 0x9B          # wait for the coprocessor to be done; there is none, so it does nothing
    PUSHF = 0x9C         # flags -> stack
    POPF = 0x9D          # stack -> flags
    SAHF = 0x9E          # AH -> SF ZF AF PF CF
    LAHF = 0x9F          # SF ZF AF PF CF -> AH
    MOV_AL_MOFFS8 = 0xA0    # followed by a 16-bit offset: AL <- byte at [offset]
    MOV_AX_MOFFS16 = 0xA1   # followed by a 16-bit offset: AX <- word at [offset]
    MOV_MOFFS8_AL = 0xA2    # followed by a 16-bit offset: byte at [offset] <- AL
    MOV_MOFFS16_AX = 0xA3   # followed by a 16-bit offset: word at [offset] <- AX
    MOVSB = 0xA4         # string instructions: work on the byte/word at DS:SI and/or ES:DI, then step SI/DI
    MOVSW = 0xA5         # copy DS:SI to ES:DI
    CMPSB = 0xA6         # compare DS:SI with ES:DI
    CMPSW = 0xA7
    TEST_ACC_IMM = 0xA8  # A8 (AL, imm8) and A9 (AX, imm16): AND the accumulator with a constant, flags only
    STOSB = 0xAA         # store AL/AX at ES:DI
    STOSW = 0xAB
    LODSB = 0xAC         # load AL/AX from DS:SI
    LODSW = 0xAD
    SCASB = 0xAE         # compare AL/AX with ES:DI
    SCASW = 0xAF
    MOV_R8_IMM8 = 0xB0   # B0..B7: AL CL DL BL AH CH DH BH <- imm8
    MOV_R16_IMM16 = 0xB8  # B8..BF: AX CX DX BX SP BP SI DI <- imm16
    RET_NEAR_POP = 0xC2  # followed by imm16: pops IP, then drops that many bytes of arguments from the stack
    RET_NEAR = 0xC3      # pops IP from the stack
    LES = 0xC4           # + ModRM (memory): load a register and ES from a far pointer
    LDS = 0xC5           # + ModRM (memory): load a register and DS from a far pointer
    ENTER = 0xC8         # 80186 and later, followed by imm16 and imm8: build a stack frame
    LEAVE = 0xC9         # 80186 and later: mov sp, bp ; pop bp
    RETF_POP = 0xCA      # followed by imm16: pops IP and CS, then drops that many bytes of arguments
    RETF = 0xCB          # pops IP, then CS
    MOV_RM_IMM = 0xC6    # C6 (byte) and C7 (word) + ModRM (reg field 0) + the constant: r/m <- imm
    IRET = 0xCF          # return from an interrupt: pops IP, CS and the flags
    INT = 0xCD           # followed by the interrupt number
    AAM = 0xD4           # followed by the base (10 in practice): AH = AL // base, AL = AL % base
    AAD = 0xD5           # followed by the base: AL = AH * base + AL, AH = 0
    SHIFT_RM = 0xD0      # D0..D3 + ModRM: shift or rotate r/m by 1 or by CL, the 'reg' field picks how (CPU.shift_rm)
    ESC = 0xD8           # D8..DF + ModRM: an instruction of the coprocessor (8087), which we do not have
    LOOPNE = 0xE0        # followed by a signed 8-bit displacement
    LOOPE = 0xE1         # followed by a signed 8-bit displacement
    LOOP = 0xE2          # followed by a signed 8-bit displacement
    JCXZ = 0xE3          # followed by a signed 8-bit displacement
    IO_IMM = 0xE4        # E4..E7: IN AL, IN AX, OUT AL, OUT AX with the port number in the next byte
    CALL_NEAR = 0xE8     # followed by a signed 16-bit displacement; pushes the return IP
    JMP_NEAR = 0xE9      # followed by a signed 16-bit displacement
    JMP_FAR = 0xEA       # followed by offset and segment (two words): a jump to another segment
    JMP_SHORT = 0xEB     # followed by a signed 8-bit displacement
    IO_DX = 0xEC         # EC..EF: the same four, with the port number in DX
    REPNE_PREFIX = 0xF2  # modifies the next string instruction: repeat while CX != 0 and ZF == 0
    REP_PREFIX = 0xF3    # ... repeat CX times (REP); with CMPS/SCAS: also while ZF == 1 (REPE)
    HLT = 0xF4
    GROUP3_RM = 0xF6     # F6 (byte) and F7 (word) + ModRM: TEST, NOT, NEG, MUL, IMUL, DIV or IDIV, by 'reg'
    CMC = 0xF5           # flip the carry flag
    CLC = 0xF8
    STC = 0xF9
    CLI = 0xFA
    STI = 0xFB
    CLD = 0xFC
    STD = 0xFD
    GROUP4_RM8 = 0xFE    # + ModRM: INC or DEC of r/m8, chosen by the 'reg' field (0 or 1)
    GROUP5_RM16 = 0xFF   # + ModRM: INC, DEC, CALL, JMP (near or far) or PUSH of r/m16, by the 'reg' field


class Alu(IntEnum):
    """The eight two-operand arithmetic/logic operations that share opcodes 00-3D.

    Each operation owns a block of 8 opcodes: opcode = (operation << 3) | form, and the forms are
        0: r/m8,  r8        3: r16, r/m16
        1: r/m16, r16       4: AL,  imm8
        2: r8,    r/m8      5: AX,  imm16
    (forms 6 and 7 are other instructions: push/pop of a segment register, prefixes, BCD adjusts).
    For example XOR is 6, so XOR AX, imm16 is (6 << 3) | 5 = 0x35. The same numbers appear
    in the 'reg' field of the ModRM byte after opcodes 80, 81 and 83 (operation with a constant).
    """
    ADD = 0
    OR = 1
    ADC = 2
    SBB = 3
    AND = 4
    SUB = 5
    XOR = 6
    CMP = 7
