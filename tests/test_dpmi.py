"""The extender that the machine plays for a 32-bit program (step 3c1): the descriptors of INT 31h, the question that
asks if DOS/4G is there, and the answers of INT 2Fh in the flat mode."""
import unittest

from mydos import dos, dpmi, flags
from mydos.cpu import CPU, Exit
from mydos.loader import CODE_SELECTOR, DATA_SELECTOR

CODE_AT = 0x00200000


class FlatMachine(unittest.TestCase):
    """A CPU in the flat mode with DOS installed, which has an `int <n>` at CS:EIP, and the registers set before it."""

    def setUp(self):
        self.cpu = CPU()
        self.state = dos.install(self.cpu)
        self.cpu.enter_flat({CODE_SELECTOR: 0, DATA_SELECTOR: 0})
        self.bases = self.cpu.bases

    def call(self, number, ax, bx=0, cx=0, dx=0):
        cpu = self.cpu
        cpu.mem[CODE_AT:CODE_AT + 2] = bytes([0xCD, number])
        cpu.s[CPU.CS] = CODE_SELECTOR
        cpu.ip = CODE_AT
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.step()

    def failed(self):
        """None if the last call went well, else the error that it gave."""
        return self.cpu.r[CPU.AX] if self.cpu.get_flag(flags.CF) else None


class TestNewDescriptors(FlatMachine):
    def test_a_descriptor_is_a_selector_of_the_local_table_with_the_base_0_and_it_is_in_the_table(self):
        self.call(0x31, 0x0000, cx=1)
        selector = self.cpu.r[CPU.AX]
        self.assertIsNone(self.failed())
        self.assertEqual(selector, 3 << 3 | 7)  # the first after the code and the data of the program
        self.assertEqual(self.bases[selector], 0)

    def test_each_call_gives_a_new_one(self):
        selectors = []
        for _ in range(3):
            self.call(0x31, 0x0000, cx=1)
            selectors.append(self.cpu.r[CPU.AX])
        self.assertEqual(selectors, [0x1F, 0x27, 0x2F])

    def test_the_places_of_the_code_and_the_data_of_the_program_are_not_given_even_if_they_are_free(self):
        del self.bases[DATA_SELECTOR]  # the code selector stays: the program is running by it
        self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.cpu.r[CPU.AX], 3 << 3 | 7)

    def test_several_come_in_a_row_with_the_increment_of_8(self):
        self.call(0x31, 0x0000, cx=4)
        first = self.cpu.r[CPU.AX]
        self.assertEqual([first + 8 * i in self.bases for i in range(5)], [True] * 4 + [False])
        self.assertEqual(dpmi.INCREMENT, 8)

    def test_a_selector_that_is_in_the_table_is_not_given_again_whatever_it_is_for(self):
        self.bases[0x1F] = 0x1000  # the next one is taken (by a program that was given it before)
        self.bases[0x37] = 0x2000
        self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.cpu.r[CPU.AX], 0x27)
        self.call(0x31, 0x0000, cx=2)  # 0x2F and 0x37: the row has to skip the one that is taken
        self.assertEqual(self.cpu.r[CPU.AX], 0x3F)
        self.assertEqual((self.bases[0x1F], self.bases[0x37]), (0x1000, 0x2000))

    def test_the_segment_of_the_psp_is_not_a_selector_that_can_be_given(self):
        self.bases[0x1000] = 0x10000  # a selector with the number of a segment: the bits of its low part are 000
        for _ in range(3):
            self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.bases[0x1000], 0x10000)

    def test_none_is_an_error_8021(self):
        self.call(0x31, 0x0000, cx=0)
        self.assertEqual(self.failed(), 0x8021)
        self.assertEqual(sorted(self.bases), [CODE_SELECTOR, DATA_SELECTOR])

    def test_when_there_is_not_a_row_of_that_many_it_is_error_8011_and_nothing_is_taken(self):
        for index in range(3, 8192, 3):  # a descriptor in every third place: no row of three
            self.bases[index << 3 | 7] = 0
        taken = len(self.bases)
        self.call(0x31, 0x0000, cx=3)
        self.assertEqual(self.failed(), 0x8011)
        self.assertEqual(len(self.bases), taken)
        self.call(0x31, 0x0000, cx=2)  # a row of two is there
        self.assertIsNone(self.failed())

    def test_the_last_descriptor_of_the_table_can_be_had_and_there_is_no_one_after_it(self):
        for index in range(3, 8191):
            self.bases[index << 3 | 7] = 0
        self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.cpu.r[CPU.AX], 8191 << 3 | 7)
        self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.failed(), 0x8011)


class TestFreeing(FlatMachine):
    def test_a_descriptor_that_is_freed_is_not_in_the_table_and_can_be_given_again(self):
        self.call(0x31, 0x0000, cx=1)
        selector = self.cpu.r[CPU.AX]
        self.call(0x31, 0x0001, bx=selector)
        self.assertIsNone(self.failed())
        self.assertNotIn(selector, self.bases)
        self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.cpu.r[CPU.AX], selector)

    def test_one_that_is_not_there_is_error_8022(self):
        self.call(0x31, 0x0001, bx=0x1F)
        self.assertEqual(self.failed(), 0x8022)

    def test_a_selector_that_is_not_of_the_local_table_is_not_freed(self):
        self.bases[0x1000] = 0x10000  # that of the PSP
        self.call(0x31, 0x0001, bx=0x1000)
        self.assertEqual(self.failed(), 0x8022)
        self.assertIn(0x1000, self.bases)


class TestTheBase(FlatMachine):
    def make(self, base=0):
        self.call(0x31, 0x0000, cx=1)
        selector = self.cpu.r[CPU.AX]
        self.bases[selector] = base
        return selector

    def test_the_base_is_got_in_cx_and_dx_the_high_half_first(self):
        selector = self.make(0x12345678)
        self.call(0x31, 0x0006, bx=selector, cx=0xAAAA, dx=0xBBBB)
        self.assertEqual((self.failed(), self.cpu.r[CPU.CX], self.cpu.r[CPU.DX]), (None, 0x1234, 0x5678))

    def test_the_base_that_is_set_is_the_one_the_cpu_uses_at_once(self):
        selector = self.make()
        self.cpu.mem[0x300004] = 0x5A
        self.call(0x31, 0x0007, bx=selector, cx=0x0030, dx=0x0000)
        self.assertIsNone(self.failed())
        self.assertEqual(self.bases[selector], 0x300000)
        self.assertEqual(self.cpu.rb(selector, 4), 0x5A)

    def test_the_selectors_of_the_loader_can_be_asked_about_too(self):
        self.call(0x31, 0x0006, bx=CODE_SELECTOR)
        self.assertEqual((self.failed(), self.cpu.r[CPU.CX], self.cpu.r[CPU.DX]), (None, 0, 0))

    def test_a_selector_that_is_not_there_is_error_8022_for_all_four_calls(self):
        for ax in (0x0006, 0x0007, 0x0008, 0x0009):
            self.call(0x31, ax, bx=0x4F)
            self.assertEqual(self.failed(), 0x8022, hex(ax))

    def test_the_limit_and_the_rights_are_taken_and_change_nothing(self):
        selector = self.make(0x1000)
        for ax in (0x0008, 0x0009):
            self.call(0x31, ax, bx=selector, cx=0x000F, dx=0xFFFF)
            self.assertIsNone(self.failed())
        self.assertEqual(self.bases[selector], 0x1000)

    def test_a_base_of_more_than_32_bits_does_not_come_from_two_words(self):
        selector = self.make()
        self.call(0x31, 0x0007, bx=selector, cx=0xFFFF, dx=0xFFFF)
        self.assertEqual(self.bases[selector], 0xFFFFFFFF)


class TestTheCall(FlatMachine):
    def test_the_version_is_dpmi_0_9_for_a_32_bit_program_on_a_486_with_the_pics_at_08_and_70(self):
        self.call(0x31, 0x0400)
        cpu = self.cpu
        self.assertIsNone(self.failed())
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x0009, 0x0001, 4, 0x0870))

    def test_a_call_that_is_not_done_stops_the_emulator_with_its_number(self):
        with self.assertRaisesRegex(NotImplementedError, "INT 31h AX=0501"):
            self.call(0x31, 0x0501)

    def test_a_successful_call_clears_the_carry_and_leaves_the_other_registers(self):
        cpu = self.cpu
        cpu.set_flag(flags.CF, True)
        cpu.set32(CPU.SI, 0x11223344)
        cpu.set32(CPU.BP, 0x55667788)
        self.call(0x31, 0x0006, bx=CODE_SELECTOR)
        self.assertEqual((cpu.get_flag(flags.CF), cpu.get32(CPU.SI), cpu.get32(CPU.BP)),
                         (False, 0x11223344, 0x55667788))

    def test_it_is_not_there_in_real_mode(self):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = b"\xCD\x31"
        with self.assertRaisesRegex(NotImplementedError, "INT 31h from real mode"):
            cpu.step()


class TestTheExtenderQuestion(FlatMachine):
    def test_al_says_it_is_there_and_gs_is_a_selector_with_the_base_0(self):
        self.cpu.set_flag(flags.CF, True)
        self.call(0x21, 0xFF00, dx=0x78)
        cpu = self.cpu
        self.assertNotEqual(cpu.r[CPU.AX] & 0xFF, 0)
        self.assertFalse(cpu.get_flag(flags.CF))
        self.assertIn(cpu.s[CPU.GS], self.bases)
        self.assertEqual(self.bases[cpu.s[CPU.GS]], 0)
        self.assertEqual(cpu.s[CPU.GS] & 7, 7)

    def test_gs_with_the_base_0_is_kept_and_another_one_is_replaced(self):
        self.call(0x21, 0xFF00, dx=0x78)
        gs = self.cpu.s[CPU.GS]
        self.call(0x21, 0xFF00, dx=0x78)
        self.assertEqual(self.cpu.s[CPU.GS], gs)
        self.assertEqual(len(self.bases), 3)
        self.bases[gs] = 0x5000  # the program made it something else
        self.call(0x21, 0xFF00, dx=0x78)
        self.assertNotEqual(self.cpu.s[CPU.GS], gs)
        self.assertEqual((self.bases[gs], self.bases[self.cpu.s[CPU.GS]]), (0x5000, 0))

    def test_a_gs_that_names_nothing_in_the_table_is_replaced(self):
        self.cpu.s[CPU.GS] = 0x77
        self.call(0x21, 0xFF00, dx=0x78)
        self.assertIn(self.cpu.s[CPU.GS], self.bases)
        self.assertNotEqual(self.cpu.s[CPU.GS], 0x77)

    def test_the_question_with_another_dx_is_not_the_one_that_is_done(self):
        with self.assertRaisesRegex(NotImplementedError, "INT 21h AX=FF00 in the flat mode"):
            self.call(0x21, 0xFF00, dx=0x79)

    def test_in_real_mode_it_is_still_a_function_that_is_not_done(self):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = b"\xCD\x21"
        cpu.r[CPU.AX], cpu.r[CPU.DX] = 0xFF00, 0x78
        with self.assertRaisesRegex(NotImplementedError, "INT 21h AH=FF"):
            cpu.step()


class TestDosInTheFlatMode(FlatMachine):
    def test_the_version_is_answered_as_in_real_mode(self):
        self.call(0x21, 0x3000)
        self.assertEqual((self.cpu.r[CPU.AX], self.failed()), (0x0107, None))

    def test_the_end_of_the_program_ends_the_emulator_with_the_code(self):
        with self.assertRaises(Exit) as stop:
            self.call(0x21, 0x4C07)
        self.assertEqual(stop.exception.code, 7)

    def test_a_function_that_is_not_done_for_the_flat_mode_stops_with_its_number_and_the_mode(self):
        for ax in (0x3D00, 0x4800, 0x0900):
            with self.assertRaisesRegex(NotImplementedError, f"INT 21h AX={ax:04X} in the flat mode"):
                self.call(0x21, ax)


class TestInt2f(FlatMachine):
    def test_the_question_if_the_program_is_in_protected_mode_is_answered_with_0(self):
        self.call(0x2F, 0x1686)
        self.assertEqual(self.cpu.r[CPU.AX], 0)

    def test_in_real_mode_the_ax_of_it_is_left_as_it_was_and_it_is_not_done(self):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = b"\xCD\x2F"
        cpu.r[CPU.AX] = 0x1686
        with self.assertRaisesRegex(NotImplementedError, "INT 2Fh AX=1686"):
            cpu.step()

    def test_the_other_questions_are_answered_as_in_real_mode(self):
        self.call(0x2F, 0x1600)  # Windows: nothing answers, AX stays
        self.assertEqual(self.cpu.r[CPU.AX], 0x1600)
        with self.assertRaisesRegex(NotImplementedError, "INT 2Fh AX=FA00"):
            self.call(0x2F, 0xFA00)


if __name__ == "__main__":
    unittest.main()
