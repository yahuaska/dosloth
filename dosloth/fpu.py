"""The numeric coprocessor (x87) of the 387 and later, which a 32-bit program of the games uses, in a naive form.

The registers are Python floats, so the arithmetic is of 53 bits and not of 64 as it is in the chip: a chain of
operations can differ from the real thing in its last bits. The control word is kept and read back (a program tests
for the chip with it), the status word has the condition codes C0-C3 and the top of the stack, and the exceptions are
all masked: a division by zero is an infinity, 0/0 is not a number, and the flags that say so stay set until
FNCLEX or FNINIT.

It is for the 32-bit programs: it is in the table of the opcodes with a 32-bit operand size, and a program of 16 bits
(no prefix 66h) has no coprocessor here (see CPU.esc): the installer that runs there looks for one and goes on
without it.

An instruction is found by its opcode (D8-DF) and the ModRM byte: with a register (mod 3) by the byte, with memory by
the `reg` field. Each form is in a table with its text (for the disassembler) and the method that does it; a form
that is not in the tables stops the emulator, with its bytes.
"""
import math
import struct

from . import modrm
from .regs import AX

CONTROL_RESET = 0x037F  # what FNINIT puts in the control word: all the exceptions masked, extended precision
C0, C1, C2, C3 = 0x0100, 0x0200, 0x0400, 0x4000  # the condition codes in the status word
CONDITIONS = C0 | C1 | C2 | C3
TOP_SHIFT, TOP_MASK = 11, 0x3800  # where the number of the top register is in the status word
INVALID_OPERATION, ZERO_DIVIDE = 0x0001, 0x0004  # the flags of the exceptions that are done
EXCEPTIONS = 0x80FF  # the flags of all of them, and the busy bit
CONTROL_BITS, CONTROL_ALWAYS = 0x1F3F, 0x0040  # the bits of the control word that are kept, and the one that reads 1

# the forms with a register: (opcode, ModRM byte) -> (text, method). `{i}` in the text is the number of the register,
# the low 3 bits of the byte, and the method takes it: these forms are in FAMILIES under the byte without them.
SINGLES = {
    (0xD9, 0xE0): ("fchs", "fchs"),
    (0xD9, 0xE8): ("fld1", "fld1"),
    (0xD9, 0xEE): ("fldz", "fldz"),
    (0xDB, 0xE2): ("fnclex", "fnclex"),
    (0xDB, 0xE3): ("fninit", "fninit"),
    (0xDE, 0xD9): ("fcompp", "fcompp"),
    (0xDF, 0xE0): ("fnstsw ax", "fnstsw_ax"),
}
FAMILIES = {
    (0xD9, 0xC0): ("fld st({i})", "fld_st"),
    (0xD9, 0xC8): ("fxch st({i})", "fxch"),
    (0xD8, 0xF0): ("fdiv st, st({i})", "fdiv_st"),
    (0xDE, 0xC8): ("fmulp st({i}), st", "fmulp"),
    (0xDE, 0xE8): ("fsubp st({i}), st", "fsubp"),
    (0xDE, 0xF8): ("fdivp st({i}), st", "fdivp"),
}
# the forms with memory: (opcode, the reg field) -> (text with the size, method)
MEMORY = {
    (0xDD, 0): ("fld qword", "fld_m64"),
    (0xDC, 3): ("fcomp qword", "fcomp_m64"),
    (0xD9, 5): ("fldcw word", "fldcw"),
    (0xD9, 7): ("fnstcw word", "fnstcw"),
    (0xDD, 7): ("fnstsw word", "fnstsw_m"),
}

# the opcodes that have at least one form that is done: the others stop the emulator as an opcode that is not known
OPCODES = sorted({op for table in (SINGLES, FAMILIES, MEMORY) for op, _ in table})


def describe(op, byte, reg, memory):
    """The text of the instruction for the disassembler: `byte` is its ModRM byte, `reg` the field of it, `memory` the
    operand if it is memory (as text), else None. None if the instruction is not one of those that are done."""
    if memory is None:
        form = SINGLES.get((op, byte))
        if form is not None:
            return form[0]
        form = FAMILIES.get((op, byte & 0xF8))
        return form[0].format(i=byte & 7) if form is not None else None
    form = MEMORY.get((op, reg))
    return f"{form[0]} {memory}" if form is not None else None


def divide(a, b):
    """a / b as the chip does it with its exceptions masked, and the flags of those it raised: (the quotient, flags)."""
    if a != a or b != b:  # a number that is not one goes through, and raises nothing
        return math.nan, 0
    if b == 0:
        if a == 0:
            return math.nan, INVALID_OPERATION
        return math.copysign(math.inf, a) * math.copysign(1.0, b), ZERO_DIVIDE
    if math.isinf(a) and math.isinf(b):
        return math.nan, INVALID_OPERATION
    return a / b, 0


def compare(a, b):
    """The condition codes of the comparison of a with b: C3 for equal, C0 for less, none for more, all three for the
    two that cannot be compared (one of them is not a number)."""
    if a != a or b != b:
        return C0 | C2 | C3
    return C3 if a == b else C0 if a < b else 0


class Fpu:
    """The part of the CPU that is the coprocessor."""

    def init_fpu(self):
        self.fst = [0.0] * 8  # the registers, by their number in the chip: ST(i) is the one that is TOP + i
        self.ftop = 0
        self.fcw = CONTROL_RESET
        self.fsw = 0  # the status word but for the top of the stack, which is ftop

    def status_word(self):
        return self.fsw & ~TOP_MASK | self.ftop << TOP_SHIFT

    def st(self, i):
        return self.fst[(self.ftop + i) & 7]

    def set_st(self, i, value):
        self.fst[(self.ftop + i) & 7] = value

    def fpu_push(self, value):
        self.ftop = (self.ftop - 1) & 7
        self.fst[self.ftop] = value

    def fpu_pop(self):
        value = self.fst[self.ftop]
        self.ftop = (self.ftop + 1) & 7
        return value

    def set_conditions(self, codes):
        self.fsw = self.fsw & ~CONDITIONS | codes

    def fpu_instruction(self, op):
        """An instruction of the coprocessor (D8-DF + ModRM)."""
        m = modrm.decode(self, self.seg_override)
        if m.is_reg:
            byte = 0xC0 | m.reg << 3 | m.n
            form = SINGLES.get((op, byte))
            if form is not None:
                return getattr(self, form[1])()
            form = FAMILIES.get((op, byte & 0xF8))
            if form is not None:
                return getattr(self, form[1])(m.n)
        else:
            form = MEMORY.get((op, m.reg))
            if form is not None:
                return getattr(self, form[1])(m)
        raise NotImplementedError(f"x87 instruction at {self.s[self.CS]:04X}:{self.start_ip:04X}: "
                                  f"{self.code_bytes().hex(' ')}")

    def fninit(self):
        self.fcw, self.fsw, self.ftop = CONTROL_RESET, 0, 0

    def fnclex(self):
        self.fsw &= ~EXCEPTIONS

    def fldcw(self, m):
        self.fcw = m.read(self, 16) & CONTROL_BITS | CONTROL_ALWAYS  # as the chip keeps it: FFFF is read back 1F7F

    def fnstcw(self, m):
        m.write(self, 16, self.fcw)

    def fnstsw_m(self, m):
        m.write(self, 16, self.status_word())

    def fnstsw_ax(self):
        self.r[AX] = self.status_word()

    def fld1(self):
        self.fpu_push(1.0)

    def fldz(self):
        self.fpu_push(0.0)

    def fld_st(self, i):
        self.fpu_push(self.st(i))

    def fchs(self):
        self.set_st(0, -self.st(0))

    def read_double(self, m):
        """The 64-bit real that the memory operand is."""
        low, high = m.read(self, 32), self.rd(m.seg, (m.off + 4) & 0xFFFFFFFF)
        return struct.unpack("<d", struct.pack("<II", low, high))[0]

    def fld_m64(self, m):
        self.fpu_push(self.read_double(m))

    def fxch(self, i):
        top, other = self.st(0), self.st(i)
        self.set_st(0, other)
        self.set_st(i, top)

    def fdiv_st(self, i):
        quotient, raised = divide(self.st(0), self.st(i))
        self.fsw |= raised
        self.set_st(0, quotient)

    def fmulp(self, i):
        a, b = self.st(i), self.st(0)
        if (math.isinf(a) and b == 0) or (a == 0 and math.isinf(b)):  # an infinity by a zero is not a number
            self.fsw |= INVALID_OPERATION
        self.set_st(i, a * b)
        self.fpu_pop()

    def fsubp(self, i):
        a, b = self.st(i), self.st(0)
        if math.isinf(a) and math.isinf(b) and a == b:  # an infinity less the same one is not a number
            self.fsw |= INVALID_OPERATION
        self.set_st(i, a - b)
        self.fpu_pop()

    def fcomp_m64(self, m):
        codes = compare(self.st(0), self.read_double(m))
        if codes == C0 | C2 | C3:  # the comparison with a number that is not one is an invalid operation
            self.fsw |= INVALID_OPERATION
        self.set_conditions(codes)
        self.fpu_pop()

    def fdivp(self, i):
        quotient, raised = divide(self.st(i), self.st(0))
        self.fsw |= raised
        self.set_st(i, quotient)
        self.fpu_pop()

    def fcompp(self):
        codes = compare(self.st(0), self.st(1))
        if codes == C0 | C2 | C3:  # the comparison with a number that is not one is an invalid operation
            self.fsw |= INVALID_OPERATION
        self.set_conditions(codes)
        self.fpu_pop()
        self.fpu_pop()
