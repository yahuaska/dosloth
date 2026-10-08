import unittest

from mydos import flags
from mydos.cpu import CPU


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


if __name__ == "__main__":
    unittest.main()
