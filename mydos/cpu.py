from . import modrm
from .opcodes import Op


class Exit(Exception):
    """Raised to stop the program; code is the DOS exit code."""

    def __init__(self, code):
        """Remember the DOS exit code."""
        self.code = code


class CPU:
    """An 8086-style real-mode CPU with 1 MB of memory; instructions are dispatched via self.ops."""
    # register numbers, available as CPU.AX, CPU.DS, ...
    from .regs import AX, CX, DX, BX, SP, BP, SI, DI, ES, CS, SS, DS

    # segment-override prefix byte -> segment register
    SEG_PREFIXES = {Op.ES_PREFIX: ES, Op.CS_PREFIX: CS, Op.SS_PREFIX: SS, Op.DS_PREFIX: DS}

    def __init__(self):
        """Fresh machine: 1 MB RAM, zeroed registers, no interrupt handlers."""
        self.mem = bytearray(1024 * 1024)
        self.r = [0] * 8
        self.s = [0] * 4
        self.ip = 0
        self.flags = 0x0002
        self.int_handlers = {}  # int number -> fn(cpu)
        self.start_ip = 0  # set by step(): IP of the instruction being executed
        self.seg_override = None  # set by step(): segment register from a prefix, or None

        # opcode byte -> handler(op). Groups get one entry per opcode value.
        self.ops = {
            Op.NOP: self.nop,
            Op.MOV_RM16_R16: self.mov_rm16_r16,
            Op.MOV_R16_RM16: self.mov_r16_rm16,
            Op.MOV_RM16_SREG: self.mov_rm16_sreg,
            Op.MOV_SREG_RM16: self.mov_sreg_rm16,
            Op.MOV_AL_MOFFS8: self.mov_al_moffs8,
            Op.MOV_AX_MOFFS16: self.mov_ax_moffs16,
            Op.MOV_MOFFS8_AL: self.mov_moffs8_al,
            Op.MOV_MOFFS16_AX: self.mov_moffs16_ax,
            Op.CALL_NEAR: self.call_near,
            Op.RET_NEAR: self.ret_near,
            Op.JMP_SHORT: self.jmp_short,
            Op.INT: self.int_,
            Op.HLT: self.hlt,
        }
        for i in range(8):
            self.ops[Op.MOV_R8_IMM8 + i] = self.mov_r8_imm8
            self.ops[Op.MOV_R16_IMM16 + i] = self.mov_r16_imm16

    # --- memory ---
    def addr(self, seg, off):
        """Linear address of seg:off, wrapped to 20 bits."""
        return (seg * 16 + off) & 0xFFFFF

    def rb(self, seg, off):
        """Read a byte at seg:off."""
        return self.mem[self.addr(seg, off)]

    def rw(self, seg, off):
        """Read a little-endian word at seg:off."""
        return self.rb(seg, off) | self.rb(seg, off + 1) << 8

    def wb(self, seg, off, val):
        """Write a byte at seg:off."""
        self.mem[self.addr(seg, off)] = val & 0xFF

    def ww(self, seg, off, val):
        """Write a little-endian word at seg:off."""
        self.wb(seg, off, val)
        self.wb(seg, off + 1, val >> 8)

    # --- 8-bit register access: 0-3 = AL CL DL BL, 4-7 = AH CH DH BH ---
    def get8(self, n):
        """Read 8-bit register n (low byte of r[n&3], or high byte if n&4)."""
        v = self.r[n & 3]
        return (v >> 8) & 0xFF if n & 4 else v & 0xFF

    def set8(self, n, val):
        """Write 8-bit register n, leaving the other half of the 16-bit register alone."""
        i = n & 3
        if n & 4:
            self.r[i] = (self.r[i] & 0x00FF) | (val & 0xFF) << 8
        else:
            self.r[i] = (self.r[i] & 0xFF00) | (val & 0xFF)

    # --- stack ---
    def push16(self, val):
        """Push a word: SP -= 2 (wrapping at 16 bits), then store it at SS:SP."""
        sp = (self.r[self.SP] - 2) & 0xFFFF
        self.r[self.SP] = sp
        self.ww(self.s[self.SS], sp, val)

    def pop16(self):
        """Pop a word: read it from SS:SP, then SP += 2 (wrapping at 16 bits)."""
        sp = self.r[self.SP]
        val = self.rw(self.s[self.SS], sp)
        self.r[self.SP] = (sp + 2) & 0xFFFF
        return val

    # --- instruction fetch ---
    def fetch8(self):
        """Read the byte at CS:IP and advance IP."""
        v = self.rb(self.s[self.CS], self.ip)
        self.ip = (self.ip + 1) & 0xFFFF
        return v

    def fetch16(self):
        """Read a little-endian word at CS:IP and advance IP by 2."""
        lo = self.fetch8()
        return lo | self.fetch8() << 8

    def step(self):
        """Execute one instruction. Unknown opcodes/interrupts raise NotImplementedError."""
        self.start_ip = self.ip  # where this instruction began (for error messages)
        self.seg_override = None  # set by a prefix, applies to this instruction only
        op = self.fetch8()
        while op in self.SEG_PREFIXES:
            self.seg_override = self.SEG_PREFIXES[op]
            op = self.fetch8()
        handler = self.ops.get(op)
        if handler is None:
            raw = bytes(self.mem[self.addr(self.s[self.CS], self.start_ip):][:8]).hex(" ")
            raise NotImplementedError(
                f"opcode {op:02X} at {self.s[self.CS]:04X}:{self.start_ip:04X}: {raw}")
        handler(op)

    # --- instructions: each takes the opcode byte (groups use its low 3 bits) ---
    def nop(self, op):
        """90: do nothing."""
        pass

    def mov_rm16_r16(self, op):
        """89 /r: MOV r/m16, r16 - copy a 16-bit register into a register or memory."""
        m = modrm.decode(self, self.seg_override)
        m.write16(self, self.r[m.reg])

    def mov_r16_rm16(self, op):
        """8B /r: MOV r16, r/m16 - copy a register or memory word into a 16-bit register."""
        m = modrm.decode(self, self.seg_override)
        self.r[m.reg] = m.read16(self)

    def mov_rm16_sreg(self, op):
        """8C /r: MOV r/m16, sreg - copy a segment register (ModRM reg field) into a register or memory."""
        m = modrm.decode(self, self.seg_override)
        if m.reg > 3:  # the reg field only names ES, CS, SS, DS here
            raise NotImplementedError(f"MOV r/m16, sreg with reg={m.reg} at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        m.write16(self, self.s[m.reg])

    def mov_sreg_rm16(self, op):
        """8E /r: MOV sreg, r/m16 - load a segment register (not CS) from a register or memory."""
        m = modrm.decode(self, self.seg_override)
        if m.reg > 3 or m.reg == self.CS:  # loading CS this way is not a valid instruction
            raise NotImplementedError(f"MOV sreg, r/m16 with reg={m.reg} at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        self.s[m.reg] = m.read16(self)

    def moffs(self):
        """Read the 16-bit direct address that follows an A0..A3 opcode; return (segment value, offset)."""
        off = self.fetch16()
        # Data lives in DS unless a prefix (2E, 26, ...) picked another segment. Compare with
        # None, not truthiness: ES is register number 0.
        seg = self.DS if self.seg_override is None else self.seg_override
        return self.s[seg], off

    def mov_al_moffs8(self, op):
        """A0: MOV AL, [moffs8] - load AL from the byte at the direct address."""
        seg, off = self.moffs()
        self.set8(0, self.rb(seg, off))  # 8-bit register 0 is AL

    def mov_ax_moffs16(self, op):
        """A1: MOV AX, [moffs16] - load AX from the word at the direct address."""
        seg, off = self.moffs()
        self.r[self.AX] = self.rw(seg, off)

    def mov_moffs8_al(self, op):
        """A2: MOV [moffs8], AL - store AL at the direct address."""
        seg, off = self.moffs()
        self.wb(seg, off, self.get8(0))

    def mov_moffs16_ax(self, op):
        """A3: MOV [moffs16], AX - store AX at the direct address."""
        seg, off = self.moffs()
        self.ww(seg, off, self.r[self.AX])

    def mov_r8_imm8(self, op):
        """B0..B7: MOV r8, imm8 - load an immediate byte into AL..BH (register = low 3 bits of op)."""
        self.set8(op - Op.MOV_R8_IMM8, self.fetch8())

    def mov_r16_imm16(self, op):
        """B8..BF: MOV r16, imm16 - load an immediate word into AX..DI (register = low 3 bits of op)."""
        self.r[op - Op.MOV_R16_IMM16] = self.fetch16()

    def call_near(self, op):
        """E8: CALL rel16 - push the return address (the next instruction), then jump."""
        d = self.fetch16()  # fetching first leaves IP pointing at the next instruction
        self.push16(self.ip)
        # the displacement is signed and counted from the next instruction
        self.ip = (self.ip + d - (0x10000 if d & 0x8000 else 0)) & 0xFFFF

    def ret_near(self, op):
        """C3: RET - pop the return address into IP."""
        self.ip = self.pop16()

    def jmp_short(self, op):
        """EB: JMP rel8 - jump by a signed 8-bit displacement relative to the next instruction."""
        d = self.fetch8()
        self.ip = (self.ip + d - (256 if d & 0x80 else 0)) & 0xFFFF

    def int_(self, op):
        """CD: INT imm8 - call the Python-side handler registered for that interrupt number."""
        n = self.fetch8()
        h = self.int_handlers.get(n)
        if h is None:
            raise NotImplementedError(f"INT {n:02X} at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        h(self)

    def hlt(self, op):
        """F4: HLT - stop the program with exit code 0."""
        raise Exit(0)

    def run(self):
        """Step until the program exits; return its exit code."""
        try:
            while True:
                self.step()
        except Exit as e:
            return e.code
