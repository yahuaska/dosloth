"""The 32-bit instructions of the 386, in the part of the machine that already is: real mode and 16-bit addresses.

A 386 has registers of 32 bits, EAX, ECX, EDX, EBX, ESP, EBP, ESI, EDI. The 16 bits that a program of the 8086 knows
are their low halves, and they are what `CPU.r` holds; what is above them is kept apart, in `CPU.rh`, as the high
halves of the same registers. So the 16-bit programs, and all the code written for them, do not know of the high
halves: a write to AX leaves what is above it as it was, as on the real chip.

An instruction works on 32 bits when the prefix 66h is in front of it (as `66 89 C8`, `mov eax, ecx`: the prefix of
the operand size). The prefix is a flag of the instruction, `CPU.osize32`, and the instructions that have a 32-bit form
are in a table of their own, `CPU.ops32`, which step() looks in when the flag is set. A 66h in front of an opcode that
is not in it stops the emulator, so a 32-bit instruction that is not done yet is never taken for the 16-bit one.

The addresses are of 32 bits when the prefix 67h is in front of the instruction (`CPU.asize32`, see modrm.py for the
SIB byte). In a code segment of 32 bits (`CPU.code32`) both sizes are 32 by default, and the prefixes make them 16.

What is here: the data moves and the arithmetic with registers, memory and constants, the stack (PUSH, POP, PUSHA,
PUSHFD, ENTER, LEAVE), INC and DEC, XCHG, TEST, LEA, the sign extensions CWDE and CDQ, the shifts, NOT, NEG, MUL, DIV,
the three-operand IMUL, the near jumps, calls and returns, and the string instructions, the loops and XLAT with the
registers that 32-bit addresses call for. Not yet: the two-byte opcodes (0Fh) of the 386, the other registers
(FS and GS), and what the flat mode needs: the instruction pointer and the stack pointer are still of 16 bits, so a
near jump is cut to 16 bits.
"""
from . import flags, modrm
from .opcodes import Alu, Op

# the alignment check flag, bit 18 of EFLAGS, as it is kept in CPU.eflags_high (the bits from the 16th up, shifted down)
AC = 0x4


def signed(value, bits):
    """Read `value` (an unsigned number of `bits` bits) as a two's complement signed number."""
    return value - (1 << bits) if value >> (bits - 1) else value


class Ops32:
    """The part of the CPU that has the 32-bit instructions. CPU is made of it and of the 16-bit instructions."""

    # --- the 32-bit registers, and 32-bit words of memory ---
    def get32(self, n):
        """Read register number n as a 32-bit register (EAX..EDI)."""
        return self.rh[n] << 16 | self.r[n]

    def set32(self, n, value):
        """Write register number n as a 32-bit register."""
        self.r[n] = value & 0xFFFF
        self.rh[n] = (value >> 16) & 0xFFFF

    def rd(self, seg, off):
        """Read a 32-bit doubleword at seg:off, low word first."""
        return self.rw(seg, off) | self.rw(seg, off + 2) << 16

    def wd(self, seg, off, val):
        """Write a 32-bit doubleword at seg:off, low word first."""
        self.ww(seg, off, val)
        self.ww(seg, off + 2, val >> 16)

    def fetch32(self):
        """Read the next little-endian doubleword of the program and move IP past it."""
        lo = self.fetch16()
        return lo | self.fetch16() << 16

    def push32(self, val):
        """Put a doubleword on the stack: SP goes down by 4, then the doubleword is stored at SS:SP."""
        sp = (self.r[self.SP] - 4) & 0xFFFF
        self.r[self.SP] = sp
        self.wd(self.s[self.SS], sp, val)

    def pop32(self):
        """Take a doubleword off the stack: read it from SS:SP, then move SP up by 4."""
        sp = self.r[self.SP]
        val = self.rd(self.s[self.SS], sp)
        self.r[self.SP] = (sp + 4) & 0xFFFF
        return val

    # --- the instructions ---
    def inc_dec_r32(self, op):
        """Add 1 to a 32-bit register (opcodes 40-47), or take 1 off it (48-4F). CF is left alone, as for 16 bits."""
        reg = op & 7
        self.set32(reg, self.inc_dec(self.get32(reg), 32, decrement=op >= Op.DEC_R16))

    def push_r32(self, op):
        """Push a 32-bit register (opcodes 50-57). PUSH ESP stores the value ESP had before the push."""
        self.push32(self.get32(op - Op.PUSH_R16))

    def pop_r32(self, op):
        """Pop a doubleword into a 32-bit register (opcodes 58-5F)."""
        self.set32(op - Op.POP_R16, self.pop32())

    def pushfd(self, op):
        """Put EFLAGS on the stack (66 9C). What a 486 has above the 16 flags is its alignment check flag only: the
        virtual-8086 and resume flags are pushed as 0, and so is the ID flag, which a 486 with no CPUID has not."""
        self.push32(self.flags | self.eflags_high << 16)

    def popfd(self, op):
        """Take a doubleword off the stack into EFLAGS (66 9D).

        The 16 flags are taken as POPF takes them. Of the bits above, only the alignment check flag is kept (the bit
        that a program toggles to tell a 486 from a 386); the ID flag cannot be set, which is how a program learns
        that there is no CPUID instruction.
        """
        value = self.pop32()
        self.flags = (value & flags.DEFINED) | flags.ALWAYS_ONE
        self.eflags_high = (value >> 16) & AC

    def alu_acc32(self, op):
        """An operation on EAX and a constant of 32 bits after the opcode (the forms 5 of the block: 05, 0D, ... 3D)."""
        self.set32(self.AX, self.alu_ops[op >> 3](self.get32(self.AX), self.fetch32(), 32))

    def alu_imm32(self, op):
        """The operations with a constant on a register or memory operand of 32 bits (opcodes 81 and 83).

        81 has the constant of 32 bits after the ModRM bytes. 83 has one byte, which is taken as a signed number and
        widened to 32 bits.
        """
        m = modrm.decode(self, self.seg_override)
        fn = self.alu_ops[m.reg]
        if op == Op.ALU_RM16_IMM16:
            imm = self.fetch32()
        else:
            imm = self.fetch8()
            if imm & 0x80:
                imm |= 0xFFFFFF00
        m.write(self, 32, fn(m.read(self, 32), imm, 32))

    def mov_r32_imm32(self, op):
        """Put a constant of 32 bits into a 32-bit register (opcodes B8-BF)."""
        self.set32(op - Op.MOV_R16_IMM16, self.fetch32())

    def mov_eax_moffs(self, op):
        """Load EAX from the doubleword at a fixed address (A1), or store EAX there (A3)."""
        seg, off = self.moffs()
        if op == Op.MOV_AX_MOFFS16:
            self.set32(self.AX, self.rd(seg, off))
        else:
            self.wd(seg, off, self.get32(self.AX))

    def mov_rm_imm32(self, op):
        """Put a constant of 32 bits into a register or into memory (opcode C7 + ModRM, whose 'reg' field is 0)."""
        m = modrm.decode(self, self.seg_override)
        if m.reg != 0:
            raise NotImplementedError(f"opcode 66 {op:02X} /{m.reg} at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        m.write(self, 32, self.fetch32())

    def lea32(self, op):
        """The address of a memory operand into a 32-bit register (66 8D). A 16-bit address is widened with zeros."""
        m = modrm.decode(self, self.seg_override)
        if m.is_reg:
            raise NotImplementedError(f"LEA with a register at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        self.set32(m.reg, m.off)

    def test_eax_imm32(self, op):
        """Find out which bits EAX has in common with a constant of 32 bits (A9), and keep only the flags."""
        self.set_logic_flags(self.get32(self.AX) & self.fetch32(), 32)

    def xchg_eax_r32(self, op):
        """Swap EAX with another 32-bit register (opcodes 91-97)."""
        reg = op - Op.XCHG_AX_R16
        a, b = self.get32(self.AX), self.get32(reg)
        self.set32(self.AX, b)
        self.set32(reg, a)

    def cwde(self, op):
        """Widen the sign of AX into the high half of EAX (66 98): AX is 8000h or more, the high half is FFFFh."""
        self.rh[self.AX] = 0xFFFF if self.r[self.AX] & 0x8000 else 0

    def cdq(self, op):
        """Widen the sign of EAX into EDX (66 99), as CWD does AX into DX:AX; the first step of a signed division."""
        self.set32(self.DX, 0xFFFFFFFF if self.rh[self.AX] & 0x8000 else 0)

    # --- the table ---
    # --- the stack and the frames of procedures ---
    def push_imm32(self, op):
        """Push a constant of 32 bits (68), or a byte widened to 32 bits (6A: 6A FE pushes FFFFFFFEh)."""
        if op == Op.PUSH_IMM16:
            self.push32(self.fetch32())
        else:
            d = self.fetch8()
            self.push32((d - 256 if d & 0x80 else d) & 0xFFFFFFFF)

    def pusha32(self, op):
        """Push the eight 32-bit registers (66 60): EAX, ECX, EDX, EBX, the ESP from before, EBP, ESI, EDI."""
        original_sp = self.get32(self.SP)
        for reg in (self.AX, self.CX, self.DX, self.BX):
            self.push32(self.get32(reg))
        self.push32(original_sp)
        for reg in (self.BP, self.SI, self.DI):
            self.push32(self.get32(reg))

    def popa32(self, op):
        """Take the eight 32-bit registers back (66 61): EDI, ESI, EBP, the saved ESP is thrown away, EBX ... EAX."""
        for reg in (self.DI, self.SI, self.BP):
            self.set32(reg, self.pop32())
        self.pop32()
        for reg in (self.BX, self.DX, self.CX, self.AX):
            self.set32(reg, self.pop32())

    def pop_rm32(self, op):
        """Take a doubleword off the stack into a register or into memory (66 8F /0)."""
        m = modrm.decode(self, self.seg_override)
        if m.reg != 0:
            raise NotImplementedError(f"opcode 66 8F /{m.reg} at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        m.write(self, 32, self.pop32())

    def enter32(self, op):
        """Build the stack frame of a procedure with doublewords (66 C8): see CPU.enter. The frame pointers of the
        outer procedures are 4 bytes apart."""
        size = self.fetch16()
        level = self.fetch8() & 0x1F
        self.push32(self.get32(self.BP))
        frame = self.get32(self.SP)
        for _ in range(level - 1):
            self.set32(self.BP, self.get32(self.BP) - 4)
            self.push32(self.rd(self.s[self.SS], self.r[self.BP]))
        if level:
            self.push32(frame)
        self.set32(self.BP, frame)
        self.r[self.SP] = (self.r[self.SP] - size) & 0xFFFF

    def leave32(self, op):
        """Tear down a frame of doublewords (66 C9): SP <- BP, then EBP <- a doubleword off the stack."""
        self.r[self.SP] = self.r[self.BP]
        self.set32(self.BP, self.pop32())

    # --- jumps, calls and returns: the distance and the stack have 32 bits, the instruction pointer has not yet
    def fetch_rel32(self):
        """Read the distance of 32 bits after an opcode and return the address it points to, cut to 16 bits."""
        d = self.fetch32()
        return (self.ip + d) & 0xFFFF

    def call_near32(self, op):
        """Call a procedure (66 E8 + a distance of 32 bits): the return address goes on the stack as a doubleword."""
        target = self.fetch_rel32()
        self.push32(self.ip)
        self.ip = target

    def jmp_near32(self, op):
        """Jump to a place at a distance of 32 bits (66 E9)."""
        self.ip = self.fetch_rel32()

    def ret_near32(self, op):
        """Return from a procedure: the address is a doubleword (66 C3)."""
        self.ip = self.pop32() & 0xFFFF

    def ret_near_pop32(self, op):
        """Return from a procedure and drop its arguments (66 C2 + imm16): the address is a doubleword."""
        count = self.fetch16()
        self.ip = self.pop32() & 0xFFFF
        self.r[self.SP] = (self.r[self.SP] + count) & 0xFFFF

    def group5_rm32(self, op):
        """INC, DEC, CALL, JMP and PUSH of a 32-bit register or memory operand (66 FF, by the 'reg' field).

        The far forms (CALL FAR and JMP FAR, 3 and 5) take a pointer of 16:32, which needs selectors: they stop.
        """
        m = modrm.decode(self, self.seg_override)
        if m.reg in (0, 1):
            m.write(self, 32, self.inc_dec(m.read(self, 32), 32, decrement=bool(m.reg)))
        elif m.reg == 2:  # read the operand before pushing: it may be addressed through SP
            target = m.read(self, 32)
            self.push32(self.ip)
            self.ip = target & 0xFFFF
        elif m.reg == 4:
            self.ip = m.read(self, 32) & 0xFFFF
        elif m.reg == 6:  # read first: PUSH ESP pushes the value it had before
            self.push32(m.read(self, 32))
        else:
            raise NotImplementedError(f"opcode 66 FF /{m.reg} at {self.s[self.CS]:04X}:{self.start_ip:04X}")

    # --- multiplying with a constant ---
    def imul_imm(self, op):
        """A register <- the r/m operand times a constant, signed (69: the constant is a word or a doubleword, 6B: a
        byte, widened). 80186 and later; the size is that of the operands, 16 or 32. CF and OF say that the product
        does not fit in the register; the other flags are left as they were."""
        m = modrm.decode(self, self.seg_override)
        bits = 32 if self.osize32 else 16
        if op == Op.IMUL_RM_IMM16:
            imm = signed(self.fetch32() if bits == 32 else self.fetch16(), bits)
        else:
            imm = signed(self.fetch8(), 8)
        product = signed(m.read(self, bits), bits) * imm
        self.set_reg(m.reg, bits, product)
        doesnt_fit = not -(1 << (bits - 1)) <= product < 1 << (bits - 1)
        self.set_flag(flags.CF, doesnt_fit)
        self.set_flag(flags.OF, doesnt_fit)

    # --- the instructions that work with SI, DI, CX and BX as addresses and counters ---
    def string32(self, op):
        """The string instructions A4-A7 and AA-AF, for the sizes and the addresses of any width.

        The operand is a doubleword with the size of 32 bits (an odd opcode), and the registers are ESI, EDI and ECX
        when the addresses are of 32 bits (and SI, DI and CX as in CPU.string_op otherwise). The rest is as there: the
        direction flag, REP counting down the counter, REPE and REPNE stopping the comparing ones on the flag.
        """
        bits = (32 if self.osize32 else 16) if op & 1 else 8
        step = -(bits // 8) if self.get_flag(flags.DF) else bits // 8
        wide = self.asize32
        mask = 0xFFFFFFFF if wide else 0xFFFF
        read = (self.rb, self.rw, self.rd)[bits // 16]
        write = (self.wb, self.ww, self.wd)[bits // 16]
        regs, source, target = self.r, self.SI, self.DI
        counter = self.CX

        def get(n):
            return self.get32(n) if wide else regs[n]

        def put(n, value):
            if wide:
                self.set32(n, value)
            else:
                regs[n] = value & mask

        kind = op & 0xFE
        compares = kind in (0xA6, 0xAE)

        def one():
            if kind == 0xA4:  # movs
                write(self.s[self.ES], get(target), read(self.data_segment(), get(source)))
                put(source, get(source) + step)
                put(target, get(target) + step)
            elif kind == 0xA6:  # cmps
                self.sub_with_flags(read(self.data_segment(), get(source)), read(self.s[self.ES], get(target)), bits)
                put(source, get(source) + step)
                put(target, get(target) + step)
            elif kind == 0xAA:  # stos
                write(self.s[self.ES], get(target), self.get_reg(self.AX, bits))
                put(target, get(target) + step)
            elif kind == 0xAC:  # lods
                self.set_reg(self.AX, bits, read(self.data_segment(), get(source)))
                put(source, get(source) + step)
            else:  # scas
                self.sub_with_flags(self.get_reg(self.AX, bits), read(self.s[self.ES], get(target)), bits)
                put(target, get(target) + step)

        if self.rep is None:
            one()
            return
        while get(counter) != 0:
            one()
            put(counter, get(counter) - 1)
            if compares and (self.rep == Op.REPNE_PREFIX) == self.get_flag(flags.ZF):
                break

    def loop_a32(self, op):
        """LOOPNE, LOOPE, LOOP and JCXZ (E0-E3) with 32-bit addresses: the counter is ECX, and JCXZ is JECXZ."""
        target = self.fetch_rel8()
        if op == Op.JCXZ:
            if self.get32(self.CX) == 0:
                self.ip = target
            return
        count = (self.get32(self.CX) - 1) & 0xFFFFFFFF
        self.set32(self.CX, count)
        zero = self.get_flag(flags.ZF)
        if count != 0 and (op == Op.LOOP or zero == (op == Op.LOOPE)):
            self.ip = target

    def xlat(self, op):
        """AL <- the byte at DS:[BX + AL] (D7): a lookup in a table of bytes. With 32-bit addresses, EBX + AL."""
        if self.asize32:
            off = (self.get32(self.BX) + self.get8(0)) & 0xFFFFFFFF
        else:
            off = (self.r[self.BX] + self.get8(0)) & 0xFFFF
        self.set8(0, self.rb(self.data_segment(), off))

    def build_ops32(self):
        """The table of the opcodes that have a 32-bit form, or that 66h means nothing to: {opcode: handler}."""
        ops, table = self.ops, {}

        def same(*codes):  # the ones that the prefix does not change: the bytes, the jumps, the flags
            for code in codes:
                if code in ops:
                    table[code] = ops[code]

        for operation in Alu:  # 00-3D: the forms 0-4 as they are (they work with `operands`, which knows the size)
            base = operation << 3
            same(*range(base, base + 5))
            table[base + 5] = self.alu_acc32
        same(Op.NOP, Op.CLC, Op.STC, Op.CLI, Op.STI, Op.CLD, Op.STD, Op.CMC, Op.HLT, Op.WAIT, Op.SAHF, Op.LAHF,
             Op.JMP_SHORT, Op.INT, Op.ALU_RM8_IMM8, Op.MOV_RM_REG, Op.MOV_RM_REG + 2, Op.TEST_RM_REG, Op.XCHG_RM_REG,
             Op.MOV_AL_MOFFS8, Op.MOV_MOFFS8_AL, Op.TEST_ACC_IMM, Op.MOV_RM_IMM, *range(Op.JCC, Op.JCC + 16),
             *range(Op.MOV_R8_IMM8, Op.MOV_R8_IMM8 + 8))
        # the word forms of the same instructions: `operands` makes them 32 bits wide, so the handlers are the same
        table[Op.MOV_RM_REG + 1] = ops[Op.MOV_RM_REG + 1]
        table[Op.MOV_RM_REG + 3] = ops[Op.MOV_RM_REG + 3]
        table[Op.TEST_RM_REG + 1] = ops[Op.TEST_RM_REG + 1]
        table[Op.XCHG_RM_REG + 1] = ops[Op.XCHG_RM_REG + 1]
        table[Op.ALU_RM16_IMM16] = table[Op.ALU_RM16_IMM8] = self.alu_imm32
        for i in range(8):
            table[Op.INC_R16 + i] = table[Op.DEC_R16 + i] = self.inc_dec_r32
            table[Op.PUSH_R16 + i] = self.push_r32
            table[Op.POP_R16 + i] = self.pop_r32
            table[Op.MOV_R16_IMM16 + i] = self.mov_r32_imm32
        for i in range(1, 8):
            table[Op.XCHG_AX_R16 + i] = self.xchg_eax_r32
        table[Op.MOV_AX_MOFFS16] = table[Op.MOV_MOFFS16_AX] = self.mov_eax_moffs
        table[Op.MOV_RM_IMM + 1] = self.mov_rm_imm32
        table[Op.LEA] = self.lea32
        table[Op.TEST_ACC_IMM + 1] = self.test_eax_imm32
        table[Op.PUSHF], table[Op.POPF] = self.pushfd, self.popfd
        table[Op.CBW], table[Op.CWD] = self.cwde, self.cdq
        # shifts, NOT, NEG, MUL, DIV and the three-operand IMUL work out their size from the opcode and the prefix
        same(*range(Op.SHIFT_RM, Op.SHIFT_RM + 4), Op.SHIFT_RM_IMM8, Op.SHIFT_RM_IMM8 + 1, Op.GROUP3_RM,
             Op.GROUP3_RM + 1, Op.GROUP4_RM8, Op.IMUL_RM_IMM16, Op.IMUL_RM_IMM8, Op.XLAT,
             *range(Op.LOOPNE, Op.JCXZ + 1))
        table[Op.GROUP5_RM16] = self.group5_rm32
        table[Op.PUSH_IMM16] = table[Op.PUSH_IMM8] = self.push_imm32
        table[Op.PUSHA], table[Op.POPA] = self.pusha32, self.popa32
        table[Op.POP_RM16] = self.pop_rm32
        table[Op.ENTER], table[Op.LEAVE] = self.enter32, self.leave32
        table[Op.CALL_NEAR], table[Op.JMP_NEAR] = self.call_near32, self.jmp_near32
        table[Op.RET_NEAR], table[Op.RET_NEAR_POP] = self.ret_near32, self.ret_near_pop32
        for code in (*range(0xA4, 0xA8), *range(0xAA, 0xB0)):
            table[code] = self.string32
        return table

    def build_address_ops32(self):
        """What the instructions that use SI, DI, CX and BX are when the addresses are of 32 bits: {opcode: handler}.
        The string instructions work out their operand size themselves, so they are the same ones as in the table of
        the 32-bit forms."""
        table = {code: self.string32 for code in (*range(0xA4, 0xA8), *range(0xAA, 0xB0))}
        table.update({code: self.loop_a32 for code in range(Op.LOOPNE, Op.JCXZ + 1)})
        table[Op.XLAT] = self.xlat
        return table
