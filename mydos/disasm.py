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
    Op.NOP: "nop", Op.RET_NEAR: "ret", Op.HLT: "hlt", Op.CLC: "clc", Op.STC: "stc", Op.CLI: "cli",
    Op.STI: "sti", Op.CLD: "cld", Op.STD: "std", Op.SCASB: "scasb", Op.SCASW: "scasw",
}
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
        elif op == Op.REPNE_PREFIX:
            rep = "repne "
        else:
            break
        op = r.u8()

    text = _decode_opcode(r, op, seg_override)
    return None if text is None else (rep or "") + text


def _decode_opcode(r, op, seg_override):
    if op < 0x40 and op & 7 <= 5:  # the arithmetic/logic block, see the Alu enum
        name = Alu(op >> 3).name.lower()
        form = op & 7
        if form == 4:
            return f"{name} al, {r.u8():02X}"
        if form == 5:
            return f"{name} ax, {r.u16():04X}"
        bits = 8 if form % 2 == 0 else 16
        regs = REG8 if bits == 8 else REG16
        reg, rm, _ = _modrm(r, seg_override, bits)
        return f"{name} {rm}, {regs[reg]}" if form < 2 else f"{name} {regs[reg]}, {rm}"
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
    if op == Op.MOV_RM16_R16:
        reg, rm, _ = _modrm(r, seg_override, 16)
        return f"mov {rm}, {REG16[reg]}"
    if op == Op.MOV_R16_RM16:
        reg, rm, _ = _modrm(r, seg_override, 16)
        return f"mov {REG16[reg]}, {rm}"
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
    if Op.MOV_R8_IMM8 <= op < Op.MOV_R8_IMM8 + 8:
        return f"mov {REG8[op - Op.MOV_R8_IMM8]}, {r.u8():02X}"
    if Op.MOV_R16_IMM16 <= op < Op.MOV_R16_IMM16 + 8:
        return f"mov {REG16[op - Op.MOV_R16_IMM16]}, {r.u16():04X}"
    if op == Op.GROUP3_RM16:
        reg, rm, is_mem = _modrm(r, seg_override, 16)
        if reg != 3:
            return None  # the CPU only knows NEG here
        return f"neg {'word ' if is_mem else ''}{rm}"
    if op == Op.SHIFT_RM16_CL:
        reg, rm, is_mem = _modrm(r, seg_override, 16)
        return f"{SHIFTS[reg]} {'word ' if is_mem else ''}{rm}, cl"
    if op == Op.INT:
        return f"int {r.u8():02X}"
    if op in LOOPS:
        d = r.s8()
        return f"{LOOPS[op]} {(r.pos + d) & 0xFFFF:04X}"
    if op == Op.CALL_NEAR:
        d = r.u16()
        return f"call {(r.pos + d) & 0xFFFF:04X}"  # + wraps like the CPU's signed displacement
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
