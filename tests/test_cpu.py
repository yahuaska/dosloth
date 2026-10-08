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
    cpu.set_flag(flags.CF, cf)
    return cpu


def cpu_flags(cpu):
    return {"C": cpu.get_flag(flags.CF), "O": cpu.get_flag(flags.OF), "Z": cpu.get_flag(flags.ZF),
            "S": cpu.get_flag(flags.SF), "P": cpu.get_flag(flags.PF)}


class TestTwoOperandForms(unittest.TestCase):
    """Opcode bit 0 is the size (0 = byte, 1 = word), bit 1 the direction (0 = into r/m, 1 = into the register)."""

    def check_flags(self, cpu, start_flags, flags_fn, a, b, c, bits, label):
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


if __name__ == "__main__":
    unittest.main()
