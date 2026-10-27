"""The 32-bit instructions of the 386, in the part of the machine that already is: real mode and 16-bit addresses.

A 386 has registers of 32 bits, EAX, ECX, EDX, EBX, ESP, EBP, ESI, EDI. The 16 bits that a program of the 8086 knows
are their low halves, and they are what `CPU.r` holds; what is above them is kept apart, in `CPU.rh`, as the high
halves of the same registers. So the 16-bit programs, and all the code written for them, do not know of the high
halves: a write to AX leaves what is above it as it was, as on the real chip.

An instruction works on 32 bits when the prefix 66h is in front of it (as `66 89 C8`, `mov eax, ecx`: the prefix of
the operand size). The prefix is a flag of the instruction, `CPU.osize32`, and the instructions that have a 32-bit form
are in a table of their own, `CPU.ops32`, which step() looks in when the flag is set. A 66h in front of an opcode that
is not in it stops the emulator, so a 32-bit instruction that is not done yet is never taken for the 16-bit one.

What is here: the data moves and the arithmetic with registers, memory (at a 16-bit address) and constants, the stack
(PUSH, POP, PUSHFD, POPFD), INC and DEC, XCHG, TEST, LEA and the sign extensions CWDE and CDQ. Not yet: the 32-bit
addresses (with a SIB byte, the prefix 67h), the shifts, MUL and DIV, the string instructions, the two-byte opcodes
(0Fh) of the 386, and the other registers (the flat mode and the selectors).
"""
from . import flags, modrm
from .opcodes import Alu, Op

# the alignment check flag, bit 18 of EFLAGS, as it is kept in CPU.eflags_high (the bits from the 16th up, shifted down)
AC = 0x4


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
        """Load EAX from the doubleword at a fixed 16-bit address (A1), or store EAX there (A3)."""
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
        return table
