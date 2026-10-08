from . import modrm
from .opcodes import Op


class Exit(Exception):
    """Raised to stop the program; code is the DOS exit code."""

    def __init__(self, code):
        self.code = code


class CPU:
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
        pass

    def mov_rm16_r16(self, op):
        m = modrm.decode(self, self.seg_override)
        m.write16(self, self.r[m.reg])

    def mov_r8_imm8(self, op):
        self.set8(op - Op.MOV_R8_IMM8, self.fetch8())

    def mov_r16_imm16(self, op):
        self.r[op - Op.MOV_R16_IMM16] = self.fetch16()

    def jmp_short(self, op):
        d = self.fetch8()
        self.ip = (self.ip + d - (256 if d & 0x80 else 0)) & 0xFFFF

    def int_(self, op):
        n = self.fetch8()
        h = self.int_handlers.get(n)
        if h is None:
            raise NotImplementedError(f"INT {n:02X} at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        h(self)

    def hlt(self, op):
        raise Exit(0)

    def run(self):
        """Step until the program exits; return its exit code."""
        try:
            while True:
                self.step()
        except Exit as e:
            return e.code
