import unittest

from mydos import flags
from mydos.cpu import CPU, DivideError, Exit


class TestSegmentOverrideMov(unittest.TestCase):
    def test_cs_prefix_mov_mem_dx(self):
        """2E 89 16 00 02 = mov cs:[0200h], dx  (CS: prefix, MOV r/m16,r16, ModRM mod=00 reg=DX rm=disp16)"""
        cpu = CPU()
        cpu.s[CPU.CS], cpu.s[CPU.DS] = 0x1000, 0x2000  # differ, so the override is visible
        cpu.r[CPU.DX] = 0xBEEF
        cpu.mem[0x10000:0x10005] = bytes([0x2E, 0x89, 0x16, 0x00, 0x02])
        cpu.step()
        self.assertEqual(cpu.rw(0x1000, 0x0200), 0xBEEF)  # written through CS, not DS
        self.assertEqual(cpu.rw(0x2000, 0x0200), 0)
        self.assertEqual(cpu.ip, 5)


# --- the two-operand forms shared by MOV (88-8B) and the arithmetic/logic block (forms 0-3) ---
#
# The test below does not use any CPU helper. It keeps its own simple model of the registers and
# of the flags, works out what each instruction must do, and compares. That way it can stay as it
# is while the CPU code underneath is reorganised.

REGS16 = [0x1234, 0x89AB, 0xFF00, 0x00FF, 0x7FFF, 0x8000, 0x0001, 0xFFFF]  # AX CX DX BX SP BP SI DI
MEM_ADDR = 0x0100  # where the memory operand lives, [bx] with BX = MEM_ADDR
MEM_BYTES = [0xDD, 0x01, 0x82, 0xEE, 0x77]  # bytes at MEM_ADDR-1 .. MEM_ADDR+3


def get(regs, n, bits):
    """Register n of the model, 16 bits or 8 bits (0-3 = AL CL DL BL, 4-7 = AH CH DH BH)."""
    if bits == 16:
        return regs[n]
    return (regs[n & 3] >> 8) & 0xFF if n & 4 else regs[n & 3] & 0xFF


def put(regs, n, bits, value):
    regs = list(regs)
    if bits == 16:
        regs[n] = value & 0xFFFF
    elif n & 4:
        regs[n & 3] = (regs[n & 3] & 0x00FF) | (value & 0xFF) << 8
    else:
        regs[n & 3] = (regs[n & 3] & 0xFF00) | (value & 0xFF)
    return regs


def signed(v, bits):
    return v - (1 << bits) if v >> (bits - 1) else v


def szp(result, bits):
    return {"Z": result == 0, "S": bool(result >> (bits - 1)), "P": bin(result & 0xFF).count("1") % 2 == 0}


def add_flags(a, b, c, bits):
    total = a + b + c
    half = 1 << (bits - 1)
    overflow = not (-half <= signed(a, bits) + signed(b, bits) + c < half)
    return {"C": total >= 1 << bits, "O": overflow, **szp(total & ((1 << bits) - 1), bits)}


def sub_flags(a, b, c, bits):
    half = 1 << (bits - 1)
    overflow = not (-half <= signed(a, bits) - signed(b, bits) - c < half)
    return {"C": a < b + c, "O": overflow, **szp((a - b - c) & ((1 << bits) - 1), bits)}


def logic_flags(result, bits):
    return {"C": False, "O": False, **szp(result, bits)}


def mask(bits):
    return (1 << bits) - 1


# name, first opcode of its block, result(a, b, carry, bits) (None = nothing is stored),
# flags(a, b, carry, bits) (None = the flags are not touched), whether the carry flag is an input
OPERATIONS = [
    ("MOV", 0x88, lambda a, b, c, n: b, None, False),
    ("ADD", 0x00, lambda a, b, c, n: (a + b) & mask(n), lambda a, b, c, n: add_flags(a, b, 0, n), False),
    ("OR", 0x08, lambda a, b, c, n: a | b, lambda a, b, c, n: logic_flags(a | b, n), False),
    ("ADC", 0x10, lambda a, b, c, n: (a + b + c) & mask(n), lambda a, b, c, n: add_flags(a, b, c, n), True),
    ("SBB", 0x18, lambda a, b, c, n: (a - b - c) & mask(n), lambda a, b, c, n: sub_flags(a, b, c, n), True),
    ("AND", 0x20, lambda a, b, c, n: a & b, lambda a, b, c, n: logic_flags(a & b, n), False),
    ("SUB", 0x28, lambda a, b, c, n: (a - b) & mask(n), lambda a, b, c, n: sub_flags(a, b, 0, n), False),
    ("XOR", 0x30, lambda a, b, c, n: a ^ b, lambda a, b, c, n: logic_flags(a ^ b, n), False),
    ("CMP", 0x38, lambda a, b, c, n: None, lambda a, b, c, n: sub_flags(a, b, 0, n), False),
]


def machine(code, cf):
    cpu = CPU()
    cpu.s[CPU.CS], cpu.s[CPU.DS], cpu.s[CPU.ES] = 0x1000, 0x2000, 0x3000
    cpu.mem[0x10000:0x10000 + len(code)] = code
    cpu.r[:] = REGS16
    cpu.r[CPU.BX] = MEM_ADDR
    # start with several flags raised, so that an instruction which wrongly clears one is noticed
    cpu.flags |= flags.ZF | flags.SF | flags.PF | flags.AF | flags.OF | flags.DF | flags.IF
    cpu.set_flag(flags.CF, cf)
    return cpu


def cpu_flags(cpu):
    return {"C": cpu.get_flag(flags.CF), "O": cpu.get_flag(flags.OF), "Z": cpu.get_flag(flags.ZF),
            "S": cpu.get_flag(flags.SF), "P": cpu.get_flag(flags.PF)}


class TestTwoOperandForms(unittest.TestCase):
    """Opcode bit 0 is the size (0 = byte, 1 = word), bit 1 the direction (0 = into r/m, 1 = into the register)."""

    def check_flags(self, cpu, start_flags, flags_fn, a, b, c, bits, label):
        untouchable = flags.DF | flags.IF | flags.TF | 0x0002  # no arithmetic or MOV instruction changes these
        self.assertEqual(cpu.flags & untouchable, start_flags & untouchable, label)
        if flags_fn is None:  # MOV: not a single flag may change
            self.assertEqual(cpu.flags, start_flags, label)
        else:
            self.assertEqual(cpu_flags(cpu), flags_fn(a, b, c, bits), label)

    def test_register_with_register(self):
        for name, base, result, flags_fn, uses_carry in OPERATIONS:
            for form in range(4):
                bits = 16 if form & 1 else 8
                for reg in range(8):
                    for rm in range(8):
                        for cf in (False, True):
                            label = f"{name} form {form} reg {reg} rm {rm} cf {cf}"
                            cpu = machine(bytes([base + form, 0xC0 | reg << 3 | rm]), cf)
                            model = list(REGS16)
                            model[CPU.BX] = MEM_ADDR
                            start_flags = cpu.flags
                            cpu.step()
                            dst, src = (reg, rm) if form & 2 else (rm, reg)
                            a, b, c = get(model, dst, bits), get(model, src, bits), int(cf and uses_carry)
                            new = result(a, b, c, bits)
                            if new is not None:
                                model = put(model, dst, bits, new)
                            self.assertEqual(cpu.r, model, label)
                            self.assertEqual(cpu.ip, 2, label)
                            self.check_flags(cpu, start_flags, flags_fn, a, b, c, bits, label)

    def test_register_with_memory(self):
        """ModRM mod=00 r/m=7 is [bx]: the memory operand is DS:MEM_ADDR."""
        for name, base, result, flags_fn, uses_carry in OPERATIONS:
            for form in range(4):
                bits = 16 if form & 1 else 8
                size = bits // 8
                for reg in range(8):
                    for cf in (False, True):
                        label = f"{name} form {form} reg {reg} cf {cf}"
                        cpu = machine(bytes([base + form, reg << 3 | 7]), cf)
                        cpu.mem[0x20000 + MEM_ADDR - 1:0x20000 + MEM_ADDR + 4] = bytes(MEM_BYTES)
                        model = list(REGS16)
                        model[CPU.BX] = MEM_ADDR
                        mem = bytearray(MEM_BYTES)  # index 1 is MEM_ADDR itself
                        mem_value = int.from_bytes(mem[1:1 + size], "little")
                        start_flags = cpu.flags
                        cpu.step()
                        reg_value = get(model, reg, bits)
                        c = int(cf and uses_carry)
                        if form & 2:  # into the register
                            a, b = reg_value, mem_value
                            new = result(a, b, c, bits)
                            if new is not None:
                                model = put(model, reg, bits, new)
                        else:  # into memory
                            a, b = mem_value, reg_value
                            new = result(a, b, c, bits)
                            if new is not None:
                                mem[1:1 + size] = new.to_bytes(size, "little")
                        self.assertEqual(cpu.r, model, label)
                        got_mem = bytes(cpu.mem[0x20000 + MEM_ADDR - 1:0x20000 + MEM_ADDR + 4])
                        self.assertEqual(got_mem, bytes(mem), label)
                        self.assertEqual(cpu.ip, 2, label)
                        self.check_flags(cpu, start_flags, flags_fn, a, b, c, bits, label)

    def test_segment_prefix_picks_the_segment_of_the_memory_operand(self):
        """No prefix means DS; 2E is CS, 26 is ES. Each segment holds a different byte at offset MEM_ADDR."""
        value_in = {0x10000: 0x10, 0x20000: 0x20, 0x30000: 0x30}  # linear start of CS, DS, ES -> byte stored there
        for name, base in (("MOV", 0x88), ("ADD", 0x00)):
            for form in range(4):
                bits = 16 if form & 1 else 8
                for prefix, seg_start in ((None, 0x20000), (0x2E, 0x10000), (0x26, 0x30000)):
                    label = f"{name} form {form} prefix {prefix}"
                    code = bytes(([prefix] if prefix else []) + [base + form, 0x07])  # [bx] with AL/AX as the register
                    cpu = machine(code, False)
                    for start, v in value_in.items():
                        cpu.mem[start + MEM_ADDR] = v
                        cpu.mem[start + MEM_ADDR + 1] = 0
                    cpu.step()
                    ax = REGS16[0]
                    if form & 2:  # the register receives the value from the chosen segment
                        got = cpu.r[0] & mask(bits)
                        want = value_in[seg_start] if name == "MOV" else (ax + value_in[seg_start]) & mask(bits)
                        self.assertEqual(got, want, label)
                    else:  # the chosen segment received the register, the others are as they were
                        written = ax & mask(bits) if name == "MOV" else (ax + value_in[seg_start]) & mask(bits)
                        for start, v in value_in.items():
                            word = cpu.mem[start + MEM_ADDR] | cpu.mem[start + MEM_ADDR + 1] << 8
                            self.assertEqual(word & mask(bits), written if start == seg_start else v, label)

    def test_accumulator_with_constant(self):
        """Forms 4 and 5 of the arithmetic/logic block: AL, imm8 and AX, imm16."""
        for name, base, result, flags_fn, uses_carry in OPERATIONS[1:]:
            for form, bits, imm in ((4, 8, 0x9C), (5, 16, 0xA5C3), (4, 8, 0x00), (5, 16, 0x7FFF)):
                for cf in (False, True):
                    label = f"{name} form {form} imm {imm:X} cf {cf}"
                    code = bytes([base + form]) + imm.to_bytes(bits // 8, "little")
                    cpu = machine(code, cf)
                    model = list(REGS16)
                    model[CPU.BX] = MEM_ADDR
                    cpu.step()
                    a, b, c = get(model, 0, bits), imm, int(cf and uses_carry)
                    new = result(a, b, c, bits)
                    if new is not None:
                        model = put(model, 0, bits, new)
                    self.assertEqual(cpu.r, model, label)
                    self.assertEqual(cpu.ip, len(code), label)
                    self.assertEqual(cpu_flags(cpu), flags_fn(a, b, c, bits), label)


class TestMovImmediate(unittest.TestCase):
    """C6 (byte) and C7 (word): mov r/m, imm."""

    def run_code(self, code, **regs):
        cpu = machine(bytes(code), False)
        for name, value in regs.items():
            cpu.r[getattr(CPU, name)] = value
        start_flags = cpu.flags
        cpu.step()
        self.assertEqual(cpu.flags, start_flags)  # MOV changes no flag
        return cpu

    def test_byte_into_memory_through_es(self):
        """26 C6 07 FF = mov byte es:[bx], FF, from esetup.exe"""
        cpu = self.run_code([0x26, 0xC6, 0x07, 0xFF])
        self.assertEqual(cpu.mem[0x30000 + MEM_ADDR], 0xFF)
        self.assertEqual(cpu.mem[0x30000 + MEM_ADDR + 1], 0)  # one byte only
        self.assertEqual(cpu.mem[0x20000 + MEM_ADDR], 0)  # not DS
        self.assertEqual(cpu.ip, 4)

    def test_word_into_memory_after_the_address_bytes(self):
        cpu = self.run_code([0xC7, 0x06, 0x34, 0x12, 0x00, 0x01])  # mov word [1234], 0100
        self.assertEqual(cpu.rw(0x2000, 0x1234), 0x0100)
        self.assertEqual(cpu.ip, 6)

    def test_displacement_then_constant(self):
        cpu = self.run_code([0xC7, 0x47, 0xFE, 0xCD, 0xAB])  # mov word [bx-2], ABCD
        self.assertEqual(cpu.rw(0x2000, MEM_ADDR - 2), 0xABCD)
        self.assertEqual(cpu.ip, 5)

    def test_into_every_register(self):
        for reg in range(8):
            cpu = self.run_code([0xC6, 0xC0 | reg, 0x5A])  # 8-bit register reg
            model = put(REGS16[:3] + [MEM_ADDR] + REGS16[4:], reg, 8, 0x5A)
            self.assertEqual(cpu.r, model, f"C6 reg {reg}")
            cpu = self.run_code([0xC7, 0xC0 | reg, 0x34, 0x12])  # 16-bit register reg
            model = list(REGS16)
            model[CPU.BX] = MEM_ADDR
            model[reg] = 0x1234
            self.assertEqual(cpu.r, model, f"C7 reg {reg}")

    def test_the_other_values_of_the_reg_field_stop_loudly(self):
        for op in (0xC6, 0xC7):
            for reg in range(1, 8):
                with self.assertRaises(NotImplementedError, msg=f"{op:02X} /{reg}"):
                    self.run_code([op, 0xC0 | reg << 3, 0, 0])


class TestGroups4And5(unittest.TestCase):
    """FE (INC/DEC byte) and FF (INC, DEC, CALL, JMP, PUSH on a word), plus the short INC/DEC/PUSH."""

    def run_code(self, code, steps=1, cf=False, **regs):
        cpu = machine(bytes(code), cf)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        for name, value in regs.items():
            cpu.r[getattr(CPU, name)] = value
        for _ in range(steps):
            cpu.step()
        return cpu

    def stack_word(self, cpu, depth=0):
        return cpu.rw(cpu.s[CPU.SS], cpu.r[CPU.SP] + 2 * depth)

    def test_call_through_a_pointer_in_memory(self):
        """26 FF 57 02 = call word es:[bx+2], from esetup.exe"""
        cpu = machine(bytes([0x26, 0xFF, 0x57, 0x02]), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        cpu.ww(0x3000, MEM_ADDR + 2, 0x165D)  # the pointer lives in ES, not in DS
        cpu.ww(0x2000, MEM_ADDR + 2, 0x1111)
        start_flags = cpu.flags
        cpu.step()
        self.assertEqual(cpu.ip, 0x165D)
        self.assertEqual(cpu.r[CPU.SP], 0x01FE)
        self.assertEqual(self.stack_word(cpu), 4)  # the address after the 4-byte instruction
        self.assertEqual(cpu.flags, start_flags)

    def test_call_through_a_register_and_come_back(self):
        cpu = self.run_code([0xFF, 0xD0], AX=0x0010)  # call ax
        self.assertEqual((cpu.ip, self.stack_word(cpu)), (0x10, 2))
        cpu.mem[0x10010] = 0xC3  # ret
        cpu.step()
        self.assertEqual((cpu.ip, cpu.r[CPU.SP]), (2, 0x0200))

    def test_the_operand_is_read_before_the_return_address_is_pushed(self):
        cpu = machine(bytes([0xFF, 0x14]), False)  # call word [si], with SI pointing at the stack slot to be written
        cpu.s[CPU.SS], cpu.s[CPU.DS], cpu.r[CPU.SP], cpu.r[CPU.SI] = 0x4000, 0x4000, 0x0200, 0x01FE
        cpu.ww(0x4000, 0x01FE, 0x0055)  # what [si] holds before the call
        cpu.step()
        self.assertEqual(cpu.ip, 0x0055)

    def test_jmp_near_through_register_and_memory(self):
        cpu = self.run_code([0xFF, 0xE3], BX=0x0777)  # jmp bx
        self.assertEqual((cpu.ip, cpu.r[CPU.SP]), (0x777, 0x0200))  # nothing pushed
        cpu = machine(bytes([0xFF, 0x27]), False)  # jmp word [bx]
        cpu.ww(0x2000, MEM_ADDR, 0x0321)
        cpu.step()
        self.assertEqual(cpu.ip, 0x321)

    def test_far_call_and_far_jump_read_offset_then_segment(self):
        cpu = machine(bytes([0xFF, 0x1F]), False)  # call far [bx]
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        cpu.ww(0x2000, MEM_ADDR, 0x1234)
        cpu.ww(0x2000, MEM_ADDR + 2, 0x5678)
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x5678, 0x1234))
        self.assertEqual(cpu.r[CPU.SP], 0x01FC)
        self.assertEqual((self.stack_word(cpu, 0), self.stack_word(cpu, 1)), (2, 0x1000))  # IP on top, CS under it
        cpu = machine(bytes([0xFF, 0x2F]), False)  # jmp far [bx]
        cpu.r[CPU.SP] = 0x0200
        cpu.ww(0x2000, MEM_ADDR, 0x0042)
        cpu.ww(0x2000, MEM_ADDR + 2, 0x6000)
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.r[CPU.SP]), (0x6000, 0x42, 0x0200))

    def test_far_forms_need_memory(self):
        for reg in (3, 5):
            with self.assertRaises(NotImplementedError):
                self.run_code([0xFF, 0xC0 | reg << 3])

    def test_push_word(self):
        cpu = machine(bytes([0xFF, 0x37]), False)  # push word [bx]
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        cpu.ww(0x2000, MEM_ADDR, 0xBEEF)
        start_flags = cpu.flags
        cpu.step()
        self.assertEqual((self.stack_word(cpu), cpu.r[CPU.SP], cpu.flags), (0xBEEF, 0x01FE, start_flags))
        cpu = self.run_code([0xFF, 0xF1], CX=0x4321)  # push cx
        self.assertEqual(self.stack_word(cpu), 0x4321)
        cpu = self.run_code([0xFF, 0xF4])  # push sp stores the value SP had before
        self.assertEqual(self.stack_word(cpu), 0x0200)

    def test_inc_dec_word_keep_the_carry_flag(self):
        for reg, name, start, want, overflow in ((0, "inc", 0x7FFF, 0x8000, True), (1, "dec", 0x8000, 0x7FFF, True),
                                                  (0, "inc", 0xFFFF, 0x0000, False), (1, "dec", 0x0000, 0xFFFF, False)):
            for cf in (False, True):
                label = f"{name} {start:04X} cf={cf}"
                cpu = self.run_code([0xFF, 0xC0 | reg << 3 | 3], cf=cf, BX=start)  # on BX
                self.assertEqual(cpu.r[CPU.BX], want, label)
                self.assertEqual(cpu.get_flag(flags.CF), cf, label)
                self.assertEqual(cpu.get_flag(flags.OF), overflow, label)
                self.assertEqual(cpu.get_flag(flags.ZF), want == 0, label)
        cpu = machine(bytes([0xFF, 0x07]), False)  # inc word [bx]
        cpu.ww(0x2000, MEM_ADDR, 0x00FF)
        cpu.step()
        self.assertEqual(cpu.rw(0x2000, MEM_ADDR), 0x0100)

    def test_inc_dec_byte(self):
        cpu = self.run_code([0xFE, 0xC1], CX=0x12FF)  # inc cl
        self.assertEqual(cpu.r[CPU.CX], 0x1200)  # CL wrapped, CH left alone
        self.assertTrue(cpu.get_flag(flags.ZF))
        cpu = self.run_code([0xFE, 0xCD], CX=0x1200, cf=True)  # dec ch
        self.assertEqual(cpu.r[CPU.CX], 0x1100)
        self.assertTrue(cpu.get_flag(flags.CF))
        cpu = machine(bytes([0xFE, 0x0F]), False)  # dec byte [bx]
        cpu.mem[0x20000 + MEM_ADDR] = 0x80
        cpu.mem[0x20000 + MEM_ADDR + 1] = 0x99
        cpu.step()
        self.assertEqual((cpu.mem[0x20000 + MEM_ADDR], cpu.mem[0x20000 + MEM_ADDR + 1]), (0x7F, 0x99))
        self.assertTrue(cpu.get_flag(flags.OF))  # -128 - 1

    def test_the_undefined_operations_stop_loudly(self):
        for code in ([0xFE, 0xD0], [0xFE, 0xF8], [0xFF, 0xF8]):  # FE /2, FE /7, FF /7
            with self.assertRaises(NotImplementedError):
                self.run_code(code)

    def test_the_short_inc_dec_still_work(self):
        for reg in range(8):
            for cf in (False, True):
                cpu = self.run_code([0x40 + reg], cf=cf)
                self.assertEqual(cpu.r[reg], (self.run_code([0x90]).r[reg] + 1) & 0xFFFF if reg != CPU.SP else 0x0201)
                self.assertEqual(cpu.get_flag(flags.CF), cf)
                cpu = self.run_code([0x48 + reg], cf=cf)
                self.assertEqual(cpu.r[reg], (self.run_code([0x90]).r[reg] - 1) & 0xFFFF if reg != CPU.SP else 0x01FF)
                self.assertEqual(cpu.get_flag(flags.CF), cf)


def reference_shift(kind, value, count, cf, bits):
    """What a shift or rotate must give: (result, CF). Worked out with whole-number arithmetic.

    This does not move one bit at a time as the CPU code does, so the two cannot share a mistake.
    """
    mask = (1 << bits) - 1
    sv = signed(value, bits)
    if kind in (4, 6):  # SHL
        return (value << count) & mask, bool((value >> (bits - count)) & 1) if count <= bits else False
    if kind == 5:  # SHR
        return (value >> count if count < bits else 0), bool((value >> (count - 1)) & 1) if count <= bits else False
    if kind == 7:  # SAR
        return (sv >> count) & mask, bool((sv >> (count - 1)) & 1)
    if kind == 0:  # ROL
        n = count % bits
        result = ((value << n) | (value >> (bits - n))) & mask
        return result, bool(result & 1)
    if kind == 1:  # ROR
        n = count % bits
        result = ((value >> n) | (value << (bits - n))) & mask
        return result, bool(result >> (bits - 1))
    ring = (1 << (bits + 1)) - 1  # RCL and RCR turn a ring that is one bit wider: the value and CF
    full = (int(cf) << bits) | value
    n = count % (bits + 1)
    if kind == 2:  # RCL
        full = ((full << n) | (full >> (bits + 1 - n))) & ring
    else:  # RCR
        full = ((full >> n) | (full << (bits + 1 - n))) & ring
    return full & mask, bool(full >> bits)


def reference_overflow(kind, value, result, cf_out, bits):
    """OF after a shift by exactly one bit."""
    top = bool(result >> (bits - 1))
    if kind in (4, 6, 0, 2):
        return top != cf_out
    if kind == 5:
        return bool(value >> (bits - 1))
    if kind == 7:
        return False
    return top != bool((result >> (bits - 2)) & 1)  # ROR, RCR


class TestShiftsAndRotates(unittest.TestCase):
    """D0 (byte, by 1), D1 (word, by 1), D2 (byte, by CL), D3 (word, by CL)."""

    VALUES = {8: [0x00, 0x01, 0x80, 0x81, 0x7F, 0xFF, 0xA5, 0x5A],
              16: [0x0000, 0x0001, 0x8000, 0x8001, 0x7FFF, 0xFFFF, 0xA55A, 0x1234]}
    COUNTS = [0, 1, 2, 3, 7, 8, 9, 15, 16, 17, 31, 32, 255]

    def test_register_operand(self):
        for op in (0xD0, 0xD1, 0xD2, 0xD3):
            bits = 16 if op & 1 else 8
            counts = self.COUNTS if op & 2 else [1]
            for kind in range(8):
                for value in self.VALUES[bits]:
                    for count in counts:
                        for cf in (False, True):
                            label = f"{op:02X} kind {kind} value {value:X} count {count} cf {cf}"
                            cpu = machine(bytes([op, 0xC0 | kind << 3 | 3]), cf)  # BX, or BL for the byte forms
                            cpu.r[CPU.BX] = value if bits == 16 else 0xA500 | value  # BH must stay untouched
                            cpu.r[CPU.CX] = 0x7F00 | count  # only CL counts
                            start_flags = cpu.flags
                            cpu.step()
                            self.assertEqual(cpu.ip, 2, label)
                            self.assertEqual(cpu.r[CPU.CX], 0x7F00 | count, label)
                            if count == 0:  # nothing changes, flags included
                                self.assertEqual(cpu.flags, start_flags, label)
                                self.assertEqual(cpu.r[CPU.BX] & mask(bits), value, label)
                                continue
                            result, cf_out = reference_shift(kind, value, count, cf, bits)
                            got = cpu.r[CPU.BX] if bits == 16 else cpu.r[CPU.BX] & 0xFF
                            self.assertEqual(got, result, label)
                            if bits == 8:
                                self.assertEqual(cpu.r[CPU.BX] >> 8, 0xA5, label)
                            self.assertEqual(cpu.get_flag(flags.CF), cf_out, label)
                            if count == 1:
                                want_of = reference_overflow(kind, value, result, cf_out, bits)
                                self.assertEqual(cpu.get_flag(flags.OF), want_of, label)
                            if kind in (4, 5, 6, 7):  # shifts set SF, ZF, PF; rotations leave them alone
                                self.assertEqual({"Z": cpu.get_flag(flags.ZF), "S": cpu.get_flag(flags.SF),
                                                  "P": cpu.get_flag(flags.PF)}, szp(result, bits), label)
                            else:
                                for flag in (flags.ZF, flags.SF, flags.PF):
                                    self.assertEqual(cpu.flags & flag, start_flags & flag, label)

    def test_memory_operand(self):
        """[bx] with BX = MEM_ADDR, and the bytes around it must stay as they were."""
        for op in (0xD0, 0xD1, 0xD2, 0xD3):
            bits = 16 if op & 1 else 8
            size = bits // 8
            for kind in range(8):
                for value in self.VALUES[bits]:
                    for count in ([1] if not op & 2 else [1, 4, 9, 17]):
                        label = f"{op:02X} kind {kind} value {value:X} count {count}"
                        cpu = machine(bytes([op, kind << 3 | 7]), False)
                        cpu.r[CPU.CX] = count
                        # a guard byte on each side of the operand
                        around = b"\xDD" + value.to_bytes(size, "little") + b"\xEE\x77"[:3 - size]
                        cpu.mem[0x20000 + MEM_ADDR - 1:0x20000 + MEM_ADDR + 3] = around
                        cpu.step()
                        result, _ = reference_shift(kind, value, count, False, bits)
                        mem = cpu.mem[0x20000 + MEM_ADDR - 1:0x20000 + MEM_ADDR + 3]
                        self.assertEqual(mem[0], 0xDD, label)
                        self.assertEqual(int.from_bytes(mem[1:1 + size], "little"), result, label)
                        if size == 1:
                            self.assertEqual(mem[2], 0xEE, label)

    def test_shl_by_one_from_esetup(self):
        """D1 E3 = shl bx, 1 turns an index into a byte offset into a table of words."""
        cpu = machine(bytes([0xD1, 0xE3]), False)
        cpu.r[CPU.BX] = 5
        cpu.step()
        self.assertEqual(cpu.r[CPU.BX], 10)


class TestGroup3(unittest.TestCase):
    """F6 (byte) and F7 (word): TEST, NOT, NEG, MUL, IMUL, DIV, IDIV. The operand is BX (or BL)."""

    VALUES = {8: [0x00, 0x01, 0x02, 0x07, 0x7F, 0x80, 0xFF, 0x10],
              16: [0x0000, 0x0001, 0x0002, 0x7FFF, 0x8000, 0xFFFF, 0x1234, 0x0100]}

    def execute(self, kind, bits, operand, ax=0, dx=0, imm=None):
        op = 0xF7 if bits == 16 else 0xF6
        code = bytes([op, 0xC0 | kind << 3 | 3]) + (imm.to_bytes(bits // 8, "little") if imm is not None else b"")
        cpu = machine(code, False)
        cpu.r[CPU.BX] = operand if bits == 16 else 0xA500 | operand  # BH must stay untouched
        cpu.r[CPU.AX], cpu.r[CPU.DX] = ax, dx
        start_flags = cpu.flags
        cpu.step()
        return cpu, start_flags

    def test_not_flips_the_bits_and_touches_no_flag(self):
        for bits in (8, 16):
            for v in self.VALUES[bits]:
                cpu, start = self.execute(2, bits, v)
                self.assertEqual(cpu.r[CPU.BX] & mask(bits), ~v & mask(bits), f"{bits} {v:X}")
                self.assertEqual(cpu.r[CPU.BX] >> 8 if bits == 8 else 0xA5, 0xA5)
                self.assertEqual(cpu.flags, start)

    def test_neg(self):
        for bits in (8, 16):
            for v in self.VALUES[bits]:
                cpu, _ = self.execute(3, bits, v)
                result = -v & mask(bits)
                self.assertEqual(cpu.r[CPU.BX] & mask(bits), result, f"{bits} {v:X}")
                self.assertEqual(cpu_flags(cpu), {"C": v != 0, "O": v == 1 << (bits - 1), **szp(result, bits)})

    def test_test_sets_the_flags_and_stores_nothing(self):
        for bits in (8, 16):
            for kind in (0, 1):  # /1 is an alias
                for v in self.VALUES[bits]:
                    for imm in self.VALUES[bits]:
                        cpu, _ = self.execute(kind, bits, v, imm=imm)
                        label = f"{bits} /{kind} {v:X} {imm:X}"
                        self.assertEqual(cpu.r[CPU.BX] & mask(bits), v, label)
                        self.assertEqual(cpu.ip, 2 + bits // 8, label)
                        self.assertEqual(cpu_flags(cpu), logic_flags(v & imm, bits), label)

    def test_mul_unsigned(self):
        for bits in (8, 16):
            for a in self.VALUES[bits]:
                for b in self.VALUES[bits]:
                    cpu, _ = self.execute(4, bits, b, ax=0x5500 | a if bits == 8 else a, dx=0x5555)
                    product = a * b
                    label = f"{bits} {a:X} * {b:X}"
                    if bits == 8:
                        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.DX]), (product, 0x5555), label)  # AX only
                    else:
                        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.DX]), (product & 0xFFFF, product >> 16), label)
                    big = product >> bits != 0
                    self.assertEqual((cpu.get_flag(flags.CF), cpu.get_flag(flags.OF)), (big, big), label)

    def test_imul_signed(self):
        for bits in (8, 16):
            for a in self.VALUES[bits]:
                for b in self.VALUES[bits]:
                    cpu, _ = self.execute(5, bits, b, ax=0x5500 | a if bits == 8 else a, dx=0x5555)
                    product = signed(a, bits) * signed(b, bits)
                    wide = product & mask(2 * bits)
                    label = f"{bits} {a:X} * {b:X}"
                    if bits == 8:
                        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.DX]), (wide, 0x5555), label)
                    else:
                        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.DX]), (wide & 0xFFFF, wide >> 16), label)
                    big = not -(1 << (bits - 1)) <= product < 1 << (bits - 1)
                    self.assertEqual((cpu.get_flag(flags.CF), cpu.get_flag(flags.OF)), (big, big), label)

    def divide_cases(self, bits):
        """(dividend as (dx, ax), divisor): a spread that includes every edge of the signed and unsigned ranges."""
        if bits == 8:
            dividends = [(0, ax) for ax in (0x0000, 0x0001, 0x00FF, 0x0100, 0x7FFF, 0x8000, 0xFF80, 0xFFFF, 0x1234)]
        else:
            highs = (0, 1, 0x7FFF, 0x8000, 0xFFFF)
            dividends = [(dx, ax) for dx in highs for ax in (0, 1, 0x7FFF, 0x8000, 0xFFFF, 0x1234)]
        return [(d, v) for d in dividends for v in self.VALUES[bits]]

    def check_division(self, kind, bits):
        for (dx, ax), divisor in self.divide_cases(bits):
            wide = (dx << 16 | ax) if bits == 16 else ax
            label = f"{bits} /{kind} {wide:X} / {divisor:X}"
            if kind == 7:
                wide, divisor_value = signed(wide, 2 * bits), signed(divisor, bits)
            else:
                divisor_value = divisor
            # float division, so that the reference shares no code with the CPU's integer arithmetic
            quotient = int(wide / divisor_value) if divisor_value else None
            fits = quotient is not None and (-(1 << (bits - 1)) <= quotient < 1 << (bits - 1) if kind == 7
                                             else 0 <= quotient < 1 << bits)
            if not fits:
                with self.assertRaises(DivideError, msg=label):
                    self.execute(kind, bits, divisor, ax=ax, dx=dx)
                continue
            cpu, _ = self.execute(kind, bits, divisor, ax=ax, dx=dx)
            remainder = wide - quotient * divisor_value
            if bits == 8:
                # AH holds the remainder, AL the quotient
                self.assertEqual(cpu.r[CPU.AX], (remainder & 0xFF) << 8 | quotient & 0xFF, label)
                self.assertEqual(cpu.r[CPU.DX], dx, label)
            else:
                self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.DX]), (quotient & 0xFFFF, remainder & 0xFFFF), label)

    def test_div_unsigned(self):
        for bits in (8, 16):
            self.check_division(6, bits)

    def test_idiv_signed(self):
        for bits in (8, 16):
            self.check_division(7, bits)

    def test_a_failed_division_changes_nothing(self):
        for kind in (6, 7):
            cpu = machine(bytes([0xF7, 0xC0 | kind << 3 | 3]), False)
            cpu.r[CPU.BX], cpu.r[CPU.AX], cpu.r[CPU.DX] = 0, 0x1234, 0x0005  # divide by zero
            registers, flag_bits = list(cpu.r), cpu.flags
            with self.assertRaises(DivideError):
                cpu.step()
            self.assertEqual((cpu.r, cpu.flags), (registers, flag_bits))

    def test_memory_operand_and_prefix(self):
        cpu = machine(bytes([0xF7, 0x27]), False)  # mul word [bx]
        cpu.r[CPU.AX] = 300
        cpu.ww(0x2000, MEM_ADDR, 1000)
        cpu.step()
        self.assertEqual((cpu.r[CPU.DX] << 16) | cpu.r[CPU.AX], 300000)
        cpu = machine(bytes([0x2E, 0xF6, 0x37]), False)  # div byte cs:[bx]
        cpu.r[CPU.AX] = 100
        cpu.mem[0x10000 + MEM_ADDR] = 7  # in CS, not in DS
        cpu.mem[0x20000 + MEM_ADDR] = 1
        cpu.step()
        self.assertEqual(cpu.r[CPU.AX], (100 % 7) << 8 | 100 // 7)

    def test_the_index_times_record_size_from_esetup(self):
        """mov ax, cx / mov dx, 14h / imul dx: item number 5 times 20 bytes per item."""
        cpu = machine(bytes([0xF7, 0xEA]), False)
        cpu.r[CPU.AX], cpu.r[CPU.DX] = 5, 0x14
        cpu.step()
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.DX]), (100, 0))


class TestSignExtension(unittest.TestCase):
    """98 CBW and 99 CWD."""

    def test_cbw(self):
        for al in range(256):
            for ah in (0x00, 0x5A, 0xFF):  # whatever AH held is replaced
                cpu = machine(bytes([0x98]), False)
                cpu.r[CPU.AX] = ah << 8 | al
                cpu.r[CPU.DX] = 0x1357
                start_flags = cpu.flags
                cpu.step()
                # AX must hold the signed value of AL, as a word
                self.assertEqual(cpu.r[CPU.AX], signed(al, 8) & 0xFFFF, f"AL={al:02X}")
                self.assertEqual((cpu.r[CPU.DX], cpu.flags, cpu.ip), (0x1357, start_flags, 1))

    def test_cwd(self):
        for ax in (0x0000, 0x0001, 0x7FFF, 0x8000, 0x8001, 0xFFFF, 0x1234, 0xA5A5):
            cpu = machine(bytes([0x99]), False)
            cpu.r[CPU.AX], cpu.r[CPU.DX] = ax, 0x1357  # the old DX is replaced
            start_flags = cpu.flags
            cpu.step()
            wide = (cpu.r[CPU.DX] << 16) | cpu.r[CPU.AX]
            self.assertEqual(signed(wide, 32), signed(ax, 16), f"AX={ax:04X}")  # same number, wider
            self.assertEqual((cpu.r[CPU.AX], cpu.flags, cpu.ip), (ax, start_flags, 1))

    def test_cwd_then_idiv_divides_a_negative_number(self):
        """The way compilers write -7 / 2: mov ax, -7 ; cwd ; idiv bx."""
        cpu = machine(bytes([0x99, 0xF7, 0xFB]), False)  # cwd ; idiv bx
        cpu.r[CPU.AX], cpu.r[CPU.BX] = 0xFFF9, 2  # -7 and 2
        cpu.step()
        cpu.step()
        self.assertEqual((signed(cpu.r[CPU.AX], 16), signed(cpu.r[CPU.DX], 16)), (-3, -1))  # towards zero, remainder -1

    def test_cbw_then_idiv_with_a_byte_divisor(self):
        cpu = machine(bytes([0x98, 0xF6, 0xFB]), False)  # cbw ; idiv bl
        cpu.r[CPU.AX], cpu.r[CPU.BX] = 0x00F9, 0x0002  # AL = -7
        cpu.step()
        cpu.step()
        self.assertEqual((signed(cpu.r[CPU.AX] & 0xFF, 8), signed(cpu.r[CPU.AX] >> 8, 8)), (-3, -1))


class TestXchg(unittest.TestCase):
    """XCHG: 90-97 (with AX) and 86, 87 (with ModRM)."""

    def test_swap_with_ax(self):
        for reg in range(1, 8):
            cpu = machine(bytes([0x90 + reg]), True)
            start_flags = cpu.flags
            model = list(REGS16)
            model[CPU.BX] = MEM_ADDR
            model[0], model[reg] = model[reg], model[0]
            cpu.step()
            self.assertEqual((cpu.r, cpu.flags, cpu.ip), (model, start_flags, 1), f"9{reg}")

    def test_90_is_still_nothing_at_all(self):
        cpu = machine(bytes([0x90]), True)
        registers, start_flags = list(cpu.r), cpu.flags
        cpu.step()
        self.assertEqual((cpu.r, cpu.flags, cpu.ip), (registers, start_flags, 1))

    def test_register_with_register(self):
        for op in (0x86, 0x87):
            bits = 16 if op & 1 else 8
            for reg in range(8):
                for rm in range(8):
                    label = f"{op:02X} reg {reg} rm {rm}"
                    cpu = machine(bytes([op, 0xC0 | reg << 3 | rm]), False)
                    model = list(REGS16)
                    model[CPU.BX] = MEM_ADDR
                    start_flags = cpu.flags
                    cpu.step()
                    a, b = get(model, rm, bits), get(model, reg, bits)
                    model = put(put(model, rm, bits, b), reg, bits, a)
                    self.assertEqual(cpu.r, model, label)
                    self.assertEqual((cpu.flags, cpu.ip), (start_flags, 2), label)

    def test_register_with_memory(self):
        for op in (0x86, 0x87):
            bits = 16 if op & 1 else 8
            size = bits // 8
            for reg in range(8):
                label = f"{op:02X} reg {reg}"
                cpu = machine(bytes([op, reg << 3 | 7]), False)  # [bx]
                cpu.mem[0x20000 + MEM_ADDR - 1:0x20000 + MEM_ADDR + 4] = bytes(MEM_BYTES)
                model = list(REGS16)
                model[CPU.BX] = MEM_ADDR
                mem = bytearray(MEM_BYTES)
                from_memory = int.from_bytes(mem[1:1 + size], "little")
                from_register = get(model, reg, bits)
                start_flags = cpu.flags
                cpu.step()
                model = put(model, reg, bits, from_memory)
                mem[1:1 + size] = from_register.to_bytes(size, "little")
                self.assertEqual(cpu.r, model, label)
                self.assertEqual(bytes(cpu.mem[0x20000 + MEM_ADDR - 1:0x20000 + MEM_ADDR + 4]), bytes(mem), label)
                self.assertEqual((cpu.flags, cpu.ip), (start_flags, 2), label)

    def test_segment_prefix_picks_the_segment_of_the_memory_operand(self):
        cpu = machine(bytes([0x26, 0x87, 0x07]), False)  # xchg ax, es:[bx]
        cpu.ww(0x3000, MEM_ADDR, 0xABCD)
        cpu.ww(0x2000, MEM_ADDR, 0x1111)
        cpu.step()
        self.assertEqual(cpu.r[CPU.AX], 0xABCD)
        self.assertEqual(cpu.rw(0x3000, MEM_ADDR), REGS16[0])  # ES got the old AX
        self.assertEqual(cpu.rw(0x2000, MEM_ADDR), 0x1111)  # DS untouched

    def test_swapping_a_register_with_itself(self):
        cpu = machine(bytes([0x87, 0xDB]), False)  # xchg bx, bx
        registers = list(cpu.r)
        cpu.step()
        self.assertEqual(cpu.r, registers)

    def test_xchg_ax_dx_from_esetup(self):
        """92 = xchg ax, dx: the C library moves the IOCTL answer from DX into AX."""
        cpu = machine(bytes([0x92]), False)
        cpu.r[CPU.AX], cpu.r[CPU.DX] = 0x44, 0x80D3
        cpu.step()
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.DX]), (0x80D3, 0x44))


class TestInstructionsThatLeaveTheFlagsAlone(unittest.TestCase):
    """Each of these must change no flag, whatever the flags were: all clear, or all raised.

    Checking one pattern only misses half of the mistakes: an instruction that wrongly SETS a flag
    cannot be seen when the flag was already set, and one that wrongly CLEARS it cannot be seen when it
    was already clear.
    """

    ALL_CLEAR = 0x0002
    ALL_RAISED = 0x0002 | flags.CF | flags.PF | flags.AF | flags.ZF | flags.SF | flags.OF | flags.DF | flags.IF

    CODES = {
        "mov r/m8,r8": [0x88, 0x07], "mov r/m16,r16": [0x89, 0x07],
        "mov r8,r/m8": [0x8A, 0x07], "mov r16,r/m16": [0x8B, 0x07],
        "mov sreg,r/m16": [0x8E, 0xD8], "mov r/m16,sreg": [0x8C, 0xD8],
        "mov al,[addr]": [0xA0, 0x00, 0x01], "mov ax,[addr]": [0xA1, 0x00, 0x01],
        "mov [addr],al": [0xA2, 0x00, 0x01], "mov [addr],ax": [0xA3, 0x00, 0x01],
        "mov r8,imm8": [0xB3, 0x05], "mov r16,imm16": [0xBB, 0x05, 0x00],
        "mov r/m8,imm8": [0xC6, 0x07, 0x05], "mov r/m16,imm16": [0xC7, 0x07, 0x05, 0x00],
        "xchg ax,r16": [0x93], "xchg r/m8,r8": [0x86, 0x07], "xchg r/m16,r16": [0x87, 0x07],
        "nop": [0x90], "cbw": [0x98], "cwd": [0x99], "pushf": [0x9C], "lahf": [0x9F],
        "push r16": [0x53], "pop r16": [0x5B], "pusha": [0x60], "popa": [0x61],
        "push imm16": [0x68, 0x34, 0x12], "push imm8": [0x6A, 0xFE], "push es": [0x06], "pop es": [0x07],
        "push r/m16": [0xFF, 0x37], "pop r/m16": [0x8F, 0x07], "lea": [0x8D, 0x07],
        "not r/m8": [0xF6, 0xD3], "not r/m16": [0xF7, 0xD3],
        "jmp short": [0xEB, 0x05], "jmp near": [0xE9, 0x05, 0x00],
        "call near": [0xE8, 0x05, 0x00], "call r/m16": [0xFF, 0xD3],
        "call far": [0x9A, 0x10, 0x00, 0x00, 0x30], "jmp far": [0xEA, 0x10, 0x00, 0x00, 0x30],
        "jmp r/m16": [0xFF, 0xE3], "ret": [0xC3], "retf": [0xCB], "leave": [0xC9],
        "enter": [0xC8, 0x08, 0x00, 0x00], "enter nested": [0xC8, 0x08, 0x00, 0x02],
        "ret n": [0xC2, 0x02, 0x00], "retf n": [0xCA, 0x02, 0x00],
        "les": [0xC4, 0x1F], "lds": [0xC5, 0x1F],
        "movsb": [0xA4], "movsw": [0xA5], "stosb": [0xAA], "stosw": [0xAB], "lodsb": [0xAC], "lodsw": [0xAD],
        "rep stosb": [0xF3, 0xAA], "rep movsw": [0xF3, 0xA5],
    }

    def test_flags_do_not_move(self):
        for name, code in self.CODES.items():
            for pattern in (self.ALL_CLEAR, self.ALL_RAISED):
                cpu = machine(bytes(code), False)
                cpu.s[CPU.SS], cpu.r[CPU.SP], cpu.r[CPU.CX] = 0x4000, 0x0200, 3
                cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x0300, 0x0400
                cpu.flags = pattern
                cpu.step()
                self.assertEqual(cpu.flags, pattern, f"{name} with flags {pattern:04X}")


class TestReturns(unittest.TestCase):
    """C3 RET, C2 RET n, CB RETF, CA RETF n."""

    def machine_with_stack(self, code, *words):
        """CS=1000, IP=0 on `code`; the stack at 4000:0200 holds `words` (the first is on top)."""
        cpu = machine(bytes(code), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        for i, word in enumerate(words):
            cpu.ww(0x4000, 0x0200 + 2 * i, word)
        return cpu

    def run_until_halt(self, cpu, limit=50):
        """Step until the program stops, but never for ever: a wrong return must fail the test, not hang it."""
        for _ in range(limit):
            try:
                cpu.step()
            except Exit:
                return
        self.fail(f"the program did not stop within {limit} instructions")

    def test_retf_pops_ip_then_cs(self):
        cpu = self.machine_with_stack([0xCB], 0x0123, 0x5678, 0x9999)
        start_flags = cpu.flags
        cpu.step()
        self.assertEqual((cpu.ip, cpu.s[CPU.CS], cpu.r[CPU.SP], cpu.flags), (0x0123, 0x5678, 0x0204, start_flags))

    def test_ret_n_drops_the_arguments_too(self):
        cpu = self.machine_with_stack([0xC2, 0x04, 0x00], 0x0123)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.s[CPU.CS], cpu.r[CPU.SP]), (0x0123, 0x1000, 0x0200 + 2 + 4))

    def test_retf_n_drops_the_arguments_too(self):
        cpu = self.machine_with_stack([0xCA, 0x06, 0x00], 0x0123, 0x5678)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.s[CPU.CS], cpu.r[CPU.SP]), (0x0123, 0x5678, 0x0200 + 4 + 6))

    def test_the_count_is_read_before_the_return_address_is_used(self):
        """The returning code must not read its own operand from the place it returns to."""
        for code, words in (([0xC2, 0x04, 0x00], (0x0500,)), ([0xCA, 0x04, 0x00], (0x0500, 0x2000))):
            cpu = self.machine_with_stack(code, *words)
            cpu.mem[0x10500:0x10502] = b"\xFF\xFF"  # what a wrong order would read as the count
            cpu.mem[0x20500:0x20502] = b"\xFF\xFF"
            cpu.step()
            self.assertEqual(cpu.r[CPU.SP], 0x0200 + 2 * len(words) + 4, code)

    def test_push_cs_and_call_then_retf_comes_back(self):
        """As in esetup.exe: push cs ; call f ; ... f: retf  (a far call done by hand)."""
        code = [0x0E, 0xE8, 0x0E, 0x00, 0xF4] + [0x90] * 13 + [0xCB]  # f is at offset 0x12
        cpu = self.machine_with_stack(code)
        self.run_until_halt(cpu)  # it returned to the HLT at offset 4 and stopped there
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.r[CPU.SP]), (0x1000, 5, 0x0200))

    def test_far_call_through_memory_and_back(self):
        cpu = self.machine_with_stack([0xFF, 0x1F])  # call far [bx]
        cpu.ww(0x2000, MEM_ADDR, 0x0010)
        cpu.ww(0x2000, MEM_ADDR + 2, 0x5000)
        cpu.mem[0x50010] = 0xCB  # retf
        cpu.step()
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.r[CPU.SP]), (0x1000, 2, 0x0200))

    def test_a_procedure_that_cleans_its_own_arguments(self):
        """push 2 words ; call f ; ... f: ret 4"""
        code = [0x50, 0x53, 0xE8, 0x01, 0x00, 0xF4, 0xC2, 0x04, 0x00]  # push ax ; push bx ; call 0x6 ; hlt ; ret 4
        cpu = self.machine_with_stack(code)
        self.run_until_halt(cpu)
        self.assertEqual(cpu.r[CPU.SP], 0x0200)  # arguments gone, nothing left on the stack


class TestLoadFarPointer(unittest.TestCase):
    """C4 LES and C5 LDS."""

    def test_les_and_lds_into_every_register(self):
        for op, segment_register in ((0xC4, CPU.ES), (0xC5, CPU.DS)):
            for reg in range(8):
                label = f"{op:02X} reg {reg}"
                cpu = machine(bytes([op, reg << 3 | 7]), False)  # [bx]
                cpu.ww(0x2000, MEM_ADDR, 0x4CB0)      # the offset
                cpu.ww(0x2000, MEM_ADDR + 2, 0x1E8A)  # the segment
                before_segments = list(cpu.s)
                before = list(cpu.r)
                cpu.step()
                before[reg] = 0x4CB0
                self.assertEqual(cpu.r, before, label)
                expected = list(before_segments)
                expected[segment_register] = 0x1E8A
                self.assertEqual(cpu.s, expected, label)
                self.assertEqual(cpu.ip, 2, label)

    def test_the_address_register_may_be_the_one_that_is_loaded(self):
        cpu = machine(bytes([0xC5, 0x1F]), False)  # lds bx, [bx]
        cpu.ww(0x2000, MEM_ADDR, 0x0777)
        cpu.ww(0x2000, MEM_ADDR + 2, 0x3333)
        cpu.step()
        self.assertEqual((cpu.r[CPU.BX], cpu.s[CPU.DS]), (0x0777, 0x3333))

    def test_les_bx_bp_plus_6_reads_the_stack_from_esetup(self):
        """C4 5E 06: a far pointer argument on the stack, at [bp+6]."""
        cpu = machine(bytes([0xC4, 0x5E, 0x06]), False)
        cpu.s[CPU.SS], cpu.r[CPU.BP] = 0x4000, 0x0100
        cpu.ww(0x4000, 0x0106, 0x4CB0)
        cpu.ww(0x4000, 0x0108, 0x1E8A)
        cpu.ww(0x2000, 0x0106, 0x1111)  # DS must not be used for a BP-based address
        cpu.step()
        self.assertEqual((cpu.r[CPU.BX], cpu.s[CPU.ES], cpu.ip), (0x4CB0, 0x1E8A, 3))

    def test_a_segment_prefix_picks_the_segment_of_the_pointer(self):
        cpu = machine(bytes([0x26, 0xC4, 0x1F]), False)  # les bx, es:[bx]
        cpu.ww(0x3000, MEM_ADDR, 0x0042)
        cpu.ww(0x3000, MEM_ADDR + 2, 0x6000)
        cpu.ww(0x2000, MEM_ADDR, 0x1111)
        cpu.step()
        self.assertEqual((cpu.r[CPU.BX], cpu.s[CPU.ES]), (0x0042, 0x6000))

    def test_the_segment_word_wraps_round_the_end_of_the_segment(self):
        cpu = machine(bytes([0xC4, 0x1F]), False)
        cpu.r[CPU.BX] = 0xFFFE
        cpu.ww(0x2000, 0xFFFE, 0x0042)  # the offset is at FFFE-FFFF
        cpu.mem[0x20000] = 0x00
        cpu.mem[0x20001] = 0x70  # the segment wraps to offset 0000
        cpu.step()
        self.assertEqual((cpu.r[CPU.BX], cpu.s[CPU.ES]), (0x0042, 0x7000))

    def test_a_register_operand_stops_loudly(self):
        for op in (0xC4, 0xC5):
            cpu = machine(bytes([op, 0xC3]), False)
            with self.assertRaises(NotImplementedError):
                cpu.step()


class TestNearJumpsAndCalls(unittest.TestCase):
    """E9 JMP rel16 and E8 CALL rel16, which share the way they work out the target."""

    def jump_target(self, code, at=0x0100):
        cpu = machine(bytes(code), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        cpu.ip = at
        cpu.mem[0x10000 + at:0x10000 + at + len(code)] = bytes(code)
        cpu.step()
        return cpu

    def test_jmp_rel16_is_counted_from_the_next_instruction(self):
        for displacement in (0, 1, 0x7FFF, 0x8000, 0xFFFF, 0xFF3F, 0x1234):
            for at in (0x0100, 0x4196, 0x0000, 0xFFF0):
                cpu = self.jump_target([0xE9, displacement & 0xFF, displacement >> 8], at)
                signed_d = displacement - 0x10000 if displacement & 0x8000 else displacement
                self.assertEqual(cpu.ip, (at + 3 + signed_d) & 0xFFFF, f"at {at:04X} by {displacement:04X}")
                self.assertEqual((cpu.r[CPU.SP], cpu.s[CPU.CS]), (0x0200, 0x1000))  # no push, no change of CS

    def test_the_jump_from_esetup(self):
        """4196: E9 3F FF is jmp 40D8; the same address the earlier checks jump to."""
        cpu = self.jump_target([0xE9, 0x3F, 0xFF], at=0x4196)
        self.assertEqual(cpu.ip, 0x40D8)

    def test_a_distance_of_minus_three_jumps_to_itself(self):
        cpu = self.jump_target([0xE9, 0xFD, 0xFF], at=0x0100)
        self.assertEqual(cpu.ip, 0x0100)

    def test_it_wraps_round_the_segment(self):
        cpu = self.jump_target([0xE9, 0x00, 0x10], at=0xF800)  # forward 0x1000 from the end
        self.assertEqual(cpu.ip, 0x0803)
        cpu = self.jump_target([0xE9, 0xFB, 0xFF], at=0x0001)  # back 5 from the start
        self.assertEqual(cpu.ip, 0xFFFF)

    def test_call_pushes_the_address_after_the_instruction(self):
        for displacement in (0x0155, 0xFFFB, 0x0000):
            cpu = self.jump_target([0xE8, displacement & 0xFF, displacement >> 8], at=0x0100)
            signed_d = displacement - 0x10000 if displacement & 0x8000 else displacement
            self.assertEqual(cpu.ip, (0x0103 + signed_d) & 0xFFFF, f"{displacement:04X}")
            self.assertEqual((cpu.r[CPU.SP], cpu.rw(0x4000, cpu.r[CPU.SP])), (0x01FE, 0x0103))

    def test_the_long_conditional_jump_idiom(self):
        """or ax, dx ; jne skip ; jmp far_away ; skip: ...  jumps away only when DX:AX is zero."""
        for ax, dx, jumps in ((0, 0, True), (1, 0, False), (0, 1, False), (0x8000, 0x8000, False)):
            code = [0x0B, 0xC2, 0x75, 0x03, 0xE9, 0x00, 0x10, 0x90]  # or ax,dx ; jne +3 ; jmp +0x1000 ; nop
            cpu = machine(bytes(code), False)
            cpu.r[CPU.AX], cpu.r[CPU.DX] = ax, dx
            for _ in range(3 if jumps else 2):
                cpu.step()
            self.assertEqual(cpu.ip, 0x1007 if jumps else 7, f"AX={ax:X} DX={dx:X}")


class TestCarryFlagInstructions(unittest.TestCase):
    """F5 CMC, F8 CLC, F9 STC: each changes the carry flag and no other."""

    OTHERS = [0x0002, 0x0FD7 & ~flags.CF, 0x0002 | flags.ZF, 0x0002 | flags.SF | flags.OF, 0x0002 | flags.DF | flags.IF,
              0x0002 | flags.AF | flags.PF | flags.TF]

    def run_one(self, opcode, others, carry):
        cpu = machine(bytes([opcode]), False)
        cpu.flags = others | (flags.CF if carry else 0)
        cpu.step()
        return cpu

    def test_cmc_turns_the_carry_flag_the_other_way_round(self):
        for others in self.OTHERS:
            for carry in (False, True):
                cpu = self.run_one(0xF5, others, carry)
                self.assertEqual(cpu.get_flag(flags.CF), not carry, (hex(others), carry))
                self.assertEqual(cpu.flags & ~flags.CF, others & ~flags.CF, (hex(others), carry))
                self.assertEqual(cpu.ip, 1)

    def test_two_cmc_in_a_row_make_no_difference(self):
        for carry in (False, True):
            cpu = machine(bytes([0xF5, 0xF5]), False)
            cpu.flags = 0x0FD7 & ~flags.CF | (flags.CF if carry else 0)
            before = cpu.flags
            cpu.step()
            cpu.step()
            self.assertEqual((cpu.flags, cpu.ip), (before, 2))

    def test_clc_clears_and_stc_sets_the_carry_and_nothing_else(self):
        for others in self.OTHERS:
            for carry in (False, True):
                for opcode, expected in ((0xF8, False), (0xF9, True)):
                    cpu = self.run_one(opcode, others, carry)
                    self.assertEqual(cpu.get_flag(flags.CF), expected, (hex(opcode), hex(others), carry))
                    self.assertEqual(cpu.flags & ~flags.CF, others & ~flags.CF)
                    self.assertEqual(cpu.ip, 1)

    def test_cmc_changes_no_register(self):
        cpu = machine(bytes([0xF5]), True)
        before = (list(cpu.r), list(cpu.s))
        cpu.step()
        self.assertEqual((cpu.r, cpu.s), before)

    def test_sub_cmc_sbb_makes_a_mask_that_is_ffff_when_there_was_no_borrow(self):
        # sub ax, dx ; cmc ; sbb cx, cx : CX is 0000h if AX was below DX, FFFFh if not (as in xcomapoc.exe)
        for ax in (0, 1, 5, 6, 7, 100, 0x7FFF, 0x8000, 0xFFFF):
            cpu = machine(bytes([0x29, 0xD0, 0xF5, 0x19, 0xC9]), False)
            cpu.r[CPU.AX], cpu.r[CPU.DX], cpu.r[CPU.CX] = ax, 6, 0x1234
            for _ in range(3):
                cpu.step()
            self.assertEqual(cpu.r[CPU.CX], 0x0000 if ax < 6 else 0xFFFF, ax)

    def test_the_whole_idiom_of_xcomapoc_gives_the_larger_of_the_number_and_6(self):
        # sub ax,dx ; cmc ; sbb cx,cx ; and ax,cx ; add ax,dx  with DX = 6
        for ax in range(0, 40):
            cpu = machine(bytes([0x29, 0xD0, 0xF5, 0x19, 0xC9, 0x21, 0xC8, 0x01, 0xD0]), False)
            cpu.r[CPU.AX], cpu.r[CPU.DX] = ax, 6
            for _ in range(5):
                cpu.step()
            self.assertEqual(cpu.r[CPU.AX], max(ax, 6), ax)
            self.assertEqual(cpu.ip, 9)


class TestFlagsRegisterInstructions(unittest.TestCase):
    """9C PUSHF, 9D POPF, 9E SAHF, 9F LAHF."""

    PATTERNS = [0x0002, 0x0FD7, 0x0002 | flags.CF, 0x0002 | flags.ZF | flags.SF, 0x0002 | flags.IF | flags.DF,
                0x0002 | flags.OF | flags.AF | flags.PF, 0x0002 | flags.TF]

    def machine_with_stack(self, code):
        cpu = machine(bytes(code), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        return cpu

    def test_pushf_puts_the_flags_on_the_stack_and_changes_none(self):
        for pattern in self.PATTERNS:
            cpu = self.machine_with_stack([0x9C])
            cpu.flags = pattern
            cpu.step()
            self.assertEqual((cpu.rw(0x4000, 0x01FE), cpu.r[CPU.SP], cpu.flags), (pattern, 0x01FE, pattern))

    def test_popf_takes_only_the_flags_that_exist(self):
        for word in (0x0000, 0xFFFF, 0x0FD7, 0xF000, 0x0001, 0x0200, 0x1234, 0xAAAA):
            cpu = self.machine_with_stack([0x9D])
            cpu.ww(0x4000, 0x0200, word)
            cpu.step()
            # the nine flags, IOPL and NT (what a 386 keeps in real mode), and bit 1 always set; bit 15 is never set
            self.assertEqual(cpu.flags, (word & 0x7FD5) | 0x0002, f"{word:04X}")
            self.assertEqual(cpu.r[CPU.SP], 0x0202)

    def test_popf_keeps_iopl_and_nt_which_is_how_a_386_is_told_from_a_286(self):
        for word, kept in ((0x3000, 0x3000), (0x4000, 0x4000), (0x7000, 0x7000), (0x1000, 0x1000), (0x8000, 0)):
            cpu = self.machine_with_stack([0x9D, 0x9C])  # popf ; pushf
            cpu.ww(0x4000, 0x0200, word)
            cpu.step()
            cpu.step()
            self.assertEqual(cpu.rw(0x4000, 0x0200) & 0xF000, kept, f"{word:04X}")

    def test_pushf_cli_popf_puts_the_interrupt_flag_back(self):
        """As in esetup.exe: save the flags, switch interrupts off, restore."""
        for start_if in (True, False):
            cpu = self.machine_with_stack([0x9C, 0xFA, 0x9D])  # pushf ; cli ; popf
            cpu.set_flag(flags.IF, start_if)
            cpu.step()
            cpu.step()
            self.assertFalse(cpu.get_flag(flags.IF))
            cpu.step()
            self.assertEqual((cpu.get_flag(flags.IF), cpu.r[CPU.SP]), (start_if, 0x0200))

    def test_pushf_stc_popf_forgets_the_carry(self):
        cpu = self.machine_with_stack([0x9C, 0xF9, 0x9D])  # pushf ; stc ; popf
        for _ in range(3):
            cpu.step()
        self.assertFalse(cpu.get_flag(flags.CF))

    def test_lahf_copies_the_five_low_flags_into_ah(self):
        for pattern in self.PATTERNS:
            cpu = machine(bytes([0x9F]), False)
            cpu.flags = pattern
            cpu.r[CPU.AX] = 0x1234
            cpu.step()
            low = pattern & (flags.SF | flags.ZF | flags.AF | flags.PF | flags.CF)
            self.assertEqual(cpu.r[CPU.AX], (low | 0x02) << 8 | 0x34, f"{pattern:04X}")  # AL is not touched
            self.assertEqual(cpu.flags, pattern)

    def test_sahf_sets_only_the_five_low_flags(self):
        high = flags.OF | flags.DF | flags.IF | flags.TF
        for ah in range(256):
            for start in (0x0002, 0x0002 | high, 0x0FD7):
                cpu = machine(bytes([0x9E]), False)
                cpu.flags = start
                cpu.r[CPU.AX] = ah << 8 | 0x77
                cpu.step()
                low = flags.SF | flags.ZF | flags.AF | flags.PF | flags.CF
                self.assertEqual(cpu.flags, (start & ~low) | (ah & low), f"AH={ah:02X} start={start:04X}")
                self.assertEqual(cpu.r[CPU.AX], ah << 8 | 0x77)  # AX is left alone

    def test_lahf_then_sahf_round_trip(self):
        """Save the low flags in AH, spoil them, and get them back; the high flags are not part of it."""
        low = flags.SF | flags.ZF | flags.AF | flags.PF | flags.CF
        for pattern in self.PATTERNS:
            cpu = machine(bytes([0x9F, 0x9E]), False)  # lahf ; sahf
            cpu.flags = pattern
            cpu.step()
            cpu.flags = (pattern & ~low) | (~pattern & low)  # every low flag turned over
            cpu.step()
            self.assertEqual(cpu.flags, pattern, f"{pattern:04X}")


class FakePorts:
    """A pretend device on a few ports: remembers what was written, answers reads from a table."""

    def __init__(self, cpu, ports, answers=None):
        self.written = []  # (port, byte) in the order of the writes
        self.answers = dict(answers or {})
        for port in ports:
            cpu.port_readers[port] = self.read
            cpu.port_writers[port] = self.write

    def read(self, port):
        return self.answers[port]

    def write(self, port, value):
        self.written.append((port, value))


class TestPortInstructions(unittest.TestCase):
    """IN and OUT: E4-E7 with the port in the next byte, EC-EF with the port in DX."""

    def test_out_byte_with_an_immediate_port(self):
        cpu = machine(bytes([0xE6, 0x43]), False)  # out 43h, al
        device = FakePorts(cpu, [0x43])
        cpu.r[CPU.AX] = 0x1234
        cpu.step()
        self.assertEqual((device.written, cpu.ip, cpu.r[CPU.AX]), ([(0x43, 0x34)], 2, 0x1234))

    def test_in_byte_with_an_immediate_port_leaves_ah_alone(self):
        cpu = machine(bytes([0xE4, 0x40]), False)  # in al, 40h
        FakePorts(cpu, [0x40], {0x40: 0xAB})
        cpu.r[CPU.AX] = 0x1234
        cpu.step()
        self.assertEqual((cpu.r[CPU.AX], cpu.ip), (0x12AB, 2))

    def test_the_port_in_dx_may_be_any_of_65536(self):
        for port in (0x0000, 0x0060, 0x03F8, 0xFFFE):
            cpu = machine(bytes([0xEE, 0xEC]), False)  # out dx, al ; in al, dx
            device = FakePorts(cpu, [port], {port: 0x5A})
            cpu.r[CPU.DX], cpu.r[CPU.AX] = port, 0x00C3
            cpu.step()
            cpu.step()
            self.assertEqual(device.written, [(port, 0xC3)], f"{port:04X}")
            self.assertEqual(cpu.r[CPU.AX], 0x005A, f"{port:04X}")

    def test_a_word_goes_to_two_neighbouring_ports_low_byte_first(self):
        cpu = machine(bytes([0xE7, 0x20]), False)  # out 20h, ax
        device = FakePorts(cpu, [0x20, 0x21])
        cpu.r[CPU.AX] = 0xBEEF
        cpu.step()
        self.assertEqual(device.written, [(0x20, 0xEF), (0x21, 0xBE)])

    def test_a_word_is_read_from_two_neighbouring_ports(self):
        cpu = machine(bytes([0xED]), False)  # in ax, dx
        FakePorts(cpu, [0x70, 0x71], {0x70: 0x34, 0x71: 0x12})
        cpu.r[CPU.DX] = 0x70
        cpu.step()
        self.assertEqual(cpu.r[CPU.AX], 0x1234)

    def test_the_second_port_of_a_word_wraps_from_ffff_to_0000(self):
        cpu = machine(bytes([0xEF]), False)  # out dx, ax
        device = FakePorts(cpu, [0xFFFF, 0x0000])
        cpu.r[CPU.DX], cpu.r[CPU.AX] = 0xFFFF, 0xA1B2
        cpu.step()
        self.assertEqual(device.written, [(0xFFFF, 0xB2), (0x0000, 0xA1)])

    def test_the_immediate_port_is_one_byte_only(self):
        cpu = machine(bytes([0xE6, 0xFF]), False)  # out FFh, al: the highest port the short form reaches
        device = FakePorts(cpu, [0xFF])
        cpu.step()
        self.assertEqual(device.written[0][0], 0xFF)

    def test_a_device_answer_is_cut_to_a_byte(self):
        cpu = machine(bytes([0xE4, 0x40]), False)
        FakePorts(cpu, [0x40], {0x40: 0x1FF})
        cpu.step()
        self.assertEqual(cpu.r[CPU.AX] & 0xFFFF, REGS16[0] & 0xFF00 | 0xFF)

    def test_flags_stay_as_they_were(self):
        for code in ([0xE4, 0x40], [0xE5, 0x40], [0xE6, 0x40], [0xE7, 0x40], [0xEC], [0xED], [0xEE], [0xEF]):
            for pattern in (0x0002, 0x0FD7):
                cpu = machine(bytes(code), False)
                FakePorts(cpu, [0x40, 0x41, 0x00, 0x01, 0x0100], {0x40: 1, 0x41: 2, 0x00: 3, 0x01: 4, 0x0100: 5})
                cpu.r[CPU.DX] = 0x40
                cpu.flags = pattern
                cpu.step()
                self.assertEqual(cpu.flags, pattern, f"{bytes(code).hex()} {pattern:04X}")

    def test_a_port_with_no_device_stops_loudly_and_says_which(self):
        for code, text in (([0xE6, 0x43], "OUT to port 0043"), ([0xE4, 0x40], "IN from port 0040"),
                           ([0xEE], "OUT to port 03F8"), ([0xEC], "IN from port 03F8")):
            cpu = machine(bytes(code), False)
            cpu.r[CPU.DX] = 0x03F8
            with self.assertRaises(NotImplementedError) as caught:
                cpu.step()
            self.assertIn(text, str(caught.exception))
            self.assertIn("1000:0000", str(caught.exception))


class TestTestInstruction(unittest.TestCase):
    """TEST: AND for the flags only. 84 and 85 take ModRM; A8 and A9 take AL or AX and a constant."""

    VALUES = {8: [0x00, 0x01, 0x02, 0x07, 0x7F, 0x80, 0xFF, 0x10],
              16: [0x0000, 0x0001, 0x0002, 0x7FFF, 0x8000, 0xFFFF, 0x1234, 0x0100]}

    def test_register_with_register(self):
        for op in (0x84, 0x85):
            bits = 16 if op & 1 else 8
            for reg in range(8):
                for rm in range(8):
                    for cf in (False, True):
                        label = f"{op:02X} reg {reg} rm {rm} cf {cf}"
                        cpu = machine(bytes([op, 0xC0 | reg << 3 | rm]), cf)
                        model = list(REGS16)
                        model[CPU.BX] = MEM_ADDR
                        cpu.step()
                        shared = get(model, rm, bits) & get(model, reg, bits)
                        self.assertEqual(cpu.r, model, label)  # nothing is stored
                        self.assertEqual(cpu_flags(cpu), logic_flags(shared, bits), label)
                        self.assertEqual(cpu.ip, 2, label)

    def test_register_with_memory(self):
        for op in (0x84, 0x85):
            bits = 16 if op & 1 else 8
            size = bits // 8
            for reg in range(8):
                label = f"{op:02X} reg {reg}"
                cpu = machine(bytes([op, reg << 3 | 7]), False)  # [bx]
                cpu.mem[0x20000 + MEM_ADDR - 1:0x20000 + MEM_ADDR + 4] = bytes(MEM_BYTES)
                model = list(REGS16)
                model[CPU.BX] = MEM_ADDR
                from_memory = int.from_bytes(bytes(MEM_BYTES)[1:1 + size], "little")
                cpu.step()
                self.assertEqual(cpu.r, model, label)
                self.assertEqual(bytes(cpu.mem[0x20000 + MEM_ADDR - 1:0x20000 + MEM_ADDR + 4]), bytes(MEM_BYTES), label)
                self.assertEqual(cpu_flags(cpu), logic_flags(from_memory & get(model, reg, bits), bits), label)

    def test_accumulator_with_constant(self):
        for op in (0xA8, 0xA9):
            bits = 16 if op & 1 else 8
            for imm in self.VALUES[bits]:
                for ax in (0x1234, 0x0000, 0xFFFF, 0x8001):
                    label = f"{op:02X} imm {imm:X} AX {ax:04X}"
                    code = bytes([op]) + imm.to_bytes(bits // 8, "little")
                    cpu = machine(code, False)
                    cpu.r[CPU.AX] = ax
                    cpu.step()
                    self.assertEqual(cpu.r[CPU.AX], ax, label)  # AX is not changed
                    self.assertEqual(cpu_flags(cpu), logic_flags(ax & mask(bits) & imm, bits), label)
                    self.assertEqual(cpu.ip, len(code), label)

    def test_a_segment_prefix_picks_the_segment_of_the_memory_operand(self):
        cpu = machine(bytes([0x26, 0x85, 0x07]), False)  # test es:[bx], ax
        cpu.ww(0x3000, MEM_ADDR, 0x0001)
        cpu.ww(0x2000, MEM_ADDR, 0x0000)
        cpu.r[CPU.AX] = 0x0001
        cpu.step()
        self.assertFalse(cpu.get_flag(flags.ZF))  # ES holds a 1: they share a bit; DS holds 0: they would not

    def test_the_odd_number_check_from_esetup(self):
        """test ax, 1 ; jne +0Eh: taken when the number is odd."""
        for ax, taken in ((5, True), (4, False), (0xFFFF, True), (0, False), (0x8000, False)):
            cpu = machine(bytes([0xA9, 0x01, 0x00, 0x75, 0x0E]), False)
            cpu.r[CPU.AX] = ax
            cpu.step()
            cpu.step()
            self.assertEqual(cpu.ip, 5 + 0x0E if taken else 5, f"AX={ax:04X}")


class TestPopIntoOperand(unittest.TestCase):
    """8F /0: pop word into a register or memory."""

    def machine_with_stack(self, code, *words):
        cpu = machine(bytes(code), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        for i, word in enumerate(words):
            cpu.ww(0x4000, 0x0200 + 2 * i, word)
        return cpu

    def test_into_memory_at_a_direct_address_from_esetup(self):
        cpu = self.machine_with_stack([0x8F, 0x06, 0x4E, 0x21, 0x8F, 0x06, 0x50, 0x21], 0x1234, 0xABCD, 0x9999)
        cpu.step()
        cpu.step()
        self.assertEqual((cpu.rw(0x2000, 0x214E), cpu.rw(0x2000, 0x2150)), (0x1234, 0xABCD))  # the first word first
        self.assertEqual((cpu.r[CPU.SP], cpu.ip), (0x0204, 8))

    def test_into_every_register(self):
        for reg in range(8):
            cpu = self.machine_with_stack([0x8F, 0xC0 | reg], 0xBEEF)
            before = list(cpu.r)
            start_flags = cpu.flags
            cpu.step()
            before[reg] = 0xBEEF
            before[CPU.SP] = 0xBEEF if reg == CPU.SP else 0x0202  # pop sp leaves the popped word in SP
            self.assertEqual(cpu.r, before, f"reg {reg}")
            self.assertEqual(cpu.flags, start_flags)

    def test_the_address_may_use_registers_and_a_displacement(self):
        cpu = self.machine_with_stack([0x8F, 0x47, 0xFE], 0x7777)  # pop word [bx-2]
        cpu.step()
        self.assertEqual(cpu.rw(0x2000, MEM_ADDR - 2), 0x7777)

    def test_a_segment_prefix_picks_the_segment_of_the_destination(self):
        cpu = self.machine_with_stack([0x26, 0x8F, 0x07], 0x5555)  # pop word es:[bx]
        cpu.step()
        self.assertEqual((cpu.rw(0x3000, MEM_ADDR), cpu.rw(0x2000, MEM_ADDR)), (0x5555, 0))

    def test_only_a_word_is_written(self):
        cpu = self.machine_with_stack([0x8F, 0x07], 0x1122)  # pop word [bx]
        cpu.mem[0x20000 + MEM_ADDR - 1] = 0xDD
        cpu.mem[0x20000 + MEM_ADDR + 2] = 0xEE
        cpu.step()
        self.assertEqual((cpu.mem[0x20000 + MEM_ADDR - 1], cpu.mem[0x20000 + MEM_ADDR + 2]), (0xDD, 0xEE))

    def test_push_and_pop_of_memory_round_trip(self):
        cpu = self.machine_with_stack([0xFF, 0x37, 0x8F, 0x47, 0x04])  # push word [bx] ; pop word [bx+4]
        cpu.ww(0x2000, MEM_ADDR, 0xC0DE)
        cpu.step()
        cpu.step()
        self.assertEqual((cpu.rw(0x2000, MEM_ADDR + 4), cpu.r[CPU.SP]), (0xC0DE, 0x0200))

    def test_the_other_values_of_the_reg_field_stop_loudly(self):
        for reg in range(1, 8):
            cpu = self.machine_with_stack([0x8F, 0xC0 | reg << 3], 0x1234)
            with self.assertRaises(NotImplementedError, msg=f"/{reg}"):
                cpu.step()


class TestFarCallAndJump(unittest.TestCase):
    """9A CALL FAR and EA JMP FAR, with the address in the instruction."""

    def at(self, code, ip=0x0100):
        cpu = machine(bytes(code), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        cpu.ip = ip
        cpu.mem[0x10000 + ip:0x10000 + ip + len(code)] = bytes(code)
        return cpu

    def test_the_call_from_esetup_goes_to_main(self):
        """014C: 9A 0F 00 EC 14 is call 14EC:000F; the return address is the next instruction."""
        cpu = self.at([0x9A, 0x0F, 0x00, 0xEC, 0x14], ip=0x014C)
        start_flags = cpu.flags
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x14EC, 0x000F))  # the offset comes first in the bytes
        self.assertEqual((cpu.r[CPU.SP], cpu.rw(0x4000, 0x01FE), cpu.rw(0x4000, 0x01FC)), (0x01FC, 0x1000, 0x0151))
        self.assertEqual(cpu.flags, start_flags)

    def test_call_far_pushes_cs_then_ip(self):
        cpu = self.at([0x9A, 0x34, 0x12, 0x78, 0x56], ip=0x0300)
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x5678, 0x1234))
        # IP is on top and CS under it: exactly what RETF takes off again, in that order
        self.assertEqual((self.word(cpu, 0), self.word(cpu, 1)), (0x0305, 0x1000))

    def word(self, cpu, depth):
        return cpu.rw(0x4000, cpu.r[CPU.SP] + 2 * depth)

    def test_retf_comes_back_to_the_instruction_after_a_far_call(self):
        cpu = self.at([0x9A, 0x10, 0x00, 0x00, 0x50])
        cpu.mem[0x50010] = 0xCB  # retf, at 5000:0010
        cpu.step()
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.r[CPU.SP]), (0x1000, 0x0105, 0x0200))

    def test_jmp_far_changes_cs_and_ip_and_pushes_nothing(self):
        for offset, segment in ((0x0000, 0x0000), (0x0100, 0x2000), (0xFFFF, 0xFFFF), (0x1234, 0x5678)):
            cpu = self.at([0xEA, offset & 0xFF, offset >> 8, segment & 0xFF, segment >> 8])
            start_flags = cpu.flags
            cpu.step()
            self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.r[CPU.SP], cpu.flags), (segment, offset, 0x0200, start_flags))

    def test_the_far_call_through_memory_does_the_same(self):
        """FF /3 and 9A are two ways to write one operation: the stack and the registers end up alike."""
        direct = self.at([0x9A, 0x34, 0x12, 0x78, 0x56], ip=0x0000)
        direct.step()
        indirect = self.at([0xFF, 0x1F], ip=0x0000)  # call far [bx]
        indirect.ww(0x2000, MEM_ADDR, 0x1234)
        indirect.ww(0x2000, MEM_ADDR + 2, 0x5678)
        indirect.step()
        registers = lambda cpu: (cpu.s[CPU.CS], cpu.ip, cpu.r[CPU.SP])
        self.assertEqual(registers(direct), registers(indirect))
        self.assertEqual((self.word(direct, 1)), self.word(indirect, 1))  # the same segment under the return address


class TestPushImmediate(unittest.TestCase):
    """68 PUSH imm16 and 6A PUSH imm8 (80186 and later)."""

    def pushed(self, code, sp=0x0200):
        cpu = machine(bytes(code), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, sp
        start_flags = cpu.flags
        cpu.step()
        self.assertEqual(cpu.flags, start_flags)
        return cpu

    def test_68_pushes_the_word(self):
        for value in (0x0000, 0x0001, 0x1234, 0x7FFF, 0x8000, 0xFFFF):
            cpu = self.pushed([0x68, value & 0xFF, value >> 8])
            self.assertEqual((cpu.r[CPU.SP], cpu.rw(0x4000, 0x01FE), cpu.ip), (0x01FE, value, 3), hex(value))

    def test_6a_pushes_a_byte_widened_to_a_word_by_its_sign(self):
        for byte, word in ((0x00, 0x0000), (0x02, 0x0002), (0x7F, 0x007F), (0x80, 0xFF80), (0xFE, 0xFFFE),
                           (0xFF, 0xFFFF)):
            cpu = self.pushed([0x6A, byte])
            self.assertEqual((cpu.r[CPU.SP], cpu.rw(0x4000, 0x01FE), cpu.ip), (0x01FE, word, 2), hex(byte))

    def test_6a_is_the_short_form_of_68(self):
        for byte in range(256):
            short = self.pushed([0x6A, byte])
            word = signed(byte, 8) & 0xFFFF
            long = self.pushed([0x68, word & 0xFF, word >> 8])
            self.assertEqual((short.r, short.mem[0x40000:0x50000]), (long.r, long.mem[0x40000:0x50000]), hex(byte))

    def test_only_the_two_bytes_of_the_word_are_written_and_the_others_are_left_alone(self):
        before = machine(b"", False)
        cpu = self.pushed([0x68, 0x34, 0x12])
        self.assertEqual(cpu.mem[0x401FE:0x40200], b"\x34\x12")
        changed = [i for i in range(0x40000, 0x50000) if cpu.mem[i] != before.mem[i]]
        self.assertEqual(changed, [0x401FE, 0x401FF])
        for number in (CPU.AX, CPU.BX, CPU.CX, CPU.DX, CPU.BP, CPU.SI, CPU.DI):
            self.assertEqual(cpu.r[number], before.r[number])

    def test_sp_wraps_round_the_segment(self):
        cpu = self.pushed([0x6A, 0x05], sp=0x0000)
        self.assertEqual((cpu.r[CPU.SP], cpu.rw(0x4000, 0xFFFE)), (0xFFFE, 5))

    def test_the_constant_is_read_after_the_opcode_not_from_the_stack_segment(self):
        cpu = machine(bytes([0x6A, 0x07]), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        cpu.mem[0x40000:0x40002] = bytes([0x6A, 0x09])
        cpu.step()
        self.assertEqual(cpu.rw(0x4000, 0x01FE), 7)


class TestPushaPopa(unittest.TestCase):
    """60 PUSHA and 61 POPA (80186 and later)."""

    VALUES = [0x1111, 0x2222, 0x3333, 0x4444, 0x0200, 0x6666, 0x7777, 0x8888]  # AX CX DX BX SP BP SI DI

    def machine_with_registers(self, code):
        cpu = machine(bytes(code), False)
        cpu.s[CPU.SS] = 0x4000
        cpu.r[:] = self.VALUES
        return cpu

    def test_pusha_pushes_the_eight_registers_with_di_on_top(self):
        cpu = self.machine_with_registers([0x60])
        start_flags = cpu.flags
        cpu.step()
        self.assertEqual(cpu.r[CPU.SP], 0x0200 - 16)
        # from the top of the stack downwards: DI SI BP SP BX DX CX AX
        stack = [cpu.rw(0x4000, cpu.r[CPU.SP] + 2 * i) for i in range(8)]
        self.assertEqual(stack, [0x8888, 0x7777, 0x6666, 0x0200, 0x4444, 0x3333, 0x2222, 0x1111])
        self.assertEqual((cpu.flags, cpu.ip), (start_flags, 1))

    def test_the_sp_that_is_pushed_is_the_one_from_before_the_instruction(self):
        for sp in (0x0200, 0x0010, 0x0000, 0xFFF0):
            cpu = self.machine_with_registers([0x60])
            cpu.r[CPU.SP] = sp
            cpu.step()
            # the fifth word pushed, after AX, CX, DX and BX, lies 10 bytes below where SP started
            self.assertEqual(cpu.rw(0x4000, (sp - 10) & 0xFFFF), sp, f"SP={sp:04X}")

    def test_pusha_leaves_every_register_but_sp_as_it_was(self):
        cpu = self.machine_with_registers([0x60])
        cpu.step()
        expected = list(self.VALUES)
        expected[CPU.SP] = 0x0200 - 16
        self.assertEqual(cpu.r, expected)

    def test_popa_loads_all_but_sp_and_drops_the_saved_sp(self):
        cpu = self.machine_with_registers([0x61])
        words = [0xD1, 0x51, 0xBB, 0x5555, 0xB1, 0xD2, 0xC3, 0xA4]  # DI SI BP (SP slot) BX DX CX AX, from the top
        for i, word in enumerate(words):
            cpu.ww(0x4000, 0x0200 + 2 * i, word)
        start_flags = cpu.flags
        cpu.step()
        self.assertEqual(cpu.r[CPU.DI], 0xD1)
        self.assertEqual((cpu.r[CPU.SI], cpu.r[CPU.BP]), (0x51, 0xBB))
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.DX], cpu.r[CPU.CX], cpu.r[CPU.AX]), (0xB1, 0xD2, 0xC3, 0xA4))
        self.assertEqual(cpu.r[CPU.SP], 0x0210)  # 16 bytes up; the 5555 in the SP slot was not used
        self.assertEqual((cpu.flags, cpu.ip), (start_flags, 1))

    def test_pusha_then_popa_gives_everything_back(self):
        # pusha ; mov ax,1234h ; mov bx,5678h ; popa
        cpu = self.machine_with_registers([0x60, 0xB8, 0x34, 0x12, 0xBB, 0x78, 0x56, 0x61])
        for _ in range(4):
            cpu.step()
        self.assertEqual(cpu.r, self.VALUES)  # SP too: it is back where it started

    def test_the_stack_wraps_round_the_segment(self):
        cpu = self.machine_with_registers([0x60, 0x61])
        cpu.r[CPU.SP] = 0x0004  # the eight words need room that wraps through 0000
        cpu.step()
        cpu.step()
        self.assertEqual(cpu.r, [self.VALUES[0], self.VALUES[1], self.VALUES[2], self.VALUES[3],
                                 0x0004, self.VALUES[5], self.VALUES[6], self.VALUES[7]])


class TestLea(unittest.TestCase):
    """8D: the address of the operand, not what is stored there."""

    BASES = [(CPU.BX, CPU.SI), (CPU.BX, CPU.DI), (CPU.BP, CPU.SI), (CPU.BP, CPU.DI),
             (CPU.SI,), (CPU.DI,), (CPU.BP,), (CPU.BX,)]  # by the r/m field, for mod 00, 01 and 10
    REGISTERS = {CPU.BX: 0x1000, CPU.SI: 0x0230, CPU.DI: 0x0045, CPU.BP: 0x0F00}

    def lea(self, code, registers=None):
        """Run one LEA with the registers of REGISTERS, changed by `registers` (a dict: register number -> value)."""
        cpu = machine(bytes(code), False)
        for reg, value in {**self.REGISTERS, **(registers or {})}.items():
            cpu.r[reg] = value
        cpu.step()
        return cpu

    def test_the_address_of_a_local_variable_from_esetup(self):
        """8D 46 80 = lea ax, [bp-80h]: where the 128-byte buffer starts."""
        cpu = machine(bytes([0x8D, 0x46, 0x80]), False)
        cpu.r[CPU.BP] = 0x1000
        cpu.ww(0x2000, 0x0F80, 0xBEEF)  # something is stored there, and LEA must not read it
        cpu.step()
        self.assertEqual((cpu.r[CPU.AX], cpu.ip), (0x0F80, 3))

    def test_every_addressing_mode_into_every_register(self):
        for mod, size in ((0, 0), (1, 1), (2, 2)):
            for rm in range(8):
                if mod == 0 and rm == 6:
                    continue  # [disp16], tested on its own
                for reg in range(8):
                    for disp in ([0] if mod == 0 else [5, -5] if mod == 1 else [0x1234, -0x1234]):
                        label = f"mod {mod} rm {rm} reg {reg} disp {disp}"
                        displacement = (disp & ((1 << 8 * size) - 1)).to_bytes(size, "little")
                        code = [0x8D, mod << 6 | reg << 3 | rm] + list(displacement)
                        cpu = self.lea(code)
                        base = sum(self.REGISTERS[b] for b in self.BASES[rm])
                        before = [self.REGISTERS.get(i, REGS16[i]) for i in range(8)]
                        self.assertEqual(cpu.r[reg], (base + disp) & 0xFFFF, label)
                        # nothing else changed, unless the destination is one of the registers of the address
                        for other in range(8):
                            if other != reg:
                                self.assertEqual(cpu.r[other], before[other], label)
                        self.assertEqual(cpu.ip, 2 + size, label)

    def test_a_direct_address(self):
        cpu = self.lea([0x8D, 0x06, 0x00, 0x04])  # lea ax, [0400h]
        self.assertEqual(cpu.r[CPU.AX], 0x0400)

    def test_the_result_wraps_round_64k(self):
        cpu = self.lea([0x8D, 0x47, 0x05], {CPU.BX: 0xFFFF})  # lea ax, [bx+5]
        self.assertEqual(cpu.r[CPU.AX], 0x0004)
        cpu = self.lea([0x8D, 0x46, 0x80], {CPU.BP: 0x0010})  # lea ax, [bp-80h]
        self.assertEqual(cpu.r[CPU.AX], 0xFF90)

    def test_memory_is_not_read_or_written(self):
        cpu = machine(bytes([0x8D, 0x07]), False)  # lea ax, [bx]
        cpu.r[CPU.BX] = 0x0100
        cpu.ww(0x2000, 0x0100, 0xAAAA)
        memory_before = bytes(cpu.mem[:1024 * 1024])  # the megabyte of DOS: a copy of all the memory is 256 MB
        cpu.step()
        self.assertEqual((cpu.r[CPU.AX], bytes(cpu.mem[:1024 * 1024])), (0x0100, memory_before))

    def test_a_segment_prefix_changes_nothing(self):
        plain = self.lea([0x8D, 0x07])
        for prefix in (0x26, 0x2E, 0x36, 0x3E):
            cpu = self.lea([prefix, 0x8D, 0x07])
            self.assertEqual((cpu.r[CPU.AX], cpu.s), (plain.r[CPU.AX], plain.s))

    def test_it_adds_without_touching_the_flags(self):
        for pattern in (0x0002, 0x0FD7):
            cpu = machine(bytes([0x8D, 0x40, 0x05]), False)  # lea ax, [bx+si+5]
            cpu.r[CPU.BX], cpu.r[CPU.SI] = 0xFFFF, 0x0001  # the sum wraps to 5: an ADD would raise CF and ZF
            cpu.flags = pattern
            cpu.step()
            self.assertEqual((cpu.r[CPU.AX], cpu.flags), (0x0005, pattern))

    def test_the_destination_may_be_a_register_of_the_address(self):
        cpu = self.lea([0x8D, 0x5F, 0x02])  # lea bx, [bx+2]: bx = bx + 2
        self.assertEqual(cpu.r[CPU.BX], 0x1002)

    def test_a_register_operand_stops_loudly(self):
        cpu = machine(bytes([0x8D, 0xC3]), False)
        with self.assertRaises(NotImplementedError):
            cpu.step()


class TestRunTick(unittest.TestCase):
    """CPU.run(every, tick): look at the machine every so many instructions."""

    def machine_with_nops(self, count):
        return machine(bytes([0x90] * count + [0xF4]), False)  # count NOPs, then HLT

    def test_without_a_tick_it_runs_to_the_end(self):
        cpu = self.machine_with_nops(5)
        self.assertEqual(cpu.run(), 0)
        self.assertEqual(cpu.instructions, 6)

    def test_the_tick_comes_after_every_n_instructions(self):
        cpu = self.machine_with_nops(10)
        seen = []
        self.assertEqual(cpu.run(4, lambda: seen.append(cpu.instructions)), 0)
        self.assertEqual(seen, [4, 8])  # the program ends in the middle of the third round: no tick then
        self.assertEqual(cpu.instructions, 11)

    def test_a_program_that_ends_before_the_first_tick_gets_none(self):
        cpu = self.machine_with_nops(2)
        seen = []
        cpu.run(100, lambda: seen.append(1))
        self.assertEqual(seen, [])

    def test_the_exit_code_still_comes_back(self):
        cpu = machine(bytes([0x90, 0xB4, 0x4C, 0xB0, 0x07, 0xCD, 0x21]), False)
        cpu.int_handlers[0x21] = lambda cpu: (_ for _ in ()).throw(Exit(cpu.get8(0)))
        self.assertEqual(cpu.run(2, lambda: None), 7)

    def test_an_error_in_the_program_comes_out_through_run(self):
        cpu = machine(bytes([0x90, 0x0F]), False)  # an opcode that does not exist
        with self.assertRaises(NotImplementedError):
            cpu.run(1, lambda: None)

    def test_an_error_in_the_tick_comes_out_through_run(self):
        cpu = self.machine_with_nops(5)

        def tick():
            raise RuntimeError("tick")
        with self.assertRaises(RuntimeError):
            cpu.run(2, tick)


class TestIret(unittest.TestCase):
    """CF IRET: pop IP, then CS, then the flags."""

    def machine_with_stack(self, *words):
        """CS=1000 on an IRET, the stack at 4000:0200 holding `words` (the first on top)."""
        cpu = machine(bytes([0xCF]), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        for i, word in enumerate(words):
            cpu.ww(0x4000, 0x0200 + 2 * i, word)
        return cpu

    def test_ip_then_cs_then_the_flags_come_off_the_stack(self):
        cpu = self.machine_with_stack(0x1234, 0x5678, 0x0246)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.s[CPU.CS], cpu.flags, cpu.r[CPU.SP]), (0x1234, 0x5678, 0x0246, 0x0206))

    def test_only_the_flags_that_exist_are_taken_and_bit_1_stays_1(self):
        for word in (0x0000, 0xFFFF, 0x0FD7, 0xF000, 0x0200, 0x0100, 0xAAAA, 0x5555):
            cpu = self.machine_with_stack(0, 0x1000, word)
            cpu.step()
            self.assertEqual(cpu.flags, (word & 0x7FD5) | 0x0002, f"{word:04X}")  # IOPL and NT too, as for POPF

    def test_if_and_tf_are_what_the_stack_says_not_what_they_were(self):
        for old in (0x0002, 0x0202, 0x0302):
            for saved in (0x0002, 0x0202, 0x0102, 0x0302):
                cpu = self.machine_with_stack(0, 0x1000, saved)
                cpu.flags = old
                cpu.step()
                self.assertEqual(cpu.flags & (flags.IF | flags.TF), saved & (flags.IF | flags.TF), (old, saved))

    def test_no_register_changes_but_sp_cs_ip_and_the_flags(self):
        cpu = self.machine_with_stack(0x0010, 0x2000, 0x0002)
        before = list(cpu.r)
        cpu.step()
        expected = list(before)
        expected[CPU.SP] += 6
        self.assertEqual((cpu.r, cpu.s[CPU.DS], cpu.s[CPU.ES]), (expected, 0x2000, 0x3000))

    def test_it_is_the_same_as_pop_ip_pop_cs_popf(self):
        for words in ((0x0100, 0x2000, 0x0FD7), (0xFFFF, 0x0000, 0x0000), (0x0010, 0xFFFF, 0x0200)):
            iret = self.machine_with_stack(*words)
            iret.step()
            by_hand = self.machine_with_stack(*words)
            by_hand.ip = by_hand.pop16()  # what retf does ...
            by_hand.s[CPU.CS] = by_hand.pop16()
            by_hand.flags = (by_hand.pop16() & 0x0FD5) | 0x0002  # ... and popf
            self.assertEqual((iret.ip, iret.s[CPU.CS], iret.flags, iret.r[CPU.SP]),
                             (by_hand.ip, by_hand.s[CPU.CS], by_hand.flags, by_hand.r[CPU.SP]))

    def test_the_stack_goes_round_the_segment(self):
        cpu = machine(bytes([0xCF]), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0xFFFC  # the words are at FFFC, FFFE and 0000
        cpu.ww(0x4000, 0xFFFC, 0x1111)
        cpu.ww(0x4000, 0xFFFE, 0x2222)
        cpu.ww(0x4000, 0x0000, 0x0246)
        cpu.step()
        self.assertEqual((cpu.ip, cpu.s[CPU.CS], cpu.flags, cpu.r[CPU.SP]), (0x1111, 0x2222, 0x0246, 0x0002))


class TestInterruptsThroughTheTable(unittest.TestCase):
    """CD n: if the table of vectors has an address for n the interrupt goes to the code there."""

    def machine_with_handler(self, vector, segment, offset, code=b"", stack=0x0200):
        """The program is at 1000:0000; the table says that `vector` is handled at segment:offset, by `code`."""
        cpu = machine(bytes([0xCD, vector, 0x90, 0x90]), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, stack
        cpu.ww(0, vector * 4, offset)
        cpu.ww(0, vector * 4 + 2, segment)
        cpu.mem[segment * 16 + offset:segment * 16 + offset + len(code)] = code
        return cpu

    def test_the_flags_cs_and_the_address_after_the_int_are_pushed_in_that_order_and_the_handler_starts(self):
        cpu = self.machine_with_handler(0x60, 0x2000, 0x0100)
        cpu.flags = 0x0246
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x2000, 0x0100))
        self.assertEqual(cpu.r[CPU.SP], 0x01FA)
        self.assertEqual([cpu.rw(0x4000, 0x01FA + 2 * i) for i in range(3)], [0x0002, 0x1000, 0x0246])

    def test_if_and_tf_are_cleared_for_the_handler_and_no_other_flag_changes(self):
        for pattern in (0x0002, 0x0FD7, 0x0302, 0x0102 | flags.CF | flags.ZF | flags.DF | flags.OF):
            cpu = self.machine_with_handler(0x60, 0x2000, 0x0100)
            cpu.flags = pattern
            cpu.step()
            self.assertEqual(cpu.flags, pattern & ~(flags.IF | flags.TF), hex(pattern))
            self.assertEqual(cpu.rw(0x4000, 0x01FC + 2), pattern, hex(pattern))  # what is saved is the whole word

    def test_the_handler_ends_with_iret_which_brings_back_the_flags_and_the_place(self):
        cpu = self.machine_with_handler(0x60, 0x2000, 0x0100, code=bytes([0xCF]))
        cpu.flags = 0x0246 | flags.CF
        cpu.step()  # int 60h
        cpu.step()  # iret
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.flags, cpu.r[CPU.SP]), (0x1000, 0x0002, 0x0246 | flags.CF, 0x0200))

    def test_a_handler_that_changes_registers_and_flags_leaves_them_changed_but_not_if_and_tf(self):
        # mov bx, 5 ; stc ; cli ; iret   (the caller had IF set and CF clear)
        cpu = self.machine_with_handler(0x60, 0x2000, 0x0100, code=bytes([0xBB, 0x05, 0x00, 0xF9, 0xFA, 0xCF]))
        cpu.flags = 0x0202
        for _ in range(5):
            cpu.step()
        self.assertEqual((cpu.r[CPU.BX], cpu.get_flag(flags.CF), cpu.get_flag(flags.IF)), (5, False, True))

    def test_the_program_goes_on_after_the_int(self):
        # int 60h ; nop ; nop   with a handler that does mov ax, 1234h ; iret
        cpu = self.machine_with_handler(0x60, 0x2000, 0x0100, code=bytes([0xB8, 0x34, 0x12, 0xCF]))
        for _ in range(3):
            cpu.step()
        self.assertEqual((cpu.r[CPU.AX], cpu.s[CPU.CS], cpu.ip), (0x1234, 0x1000, 2))
        cpu.step()
        self.assertEqual(cpu.ip, 3)

    def test_a_handler_may_change_the_address_it_returns_to(self):
        """What an emulator of the coprocessor does: it takes the bytes after the INT as its own and skips them."""
        # pop bx ; add bx, 2 ; push bx ; iret
        code = bytes([0x5B, 0x83, 0xC3, 0x02, 0x53, 0xCF])
        cpu = self.machine_with_handler(0x37, 0x2000, 0x0100, code=code)
        for _ in range(5):
            cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 0x0004))

    def test_a_handler_can_read_the_bytes_after_the_int_through_the_return_address(self):
        # mov bp, sp ; les bx, [bp] ; mov al, es:[bx] ; iret   (the return address is at the top of the stack)
        code = bytes([0x89, 0xE5, 0xC4, 0x5E, 0x00, 0x26, 0x8A, 0x07, 0xCF])
        cpu = self.machine_with_handler(0x37, 0x2000, 0x0100, code=code)
        cpu.mem[0x10002] = 0xE3  # the byte after the INT, as in "int 37h ; db E3h" (a coprocessor instruction)
        for _ in range(5):
            cpu.step()
        self.assertEqual(cpu.get8(0), 0xE3)

    def test_interrupts_inside_interrupts_come_back_one_by_one(self):
        # the handler of 60h is: int 61h ; iret.  the handler of 61h is: inc bx ; iret
        cpu = self.machine_with_handler(0x60, 0x2000, 0x0100, code=bytes([0xCD, 0x61, 0xCF]))
        cpu.ww(0, 0x61 * 4, 0x0200)
        cpu.ww(0, 0x61 * 4 + 2, 0x2000)
        cpu.mem[0x20200:0x20202] = bytes([0x43, 0xCF])
        cpu.r[CPU.BX] = 0
        for _ in range(5):
            cpu.step()
        self.assertEqual((cpu.r[CPU.BX], cpu.s[CPU.CS], cpu.ip, cpu.r[CPU.SP]), (1, 0x1000, 2, 0x0200))

    def test_the_vector_counts_as_set_when_either_half_is_not_zero(self):
        for segment, offset in ((0x0000, 0x1234), (0x2000, 0x0000), (0x0001, 0x0001)):
            cpu = self.machine_with_handler(0x60, segment, offset)
            cpu.step()
            self.assertEqual((cpu.s[CPU.CS], cpu.ip), (segment, offset))

    def test_with_nothing_in_the_table_it_is_our_own_handler_or_an_error(self):
        cpu = machine(bytes([0xCD, 0x60]), False)
        calls = []
        cpu.int_handlers[0x60] = lambda c: calls.append(c.ip)
        cpu.step()
        self.assertEqual((calls, cpu.s[CPU.CS], cpu.ip), ([2], 0x1000, 2))
        cpu = machine(bytes([0xCD, 0x61]), False)
        with self.assertRaises(NotImplementedError) as caught:
            cpu.step()
        self.assertIn("INT 61 at 1000:0000", str(caught.exception))

    def test_the_program_wins_over_our_handler_when_it_has_set_the_vector(self):
        cpu = self.machine_with_handler(0x60, 0x2000, 0x0100)
        cpu.int_handlers[0x60] = lambda c: self.fail("our handler must not run")
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x2000, 0x0100))

    def test_our_handler_does_not_push_anything(self):
        cpu = machine(bytes([0xCD, 0x60]), False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        cpu.int_handlers[0x60] = lambda c: None
        cpu.flags = 0x0302
        cpu.step()
        self.assertEqual((cpu.r[CPU.SP], cpu.flags), (0x0200, 0x0302))

    def test_the_stack_goes_round_the_segment(self):
        cpu = self.machine_with_handler(0x60, 0x2000, 0x0100, stack=0x0002)
        cpu.flags = 0x0246
        cpu.step()
        self.assertEqual(cpu.r[CPU.SP], 0xFFFC)  # 2 - 6 = -4
        self.assertEqual([cpu.rw(0x4000, a) for a in (0xFFFC, 0xFFFE, 0x0000)], [0x0002, 0x1000, 0x0246])

    def test_the_vectors_of_any_number_0_to_255_are_looked_up_at_four_bytes_each(self):
        for vector in (0x00, 0x01, 0x08, 0x21, 0x7F, 0x80, 0xFE, 0xFF):
            cpu = self.machine_with_handler(vector, 0x3000 + vector, 0x0010 + vector)
            cpu.step()
            self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x3000 + vector, 0x0010 + vector), vector)

    def test_one_instruction_is_counted_for_the_int_and_the_handler_starts_at_the_next_step(self):
        cpu = self.machine_with_handler(0x60, 0x2000, 0x0100, code=bytes([0x90]))
        cpu.step()
        self.assertEqual((cpu.instructions, cpu.start_ip), (1, 0))
        cpu.step()
        self.assertEqual((cpu.instructions, cpu.start_ip), (2, 0x0100))

    def test_dos_sets_a_vector_and_int_goes_there(self):
        from mydos import dos
        # mov ax, 2560h ; mov dx, 0100h ; (DS = 2000h) ; int 21h ; int 60h
        cpu = machine(bytes([0xB8, 0x60, 0x25, 0xBA, 0x00, 0x01, 0xCD, 0x21, 0xCD, 0x60]), False)
        dos.install(cpu)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0200
        for _ in range(4):
            cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x2000, 0x0100))
        self.assertEqual(cpu.rw(0x4000, 0x01FA), 0x000A)  # the place after the int 60h
        cpu.s[CPU.DS] = 0x1234  # and the program can ask for the vector again
        cpu.r[CPU.AX] = 0x3560
        cpu.mem[0x20100:0x20102] = bytes([0xCD, 0x21])
        cpu.step()
        self.assertEqual((cpu.s[CPU.ES], cpu.r[CPU.BX]), (0x2000, 0x0100))


class TestNearConditionalJumps(unittest.TestCase):
    """0F 80-8F (80386 and later): the conditional jumps with a distance of a word."""

    FLAG_SETS = [0x0002, 0x0002 | flags.ZF, 0x0002 | flags.CF, 0x0002 | flags.SF, 0x0002 | flags.OF,
                 0x0002 | flags.PF, 0x0002 | flags.SF | flags.OF, 0x0002 | flags.CF | flags.ZF,
                 0x0002 | flags.ZF | flags.SF, 0x0002 | flags.ZF | flags.OF | flags.PF,
                 0x0FD7, 0x0002 | flags.DF | flags.IF | flags.AF | flags.TF]

    def jump(self, second, distance, flag_word=0x0002, at=0x0000, extra=b""):
        cpu = machine(bytes(extra) + bytes([0x0F, second, distance & 0xFF, distance >> 8 & 0xFF]), False)
        cpu.ip = len(extra) + at if at == 0 else at
        if at:
            cpu.mem[0x10000 + at:0x10000 + at + 4] = bytes([0x0F, second, distance & 0xFF, distance >> 8 & 0xFF])
        cpu.flags = flag_word
        cpu.step()
        return cpu

    def test_it_is_four_bytes_long_taken_or_not_and_a_distance_of_0_is_the_next_instruction(self):
        for second in range(0x80, 0x90):
            for flag_word in self.FLAG_SETS:
                cpu = self.jump(second, 0, flag_word)
                self.assertEqual(cpu.ip, 4, (hex(second), hex(flag_word)))
                self.assertEqual(cpu.flags, flag_word)

    def test_it_agrees_with_the_short_jump_of_the_same_condition(self):
        for cc in range(16):
            for flag_word in self.FLAG_SETS:
                short = machine(bytes([0x70 + cc, 0x10]), False)
                short.flags = flag_word
                short.step()
                near = self.jump(0x80 + cc, 0x0010, flag_word)
                self.assertEqual(short.ip == 0x12, near.ip == 0x14, (cc, hex(flag_word)))  # taken, or not
                self.assertEqual(near.ip, 0x14 if short.ip == 0x12 else 4)

    def test_the_names_of_the_jumps(self):
        rows = [(0x84, flags.ZF, True), (0x84, 0, False), (0x85, 0, True), (0x85, flags.ZF, False),
                (0x82, flags.CF, True), (0x83, flags.CF, False), (0x86, flags.CF, True), (0x86, flags.ZF, True),
                (0x87, 0, True), (0x87, flags.CF, False), (0x88, flags.SF, True), (0x89, flags.SF, False),
                (0x80, flags.OF, True), (0x81, flags.OF, False), (0x8A, flags.PF, True), (0x8B, flags.PF, False),
                (0x8C, flags.SF, True), (0x8C, flags.SF | flags.OF, False), (0x8D, flags.SF | flags.OF, True),
                (0x8E, flags.ZF, True), (0x8E, flags.SF | flags.OF, False), (0x8F, 0, True), (0x8F, flags.ZF, False)]
        for second, bits, taken in rows:
            cpu = self.jump(second, 0x0100, 0x0002 | bits)
            self.assertEqual(cpu.ip, 0x0104 if taken else 4, (hex(second), hex(bits)))

    def test_the_distance_is_signed_and_counted_from_the_end_of_the_instruction(self):
        for distance, target in ((0x0001, 5), (0x0100, 0x0104), (0x7FFF, 0x8003), (0x8000, 0x8004), (0xFFFF, 3),
                                 (0xFFFC, 0), (0xFFF0, 0xFFF4), (0xFF00, 0xFF04)):
            cpu = self.jump(0x84, distance, 0x0002 | flags.ZF)
            self.assertEqual(cpu.ip, target, hex(distance))

    def test_it_goes_round_the_segment_in_both_directions(self):
        cpu = self.jump(0x84, 0x0020, 0x0002 | flags.ZF, at=0xFFF0)  # FFF4 + 20 = 10014
        self.assertEqual(cpu.ip, 0x0014)
        cpu = self.jump(0x84, 0xFF00, 0x0002 | flags.ZF, at=0x0010)  # 0014 - 100
        self.assertEqual(cpu.ip, 0xFF14)

    def test_it_can_be_the_last_bytes_of_the_segment(self):
        cpu = machine(b"", False)
        cpu.ip = 0xFFFE
        cpu.mem[0x1FFFE:0x20000] = bytes([0x0F, 0x84])
        cpu.mem[0x10000:0x10002] = bytes([0x05, 0x00])  # the distance is read from the start of the segment
        cpu.flags = 0x0002 | flags.ZF
        cpu.step()
        self.assertEqual(cpu.ip, 0x0002 + 5)

    def test_no_register_and_no_flag_changes(self):
        for second in range(0x80, 0x90):
            cpu = machine(bytes([0x0F, second, 0x10, 0x00]), False)
            cpu.flags = 0x0FD7
            before = (list(cpu.r), list(cpu.s))
            cpu.step()
            self.assertEqual(((cpu.r, cpu.s), cpu.flags), (before, 0x0FD7), hex(second))

    def test_a_prefix_in_front_is_taken_and_does_no_harm(self):
        for prefix in (0x26, 0x2E, 0x36, 0x3E, 0xF2, 0xF3):
            cpu = machine(bytes([prefix, 0x0F, 0x84, 0x10, 0x00]), False)
            cpu.flags = 0x0002 | flags.ZF
            cpu.step()
            self.assertEqual((cpu.ip, cpu.start_ip), (0x15, 0), hex(prefix))

    def test_the_other_instructions_that_start_with_0f_stop_the_emulator_and_say_which(self):
        for second in (0x00, 0x01, 0x0B, 0x20, 0x7F, 0xA2, 0xB0, 0xC0, 0xFF):
            cpu = machine(bytes([0x0F, second, 0x12, 0x34]), False)
            with self.assertRaises(NotImplementedError) as caught:
                cpu.step()
            self.assertIn(f"opcode 0F {second:02X} at 1000:0000: 0f {second:02x} 12 34", str(caught.exception))

    def test_the_address_in_the_message_is_where_the_instruction_began_prefixes_and_all(self):
        cpu = machine(bytes([0x90, 0x26, 0x0F, 0xA2]), False)
        cpu.ip = 1
        with self.assertRaises(NotImplementedError) as caught:
            cpu.step()
        self.assertIn("opcode 0F A2 at 1000:0001", str(caught.exception))

    def test_it_is_one_instruction_for_the_count_and_for_the_trace(self):
        cpu = machine(bytes([0x0F, 0x84, 0x00, 0x00]), False)
        seen = []
        cpu.trace = lambda c: seen.append((c.ip, c.instructions))
        cpu.step()
        self.assertEqual((seen, cpu.instructions), ([(0, 1)], 1))


class TestEnter(unittest.TestCase):
    """C8 ENTER size, level (80186 and later): push bp ; mov bp, sp ; sub sp, size, and for nested levels a display."""

    def machine_with_code(self, code, bp=0x01F0, sp=0x0100):
        cpu = machine(bytes(code), False)
        cpu.s[CPU.SS] = 0x4000
        cpu.r[CPU.BP], cpu.r[CPU.SP] = bp, sp
        return cpu

    def enter(self, size, level, bp=0x01F0, sp=0x0100):
        cpu = self.machine_with_code([0xC8, size & 0xFF, size >> 8, level], bp, sp)
        start_flags = cpu.flags
        cpu.step()
        self.assertEqual((cpu.ip, cpu.flags), (4, start_flags))  # four bytes long; no flag moves
        return cpu

    def test_level_0_makes_the_frame_and_the_room_for_the_locals(self):
        cpu = self.enter(0x12, 0)
        self.assertEqual(cpu.rw(0x4000, 0x00FE), 0x01F0)  # the old BP was pushed
        self.assertEqual(cpu.r[CPU.BP], 0x00FE)  # the frame is where SP was after the push
        self.assertEqual(cpu.r[CPU.SP], 0x00FE - 0x12)

    def test_level_0_does_what_the_three_instructions_it_replaces_do(self):
        for size, bp, sp in ((0, 0x01F0, 0x0100), (0x12, 0x0200, 0x0200), (0xFFFF, 0x0010, 0x0004),
                             (0x0100, 0xFFFE, 0x0000), (0x0002, 0x1234, 0x0001)):
            enter = self.machine_with_code([0xC8, size & 0xFF, size >> 8, 0x00], bp, sp)
            three = self.machine_with_code([0x55, 0x8B, 0xEC, 0x81, 0xEC, size & 0xFF, size >> 8], bp, sp)
            enter.step()
            for _ in range(3):
                three.step()
            self.assertEqual(enter.r, three.r, f"size={size:04X} BP={bp:04X} SP={sp:04X}")
            self.assertEqual(enter.mem[0x40000:0x50000], three.mem[0x40000:0x50000], f"size={size:04X} SP={sp:04X}")

    def test_level_1_adds_one_word_the_frame_pointer_itself(self):
        cpu = self.enter(0x08, 1)
        self.assertEqual(cpu.r[CPU.BP], 0x00FE)
        self.assertEqual(cpu.rw(0x4000, 0x00FE), 0x01F0)  # the old BP
        self.assertEqual(cpu.rw(0x4000, 0x00FC), 0x00FE)  # the frame pointer of this procedure
        self.assertEqual(cpu.r[CPU.SP], 0x00FC - 0x08)

    def test_a_deeper_level_copies_the_frame_pointers_of_the_outer_procedures(self):
        # the caller's frame is at BP=0x01F0; the words below it stand for the frame pointers of two outer ones
        cpu = self.machine_with_code([0xC8, 0x04, 0x00, 0x03])
        cpu.ww(0x4000, 0x01EE, 0x1111)  # the first pointer to copy, at BP-2
        cpu.ww(0x4000, 0x01EC, 0x2222)  # the second, at BP-4: level 3 copies only two before its own
        cpu.step()
        self.assertEqual(cpu.rw(0x4000, 0x00FE), 0x01F0)  # the old BP
        self.assertEqual(cpu.rw(0x4000, 0x00FC), 0x1111)
        self.assertEqual(cpu.rw(0x4000, 0x00FA), 0x2222)
        self.assertEqual(cpu.rw(0x4000, 0x00F8), 0x00FE)  # the pointer to this frame is the last
        self.assertEqual((cpu.r[CPU.BP], cpu.r[CPU.SP]), (0x00FE, 0x00F8 - 4))

    def test_every_level_agrees_with_the_definition_written_out_in_python(self):
        for level in range(0, 33):
            for size in (0, 6):
                cpu = self.machine_with_code([0xC8, size, 0, level], bp=0x0300, sp=0x0200)
                for i in range(40):
                    cpu.ww(0x4000, 0x0300 - 2 * i, 0x9000 + i)  # distinct words below BP
                before = cpu.mem[:1024 * 1024]  # the megabyte of DOS: a copy of all the memory is 256 MB
                cpu.step()
                bp, sp, memory = 0x0300, 0x0200, {}
                sp -= 2
                memory[sp] = bp
                frame, bp_at_start = sp, bp
                count = level % 32
                if count > 0:
                    for _ in range(count - 1):
                        bp -= 2
                        sp -= 2
                        memory[sp] = 0x9000 + (0x0300 - bp) // 2  # the word that was at the new BP
                    sp -= 2
                    memory[sp] = frame
                bp = frame
                sp -= size
                self.assertEqual((cpu.r[CPU.BP], cpu.r[CPU.SP]), (bp, sp), f"level {level} size {size}")
                for address, word in memory.items():
                    self.assertEqual(cpu.rw(0x4000, address), word, f"level {level} at {address:04X}")
                changed = {i for i in range(len(before)) if before[i] != cpu.mem[i]}
                allowed = {0x40000 + a + k for a in memory for k in (0, 1)}
                self.assertLessEqual(changed, allowed, f"level {level}: something else was written")

    def test_only_the_low_five_bits_of_the_level_count(self):
        plain, wrapped = self.enter(0x08, 1), self.enter(0x08, 0x21)
        self.assertEqual((plain.r, plain.mem[0x40000:0x50000]), (wrapped.r, wrapped.mem[0x40000:0x50000]))
        zero, thirty_two = self.enter(0x08, 0), self.enter(0x08, 32)
        self.assertEqual((zero.r, zero.mem[0x40000:0x50000]), (thirty_two.r, thirty_two.mem[0x40000:0x50000]))

    def test_the_other_registers_are_left_alone(self):
        before = machine(b"", False).r
        cpu = self.enter(0x08, 2)
        for number in (CPU.AX, CPU.BX, CPU.CX, CPU.DX, CPU.SI, CPU.DI):
            self.assertEqual(cpu.r[number], before[number], number)

    def test_leave_undoes_enter(self):
        for level in (0, 1, 3):
            cpu = self.machine_with_code([0xC8, 0x20, 0x00, level, 0xC9], bp=0x01F0, sp=0x0100)
            cpu.ww(0x4000, 0x01EE, 0x1111)
            cpu.ww(0x4000, 0x01EC, 0x2222)
            cpu.step()
            cpu.step()
            self.assertEqual((cpu.r[CPU.BP], cpu.r[CPU.SP]), (0x01F0, 0x0100), f"level {level}")

    def test_sp_and_the_frame_wrap_round_the_segment(self):
        cpu = self.enter(0x0010, 0, bp=0x1234, sp=0x0000)
        self.assertEqual(cpu.rw(0x4000, 0xFFFE), 0x1234)
        self.assertEqual((cpu.r[CPU.BP], cpu.r[CPU.SP]), (0xFFFE, (0xFFFE - 0x10) & 0xFFFF))


class TestLeave(unittest.TestCase):
    """C9 LEAVE (80186 and later): mov sp, bp ; pop bp."""

    def machine_with_frame(self, code, bp=0x01F0, sp=0x0100, saved_bp=0xABCD):
        cpu = machine(bytes(code), False)
        cpu.s[CPU.SS] = 0x4000
        cpu.r[CPU.BP], cpu.r[CPU.SP] = bp, sp
        cpu.ww(0x4000, bp, saved_bp)  # the caller's BP, which the procedure pushed
        return cpu

    def test_sp_goes_back_to_the_frame_and_bp_is_restored(self):
        cpu = self.machine_with_frame([0xC9])
        start_flags = cpu.flags
        cpu.step()
        self.assertEqual((cpu.r[CPU.BP], cpu.r[CPU.SP], cpu.ip, cpu.flags), (0xABCD, 0x01F2, 1, start_flags))

    def test_it_does_what_the_two_instructions_it_replaces_do(self):
        for bp, sp in ((0x01F0, 0x0100), (0x0200, 0x0200), (0x0010, 0xFFF0), (0xFFFE, 0x0000)):
            leave = self.machine_with_frame([0xC9], bp=bp, sp=sp)
            two = self.machine_with_frame([0x8B, 0xE5, 0x5D], bp=bp, sp=sp)  # mov sp,bp ; pop bp
            leave.step()
            two.step()
            two.step()
            self.assertEqual((leave.r, leave.flags), (two.r, two.flags), f"BP={bp:04X} SP={sp:04X}")

    def test_the_value_of_sp_before_it_does_not_matter(self):
        for sp in (0x0000, 0x0100, 0x0F00, 0xFFFF):
            cpu = self.machine_with_frame([0xC9], sp=sp)
            cpu.step()
            self.assertEqual(cpu.r[CPU.SP], 0x01F2, f"SP={sp:04X}")

    def test_a_procedure_from_start_to_finish(self):
        """push bp ; mov bp,sp ; sub sp,10h ; (the locals are used) ; leave ; ret"""
        code = [0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x10, 0x50, 0x53, 0xC9, 0xC3]  # ... push ax ; push bx ; leave ; ret
        cpu = machine(bytes(code), False)
        cpu.s[CPU.SS] = 0x4000
        cpu.r[CPU.SP], cpu.r[CPU.BP] = 0x01FE, 0x1234
        cpu.ww(0x4000, 0x01FE, 0x0777)  # the return address, as a call would have pushed it
        for _ in range(7):  # up to and including the ret
            cpu.step()
        self.assertEqual((cpu.r[CPU.BP], cpu.r[CPU.SP], cpu.ip), (0x1234, 0x0200, 0x0777))


if __name__ == "__main__":
    unittest.main()


class TestWaitAndTheCoprocessor(unittest.TestCase):
    """9B WAIT, and D8-DF + ModRM, the instructions of the 8087: with no coprocessor they only take their bytes."""

    def state(self, cpu):
        return (list(cpu.r), list(cpu.s), cpu.flags, bytes(cpu.mem[:1024 * 1024]))

    def test_wait_does_nothing(self):
        cpu = machine(bytes([0x9B, 0xF4]), True)
        before = self.state(cpu)
        cpu.step()
        self.assertEqual((cpu.ip, self.state(cpu)), (1, before))

    def test_every_esc_opcode_takes_its_modrm_byte_and_changes_nothing(self):
        for op in range(0xD8, 0xE0):
            for modrm_byte in (0xE3, 0xC1, 0xF8, 0x07, 0x1F):
                cpu = machine(bytes([op, modrm_byte, 0xF4]), True)
                before = self.state(cpu)
                cpu.step()
                self.assertEqual(cpu.ip, 2, f"{op:02X} {modrm_byte:02X}")
                self.assertEqual(self.state(cpu), before, f"{op:02X} {modrm_byte:02X}")

    def test_the_displacement_and_the_direct_address_are_taken_too(self):
        for modrm_byte, length in ((0x47, 3), (0x87, 4), (0x06, 4), (0x46, 3), (0x80, 4), (0x00, 2), (0xC0, 2)):
            cpu = machine(bytes([0xDB, modrm_byte, 0x11, 0x22, 0xF4]), False)
            cpu.step()
            self.assertEqual(cpu.ip, length, f"{modrm_byte:02X}")

    def test_the_test_for_a_coprocessor_finds_none(self):
        # wait ; fninit ; sub sp,2 ; mov bp,sp ; mov word [bp],5A5Ah ; fnstsw [bp] ; mov ax,[bp]
        code = bytes([0x9B, 0xDB, 0xE3, 0x83, 0xEC, 0x02, 0x8B, 0xEC, 0xC7, 0x46, 0x00, 0x5A, 0x5A,
                      0xDD, 0x7E, 0x00, 0x8B, 0x46, 0x00, 0xF4])
        cpu = machine(code, False)
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0100
        cpu.run()
        self.assertEqual(cpu.r[CPU.AX], 0x5A5A)  # the status word was never written: no coprocessor

    def test_a_segment_prefix_before_esc_does_not_upset_it(self):
        cpu = machine(bytes([0x2E, 0xD9, 0x46, 0x02, 0xF4]), False)
        cpu.step()
        self.assertEqual(cpu.ip, 4)


class TestAamAad(unittest.TestCase):
    """D4 AAM and D5 AAD, with the base that follows them: AL split into two digits, and two digits put together."""

    def run_one(self, cpu, code, ax):
        cpu.mem[0x10000:0x10000 + len(code)] = code
        cpu.ip = 0
        cpu.r[CPU.AX] = ax
        cpu.flags = 0x0002 | flags.DF | flags.IF | flags.CF | flags.AF | flags.OF  # these three must be cleared
        cpu.step()

    def expected_flags(self, result):
        """What the sign, zero and parity flags say about the byte, and the carry flags nothing."""
        return {"S": bool(result & 0x80), "Z": result == 0, "P": bin(result).count("1") % 2 == 0,
                "C": False, "A": False, "O": False}

    def flags_of(self, cpu):
        return {"S": cpu.get_flag(flags.SF), "Z": cpu.get_flag(flags.ZF), "P": cpu.get_flag(flags.PF),
                "C": cpu.get_flag(flags.CF), "A": cpu.get_flag(flags.AF), "O": cpu.get_flag(flags.OF)}

    def test_aam_splits_every_value_of_al_into_tens_and_ones(self):
        cpu = machine(b"", False)
        for al in range(256):
            self.run_one(cpu, bytes([0xD4, 10]), 0xAB00 | al)
            quotient, remainder = divmod(al, 10)
            self.assertEqual(cpu.r[CPU.AX], quotient << 8 | remainder, al)
            self.assertEqual(cpu.ip, 2)
            self.assertEqual(self.flags_of(cpu), self.expected_flags(remainder), al)

    def test_aam_takes_the_base_from_the_next_byte(self):
        cpu = machine(b"", False)
        for base in (1, 2, 8, 10, 16, 100, 255):
            for al in (0, 1, 7, 8, 9, 100, 200, 255):
                self.run_one(cpu, bytes([0xD4, base]), 0x1200 | al)
                self.assertEqual(cpu.r[CPU.AX], (al // base) << 8 | al % base, (base, al))
                self.assertEqual(self.flags_of(cpu), self.expected_flags(al % base), (base, al))

    def test_aam_ignores_what_ah_held(self):
        cpu = machine(b"", False)
        for ah in (0, 1, 0x80, 0xFF):
            self.run_one(cpu, bytes([0xD4, 10]), ah << 8 | 47)
            self.assertEqual(cpu.r[CPU.AX], 0x0407)

    def test_aam_with_base_0_is_a_division_by_zero_and_changes_nothing(self):
        cpu = machine(b"", False)
        with self.assertRaisesRegex(DivideError, "divide by zero at 1000:0000"):
            self.run_one(cpu, bytes([0xD4, 0]), 0x1234)
        self.assertEqual(cpu.r[CPU.AX], 0x1234)

    def test_aad_puts_every_pair_of_digits_together(self):
        cpu = machine(b"", False)
        for ah in range(256):
            for al in range(0, 256, 5):
                self.run_one(cpu, bytes([0xD5, 10]), ah << 8 | al)
                result = (ah * 10 + al) & 0xFF
                self.assertEqual(cpu.r[CPU.AX], result, (ah, al))
                self.assertEqual(self.flags_of(cpu), self.expected_flags(result), (ah, al))

    def test_aad_undoes_aam(self):
        cpu = machine(b"", False)
        for al in range(100):
            self.run_one(cpu, bytes([0xD4, 10]), al)
            self.run_one(cpu, bytes([0xD5, 10]), cpu.r[CPU.AX])
            self.assertEqual(cpu.r[CPU.AX], al)

    def test_aad_takes_the_base_from_the_next_byte_and_a_base_of_0_just_clears_ah(self):
        cpu = machine(b"", False)
        self.run_one(cpu, bytes([0xD5, 16]), 0x0305)
        self.assertEqual(cpu.r[CPU.AX], 0x0035)
        self.run_one(cpu, bytes([0xD5, 0]), 0x0305)
        self.assertEqual(cpu.r[CPU.AX], 0x0005)

    def test_the_other_registers_and_flags_stay(self):
        for code in (bytes([0xD4, 10]), bytes([0xD5, 10])):
            cpu = machine(b"", False)
            cpu.r[1:] = [0x1111, 0x2222, 0x3333, 0x4444, 0x5555, 0x6666, 0x7777]
            self.run_one(cpu, code, 0x0529)
            self.assertEqual(cpu.r[1:], [0x1111, 0x2222, 0x3333, 0x4444, 0x5555, 0x6666, 0x7777])
            self.assertTrue(cpu.get_flag(flags.DF) and cpu.get_flag(flags.IF))
