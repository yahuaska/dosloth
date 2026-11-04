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
registers that 32-bit addresses call for, the two-byte opcodes (0Fh) that the programs use: the long conditional
jumps, SETcc, MOVZX, MOVSX, two-operand IMUL, SHLD, SHRD, BT and its kin, BSF, BSR, BSWAP, and the registers FS and
GS with their prefixes (64h, 65h), PUSH and POP, LFS, LGS and LSS. Not yet: what the flat mode needs. The instruction
pointer and the stack pointer are still of 16 bits, so a near jump is cut to 16 bits.
"""
from . import flags, modrm
from .fpu import OPCODES as FPU_OPCODES
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
        if self.stack32:
            sp = (self.get32(self.SP) - 4) & 0xFFFFFFFF
            self.set32(self.SP, sp)
        else:
            sp = (self.r[self.SP] - 4) & 0xFFFF
            self.r[self.SP] = sp
        self.wd(self.s[self.SS], sp, val)

    def pop32(self):
        """Take a doubleword off the stack: read it from SS:SP, then move SP up by 4."""
        sp = self.get32(self.SP) if self.stack32 else self.r[self.SP]
        val = self.rd(self.s[self.SS], sp)
        if self.stack32:
            self.set32(self.SP, sp + 4)
        else:
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
        frame = self.get_stack(self.SP)
        for _ in range(level - 1):
            self.set32(self.BP, self.get32(self.BP) - 4)
            self.push32(self.rd(self.s[self.SS], self.get_stack(self.BP)))
        if level:
            self.push32(frame)
        self.set32(self.BP, frame)
        self.set_stack(self.SP, self.get_stack(self.SP) - size)

    def leave32(self, op):
        """Tear down a frame of doublewords (66 C9): SP <- BP, then EBP <- a doubleword off the stack."""
        self.set_stack(self.SP, self.get_stack(self.BP))
        self.set32(self.BP, self.pop32())

    # --- jumps, calls and returns: the distance and the stack have 32 bits, the instruction pointer has not yet
    def fetch_rel32(self):
        """Read the distance of 32 bits after an opcode and return the address it points to, cut to the width of IP."""
        d = self.fetch32()
        return (self.ip + d) & self.ip_mask

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
        self.ip = self.pop32() & self.ip_mask

    def ret_near_pop32(self, op):
        """Return from a procedure and drop its arguments (66 C2 + imm16): the address is a doubleword."""
        count = self.fetch16()
        self.ip = self.pop32() & self.ip_mask
        self.set_stack(self.SP, self.get_stack(self.SP) + count)

    def iretd(self, op):
        """Return from an interrupt whose handler was called with doublewords (the flat mode): EIP, CS and the flags."""
        self.ip = self.pop32() & self.ip_mask
        self.s[self.CS] = self.pop32() & 0xFFFF
        value = self.pop32()
        self.flags = (value & flags.DEFINED) | flags.ALWAYS_ONE
        self.eflags_high = (value >> 16) & AC

    def retf32(self, op):
        """Return from a far procedure that was called with doublewords on the stack: pop EIP, then CS (66 CB)."""
        self.ip = self.pop32() & self.ip_mask
        self.s[self.CS] = self.pop32() & 0xFFFF

    def retf_pop32(self, op):
        """The same, and drop the arguments of the procedure (66 CA + imm16)."""
        count = self.fetch16()
        self.ip = self.pop32() & self.ip_mask
        self.s[self.CS] = self.pop32() & 0xFFFF
        self.set_stack(self.SP, self.get_stack(self.SP) + count)

    def group5_rm32(self, op):
        """INC, DEC, CALL, JMP and PUSH of a 32-bit register or memory operand (66 FF, by the 'reg' field).

        The far forms (CALL FAR and JMP FAR, 3 and 5) take a pointer of 16:32 in memory: the offset of 32 bits, then
        the selector. A far call pushes CS and then EIP, both as doublewords, and 66 CB or 66 CA gets them back.
        """
        m = modrm.decode(self, self.seg_override)
        if m.reg in (0, 1):
            m.write(self, 32, self.inc_dec(m.read(self, 32), 32, decrement=bool(m.reg)))
        elif m.reg == 2:  # read the operand before pushing: it may be addressed through SP
            target = m.read(self, 32)
            self.push32(self.ip)
            self.ip = target & self.ip_mask
        elif m.reg == 4:
            self.ip = m.read(self, 32) & self.ip_mask
        elif m.reg in (3, 5):
            if m.is_reg:  # there is no register that could hold 6 bytes
                raise NotImplementedError(
                    f"opcode 66 FF /{m.reg} with a register at {self.s[self.CS]:04X}:{self.start_ip:04X}")
            offset, segment = self.rd(m.seg, m.off), self.rw(m.seg, m.off + 4)
            if m.reg == 3:
                self.push32(self.s[self.CS])
                self.push32(self.ip)
            self.s[self.CS], self.ip = segment, offset & self.ip_mask
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
        if self.rep is not None and wide and self.flat and kind in (0xA4, 0xAA) and self.rep_block(kind, bits):
            return

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

    def rep_block(self, kind, bits):
        """REP MOVS (A4) and REP STOS (AA) of the flat mode with 32-bit addresses, as one copy or one fill when the result
        is what the element-by-element loop gives: the direction is up, the whole run is in the memory and does not wrap at
        4 GB, the window of the video card is not touched, and for MOVS the target is not inside what is read after it
        (a copy that goes over its source repeats it). Then ESI and EDI are moved on, and ECX is 0; the flags do not
        change. Returns False when the run is not one of them, and nothing is done: the loop goes element by element."""
        if self.get_flag(flags.DF):
            return False
        count = self.get32(self.CX)
        if count == 0:
            return True  # the loop would do nothing
        size = bits // 8
        total = count * size
        memory = len(self.mem)
        target_offset = self.get32(self.DI)
        target = self.bases[self.s[self.ES]] + target_offset
        if (target_offset + total > 1 << 32 or target + total > memory
                or target < self.video_end and target + total > self.video_start):
            return False
        if kind == 0xA4:
            source_offset = self.get32(self.SI)
            source = self.bases[self.data_segment()] + source_offset
            if (source_offset + total > 1 << 32 or source + total > memory
                    or source < self.video_end and source + total > self.video_start
                    or source < target < source + total):
                return False
            self.mem[target:target + total] = self.mem[source:source + total]
            self.set32(self.SI, source_offset + total)
        else:
            self.mem[target:target + total] = self.get_reg(self.AX, bits).to_bytes(size, "little") * count
        self.set32(self.DI, target_offset + total)
        self.set32(self.CX, 0)
        return True

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

    # --- the segment registers: the 32-bit forms of the instructions that have them, and FS and GS ---
    def push_sreg32(self, op):
        """Push a segment register as a doubleword (66 06, 0E, 16, 1E)."""
        self.push32(self.s[(op >> 3) & 3])

    def pop_sreg32(self, op):
        """Pop a doubleword into ES, SS or DS (66 07, 17, 1F): the register takes its low 16 bits."""
        self.s[(op >> 3) & 3] = self.pop32() & 0xFFFF

    def mov_rm_sreg32(self, op):
        """Copy a segment register into a register or memory (66 8C): a register gets all 32 bits, with zeros in the
        high half, and memory a word."""
        m = modrm.decode(self, self.seg_override)
        if m.reg > 5:
            raise NotImplementedError(f"MOV r/m, sreg with reg={m.reg} at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        m.write(self, 32 if m.is_reg else 16, self.s[m.reg])

    def load_far(self, segment, opcode):
        """Load a register and the segment register `segment` from a far pointer in memory: the offset, of 16 or of 32
        bits (the size of the operands), and then the segment word. Both are read before anything is written."""
        m = modrm.decode(self, self.seg_override)
        if m.is_reg:
            raise NotImplementedError(f"opcode {opcode} with a register at {self.s[self.CS]:04X}:{self.start_ip:04X}")
        bits = 32 if self.osize32 else 16
        offset = m.read(self, bits)
        selector = self.rw(m.seg, m.off + bits // 8)
        self.set_reg(m.reg, bits, offset)
        self.s[segment] = selector

    def les_lds_wide(self, op):
        """LES and LDS with a pointer of 16:32 (66 C4, C5)."""
        self.load_far(self.ES if op == Op.LES else self.DS, f"66 {op:02X}")

    def load_far_386(self, second):
        """LSS, LFS and LGS (0F B2, B4, B5): like LES and LDS, for SS, FS and GS."""
        self.load_far({0xB2: self.SS, 0xB4: self.FS, 0xB5: self.GS}[second], f"0F {second:02X}")

    def push_pop_fs_gs(self, second):
        """Push (0F A0, A8) or pop (0F A1, A9) FS or GS, as a word or, with the size of 32 bits, as a doubleword."""
        segment = self.FS if second < 0xA8 else self.GS
        if second & 1 == 0:
            (self.push32 if self.osize32 else self.push16)(self.s[segment])
        else:
            self.s[segment] = (self.pop32() if self.osize32 else self.pop16()) & 0xFFFF

    # --- the instructions of two bytes (0F xx): `second` is the byte after the 0F ---
    def jcc_near(self, second):
        """Jump if a condition holds (0F 80-8F), by a distance of 16 bits, or of 32 bits with the size of 32 bits."""
        target = self.fetch_rel32() if self.osize32 else self.fetch_rel16()
        if self.condition(second & 0xF):
            self.ip = target

    def setcc(self, second):
        """Put 1 in a byte (a register or memory) if a condition holds, and 0 if it does not (0F 90-9F)."""
        m = modrm.decode(self, self.seg_override)
        m.write(self, 8, 1 if self.condition(second & 0xF) else 0)

    def movzx_movsx(self, second):
        """Widen a byte or a word into a register: with zeros (MOVZX, 0F B6 and B7) or with the sign (MOVSX, BE and BF).

        The odd opcodes take a word, the even ones a byte. The register is of 32 bits with the size of 32 bits, else of
        16 (and then only the byte forms do something that MOV does not).
        """
        m = modrm.decode(self, self.seg_override)
        source_bits = 16 if second & 1 else 8
        value = m.read(self, source_bits)
        if second >= 0xBE:
            value = signed(value, source_bits)
        self.set_reg(m.reg, 32 if self.osize32 else 16, value)

    def load_access_rights(self, second):
        """LAR (0F 02): the access rights of the descriptor of a selector go to a register, and ZF says it was found.

        The descriptors are not kept here, only the bases of the selectors (see flat.py), so what comes is what
        every selector of a 32-bit program has: a descriptor of the whole address space with a granularity of 4 KB, of
        32 bits, present, of the ring 0, and a readable code segment if it is the one that is being run, else a data
        segment that can be written. The access byte is in bits 8-15 and the flags in bits 20-23, as in the descriptor.
        A selector that is not there leaves the register as it was and clears ZF.
        """
        if not self.flat:
            raise NotImplementedError("LAR in the real mode")
        m = modrm.decode(self, self.seg_override)
        selector = m.read(self, 16)
        try:
            self.bases[selector]
        except (KeyError, NotImplementedError):
            self.set_flag(flags.ZF, False)
            return
        access = 0x9A if selector == self.s[self.CS] else 0x92
        self.set_reg(m.reg, 32 if self.osize32 else 16, 0x00C00000 | access << 8)
        self.set_flag(flags.ZF, True)

    def load_segment_limit(self, second):
        """LSL (0F 03): the limit of the segment of a selector goes to a register, and ZF says it was found.

        The limits are not kept (see load_access_rights), and a selector of a 32-bit program is of the whole address
        space, so the answer is the limit of that: the whole 4 GB. A selector that is not there leaves the register as
        it was and clears ZF.
        """
        if not self.flat:
            raise NotImplementedError("LSL in the real mode")
        m = modrm.decode(self, self.seg_override)
        selector = m.read(self, 16)
        try:
            self.bases[selector]
        except (KeyError, NotImplementedError):
            self.set_flag(flags.ZF, False)
            return
        self.set_reg(m.reg, 32 if self.osize32 else 16, 0xFFFFFFFF)
        self.set_flag(flags.ZF, True)

    def imul_two(self, second):
        """A register <- the register times the r/m operand, signed, cut to the size of the register (0F AF)."""
        m = modrm.decode(self, self.seg_override)
        bits = 32 if self.osize32 else 16
        product = signed(self.get_reg(m.reg, bits), bits) * signed(m.read(self, bits), bits)
        self.set_reg(m.reg, bits, product)
        doesnt_fit = not -(1 << (bits - 1)) <= product < 1 << (bits - 1)
        self.set_flag(flags.CF, doesnt_fit)
        self.set_flag(flags.OF, doesnt_fit)

    def double_shift(self, second):
        """Shift the r/m operand and fill what comes free from a register: SHLD from its top, SHRD from its bottom.

        0F A4 and AC (SHLD and SHRD) take the count from a byte after the ModRM bytes, A5 and AD from CL; the low 5 bits
        count. With a count of 0 nothing changes. CF is the last bit that went out, SF, ZF and PF come from the result,
        OF (for a count of 1) says that the sign changed.
        """
        m = modrm.decode(self, self.seg_override)
        bits = 32 if self.osize32 else 16
        count = (self.fetch8() if second in (0xA4, 0xAC) else self.get8(1)) & 0x1F
        if count == 0:
            return
        value, fill, mask = m.read(self, bits), self.get_reg(m.reg, bits), (1 << bits) - 1
        if second in (0xA4, 0xA5):
            result = ((value << count) | (fill >> (bits - count))) & mask
            carry = (value >> (bits - count)) & 1
        else:
            result = ((value >> count) | (fill << (bits - count))) & mask
            carry = (value >> (count - 1)) & 1
        m.write(self, bits, result)
        self.set_flag(flags.CF, bool(carry))
        self.set_flag(flags.OF, bool((value ^ result) >> (bits - 1) & 1))
        self.set_szp(result, bits)

    def bit_operation(self, second, kind=None, by_register=True, bit=0):
        """BT, BTS, BTR and BTC: copy a bit of the r/m operand to CF, and (S) set it, (R) clear it or (C) flip it.

        0F A3, AB, B3 and BB take the number of the bit from a register; 0F BA takes it from a byte after the ModRM
        bytes, and its 'reg' field (4 to 7) says which of the four it is. For a register operand the number counts
        modulo the size of the register. For a memory operand and a number from a register it is not limited: the
        bit may be in the memory after the operand (or before it, for a negative number), and the address is moved
        that many whole operands on. A number from a byte is taken modulo the size.
        """
        m = modrm.decode(self, self.seg_override)
        bits = 32 if self.osize32 else 16
        if second == 0xBA:
            kind = m.reg - 4
            if kind < 0:
                raise NotImplementedError(f"opcode 0F BA /{m.reg} at {self.s[self.CS]:04X}:{self.start_ip:04X}")
            number, by_register = self.fetch8(), False
        else:
            kind = (second - 0xA3) >> 3  # A3 bt, AB bts, B3 btr, BB btc: 0 to 3
            number = signed(self.get_reg(m.reg, bits), bits)
        if by_register and not m.is_reg:
            m.off = (m.off + number // bits * (bits // 8)) & (0xFFFFFFFF if self.asize32 else 0xFFFF)
        bit = 1 << (number % bits)
        value = m.read(self, bits)
        self.set_flag(flags.CF, bool(value & bit))
        if kind == 1:
            m.write(self, bits, value | bit)
        elif kind == 2:
            m.write(self, bits, value & ~bit)
        elif kind == 3:
            m.write(self, bits, value ^ bit)

    def bit_scan(self, second):
        """Find the lowest (BSF, 0F BC) or the highest (BSR, BD) bit that is set in the r/m operand and put its number
        in the register. ZF is set if there is none (the register is then left alone), and clear if there is."""
        m = modrm.decode(self, self.seg_override)
        bits = 32 if self.osize32 else 16
        value = m.read(self, bits)
        self.set_flag(flags.ZF, value == 0)
        if value:
            self.set_reg(m.reg, bits, (value & -value).bit_length() - 1 if second == 0xBC else value.bit_length() - 1)

    def bswap(self, second):
        """Reverse the order of the four bytes of a 32-bit register (0F C8-CF; 486 and later)."""
        reg = second & 7
        self.set32(reg, int.from_bytes(self.get32(reg).to_bytes(4, "little"), "big"))

    def build_ops0f(self):
        """The instructions that start with 0F: {the second byte: handler}."""
        table = {code: self.jcc_near for code in range(0x80, 0x90)}
        table.update({code: self.setcc for code in range(0x90, 0xA0)})
        table.update({code: self.movzx_movsx for code in (0xB6, 0xB7, 0xBE, 0xBF)})
        table[0xAF] = self.imul_two
        table[0x02] = self.load_access_rights
        table[0x03] = self.load_segment_limit
        table.update({code: self.double_shift for code in (0xA4, 0xA5, 0xAC, 0xAD)})
        table.update({code: self.bit_operation for code in (0xA3, 0xAB, 0xB3, 0xBB, 0xBA)})
        table.update({0xBC: self.bit_scan, 0xBD: self.bit_scan})
        table.update({code: self.bswap for code in range(0xC8, 0xD0)})
        table.update({code: self.push_pop_fs_gs for code in (0xA0, 0xA1, 0xA8, 0xA9)})
        table.update({code: self.load_far_386 for code in (0xB2, 0xB4, 0xB5)})
        return table

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
        for form in range(4):  # IN and OUT: the size is that of the operands, and in_out knows it
            same(Op.IO_IMM + form, Op.IO_DX + form)
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
        table[Op.TWO_BYTE] = ops[Op.TWO_BYTE]  # it finds the size itself
        for code in (Op.PUSH_ES, Op.PUSH_CS, Op.PUSH_SS, Op.PUSH_DS):
            table[code] = self.push_sreg32
        for code in (Op.POP_ES, Op.POP_SS, Op.POP_DS):
            table[code] = self.pop_sreg32
        table[Op.MOV_RM16_SREG] = self.mov_rm_sreg32
        same(Op.MOV_SREG_RM16)  # it loads the 16 bits of its operand, whatever the size
        table[Op.LES] = table[Op.LDS] = self.les_lds_wide
        table[Op.PUSH_IMM16] = table[Op.PUSH_IMM8] = self.push_imm32
        table[Op.PUSHA], table[Op.POPA] = self.pusha32, self.popa32
        table[Op.POP_RM16] = self.pop_rm32
        table[Op.ENTER], table[Op.LEAVE] = self.enter32, self.leave32
        table[Op.CALL_NEAR], table[Op.JMP_NEAR] = self.call_near32, self.jmp_near32
        table[Op.RET_NEAR], table[Op.RET_NEAR_POP] = self.ret_near32, self.ret_near_pop32
        table[Op.RETF], table[Op.RETF_POP] = self.retf32, self.retf_pop32
        table[Op.IRET] = self.iretd
        for code in (*range(0xA4, 0xA8), *range(0xAA, 0xB0)):
            table[code] = self.string32
        for code in FPU_OPCODES:  # the coprocessor is there for a 32-bit program (see fpu.py)
            table[code] = self.fpu_instruction
        return table

    def build_address_ops32(self):
        """What the instructions that use SI, DI, CX and BX are when the addresses are of 32 bits: {opcode: handler}.
        The string instructions work out their operand size themselves, so they are the same ones as in the table of
        the 32-bit forms."""
        table = {code: self.string32 for code in (*range(0xA4, 0xA8), *range(0xAA, 0xB0))}
        table.update({code: self.loop_a32 for code in range(Op.LOOPNE, Op.JCXZ + 1)})
        table[Op.XLAT] = self.xlat
        return table
