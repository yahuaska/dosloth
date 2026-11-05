"""The coprocessor in the 32-bit mode (step x1): what the start code of the Watcom runtime asks of it. The expected
status words are what the coprocessor of a real machine answered to the same instructions (a C program with the bytes
of the instructions in inline assembly, run on the host); the control word is what it reads back."""
import math
import struct
import unittest

from dosloth import fpu
from dosloth.cpu import CPU
from dosloth.disasm import disasm
from dosloth.flat import Bases

FLAT = 0x10
AT = 0x00400000
DATA = 0x00500000
STACK = 0x00600000

FNINIT, FNCLEX = "db e3", "db e2"
FLD1, FLDZ, FCHS, FLD_ST0 = "d9 e8", "d9 ee", "d9 e0", "d9 c0"
FDIVP1, FDIVP2, FCOMPP = "de f9", "de fa", "de d9"
FNSTSW_AX = "df e0"
FNSTSW_MEM = "dd 3d 00 00 50 00"  # fnstsw [00500000]
FLDCW_MEM = "d9 2d 00 00 50 00"
FNSTCW_MEM = "d9 3d 00 00 50 00"

# the start code of the runtime that tells the kind of the coprocessor: push ax / fwait fninit / fld1 fldz
# fdivp st(1),st / fld st(0) fchs fcompp / fnstsw ax / mov al,2 sahf je +2 / mov al,3 / fwait fninit / fldcw [esp] /
# xchg [esp],ax / pop ax / ret   (66 prefixes, as the game has it; from smkp.exe)
KIND = ("66 50 9b db e3 d9 e8 d9 ee de f9 d9 c0 d9 e0 de d9 df e0 b0 02 9e 74 02 b0 03 9b db e3 d9 2c 24 "
        "66 87 04 24 66 58 c3")


def bytes_of(*parts):
    return bytes.fromhex(" ".join(parts))


class FpuMachine(unittest.TestCase):
    def machine(self, code, esp=STACK):
        cpu = CPU()
        cpu.enter_flat(Bases({FLAT: 0}))
        cpu.s[:] = [FLAT] * 6
        cpu.mem[AT:AT + len(code)] = code
        cpu.ip = AT
        cpu.set32(CPU.SP, esp)
        return cpu

    def run_instructions(self, *instructions, cpu=None):
        """Run the instructions (each a string of hex bytes) one after another, and give the CPU."""
        cpu = cpu or self.machine(bytes_of(*instructions))
        for _ in instructions:
            cpu.step()
        return cpu

    def status(self, *instructions):
        return self.run_instructions(*instructions, FNSTSW_AX).r[CPU.AX]


class TestTheKindOfTheChip(FpuMachine):
    def test_after_fninit_the_control_word_says_a_chip_is_there(self):
        cpu = self.machine(bytes_of("6a 00", FNINIT, "d9 3c 24", "58"))  # push 0 / fninit / fnstcw [esp] / pop eax
        for _ in range(4):
            cpu.step()
        self.assertEqual(cpu.get32(CPU.AX), 0x037F)  # the high byte is 3: it is a coprocessor

    def test_the_routine_of_the_runtime_finds_a_387(self):
        cpu = self.machine(bytes_of(KIND))
        cpu.set32(CPU.AX, 0x037F)
        cpu.set32(CPU.SP, STACK)
        cpu.push32(0xAAAAAAAA)  # the address of whoever called it
        for _ in range(20):  # the instructions of the routine, the return at the end of them
            cpu.step()
        self.assertEqual(cpu.r[CPU.AX] & 0xFF, 3)  # 3: the infinities are of the 387 (+inf is not -inf)
        self.assertEqual(cpu.ip, 0xAAAAAAAA)
        self.assertEqual(cpu.get32(CPU.SP), STACK + 0)  # what it pushed it took off

    def test_the_comparison_of_the_two_infinities_in_it_leaves_c0_and_the_zero_divide_flag(self):
        self.assertEqual(self.status(FLD1, FLDZ, FDIVP1, FLD_ST0, FCHS, FCOMPP), 0x0104)


class TestReset(FpuMachine):
    def test_fninit_puts_the_control_and_status_words_and_the_top_as_they_were_at_the_start(self):
        cpu = self.machine(bytes_of(FLD1, FLDZ, FDIVP1, FLD1, FNINIT, FNSTSW_AX))
        cpu.fcw = 0x0C7F
        for _ in range(6):
            cpu.step()
        self.assertEqual((cpu.r[CPU.AX], cpu.fcw), (0x0000, 0x037F))

    def test_fnclex_clears_the_flags_of_the_exceptions_and_leaves_the_rest(self):
        self.assertEqual(self.status(FLD1, FLDZ, FDIVP1, FNCLEX), 0x3800)
        self.assertEqual(self.status(FLD1, FLD1, FCOMPP, FNCLEX), 0x4000)  # the condition codes stay

    def test_a_new_cpu_has_the_state_of_a_chip_that_was_reset(self):
        cpu = CPU()
        self.assertEqual((cpu.fcw, cpu.status_word()), (0x037F, 0))


class TestTheControlWord(FpuMachine):
    def test_it_is_read_back_as_the_chip_keeps_it(self):
        # what a chip answered: 0C7F, FFFF, 0000 are read back 0C7F, 1F7F, 0040
        for written, read in ((0x0C7F, 0x0C7F), (0xFFFF, 0x1F7F), (0x0000, 0x0040), (0x037F, 0x037F)):
            cpu = self.machine(bytes_of(FLDCW_MEM, FNSTCW_MEM))
            cpu.mem[DATA:DATA + 2] = written.to_bytes(2, "little")
            cpu.step()
            cpu.step()
            self.assertEqual(int.from_bytes(cpu.mem[DATA:DATA + 2], "little"), read, hex(written))

    def test_fninit_after_a_new_control_word_gives_037f_back(self):
        cpu = self.machine(bytes_of(FLDCW_MEM, FNINIT, FNSTCW_MEM))
        cpu.mem[DATA:DATA + 2] = (0x127F).to_bytes(2, "little")
        for _ in range(3):
            cpu.step()
        self.assertEqual(int.from_bytes(cpu.mem[DATA:DATA + 2], "little"), 0x037F)

    def test_the_memory_operand_may_be_any_form_of_the_32_bit_addressing(self):
        cpu = self.machine(bytes_of("d9 7c 24 04", "d9 6c 24 04"))  # fnstcw [esp+4] / fldcw [esp+4]
        cpu.fcw = 0x0C7F
        cpu.step()
        self.assertEqual(int.from_bytes(cpu.mem[STACK + 4:STACK + 6], "little"), 0x0C7F)
        cpu.mem[STACK + 4:STACK + 6] = (0x0F7F).to_bytes(2, "little")
        cpu.step()
        self.assertEqual(cpu.fcw, 0x0F7F)


class TestTheStack(FpuMachine):
    def test_a_push_lowers_the_top_and_it_is_in_the_status_word(self):
        self.assertEqual(self.status(), 0x0000)
        self.assertEqual(self.status(FLD1), 0x3800)
        self.assertEqual(self.status(FLD1, FLDZ, FLD1), 0x2800)

    def test_eight_pushes_go_round_to_the_top_it_began_with(self):
        self.assertEqual(self.status(*[FLD1] * 8), 0x0000)

    def test_fnstsw_to_memory_is_the_same_word_as_to_ax(self):
        cpu = self.run_instructions(FLD1, FLDZ, FNSTSW_MEM)
        self.assertEqual(int.from_bytes(cpu.mem[DATA:DATA + 2], "little"), 0x3000)

    def test_fld_st_copies_a_register_of_the_stack_to_the_top(self):
        cpu = self.run_instructions(FLD1, FLDZ, "d9 c1")  # fld st(1)
        self.assertEqual([cpu.st(0), cpu.st(1), cpu.st(2)], [1.0, 0.0, 1.0])

    def test_fldz_pushes_a_zero_and_fchs_changes_its_sign(self):
        cpu = self.run_instructions(FLDZ, FCHS)
        self.assertTrue(math.copysign(1.0, cpu.st(0)) < 0 and cpu.st(0) == 0.0)
        cpu = self.run_instructions(FLD1, FCHS, FCHS)
        self.assertEqual(cpu.st(0), 1.0)


class TestDivision(FpuMachine):
    def test_one_by_zero_is_an_infinity_and_raises_the_zero_divide_flag(self):
        cpu = self.run_instructions(FLD1, FLDZ, FDIVP1, FNSTSW_AX)
        self.assertEqual((cpu.r[CPU.AX], cpu.st(0)), (0x3804, math.inf))

    def test_minus_one_by_zero_is_minus_infinity(self):
        cpu = self.run_instructions(FLD1, FCHS, FLDZ, FDIVP1, FNSTSW_AX)
        self.assertEqual((cpu.r[CPU.AX], cpu.st(0)), (0x3804, -math.inf))

    def test_the_sign_of_the_zero_is_the_sign_of_the_infinity(self):
        cpu = self.run_instructions(FLD1, FLDZ, FCHS, FDIVP1)
        self.assertEqual(cpu.st(0), -math.inf)

    def test_zero_by_zero_is_not_a_number_and_raises_the_invalid_operation_flag(self):
        cpu = self.run_instructions(FLDZ, FLDZ, FDIVP1, FNSTSW_AX)
        self.assertEqual(cpu.r[CPU.AX], 0x3801)
        self.assertTrue(math.isnan(cpu.st(0)))

    def test_a_number_that_is_not_one_is_divided_and_divides_and_raises_nothing(self):
        # as a chip answered: nan/0, 1/nan and nan/1 all leave the status word 3800 (one register in use, no flags)
        for first in ((FLDZ, FLDZ, FDIVP1, FNCLEX, FLDZ), (FLD1, FLDZ, FLDZ, FDIVP1, FNCLEX),
                      (FLDZ, FLDZ, FDIVP1, FNCLEX, FLD1)):
            cpu = self.run_instructions(*first, FDIVP1, FNSTSW_AX)
            self.assertEqual(cpu.r[CPU.AX], 0x3800, first)
            self.assertTrue(math.isnan(cpu.st(0)), first)

    def test_an_infinity_by_an_infinity_is_not_a_number_and_the_invalid_operation_flag_is_raised(self):
        cpu = self.run_instructions(FLD1, FLDZ, FDIVP1, FLD_ST0, FDIVP1, FNSTSW_AX)
        self.assertEqual(cpu.r[CPU.AX], 0x3805)  # the zero divide of the first division stays
        self.assertTrue(math.isnan(cpu.st(0)))

    def test_a_number_by_an_infinity_is_a_zero_and_an_infinity_by_a_number_is_an_infinity_with_no_flag(self):
        cpu = self.run_instructions(FLD1, FLD1, FLDZ, FDIVP1, FNCLEX, FDIVP1, FNSTSW_AX)  # 1/inf
        self.assertEqual((cpu.r[CPU.AX], cpu.st(0)), (0x3800, 0.0))
        cpu = self.run_instructions(FLD1, FLDZ, FDIVP1, FNCLEX, FLD1, FDIVP1, FNSTSW_AX)  # inf/1
        self.assertEqual((cpu.r[CPU.AX], cpu.st(0)), (0x3800, math.inf))

    def test_a_division_that_is_all_right_raises_nothing_and_pops_the_divisor(self):
        cpu = self.run_instructions(FLD1, FLD1, FDIVP1, FNSTSW_AX)
        self.assertEqual((cpu.r[CPU.AX], cpu.st(0)), (0x3800, 1.0))

    def test_the_register_is_the_dividend_and_the_top_is_the_divisor(self):
        cpu = self.machine(bytes_of(FLD1, FLD1, FLD1, FDIVP2, FNSTSW_AX))
        cpu.fst[:] = [0.0] * 8
        for _ in range(3):
            cpu.step()
        cpu.set_st(0, 4.0)  # ST(0)
        cpu.set_st(1, 7.0)
        cpu.set_st(2, 6.0)  # the dividend of FDIVP ST(2),ST
        cpu.step()
        cpu.step()
        self.assertEqual((cpu.r[CPU.AX], cpu.st(0), cpu.st(1)), (0x3000, 7.0, 1.5))

    def test_the_flags_stay_until_they_are_cleared_and_are_ored_together(self):
        self.assertEqual(self.status(FLDZ, FLDZ, FDIVP1, FNCLEX, FLD1, FLDZ, FDIVP1), 0x3004)
        self.assertEqual(self.status(FLD1, FLDZ, FDIVP1, FLDZ, FLDZ, FDIVP1), 0x3005)


class TestComparison(FpuMachine):
    def test_less_equal_and_more_are_c0_c3_and_none_and_the_two_are_taken_off(self):
        cpu = self.machine(bytes_of(FLD1, FLD1, FCOMPP, FNSTSW_AX))
        for _ in range(4):
            cpu.step()
        self.assertEqual(cpu.r[CPU.AX], 0x4000)  # equal
        for first, second, expected in ((1.0, 2.0, 0x0100), (2.0, 1.0, 0x0000)):  # ST(0)=first, ST(1)=second
            cpu = self.machine(bytes_of(FLD1, FLD1, FCOMPP, FNSTSW_AX))
            cpu.step()
            cpu.step()
            cpu.set_st(0, first)
            cpu.set_st(1, second)
            cpu.step()
            cpu.step()
            self.assertEqual(cpu.r[CPU.AX], expected, (first, second))

    def test_the_condition_codes_of_the_one_before_are_gone(self):
        cpu = self.machine(bytes_of(FLD1, FLD1, FCOMPP, FLD1, FLD1, FCOMPP, FNSTSW_AX))
        cpu.step()
        cpu.step()
        cpu.set_st(1, 2.0)  # ST(0) = 1 is less than ST(1) = 2
        cpu.step()
        self.assertEqual(cpu.status_word() & 0x4700, 0x0100)
        for _ in range(4):  # then 1 and 1: a chip answered 4000 to less and then equal, C0 is cleared
            cpu.step()
        self.assertEqual(cpu.r[CPU.AX], 0x4000)

    def test_a_number_that_is_not_one_makes_c0_c2_c3_and_the_invalid_operation_flag(self):
        self.assertEqual(self.status(FLDZ, FLDZ, FDIVP1, FNCLEX, FLDZ, FCOMPP), 0x4501)
        self.assertEqual(self.status(FLDZ, FLDZ, FDIVP1, FLD_ST0, FCOMPP), 0x4501)

    def test_an_infinity_is_less_than_a_number_and_it_is_not_the_invalid_operation(self):
        self.assertEqual(self.status(FLD1, FLDZ, FDIVP1, FNCLEX, FLD1, FCOMPP), 0x0100)

    def test_fnstsw_into_ax_and_sahf_make_the_flags_of_a_jump(self):
        cpu = self.run_instructions(FLD1, FLD1, FCOMPP, FNSTSW_AX, "9e")  # equal: ZF
        self.assertEqual(cpu.flags & 0x0041, 0x0040)
        cpu = self.run_instructions(FLD1, FLDZ, FDIVP1, FLD_ST0, FCHS, FCOMPP, FNSTSW_AX, "9e")  # less: CF
        self.assertEqual(cpu.flags & 0x0041, 0x0001)


class TestWhatIsNotThere(FpuMachine):
    def test_a_form_that_is_not_done_stops_with_its_bytes(self):
        cpu = self.machine(bytes_of("d9 d0"))  # fnop
        with self.assertRaisesRegex(NotImplementedError, "x87 instruction at 0010:400000: d9 d0"):
            cpu.step()
        cpu = self.machine(bytes_of("da 05 00 00 50 00"))  # fiadd dword [00500000]: no form of DA is done
        with self.assertRaisesRegex(NotImplementedError, "opcode DA at 0010:400000: da 05 00 00 50 00"):
            cpu.step()

    def test_real_mode_has_no_coprocessor_and_the_instructions_do_nothing_but_take_their_bytes(self):
        cpu = CPU()
        cpu.mem[0x10000:0x10007] = bytes_of("db e3", "d9 2e 00 02", "90")  # fninit / fldcw [0200] / nop
        cpu.s[CPU.CS] = cpu.s[CPU.DS] = 0x1000
        cpu.mem[0x10200:0x10202] = b"\xFF\xFF"
        cpu.fcw = 0x0C7F
        cpu.step()
        cpu.step()
        self.assertEqual((cpu.ip, cpu.fcw), (6, 0x0C7F))  # the length was right, and nothing was done


class TestTheRealsOfTheStack(FpuMachine):
    """The loads and the arithmetic of the stack that the check of the divide of a Pentium in the runtime of the game
    uses (fld qword, fxch, fdiv st,st(i), fmulp, fsubp, fcomp qword). Which register is the dividend and which is taken
    from which is what the chip of the host answered to the bytes of the instructions (fdiv st,st(1) with 8 over 2 is 4,
    fsubp st(1),st with 3 and 10 is 7, fmulp with 6 and 7 is 42); the numbers are chosen to be the same in doubles."""

    def with_reals(self, *reals, code=()):
        """A machine with the doubles at [esp], [esp+8], ... and the code ready to run."""
        cpu = self.machine(bytes_of(*code))
        for number, real in enumerate(reals):
            cpu.mem[STACK + 8 * number:STACK + 8 * number + 8] = struct.pack("<d", real)
        return cpu

    def pushed(self, *reals, code):
        """A machine with the reals pushed (the last is the top) and the code ready."""
        cpu = self.machine(bytes_of(*code))
        for real in reals:
            cpu.fpu_push(real)
        return cpu

    def test_fld_qword_pushes_the_real_in_memory(self):
        cpu = self.with_reals(1.5, -2.25, code=("dd 44 24 08", "dd 04 24"))
        cpu.step()
        self.assertEqual((cpu.st(0), cpu.ftop), (-2.25, 7))
        cpu.step()
        self.assertEqual((cpu.st(0), cpu.st(1), cpu.ftop), (1.5, -2.25, 6))

    def test_fld_qword_takes_the_real_from_any_form_of_the_address(self):
        cpu = self.machine(bytes_of("dd 05 00 00 60 00"))  # fld qword [00600000]
        cpu.mem[0x00600000:0x00600008] = struct.pack("<d", 3.5)
        cpu.step()
        self.assertEqual(cpu.st(0), 3.5)

    def test_fxch_swaps_the_top_with_a_register(self):
        cpu = self.pushed(1.0, 2.0, 3.0, code=("d9 c9",))
        cpu.step()
        self.assertEqual((cpu.st(0), cpu.st(1), cpu.st(2)), (2.0, 3.0, 1.0))
        cpu = self.pushed(1.0, 2.0, 3.0, code=("d9 ca",))
        cpu.step()
        self.assertEqual((cpu.st(0), cpu.st(1), cpu.st(2)), (1.0, 2.0, 3.0))

    def test_fxch_takes_the_sign_of_a_zero_with_it(self):
        cpu = self.pushed(-0.0, 1.0, code=("d9 c9",))
        cpu.step()
        self.assertEqual(math.copysign(1, cpu.st(0)), -1.0)

    def test_fdiv_st_divides_the_top_by_a_register_and_nothing_is_popped(self):
        cpu = self.pushed(2.0, 8.0, code=("d8 f1",))  # the top is 8, st(1) is 2
        cpu.step()
        self.assertEqual((cpu.st(0), cpu.st(1), cpu.ftop), (4.0, 2.0, 6))

    def test_fdiv_st_by_a_zero_is_an_infinity_and_the_flag(self):
        cpu = self.pushed(0.0, 1.0, code=("d8 f1",))
        cpu.step()
        self.assertEqual(cpu.st(0), math.inf)
        self.assertEqual(cpu.fsw & fpu.ZERO_DIVIDE, fpu.ZERO_DIVIDE)

    def test_fmulp_multiplies_the_register_by_the_top_and_takes_the_top_off(self):
        cpu = self.pushed(6.0, 7.0, code=("de c9",))
        cpu.step()
        self.assertEqual((cpu.st(0), cpu.ftop), (42.0, 7))

    def test_an_infinity_by_a_zero_is_not_a_number_and_the_flag_is_raised(self):
        for infinity, zero in ((math.inf, 0.0), (0.0, math.inf)):
            cpu = self.pushed(infinity, zero, code=("de c9",))
            cpu.step()
            self.assertTrue(math.isnan(cpu.st(0)))
            self.assertEqual(cpu.fsw & fpu.INVALID_OPERATION, fpu.INVALID_OPERATION)

    def test_fsubp_takes_the_top_from_the_register_and_takes_the_top_off(self):
        cpu = self.pushed(10.0, 3.0, code=("de e9",))  # st(1) = 10 less the top, 3
        cpu.step()
        self.assertEqual((cpu.st(0), cpu.ftop), (7.0, 7))

    def test_two_infinities_of_the_same_sign_less_each_other_are_not_a_number_and_the_flag_is_raised(self):
        cpu = self.pushed(math.inf, math.inf, code=("de e9",))
        cpu.step()
        self.assertTrue(math.isnan(cpu.st(0)))
        self.assertEqual(cpu.fsw & fpu.INVALID_OPERATION, fpu.INVALID_OPERATION)
        cpu = self.pushed(math.inf, -math.inf, code=("de e9",))
        cpu.step()
        self.assertEqual((cpu.st(0), cpu.fsw & fpu.INVALID_OPERATION), (math.inf, 0))

    def test_fcomp_qword_compares_the_top_with_memory_and_takes_it_off(self):
        for top, real, codes in ((1.0, 2.0, fpu.C0), (2.0, 2.0, fpu.C3), (3.0, 2.0, 0)):
            cpu = self.pushed(top, code=("dc 1d 00 00 60 00",))  # fcomp qword [00600000]
            cpu.mem[0x00600000:0x00600008] = struct.pack("<d", real)
            cpu.step()
            self.assertEqual((cpu.fsw & fpu.CONDITIONS, cpu.ftop), (codes, 0), (top, real))

    def test_fcomp_qword_with_a_number_that_is_not_one_is_c0_c2_c3_and_the_flag(self):
        cpu = self.pushed(1.0, code=("dc 1d 00 00 60 00",))
        cpu.mem[0x00600000:0x00600008] = struct.pack("<d", math.nan)
        cpu.step()
        self.assertEqual(cpu.fsw & fpu.CONDITIONS, fpu.C0 | fpu.C2 | fpu.C3)
        self.assertEqual(cpu.fsw & fpu.INVALID_OPERATION, fpu.INVALID_OPERATION)

    def test_the_check_of_the_divide_of_the_pentium_in_the_runtime_finds_no_fault(self):
        """From the game: a / b * b - a for 4195835 and 3145727, compared with a constant (here 1.0): the difference is 0
        (the chip of the host: 0, and the same in doubles), it is less than the constant, and all the stack is taken off."""
        code = bytes_of("dd 44 24 08", "d9 c0", "dd 04 24", "d9 c9", "d8 f1", "de c9", "de e9",
                        "dc 1d 00 00 60 00", FNSTSW_AX)
        cpu = self.with_reals(3145727.0, 4195835.0, code=())  # [esp] = the divisor, [esp+8] = the dividend
        cpu.mem[AT:AT + len(code)] = code
        cpu.mem[0x00600000:0x00600008] = struct.pack("<d", 1.0)
        for _ in range(9):
            cpu.step()
        self.assertEqual(cpu.r[CPU.AX] & fpu.CONDITIONS, fpu.C0)
        self.assertEqual(cpu.ftop, 0)


class TestTheDisassembler(FpuMachine):
    def text(self, *instruction):
        cpu = self.machine(bytes_of(*instruction))
        return disasm(cpu, FLAT, AT)

    def test_the_forms_that_are_done_have_their_names_and_their_lengths(self):
        for hexbytes, text in ((FNINIT, "fninit"), (FNCLEX, "fnclex"), (FLD1, "fld1"), (FLDZ, "fldz"), (FCHS, "fchs"),
                               ("d9 c0", "fld st(0)"), ("d9 c3", "fld st(3)"), ("de f9", "fdivp st(1), st"),
                               ("de fa", "fdivp st(2), st"), (FCOMPP, "fcompp"), (FNSTSW_AX, "fnstsw ax"),
                               ("d9 3c 24", "fnstcw word [esp]"), ("d9 2c 24", "fldcw word [esp]"),
                               (FNSTSW_MEM, "fnstsw word [00500000]"), ("d9 7c 24 04", "fnstcw word [esp+4]"),
                               ("dd 44 24 08", "fld qword [esp+8]"), ("d9 c9", "fxch st(1)"),
                               ("d8 f1", "fdiv st, st(1)"), ("de c9", "fmulp st(1), st"),
                               ("de e9", "fsubp st(1), st"), ("dc 1d 18 4a 1c 00", "fcomp qword [001C4A18]")):
            length, shown = self.text(hexbytes)
            self.assertEqual((length, shown), (len(bytes.fromhex(hexbytes)), text), hexbytes)

    def test_a_form_that_is_not_done_is_a_db_in_the_32_bit_code_where_the_cpu_stops_at_it(self):
        self.assertEqual(self.text("d9 d0"), (1, "db D9"))
        self.assertEqual(self.text("da c1"), (1, "db DA"))  # an opcode that has no form at all

    def test_in_the_16_bit_code_where_the_cpu_does_nothing_with_them_they_are_written_as_esc(self):
        cpu = CPU()
        cpu.mem[0x10100:0x10102] = b"\xD9\xD0"
        cpu.s[CPU.CS] = 0x1000
        self.assertEqual(disasm(cpu, 0x1000, 0x100), (2, "esc 0A, ax"))


class TestTheTables(unittest.TestCase):
    def test_the_opcodes_are_those_of_the_forms_and_only_them(self):
        self.assertEqual(fpu.OPCODES, [0xD8, 0xD9, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF])
        self.assertEqual(sorted(op for op in range(0xD8, 0xE0) if op in CPU().ops32), fpu.OPCODES)

    def test_every_form_in_the_tables_names_a_method_of_the_cpu(self):
        for table in (fpu.SINGLES, fpu.FAMILIES, fpu.MEMORY):
            for text, method in table.values():
                self.assertTrue(callable(getattr(CPU, method, None)), method)


if __name__ == "__main__":
    unittest.main()
