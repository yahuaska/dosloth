from . import flags, modrm
from .opcodes import Alu, Op


class Exit(Exception):
    """Raised to stop the emulated program. It carries the DOS exit code the program asked for."""

    def __init__(self, code):
        """Remember the exit code so that run() can return it."""
        self.code = code


class CPU:
    """A tiny 8086-style CPU running in real mode, with 1 MB of flat memory.

    Registers live in two lists: self.r (AX, CX, DX, BX, SP, BP, SI, DI) and self.s (ES, CS, SS, DS).
    step() reads one instruction and looks its opcode up in self.ops; every entry there is one of
    the instruction methods further down.
    """
    # register numbers, available as CPU.AX, CPU.DS, ...
    from .regs import AX, CX, DX, BX, SP, BP, SI, DI, ES, CS, SS, DS

    # segment-override prefix byte -> segment register
    SEG_PREFIXES = {Op.ES_PREFIX: ES, Op.CS_PREFIX: CS, Op.SS_PREFIX: SS, Op.DS_PREFIX: DS}

    def __init__(self):
        """Make a fresh machine: 1 MB of zeroed memory, zeroed registers, no interrupts installed."""
        self.mem = bytearray(1024 * 1024)
        self.r = [0] * 8
        self.s = [0] * 4
        self.ip = 0
        self.flags = 0x0002
        self.int_handlers = {}  # int number -> fn(cpu)
        self.start_ip = 0  # set by step(): IP of the instruction being executed
        self.seg_override = None  # set by step(): segment register from a prefix, or None
        self.rep = None  # set by step(): the repeat prefix (Op.REPNE_PREFIX) before this instruction, or None

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
            Op.PUSH_ES: self.push_sreg,
            Op.PUSH_CS: self.push_sreg,
            Op.PUSH_SS: self.push_sreg,
            Op.PUSH_DS: self.push_sreg,
            Op.POP_ES: self.pop_sreg,
            Op.POP_SS: self.pop_sreg,
            Op.POP_DS: self.pop_sreg,
            Op.SCASB: self.scas,
            Op.SCASW: self.scas,
            Op.LOOPNE: self.loopne,
            Op.LOOPE: self.loope,
            Op.LOOP: self.loop,
            Op.JCXZ: self.jcxz,
            Op.CALL_NEAR: self.call_near,
            Op.RET_NEAR: self.ret_near,
            Op.JMP_SHORT: self.jmp_short,
            Op.INT: self.int_,
            Op.HLT: self.hlt,
            Op.CLC: self.clc,
            Op.STC: self.stc,
            Op.CLI: self.cli,
            Op.STI: self.sti,
            Op.CLD: self.cld,
            Op.STD: self.std,
        }

        # Arithmetic/logic operations: operation -> fn(a, b, bits) that sets the flags and returns the
        # result. Each one gets its whole block of 6 opcodes (see the Alu enum).
        self.alu_ops = {
            Alu.XOR: self.alu_xor,
            Alu.CMP: self.alu_cmp,
        }
        for operation in self.alu_ops:
            for form in range(6):
                self.ops[(operation << 3) | form] = self.alu

        for cc in range(16):
            self.ops[Op.JCC + cc] = self.jcc

        for i in range(8):
            self.ops[Op.INC_R16 + i] = self.inc_r16
            self.ops[Op.DEC_R16 + i] = self.dec_r16
            self.ops[Op.PUSH_R16 + i] = self.push_r16
            self.ops[Op.POP_R16 + i] = self.pop_r16
            self.ops[Op.MOV_R8_IMM8 + i] = self.mov_r8_imm8
            self.ops[Op.MOV_R16_IMM16 + i] = self.mov_r16_imm16

    # --- memory ---
    def addr(self, seg, off):
        """Turn a segment:offset pair into a real memory address.

        The segment is multiplied by 16 and the offset is added. The result wraps around at 1 MB
        (20 bits), like on a real 8086.
        """
        return (seg * 16 + off) & 0xFFFFF

    def rb(self, seg, off):
        """Read one byte of memory at seg:off."""
        return self.mem[self.addr(seg, off)]

    def rw(self, seg, off):
        """Read a 16-bit word at seg:off. The x86 keeps the low byte first (little-endian)."""
        return self.rb(seg, off) | self.rb(seg, off + 1) << 8

    def wb(self, seg, off, val):
        """Write one byte to memory at seg:off. Bits above the low 8 of val are dropped."""
        self.mem[self.addr(seg, off)] = val & 0xFF

    def ww(self, seg, off, val):
        """Write a 16-bit word at seg:off, low byte first."""
        self.wb(seg, off, val)
        self.wb(seg, off + 1, val >> 8)

    # --- 8-bit register access: 0-3 = AL CL DL BL, 4-7 = AH CH DH BH ---
    def get8(self, n):
        """Read an 8-bit register by its x86 number.

        0-3 are AL, CL, DL, BL (the low halves of AX, CX, DX, BX); 4-7 are AH, CH, DH, BH (the high halves).
        """
        v = self.r[n & 3]
        return (v >> 8) & 0xFF if n & 4 else v & 0xFF

    def set8(self, n, val):
        """Write an 8-bit register (numbered as in get8).

        The other half of the 16-bit register keeps its old value.
        """
        i = n & 3
        if n & 4:
            self.r[i] = (self.r[i] & 0x00FF) | (val & 0xFF) << 8
        else:
            self.r[i] = (self.r[i] & 0xFF00) | (val & 0xFF)

    # --- flags (masks are in flags.py) ---
    def get_flag(self, mask):
        """Tell whether a flag is set. Pass one of the masks from flags.py."""
        return bool(self.flags & mask)

    def set_flag(self, mask, on):
        """Turn one flag on or off without touching the others."""
        if on:
            self.flags |= mask
        else:
            self.flags &= ~mask

    def set_szp(self, result, bits):
        """Update three flags from the result of an operation: zero, sign and parity.

        `bits` is the width of the operation, 8 or 16. Sign is the top bit of that width. Parity is an
        x86 quirk: it only looks at the low byte, even for a 16-bit result.
        """
        result &= (1 << bits) - 1
        self.set_flag(flags.ZF, result == 0)
        self.set_flag(flags.SF, result >> (bits - 1))
        # parity looks at the low byte only, even for 16-bit results
        self.set_flag(flags.PF, bin(result & 0xFF).count("1") % 2 == 0)

    def set_logic_flags(self, result, bits):
        """Set the flags the way AND, OR, XOR and TEST do.

        Those operations can never carry or overflow, so CF and OF are cleared; the remaining flags
        describe the result.
        """
        self.set_flag(flags.CF, False)
        self.set_flag(flags.OF, False)
        self.set_szp(result, bits)

    def fetch_rel8(self):
        """Read the one-byte jump distance after an opcode and return the address it points to.

        The byte is a signed number (-128..127) counted from the end of the instruction, so the
        address is worked out after the byte has been read. Jumps that may or may not be taken
        read the byte first and then decide whether to go there.
        """
        d = self.fetch8()
        return (self.ip + d - (256 if d & 0x80 else 0)) & 0xFFFF

    def add_with_flags(self, a, b, bits):
        """Compute a + b like the CPU's ADD does: set the flags and return the result.

        CF means the sum did not fit and a 1 was carried out of the top bit. OF means the result is
        wrong when a and b are read as signed numbers (two positives gave a negative or the other
        way round). AF is the carry out of bit 3, used by BCD code.
        """
        mask = (1 << bits) - 1
        a &= mask
        b &= mask
        result = (a + b) & mask
        sign = 1 << (bits - 1)
        self.set_flag(flags.CF, a + b > mask)
        self.set_flag(flags.OF, bool(~(a ^ b) & (a ^ result) & sign))
        self.set_flag(flags.AF, bool((a ^ b ^ result) & 0x10))
        self.set_szp(result, bits)
        return result

    def condition(self, cc):
        """Tell whether condition number cc (0-15, the low 4 bits of a 70-7F opcode) holds.

        The conditions come in pairs that are each other's opposite: an even number tests something
        and the odd number after it tests that it is not so. The tested things are:
            0 overflow (OF)            4 negative result (SF)
            1 below, unsigned (CF)     5 even parity (PF)
            2 equal (ZF)               6 less, signed (SF differs from OF)
            3 below or equal, unsigned 7 less or equal, signed
              (CF or ZF)                 (ZF, or SF differs from OF)
        """
        cf = self.get_flag(flags.CF)
        zf = self.get_flag(flags.ZF)
        sf = self.get_flag(flags.SF)
        of = self.get_flag(flags.OF)
        holds = (of, cf, zf, cf or zf, sf, self.get_flag(flags.PF), sf != of, zf or sf != of)[cc >> 1]
        return holds != bool(cc & 1)  # odd numbers are the opposite condition

    def sub_with_flags(self, a, b, bits):
        """Compute a - b like the CPU's SUB and CMP do: set the flags and return the result.

        CF means "a borrow was needed" (a < b as unsigned numbers). OF means the result does not
        fit when a and b are read as signed numbers. AF is the borrow out of bit 3, used by BCD code.
        """
        mask = (1 << bits) - 1
        result = (a - b) & mask
        sign = 1 << (bits - 1)
        self.set_flag(flags.CF, (a & mask) < (b & mask))
        self.set_flag(flags.OF, bool((a ^ b) & (a ^ result) & sign))
        self.set_flag(flags.AF, bool((a ^ b ^ result) & 0x10))
        self.set_szp(result, bits)
        return result

    # --- stack ---
    def push16(self, val):
        """Put a word on the stack: SP goes down by 2, then the word is stored at SS:SP.

        The stack grows toward lower addresses.
        """
        sp = (self.r[self.SP] - 2) & 0xFFFF
        self.r[self.SP] = sp
        self.ww(self.s[self.SS], sp, val)

    def pop16(self):
        """Take a word off the stack: read it from SS:SP, then move SP up by 2."""
        sp = self.r[self.SP]
        val = self.rw(self.s[self.SS], sp)
        self.r[self.SP] = (sp + 2) & 0xFFFF
        return val

    # --- instruction fetch ---
    def fetch8(self):
        """Read the next byte of the program (at CS:IP) and move IP past it."""
        v = self.rb(self.s[self.CS], self.ip)
        self.ip = (self.ip + 1) & 0xFFFF
        return v

    def fetch16(self):
        """Read the next little-endian word of the program and move IP past it."""
        lo = self.fetch8()
        return lo | self.fetch8() << 8

    def step(self):
        """Run one instruction.

        Segment-override prefixes (like 2E for "use CS:") are consumed first and remembered for this
        one instruction. Then the opcode is looked up in self.ops. An opcode we have not implemented
        stops the emulator with an error showing where it happened and the bytes around it, so the
        next thing to implement is always obvious.
        """
        self.start_ip = self.ip  # where this instruction began (for error messages)
        self.seg_override = None  # set by a prefix, applies to this instruction only
        self.rep = None  # same for the repeat prefix
        op = self.fetch8()
        while True:  # prefixes come before the opcode, in any order
            if op in self.SEG_PREFIXES:
                self.seg_override = self.SEG_PREFIXES[op]
            elif op == Op.REPNE_PREFIX:
                self.rep = op
            else:
                break
            op = self.fetch8()
        handler = self.ops.get(op)
        if handler is None:
            raw = bytes(self.mem[self.addr(self.s[self.CS], self.start_ip):][:8]).hex(" ")
            raise NotImplementedError(
                f"opcode {op:02X} at {self.s[self.CS]:04X}:{self.start_ip:04X}: {raw}")
        handler(op)

    # --- instructions: each takes the opcode byte (groups use its low 3 bits) ---
    def nop(self, op):
        """Do nothing (opcode 90)."""
        pass

    def inc_r16(self, op):
        """Add 1 to a 16-bit register (opcodes 40-47; the register is the low 3 bits of the opcode).

        Sets the flags like ADD would, except that CF is left alone: INC is often used for counters in
        the middle of multi-word arithmetic, where the carry from the previous step must survive.
        """
        reg = op - Op.INC_R16
        carry = self.get_flag(flags.CF)
        self.r[reg] = self.add_with_flags(self.r[reg], 1, 16)
        self.set_flag(flags.CF, carry)

    def dec_r16(self, op):
        """Subtract 1 from a 16-bit register (opcodes 48-4F). Like INC, it leaves CF alone."""
        reg = op - Op.DEC_R16
        carry = self.get_flag(flags.CF)
        self.r[reg] = self.sub_with_flags(self.r[reg], 1, 16)
        self.set_flag(flags.CF, carry)

    def push_r16(self, op):
        """Push a 16-bit register onto the stack (opcodes 50-57).

        The register is the low 3 bits of the opcode, in the usual order: AX, CX, DX, BX, SP, BP, SI,
        DI. PUSH SP stores the value SP had before the push, like 286 and newer CPUs do (the 8086
        stored the already decreased value).
        """
        self.push16(self.r[op - Op.PUSH_R16])

    def pop_r16(self, op):
        """Pop a word from the stack into a 16-bit register (opcodes 58-5F).

        The register is chosen the same way as in PUSH.
        """
        self.r[op - Op.POP_R16] = self.pop16()

    def push_sreg(self, op):
        """Push a segment register onto the stack (opcodes 06, 0E, 16, 1E for ES, CS, SS, DS).

        Which register is encoded in bits 3-4 of the opcode.
        """
        self.push16(self.s[(op >> 3) & 3])

    def pop_sreg(self, op):
        """Pop a word from the stack into ES, SS or DS (opcodes 07, 17, 1F).

        There is no POP CS: 0F, where it would be, is not this instruction.
        """
        self.s[(op >> 3) & 3] = self.pop16()

    def mov_rm16_r16(self, op):
        """Copy a 16-bit register into a register or a word in memory (opcode 89 + ModRM).

        The ModRM byte says where the value goes: another register, or an address like [bx+si+5].
        """
        m = modrm.decode(self, self.seg_override)
        m.write16(self, self.r[m.reg])

    def mov_r16_rm16(self, op):
        """Copy a register or a word in memory into a 16-bit register (opcode 8B + ModRM).

        The same as opcode 89 with the direction reversed.
        """
        m = modrm.decode(self, self.seg_override)
        self.r[m.reg] = m.read16(self)

    def mov_rm16_sreg(self, op):
        """Copy a segment register into a register or memory (opcode 8C + ModRM).

        The segment register is picked by the 'reg' field of the ModRM byte: 0=ES, 1=CS, 2=SS, 3=DS.
        Values 4-7 mean nothing here, so they stop the emulator.
        """
        m = modrm.decode(self, self.seg_override)
        if m.reg > 3:  # the reg field only names ES, CS, SS, DS here
            raise NotImplementedError(f"MOV r/m16, sreg with reg={m.reg} at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        m.write16(self, self.s[m.reg])

    def mov_sreg_rm16(self, op):
        """Load a segment register from a register or memory (opcode 8E + ModRM).

        This is how a program points DS at its data: `mov ax, <segment>` and then `mov ds, ax`, because
        a constant cannot be moved into a segment register directly. Loading CS this way is not
        allowed on the x86, so that stops the emulator too.
        """
        m = modrm.decode(self, self.seg_override)
        if m.reg > 3 or m.reg == self.CS:  # loading CS this way is not a valid instruction
            raise NotImplementedError(f"MOV sreg, r/m16 with reg={m.reg} at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        self.s[m.reg] = m.read16(self)

    def moffs(self):
        """Read the fixed address that follows the A0-A3 opcodes and say where it points.

        Returns (segment value, offset). The segment is DS unless a prefix chose another one.
        """
        off = self.fetch16()
        # Data lives in DS unless a prefix (2E, 26, ...) picked another segment. Compare with
        # None, not truthiness: ES is register number 0.
        seg = self.DS if self.seg_override is None else self.seg_override
        return self.s[seg], off

    def mov_al_moffs8(self, op):
        """Load AL from the byte at a fixed address (opcode A0 + 16-bit offset)."""
        seg, off = self.moffs()
        self.set8(0, self.rb(seg, off))  # 8-bit register 0 is AL

    def mov_ax_moffs16(self, op):
        """Load AX from the word at a fixed address (opcode A1 + 16-bit offset).

        A shorter encoding of the general MOV for the case where the register is AX.
        """
        seg, off = self.moffs()
        self.r[self.AX] = self.rw(seg, off)

    def mov_moffs8_al(self, op):
        """Store AL at a fixed address (opcode A2 + 16-bit offset)."""
        seg, off = self.moffs()
        self.wb(seg, off, self.get8(0))

    def mov_moffs16_ax(self, op):
        """Store AX at a fixed address (opcode A3 + 16-bit offset).

        Compilers like it for writing global variables.
        """
        seg, off = self.moffs()
        self.ww(seg, off, self.r[self.AX])

    def mov_r8_imm8(self, op):
        """Put a constant into an 8-bit register (opcodes B0-B7).

        The register is the low 3 bits of the opcode: AL, CL, DL, BL, AH, CH, DH, BH.
        """
        self.set8(op - Op.MOV_R8_IMM8, self.fetch8())

    def mov_r16_imm16(self, op):
        """Put a constant into a 16-bit register (opcodes B8-BF).

        The register is the low 3 bits of the opcode: AX, CX, DX, BX, SP, BP, SI, DI.
        """
        self.r[op - Op.MOV_R16_IMM16] = self.fetch16()

    def alu(self, op):
        """Run one of the arithmetic/logic instructions in opcodes 00-3D (XOR so far).

        The opcode says which operation (bits 3-5) and where the operands come from (bits 0-2): a
        register and a register-or-memory operand given by a ModRM byte, in either direction, or
        AL/AX and a constant that follows the opcode. See the Alu enum for the table. The result goes
        into the first operand, the one written first in assembly.
        """
        fn = self.alu_ops[op >> 3]
        form = op & 7
        if form <= 3:
            m = modrm.decode(self, self.seg_override)
        if form == 0:  # r/m8, r8
            m.write8(self, fn(m.read8(self), self.get8(m.reg), 8))
        elif form == 1:  # r/m16, r16
            m.write16(self, fn(m.read16(self), self.r[m.reg], 16))
        elif form == 2:  # r8, r/m8
            self.set8(m.reg, fn(self.get8(m.reg), m.read8(self), 8))
        elif form == 3:  # r16, r/m16
            self.r[m.reg] = fn(self.r[m.reg], m.read16(self), 16)
        elif form == 4:  # AL, imm8 (8-bit register 0 is AL)
            self.set8(0, fn(self.get8(0), self.fetch8(), 8))
        else:  # form 5: AX, imm16
            self.r[self.AX] = fn(self.r[self.AX], self.fetch16(), 16)

    def alu_cmp(self, a, b, bits):
        """CMP: subtract b from a only to get the flags, and throw the result away.

        The flags then tell how a relates to b: equal (ZF), smaller as an unsigned number (CF),
        smaller as a signed number (SF differs from OF). A conditional jump usually follows.

        alu() stores whatever we return into the first operand. CMP must leave it unchanged, so it
        returns a itself: writing back the value that was already there changes nothing.
        """
        self.sub_with_flags(a, b, bits)
        return a

    def alu_xor(self, a, b, bits):
        """XOR: flip every bit of a that is 1 in b.

        Most often seen as `xor ax, ax` (33 C0): XOR-ing a value with itself always gives 0, so it is
        the short way to clear a register.
        """
        result = a ^ b
        self.set_logic_flags(result, bits)
        return result

    def scas(self, op):
        """Compare AL (opcode AE) or AX (opcode AF) with the byte or word at ES:DI, then step DI.

        The comparison is a subtraction that only sets flags, like CMP. DI moves forward, or backward
        if the direction flag is set (see CLD and STD). The segment is always ES, prefixes cannot
        change it.

        With the REPNE prefix (F2) this repeats while CX is not zero, taking 1 off CX each time, and
        stops as soon as the values are equal. The classic use is measuring a zero-terminated string:
        AL = 0, CX = 0FFFFh, `repne scasb`, and the length can be worked out from what is left in CX.
        """
        bits = 16 if op & 1 else 8
        size = bits // 8
        step = -size if self.get_flag(flags.DF) else size
        while True:
            if self.rep is not None and self.r[self.CX] == 0:
                break  # nothing (more) to repeat
            if bits == 16:
                self.sub_with_flags(self.r[self.AX], self.rw(self.s[self.ES], self.r[self.DI]), 16)
            else:
                self.sub_with_flags(self.get8(0), self.rb(self.s[self.ES], self.r[self.DI]), 8)  # 0 is AL
            self.r[self.DI] = (self.r[self.DI] + step) & 0xFFFF
            if self.rep is None:
                break  # no prefix: just one comparison
            self.r[self.CX] -= 1
            if self.get_flag(flags.ZF):
                break  # REPNE stops on the first match

    def loop(self, op):
        """Repeat a block of code CX times (opcode E2 + one signed byte).

        Takes 1 off CX and jumps back to the start of the block unless CX has reached zero. Mind
        that CX is decreased first: with CX = 0 on entry it wraps to FFFFh and the loop runs 65536
        times. The flags are not touched.
        """
        target = self.fetch_rel8()
        self.r[self.CX] = (self.r[self.CX] - 1) & 0xFFFF
        if self.r[self.CX] != 0:
            self.ip = target

    def loope(self, op):
        """Like LOOP, but also stops as soon as ZF is clear (opcode E1; alias LOOPZ).

        Handy for "repeat while still equal": the loop body compares something and sets ZF.
        """
        target = self.fetch_rel8()
        self.r[self.CX] = (self.r[self.CX] - 1) & 0xFFFF
        if self.r[self.CX] != 0 and self.get_flag(flags.ZF):
            self.ip = target

    def loopne(self, op):
        """Like LOOP, but also stops as soon as ZF is set (opcode E0; alias LOOPNZ).

        Handy for "repeat until a match is found".
        """
        target = self.fetch_rel8()
        self.r[self.CX] = (self.r[self.CX] - 1) & 0xFFFF
        if self.r[self.CX] != 0 and not self.get_flag(flags.ZF):
            self.ip = target

    def jcxz(self, op):
        """Jump if CX is zero (opcode E3 + one signed byte). CX is not changed.

        Placed before a LOOP to skip the whole loop when there is nothing to repeat.
        """
        target = self.fetch_rel8()
        if self.r[self.CX] == 0:
            self.ip = target

    def call_near(self, op):
        """Call a procedure in the same code segment (opcode E8 + 16-bit displacement).

        The address of the next instruction is pushed on the stack so that RET can come back to it,
        then execution jumps to that address plus the displacement. The displacement is signed, so a
        call can go backwards too.
        """
        d = self.fetch16()  # fetching first leaves IP pointing at the next instruction
        self.push16(self.ip)
        # the displacement is signed and counted from the next instruction
        self.ip = (self.ip + d - (0x10000 if d & 0x8000 else 0)) & 0xFFFF

    def ret_near(self, op):
        """Return from a procedure: pop the saved address off the stack into IP (opcode C3)."""
        self.ip = self.pop16()

    def jcc(self, op):
        """Jump a short distance only if a condition on the flags holds (opcodes 70-7F + one signed byte).

        These usually follow CMP (or any instruction that sets flags). The sixteen opcodes and the
        names assemblers give them:
            70 JO          74 JE / JZ       78 JS       7C JL / JNGE
            71 JNO         75 JNE / JNZ     79 JNS      7D JGE / JNL
            72 JB / JC     76 JBE / JNA     7A JP       7E JLE / JNG
            73 JAE / JNC   77 JA / JNBE     7B JNP      7F JG / JNLE
        "Below"/"above" compare unsigned numbers; "less"/"greater" compare signed ones.
        The displacement is read even when the jump is not taken, to get past it.
        """
        target = self.fetch_rel8()
        if self.condition(op & 0xF):
            self.ip = target

    def jmp_short(self, op):
        """Jump a short distance (opcode EB + one signed byte).

        The byte is added to the address of the next instruction, so the jump reaches 128 bytes back
        or 127 bytes forward.
        """
        self.ip = self.fetch_rel8()

    def int_(self, op):
        """Call an interrupt (opcode CD + the interrupt number).

        Here the interrupts are Python functions registered in self.int_handlers (see dos.py), not
        code in the emulated memory. An interrupt nobody registered stops the emulator.
        """
        n = self.fetch8()
        h = self.int_handlers.get(n)
        if h is None:
            raise NotImplementedError(f"INT {n:02X} at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        h(self)

    def hlt(self, op):
        """Stop the CPU (opcode F4). We treat it as the end of the program, with exit code 0."""
        raise Exit(0)

    def clc(self, op):
        """Clear the carry flag (opcode F8)."""
        self.set_flag(flags.CF, False)

    def stc(self, op):
        """Set the carry flag (opcode F9)."""
        self.set_flag(flags.CF, True)

    def cli(self, op):
        """Clear the interrupt flag (opcode FA): "don't interrupt me".

        We have no hardware interrupts yet, so for now this only changes the flag.
        """
        self.set_flag(flags.IF, False)

    def sti(self, op):
        """Set the interrupt flag (opcode FB): hardware interrupts are allowed again."""
        self.set_flag(flags.IF, True)

    def cld(self, op):
        """Clear the direction flag (opcode FC): string instructions will walk forward through memory."""
        self.set_flag(flags.DF, False)

    def std(self, op):
        """Set the direction flag (opcode FD): string instructions will walk backward through memory."""
        self.set_flag(flags.DF, True)

    def run(self):
        """Keep executing instructions until the program exits, then return its exit code."""
        try:
            while True:
                self.step()
        except Exit as e:
            return e.code
