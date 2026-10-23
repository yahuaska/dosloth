"""Turn the bytes of an instruction into text like `mov ax, bx`, for the -d trace.

disasm() only reads memory; it never changes the machine and never raises. It is written apart from
the CPU on purpose, so the instruction handlers stay free of formatting code. The price: every opcode
the CPU learns has to be taught here too. tests/test_disasm.py checks that the two agree on the
length of every opcode the CPU knows.

Numbers are printed in hex without a prefix: bytes as 2 digits, words and addresses as 4.
"""
from .opcodes import Alu, Op

REG16 = ["ax", "cx", "dx", "bx", "sp", "bp", "si", "di"]
REG8 = ["al", "cl", "dl", "bl", "ah", "ch", "dh", "bh"]
SREG = ["es", "cs", "ss", "ds"]
# registers added up into the address, by the r/m field of a ModRM byte (see modrm.py)
BASES = ["bx+si", "bx+di", "bp+si", "bp+di", "si", "di", "bp", "bx"]
SEG_PREFIXES = {Op.ES_PREFIX: "es", Op.CS_PREFIX: "cs", Op.SS_PREFIX: "ss", Op.DS_PREFIX: "ds"}
JCC = ["jo", "jno", "jb", "jae", "je", "jne", "jbe", "ja", "js", "jns", "jp", "jnp", "jl", "jge", "jle", "jg"]
# instructions that are a single byte with no operands
PLAIN = {
    Op.NOP: "nop", Op.CBW: "cbw", Op.CWD: "cwd", Op.HLT: "hlt",
    Op.PUSHF: "pushf", Op.POPF: "popf", Op.SAHF: "sahf", Op.LAHF: "lahf", Op.PUSHA: "pusha", Op.POPA: "popa",
    Op.RET_NEAR: "ret", Op.RETF: "retf", Op.IRET: "iret", Op.LEAVE: "leave",
    Op.CMC: "cmc", Op.CLC: "clc", Op.STC: "stc", Op.CLI: "cli", Op.STI: "sti", Op.CLD: "cld", Op.STD: "std",
    Op.SCASB: "scasb", Op.SCASW: "scasw", Op.MOVSB: "movsb", Op.MOVSW: "movsw", Op.CMPSB: "cmpsb", Op.CMPSW: "cmpsw",
    Op.STOSB: "stosb", Op.STOSW: "stosw", Op.LODSB: "lodsb", Op.LODSW: "lodsw",
}
COMPARING = (Op.CMPSB, Op.CMPSW, Op.SCASB, Op.SCASW)  # these look at ZF, so a prefix F3 reads "repe"
SHIFTS = ["rol", "ror", "rcl", "rcr", "shl", "shr", "sal", "sar"]  # by the ModRM reg field
LOOPS = {Op.LOOPNE: "loopne", Op.LOOPE: "loope", Op.LOOP: "loop", Op.JCXZ: "jcxz"}


class _Reader:
    """Walks over the bytes of one instruction, remembering how far it has got."""

    def __init__(self, cpu, seg, off):
        self.cpu, self.seg, self.start, self.pos = cpu, seg, off, off

    def u8(self):
        v = self.cpu.rb(self.seg, self.pos)
        self.pos = (self.pos + 1) & 0xFFFF
        return v

    def u16(self):
        lo = self.u8()
        return lo | self.u8() << 8

    def s8(self):
        v = self.u8()
        return v - 256 if v & 0x80 else v

    def length(self):
        return (self.pos - self.start) & 0xFFFF


def _signed(v):
    """Hex with a minus sign for negative numbers: -1, 4."""
    return f"-{-v:X}" if v < 0 else f"{v:X}"


def _modrm(r, seg_override, bits):
    """Read a ModRM byte (and its address bytes). Returns (reg field, r/m text, r/m is memory).

    `bits` (8 or 16) only decides which register names an r/m that is a register gets.
    """
    b = r.u8()
    mod, reg, rm = b >> 6, (b >> 3) & 7, b & 7
    if mod == 3:
        return reg, (REG8 if bits == 8 else REG16)[rm], False
    if mod == 0 and rm == 6:
        inner = f"{r.u16():04X}"  # a bare address
    else:
        inner = BASES[rm]
        if mod == 1:
            inner += _disp(r.s8())
        elif mod == 2:
            d = r.u16()
            inner += _disp(d - 0x10000 if d & 0x8000 else d)
    return reg, f"{seg_override + ':' if seg_override else ''}[{inner}]", True


def _disp(d):
    """A displacement as it is written inside the brackets: +5, -2, or nothing for 0."""
    return "" if d == 0 else (f"-{-d:X}" if d < 0 else f"+{d:X}")


def _operands(r, op, seg_override):
    """The two operands, as (destination, source) text, of an opcode in the layout CPU.operands() describes."""
    bits = 16 if op & 1 else 8
    regs = REG16 if bits == 16 else REG8
    reg, rm, _ = _modrm(r, seg_override, bits)
    return (regs[reg], rm) if op & 2 else (rm, regs[reg])


def _direct(r, seg_override):
    """The [address] that follows the A0-A3 opcodes."""
    return f"{seg_override + ':' if seg_override else ''}[{r.u16():04X}]"


def _decode(r):
    """Return the text of the instruction at the reader, or None if the opcode is not known."""
    seg_override = rep = None
    op = r.u8()
    while True:  # prefixes, as in CPU.step
        if op in SEG_PREFIXES:
            seg_override = SEG_PREFIXES[op]
        elif op in (Op.REP_PREFIX, Op.REPNE_PREFIX):
            rep = op
        else:
            break
        op = r.u8()

    text = _decode_opcode(r, op, seg_override)
    if text is None:
        return None
    if rep == Op.REPNE_PREFIX:
        return "repne " + text
    if rep == Op.REP_PREFIX:
        return ("repe " if op in COMPARING else "rep ") + text
    return text


def _decode_opcode(r, op, seg_override):
    if op < 0x40 and op & 7 <= 5:  # the arithmetic/logic block, see the Alu enum
        name = Alu(op >> 3).name.lower()
        form = op & 7
        if form == 4:
            return f"{name} al, {r.u8():02X}"
        if form == 5:
            return f"{name} ax, {r.u16():04X}"
        dst, src = _operands(r, op, seg_override)
        return f"{name} {dst}, {src}"
    if op in PLAIN:
        return PLAIN[op]
    if op in (Op.PUSH_ES, Op.PUSH_CS, Op.PUSH_SS, Op.PUSH_DS):
        return f"push {SREG[(op >> 3) & 3]}"
    if op in (Op.POP_ES, Op.POP_SS, Op.POP_DS):
        return f"pop {SREG[(op >> 3) & 3]}"
    if Op.INC_R16 <= op < Op.INC_R16 + 8:
        return f"inc {REG16[op - Op.INC_R16]}"
    if Op.DEC_R16 <= op < Op.DEC_R16 + 8:
        return f"dec {REG16[op - Op.DEC_R16]}"
    if op == Op.PUSH_IMM16:
        return f"push {r.u16():04X}"
    if op == Op.PUSH_IMM8:
        return f"push {_signed(r.s8())}"  # the CPU sign-extends it to a word
    if Op.PUSH_R16 <= op < Op.PUSH_R16 + 8:
        return f"push {REG16[op - Op.PUSH_R16]}"
    if Op.POP_R16 <= op < Op.POP_R16 + 8:
        return f"pop {REG16[op - Op.POP_R16]}"
    if Op.JCC <= op < Op.JCC + 16:
        d = r.s8()
        return f"{JCC[op - Op.JCC]} {(r.pos + d) & 0xFFFF:04X}"
    if op in (Op.ALU_RM8_IMM8, Op.ALU_RM16_IMM16, Op.ALU_RM16_IMM8):
        bits = 8 if op == Op.ALU_RM8_IMM8 else 16
        reg, rm, is_mem = _modrm(r, seg_override, bits)
        if op == Op.ALU_RM8_IMM8:
            imm = f"{r.u8():02X}"
        elif op == Op.ALU_RM16_IMM16:
            imm = f"{r.u16():04X}"
        else:
            imm = _signed(r.s8())  # a one-byte constant that the CPU sign-extends
        size = ("byte " if bits == 8 else "word ") if is_mem else ""  # memory needs a size
        return f"{Alu(reg).name.lower()} {size}{rm}, {imm}"
    if Op.XCHG_AX_R16 < op < Op.XCHG_AX_R16 + 8:
        return f"xchg ax, {REG16[op - Op.XCHG_AX_R16]}"
    if op == Op.LEA:
        reg, rm, is_mem = _modrm(r, None, 16)  # a segment prefix does nothing to LEA, so it is not shown
        return f"lea {REG16[reg]}, {rm}" if is_mem else None
    if op == Op.POP_RM16:
        reg, rm, is_mem = _modrm(r, seg_override, 16)
        if reg != 0:
            return None
        return f"pop {'word ' if is_mem else ''}{rm}"
    if op in (Op.TEST_RM_REG, Op.TEST_RM_REG + 1):
        dst, src = _operands(r, op, seg_override)  # the opcode's direction bit is 0: r/m comes first
        return f"test {dst}, {src}"
    if op in (Op.TEST_ACC_IMM, Op.TEST_ACC_IMM + 1):
        return f"test ax, {r.u16():04X}" if op & 1 else f"test al, {r.u8():02X}"
    if op in (Op.XCHG_RM_REG, Op.XCHG_RM_REG + 1):
        dst, src = _operands(r, op, seg_override)  # the layout puts the register first for these opcodes
        return f"xchg {dst}, {src}"
    if Op.MOV_RM_REG <= op < Op.MOV_RM_REG + 4:
        dst, src = _operands(r, op, seg_override)
        return f"mov {dst}, {src}"
    if op in (Op.MOV_RM16_SREG, Op.MOV_SREG_RM16):
        reg, rm, _ = _modrm(r, seg_override, 16)
        if reg > 3:
            return None
        return f"mov {rm}, {SREG[reg]}" if op == Op.MOV_RM16_SREG else f"mov {SREG[reg]}, {rm}"
    if op == Op.MOV_AL_MOFFS8:
        return f"mov al, {_direct(r, seg_override)}"
    if op == Op.MOV_AX_MOFFS16:
        return f"mov ax, {_direct(r, seg_override)}"
    if op == Op.MOV_MOFFS8_AL:
        return f"mov {_direct(r, seg_override)}, al"
    if op == Op.MOV_MOFFS16_AX:
        return f"mov {_direct(r, seg_override)}, ax"
    if op in (Op.MOV_RM_IMM, Op.MOV_RM_IMM + 1):
        bits = 16 if op & 1 else 8
        reg, rm, is_mem = _modrm(r, seg_override, bits)
        if reg != 0:
            return None
        imm = f"{r.u16():04X}" if bits == 16 else f"{r.u8():02X}"
        size = ("word " if bits == 16 else "byte ") if is_mem else ""
        return f"mov {size}{rm}, {imm}"
    if Op.MOV_R8_IMM8 <= op < Op.MOV_R8_IMM8 + 8:
        return f"mov {REG8[op - Op.MOV_R8_IMM8]}, {r.u8():02X}"
    if Op.MOV_R16_IMM16 <= op < Op.MOV_R16_IMM16 + 8:
        return f"mov {REG16[op - Op.MOV_R16_IMM16]}, {r.u16():04X}"
    if op == Op.GROUP4_RM8:
        reg, rm, is_mem = _modrm(r, seg_override, 8)
        if reg > 1:
            return None
        return f"{'dec' if reg else 'inc'} {'byte ' if is_mem else ''}{rm}"
    if op == Op.GROUP5_RM16:
        reg, rm, is_mem = _modrm(r, seg_override, 16)
        word = f"word {rm}" if is_mem else rm
        if reg in (0, 1, 2, 4, 6):
            return f"{['inc', 'dec', 'call', None, 'jmp', None, 'push'][reg]} {word}"
        if reg in (3, 5) and is_mem:  # a far pointer needs memory
            return f"{'call' if reg == 3 else 'jmp'} far {rm}"
        return None
    if op in (Op.GROUP3_RM, Op.GROUP3_RM + 1):
        bits = 16 if op & 1 else 8
        reg, rm, is_mem = _modrm(r, seg_override, bits)
        size = ("word " if bits == 16 else "byte ") if is_mem else ""
        if reg in (0, 1):  # TEST has a constant after the ModRM bytes
            return f"test {size}{rm}, {r.u16():04X}" if bits == 16 else f"test {size}{rm}, {r.u8():02X}"
        return f"{['', '', 'not', 'neg', 'mul', 'imul', 'div', 'idiv'][reg]} {size}{rm}"
    if Op.SHIFT_RM <= op < Op.SHIFT_RM + 4:
        bits = 16 if op & 1 else 8
        reg, rm, is_mem = _modrm(r, seg_override, bits)
        size = ("word " if bits == 16 else "byte ") if is_mem else ""
        return f"{SHIFTS[reg]} {size}{rm}, {'cl' if op & 2 else '1'}"
    if op in (Op.LES, Op.LDS):
        reg, rm, is_mem = _modrm(r, seg_override, 16)
        if not is_mem:  # the CPU wants a pointer in memory
            return None
        return f"{'les' if op == Op.LES else 'lds'} {REG16[reg]}, {rm}"
    if Op.IO_IMM <= op < Op.IO_IMM + 4 or Op.IO_DX <= op < Op.IO_DX + 4:
        reg = "ax" if op & 1 else "al"
        port = "dx" if op & 8 else f"{r.u8():02X}"
        return f"out {port}, {reg}" if op & 2 else f"in {reg}, {port}"
    if op in (Op.RET_NEAR_POP, Op.RETF_POP):
        return f"{'ret' if op == Op.RET_NEAR_POP else 'retf'} {r.u16():04X}"
    if op == Op.ENTER:
        return f"enter {r.u16():04X}, {r.u8():02X}"
    if op == Op.INT:
        return f"int {r.u8():02X}"
    if op in LOOPS:
        d = r.s8()
        return f"{LOOPS[op]} {(r.pos + d) & 0xFFFF:04X}"
    if op in (Op.CALL_FAR, Op.JMP_FAR):
        offset = r.u16()
        return f"{'call' if op == Op.CALL_FAR else 'jmp'} {r.u16():04X}:{offset:04X}"
    if op in (Op.CALL_NEAR, Op.JMP_NEAR):
        d = r.u16()
        return f"{'call' if op == Op.CALL_NEAR else 'jmp'} {(r.pos + d) & 0xFFFF:04X}"  # wraps like the CPU
    if op == Op.JMP_SHORT:
        d = r.s8()
        return f"jmp {(r.pos + d) & 0xFFFF:04X}"
    return None


def disasm(cpu, seg, off):
    """Describe the instruction at seg:off. Returns (length in bytes, text).

    An opcode we do not know comes back as one byte of "db XX", so a trace never stops half way.
    """
    r = _Reader(cpu, seg, off)
    try:
        text = _decode(r)
    except Exception:  # a trace must never be the thing that crashes the program
        text = None
    if text is None:
        return 1, f"db {cpu.rb(seg, off):02X}"
    return r.length(), text
