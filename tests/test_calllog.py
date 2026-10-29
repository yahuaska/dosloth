import contextlib
import io
import re
import unittest

from mydos import bios, calllog, dos
from mydos.cpu import CPU, Exit
from tests import test_dos

ARROW = " " * 22 + "-> "  # the second line of a call begins like this


class LoggedTest(test_dos.FileFunctionsTest):
    """The machine of the file function tests, with the log in front of the calls of DOS."""

    def setUp(self):
        super().setUp()
        self.out = io.StringIO()
        calllog.install(self.cpu, self.out)

    def lines(self):
        return self.out.getvalue().splitlines()


class TestOneCall(LoggedTest):
    def test_a_call_is_two_lines_what_was_asked_and_what_was_answered(self):
        self.call(0x3C00, "NEW.DAT")
        first, second = self.lines()
        self.assertRegex(first, r'^ +1  1000:0000  INT 21h AX=3C00 BX=0000 CX=0000 DX=0100 SI=0000 DI=0000 DS=2000 '
                                r'ES=0000 "NEW\.DAT"$')
        self.assertEqual(second, ARROW + "CF=0 AX=0005 BX=0000 CX=0000 DX=0100")  # the handle is in AX

    def test_the_arrow_is_under_the_int_of_the_first_line(self):
        self.call(0x3C00, "NEW.DAT")
        first, second = self.lines()
        self.assertEqual(first.index("INT"), second.index("->"))

    def test_an_error_is_cf_1_and_the_number_in_ax(self):
        self.call(0x3D00, "MISSING.TXT")
        self.assertEqual(self.lines()[1], ARROW + "CF=1 AX=0002 BX=0000 CX=0000 DX=0100")

    def test_the_carry_flag_shown_is_the_one_after_the_call_not_the_one_before(self):
        self.cpu.set_flag(1, True)  # CF set before; the call goes well and clears it
        self.call(0x3D00, "NOTES.TXT")
        self.assertIn("CF=0", self.lines()[1])

    def test_the_count_is_the_instructions_started_so_far(self):
        self.call(0x0E00, dx=2)
        self.call(0x0E00, dx=3)
        self.assertEqual([line.split()[0] for line in self.lines() if "INT" in line], ["1", "2"])

    def test_the_address_is_that_of_the_int_instruction(self):
        self.cpu.s[test_dos.CPU.CS] = 0x1234
        self.cpu.mem[0x12340:0x12342] = bytes([0xCD, 0x21])
        self.call(0x0E00, dx=2)
        self.assertIn("  1234:0000  INT 21h ", self.lines()[0])

    def test_the_log_changes_nothing_in_what_the_call_does(self):
        self.call(0x3C00, "NEW.DAT")
        self.assertEqual(self.cpu.r[CPU.AX], 5)
        self.assertTrue((self.base / "NEW.DAT").exists())


class TestNames(LoggedTest):
    def test_the_functions_that_take_a_name_show_it(self):
        for ax in (0x3900, 0x3A00, 0x3B00, 0x3C00, 0x3D00, 0x4100, 0x4300, 0x4B00, 0x4E00, 0x5B00):
            self.out.truncate(0)
            self.out.seek(0)
            try:
                self.call(ax, "C:\\GAMES\\X.COM")
            except Exception:  # some of these do things (the exec starts a program that is not there, and so on)
                pass
            self.assertIn(' "C:\\GAMES\\X.COM"', self.lines()[0], hex(ax))

    def test_a_function_that_takes_no_name_shows_none(self):
        self.call(0x0E00, dx=2)
        self.assertNotIn('"', self.lines()[0])

    def test_a_backslash_is_shown_as_it_is_and_the_others_are_escaped(self):
        self.call(0x3D00, "A\\B\x01\u00e4\"Z")  # in the code page of DOS, an a with two dots is the byte 84h
        self.assertIn(' "A\\B\\x01\\x84\\x22Z"', self.lines()[0])

    def test_a_long_name_is_cut_with_three_dots(self):
        self.call(0x3D00, "A" * 150)
        text = re.search(r'"(.*)"', self.lines()[0]).group(1)
        self.assertEqual(text, "A" * calllog.MAX_TEXT + "...")

    def test_a_name_of_exactly_the_most_is_not_cut(self):
        self.call(0x3D00, "A" * calllog.MAX_TEXT)
        self.assertIn('"' + "A" * calllog.MAX_TEXT + '"', self.lines()[0])

    def test_the_limit_is_100_characters(self):
        self.call(0x3D00, "A" * 100)
        self.assertIn('"' + "A" * 100 + '"', self.lines()[0])
        self.call(0x3D00, "A" * 101)
        self.assertIn('"' + "A" * 100 + '..."', self.lines()[2])

    def test_every_printable_character_is_as_it_is_and_the_delete_character_is_not(self):
        self.call(0x3D00, " !#~}|{abz09")
        self.assertIn('" !#~}|{abz09"', self.lines()[0])
        for i, byte in enumerate(b"A\x7fB\0"):
            self.cpu.wb(0x2000, 0x0100 + i, byte)
        self.call(0x3D00, dx=0x0100)
        self.assertIn('"A\\x7FB"', self.lines()[2])

    def test_the_bytes_written_may_go_round_the_end_of_the_segment_too(self):
        for i, byte in enumerate(b"wrap"):
            self.cpu.wb(0x2000, (0xFFFE + i) & 0xFFFF, byte)
        with contextlib.redirect_stdout(io.StringIO()):
            self.call(0x4000, bx=1, cx=4, dx=0xFFFE)
        self.assertTrue(self.lines()[0].endswith(' "wrap"'))

    def test_a_name_that_goes_round_the_end_of_the_segment_is_read_whole(self):
        self.call(0x3D00, "WRAPPED.TXT", dx=0xFFF8)
        self.assertIn('"WRAPPED.TXT"', self.lines()[0])

    def test_the_text_of_ah_09_ends_with_a_dollar_sign(self):
        for i, byte in enumerate(b"Hello, $ and not this\0"):
            self.cpu.wb(0x2000, 0x0300 + i, byte)
        with contextlib.redirect_stdout(io.StringIO()):
            self.call(0x0900, dx=0x0300)
        self.assertIn(' "Hello, "', self.lines()[0])

    def test_the_bytes_of_ah_40_are_shown_up_to_the_count(self):
        for i, byte in enumerate(b"hello there"):
            self.cpu.wb(0x2000, 0x0300 + i, byte)
        with contextlib.redirect_stdout(io.StringIO()):
            self.call(0x4000, bx=1, cx=5, dx=0x0300)
        self.assertTrue(self.lines()[0].endswith(' "hello"'))
        self.assertIn("CX=0005", self.lines()[0])
        self.assertIn("AX=0005", self.lines()[1])

    def test_many_bytes_written_are_cut_with_three_dots(self):
        for i in range(300):
            self.cpu.wb(0x2000, 0x0300 + i, ord("z"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.call(0x4000, bx=1, cx=300, dx=0x0300)
        self.assertTrue(self.lines()[0].endswith('"' + "z" * calllog.MAX_TEXT + '..."'))

    def test_the_directory_of_ah_47_is_shown_with_the_answer(self):
        self.call(0x3B00, "GAMES")
        self.out.truncate(0)
        self.out.seek(0)
        self.cpu.r[test_dos.CPU.SI] = 0x0400
        self.call(0x4700, dx=0)
        self.assertEqual(self.lines()[1], ARROW + 'CF=0 AX=0100 BX=0000 CX=0000 DX=0000 "GAMES"')

    def test_an_error_of_ah_47_has_no_directory(self):
        self.call(0x4700, dx=26)  # no such drive
        self.assertNotIn('"', self.lines()[1])


class TestTheFlatMode(unittest.TestCase):
    """A program of 32 bits: the registers are shown whole, and so are the addresses and the pointers."""

    def setUp(self):
        self.cpu = CPU()
        dos.install(self.cpu)
        self.out = io.StringIO()
        calllog.install(self.cpu, self.out)
        self.cpu.enter_flat({0x0F: 0, 0x17: 0})
        self.cpu.s[:] = [0x17, 0x0F, 0x17, 0x17, 0, 0]

    def call(self, number, **registers):
        cpu = self.cpu
        cpu.mem[0x200000:0x200002] = bytes([0xCD, number])
        cpu.ip = 0x200000
        for name, value in registers.items():
            cpu.set32(getattr(CPU, name.upper()), value)
        cpu.step()

    def lines(self):
        return self.out.getvalue().splitlines()

    def test_the_registers_of_the_call_and_of_the_answer_are_of_32_bits_and_so_is_the_address(self):
        self.call(0x21, ax=0x3000, bx=0x50484152, si=0x12345678)
        first, second = self.lines()
        self.assertRegex(first, r"^ +1  000F:00200000  INT 21h EAX=00003000 EBX=50484152 ECX=00000000 EDX=00000000 "
                                r"ESI=12345678 EDI=00000000 DS=0017 ES=0017$")
        self.assertEqual(second, ARROW + "CF=0 EAX=00000107 EBX=5048FF00 ECX=00000000 EDX=00000000")

    def test_the_calls_of_the_dpmi_host_are_in_the_log_too(self):
        self.call(0x31, ax=0x0006, bx=0x0F)
        first, second = self.lines()
        self.assertIn("INT 31h EAX=00000006 EBX=0000000F", first)
        self.assertEqual(second, ARROW + "CF=0 EAX=00000006 EBX=0000000F ECX=00000000 EDX=00000000")

    def test_a_name_that_is_above_64_kb_is_shown_and_the_call_that_is_not_done_is_said_so(self):
        self.cpu.mem[0x301000:0x301006] = b"A.DAT\0"
        with self.assertRaises(NotImplementedError):
            self.call(0x21, ax=0x3C00, dx=0x301000)
        first, second = self.lines()
        self.assertTrue(first.endswith(' "A.DAT"'), first)
        self.assertEqual(second, ARROW + "NOT IMPLEMENTED")

    def test_the_bytes_to_write_are_read_with_a_count_and_a_pointer_of_32_bits(self):
        self.cpu.mem[0x302000:0x302003] = b"abc"
        self.call(0x21, ax=0x4000, bx=9, cx=0x10003, dx=0x302000)  # a count of more than 64 KB, to a handle not open
        first, second = self.lines()
        self.assertIn(' "abc\\x00\\x00', first)
        self.assertTrue(first.endswith('..."'), first)
        self.assertIn("CF=1 EAX=00000006", second)  # error 6: the handle is not open; nothing was written

    def test_real_mode_is_shown_as_before(self):
        cpu = CPU()
        dos.install(cpu)
        out = io.StringIO()
        calllog.install(cpu, out)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = b"\xCD\x21"
        cpu.r[CPU.AX] = 0x3000
        cpu.step()
        self.assertRegex(out.getvalue().splitlines()[0], r"1000:0000  INT 21h AX=3000 BX=0000 ")


class TestFolding(test_dos.FileFunctionsTest):
    """A run of calls that are the same in every way is written once, and counted."""

    def setUp(self):
        super().setUp()
        self.out = io.StringIO()
        self.log = calllog.install(self.cpu, self.out)

    def lines(self):
        return self.out.getvalue().splitlines()

    def test_the_same_call_with_the_same_answer_is_written_once_and_the_rest_are_counted(self):
        for _ in range(5):
            self.call(0x0E00, dx=2)
        self.log.finish()
        lines = self.lines()
        self.assertEqual(len(lines), 3)
        self.assertIn("INT 21h AX=0E00", lines[0])
        self.assertEqual(lines[2], ARROW[:-3] + "... the same call 4 more times")

    def test_nothing_is_said_of_a_run_that_is_not_one(self):
        self.call(0x0E00, dx=2)
        self.log.finish()
        self.assertEqual(len(self.lines()), 2)
        self.assertNotIn("same call", self.out.getvalue())

    def test_a_different_call_ends_the_run_and_comes_after_the_count(self):
        for _ in range(3):
            self.call(0x0E00, dx=2)
        self.call(0x0E00, dx=3)
        lines = self.lines()
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[2], ARROW[:-3] + "... the same call 2 more times")
        self.assertIn("INT 21h AX=0E00 BX=0000 CX=0000 DX=0003", lines[3])  # its own two lines, whole
        self.assertTrue(lines[4].startswith(ARROW))

    def test_the_count_of_the_calls_is_that_of_the_first_of_a_run(self):
        for _ in range(3):
            self.call(0x0E00, dx=2)
        self.assertEqual(self.lines()[0].split()[0], "1")

    def test_the_same_question_with_another_answer_is_not_the_same_call(self):
        self.call(0x3D00, "NOTES.TXT")  # a handle
        self.call(0x3D00, "NOTES.TXT")  # the next handle: another answer
        self.log.finish()
        arrows = [line for line in self.lines() if "->" in line]
        self.assertEqual(len(arrows), 2)
        self.assertIn("AX=0005", arrows[0])
        self.assertIn("AX=0006", arrows[1])

    def test_another_question_with_the_same_answer_is_not_the_same_call(self):
        self.call(0x0E00, dx=2)
        self.call(0x0E00, dx=2, bx=1)
        self.log.finish()
        self.assertEqual(len([line for line in self.lines() if "INT" in line]), 2)

    def test_a_run_that_comes_again_later_is_counted_again(self):
        for dx in (2, 2, 2, 3, 2, 2):
            self.call(0x0E00, dx=dx)
        self.log.finish()
        counts = [line.strip() for line in self.lines() if "same call" in line]
        self.assertEqual(counts, ["... the same call 2 more times", "... the same call 1 more times"])

    def test_the_end_of_the_program_ends_a_run_and_is_written_after_its_count(self):
        for _ in range(3):
            self.call(0x0E00, dx=2)
        with self.assertRaises(Exit):
            self.call(0x4C00)
        lines = self.lines()
        self.assertEqual(lines[2], ARROW[:-3] + "... the same call 2 more times")
        self.assertIn("AX=4C00", lines[3])
        self.assertEqual(lines[4], ARROW + "exit 0")

    def test_the_first_line_of_a_call_is_there_before_it_is_made(self):
        cpu = CPU()
        dos.install(cpu)
        seen = []
        real = cpu.int_handlers[0x21]
        # what the log is put in front of: it looks at the log when it is called
        cpu.int_handlers[0x21] = lambda cpu: (seen.append(out.getvalue()), real(cpu))[1]
        out = io.StringIO()
        calllog.install(cpu, out)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        cpu.r[CPU.AX], cpu.r[CPU.DX] = 0x0E00, 2
        cpu.step()
        self.assertEqual(len(seen[0].splitlines()), 1)  # the question, and not yet the answer
        self.assertEqual(len(out.getvalue().splitlines()), 2)

    def test_a_log_in_a_real_file_is_folded_as_well(self):
        import tempfile
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/calls.log"
            with open(path, "w", buffering=1) as file:
                log = calllog.install(cpu, file)
                for _ in range(4):
                    cpu.r[CPU.AX], cpu.r[CPU.DX], cpu.ip = 0x0E00, 2, 0
                    cpu.step()
                cpu.r[CPU.AX], cpu.r[CPU.DX], cpu.ip = 0x0E00, 3, 0
                cpu.step()
                log.finish()
            with open(path) as file:
                lines = file.read().splitlines()
        self.assertEqual(len(lines), 5)
        self.assertIn("DX=0002", lines[0])
        self.assertEqual(lines[2].strip(), "... the same call 3 more times")
        self.assertIn("DX=0003", lines[3])

    def test_finish_twice_says_it_once(self):
        for _ in range(3):
            self.call(0x0E00, dx=2)
        self.log.finish()
        self.log.finish()
        self.assertEqual(self.out.getvalue().count("same call"), 1)

    def test_finish_with_nothing_pending_writes_nothing(self):
        self.log.finish()
        self.assertEqual(self.out.getvalue(), "")


class TestWhatAProgramIsAskedToDo(LoggedTest):
    """AH=4Bh shows the command line that is given to the program, and AH=29h the text it takes apart."""

    def exec(self, name, tail, al=0, at=0x0400):
        """Put a parameter block at ES:BX = 3000:at, with a command tail of `tail` (without its carriage return)."""
        cpu = self.cpu
        data = tail.encode("cp437")
        cpu.wb(0x3000, 0x0500, len(data))
        for i, byte in enumerate(data + b"\r"):
            cpu.wb(0x3000, 0x0501 + i, byte)
        for i, word in enumerate((0, 0x0500, 0x3000, 0, 0, 0, 0)):  # no environment of its own; the tail; no FCBs
            cpu.ww(0x3000, at + 2 * i, word)
        cpu.s[CPU.ES] = 0x3000
        self.call(0x4B00 | al, name, bx=at)

    def test_the_line_has_the_name_and_the_command_tail(self):
        self.exec("C:\\TOOL.COM", "/C md C:\\XCOMA\\tacexe")
        self.assertTrue(self.lines()[0].endswith(' "C:\\TOOL.COM" tail "/C md C:\\XCOMA\\tacexe"'), self.lines()[0])
        self.assertIn("CF=1 AX=0002", self.lines()[1])  # there is no such program, and DOS says so

    def test_an_empty_tail_is_shown_as_empty(self):
        self.exec("C:\\TOOL.COM", "")
        self.assertTrue(self.lines()[0].endswith(' tail ""'))

    def test_a_long_tail_is_cut_and_the_carriage_return_is_not_in_it(self):
        self.exec("C:\\TOOL.COM", "x" * 120)
        self.assertTrue(self.lines()[0].endswith(' tail "' + "x" * calllog.MAX_TEXT + '..."'))
        self.exec("C:\\TOOL.COM", "abc")
        self.assertTrue(self.lines()[2].endswith(' tail "abc"'))

    def test_the_other_ways_to_exec_have_no_tail_shown(self):
        for al in (3, 5):
            self.out.truncate(0)
            self.out.seek(0)
            try:
                self.exec("C:\\TOOL.COM", "abc", al=al)
            except NotImplementedError:
                pass
            self.assertNotIn("tail", self.lines()[0], al)

    def test_the_tail_of_a_parameter_block_that_goes_round_the_end_of_a_segment_is_read_whole(self):
        self.exec("C:\\TOOL.COM", "wrapped", at=0xFFF6)
        self.assertTrue(self.lines()[0].endswith(' tail "wrapped"'), self.lines()[0])

    def test_the_pointer_to_the_tail_may_go_round_the_end_of_the_segment_too(self):
        cpu = self.cpu
        for i, byte in enumerate(bytes([5]) + b"wrapp"):
            cpu.wb(0x3000, 0x0500 + i, byte)
        words = (0, 0x0500, 0x3000, 0, 0, 0, 0)
        for i, word in enumerate(words):  # the block begins 2 bytes before the end of the segment
            cpu.wb(0x3000, (0xFFFE + 2 * i) & 0xFFFF, word & 0xFF)
            cpu.wb(0x3000, (0xFFFE + 2 * i + 1) & 0xFFFF, word >> 8)
        cpu.s[CPU.ES] = 0x3000
        self.call(0x4B00, "C:\\TOOL.COM", bx=0xFFFE)
        self.assertTrue(self.lines()[0].endswith(' tail "wrapp"'), self.lines()[0])

    def test_ah_29h_shows_the_text_it_takes_apart_up_to_the_end_of_the_line(self):
        for i, byte in enumerate(b"d:xcom.cfg /x\rmore"):
            self.cpu.wb(0x2000, 0x0300 + i, byte)
        self.cpu.r[CPU.SI], self.cpu.r[CPU.DI], self.cpu.s[CPU.ES] = 0x0300, 0x0600, 0x3000
        self.call(0x2901)
        self.assertTrue(self.lines()[0].endswith(' "d:xcom.cfg /x"'), self.lines()[0])

    def test_ah_29h_text_may_end_with_a_zero_too(self):
        for i, byte in enumerate(b"abc\0def"):
            self.cpu.wb(0x2000, 0x0300 + i, byte)
        self.cpu.r[CPU.SI], self.cpu.s[CPU.ES] = 0x0300, 0x3000
        self.call(0x2900)
        self.assertTrue(self.lines()[0].endswith(' "abc"'))


class TestWhatStopsTheProgram(LoggedTest):
    def test_a_call_that_is_not_done_is_logged_and_still_stops_the_emulator(self):
        with self.assertRaises(NotImplementedError):
            self.call(0xFF00)
        self.assertEqual(len(self.lines()), 2)
        self.assertIn("INT 21h AX=FF00", self.lines()[0])
        self.assertEqual(self.lines()[1], ARROW + "NOT IMPLEMENTED")

    def test_the_end_of_the_program_is_logged_with_its_code(self):
        with self.assertRaises(Exit):
            self.call(0x4C07)
        self.assertEqual(self.lines()[1], ARROW + "exit 7")

    def test_int_20h_is_logged_as_an_exit_with_code_0(self):
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x20])
        with self.assertRaises(Exit):
            self.call(0x0000)
        self.assertIn("INT 20h", self.lines()[0])
        self.assertEqual(self.lines()[1], ARROW + "exit 0")


class TestWhichInterrupts(unittest.TestCase):
    def machine(self, interrupts=None):
        cpu = CPU()
        bios.install(cpu)
        dos.install(cpu)
        self.out = io.StringIO()
        if interrupts is None:
            calllog.install(cpu, self.out)
        else:
            calllog.install(cpu, self.out, interrupts)
        cpu.s[CPU.CS], cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x1000, 0x4000, 0x0200
        return cpu

    def run_int(self, cpu, number, ax):
        cpu.mem[0x10000:0x10002] = bytes([0xCD, number])
        cpu.r[CPU.AX] = ax
        cpu.ip = 0
        cpu.step()

    def test_the_interrupts_of_dos_are_logged_and_those_of_the_bios_are_not(self):
        cpu = self.machine()
        self.run_int(cpu, 0x21, 0x3000)
        self.run_int(cpu, 0x2F, 0x1600)
        self.run_int(cpu, 0x25, 0x0002)
        self.run_int(cpu, 0x10, 0x0F00)
        self.run_int(cpu, 0x11, 0x0000)
        self.run_int(cpu, 0x1A, 0x0000)
        shown = [line.split("INT ")[1][:3] for line in self.out.getvalue().splitlines() if "INT" in line]
        self.assertEqual(shown, ["21h", "2Fh", "25h"])
        self.assertEqual(cpu.get8(4), 0x00)  # and the BIOS call did what it does

    def test_the_interrupts_can_be_chosen(self):
        cpu = self.machine(interrupts=(0x2F,))
        self.run_int(cpu, 0x21, 0x3000)
        self.assertEqual(self.out.getvalue(), "")
        self.run_int(cpu, 0x2F, 0x1600)
        self.assertIn("INT 2Fh AX=1600", self.out.getvalue())

    def test_an_interrupt_that_has_no_handler_is_passed_over(self):
        cpu = self.machine(interrupts=(0x99, 0x21))
        self.run_int(cpu, 0x21, 0x3000)
        self.assertIn("INT 21h", self.out.getvalue())
        self.assertNotIn(0x99, cpu.int_handlers)

    def test_a_program_that_sets_its_own_vector_is_not_logged_for_it(self):
        cpu = self.machine()
        cpu.ww(0, 0x21 * 4, 0x0500)  # INT 21h goes to 1000:0500 now
        cpu.ww(0, 0x21 * 4 + 2, 0x1000)
        cpu.mem[0x10500] = 0xCF  # iret
        self.run_int(cpu, 0x21, 0x3000)
        self.assertEqual(self.out.getvalue(), "")

    def test_the_answer_of_a_call_is_the_same_with_or_without_the_log(self):
        logged = self.machine()
        plain = CPU()
        bios.install(plain)
        dos.install(plain)
        plain.s[CPU.CS], plain.s[CPU.SS], plain.r[CPU.SP] = 0x1000, 0x4000, 0x0200
        for cpu in (logged, plain):
            self.run_int(cpu, 0x21, 0x3000)
        self.assertEqual((logged.r, logged.flags), (plain.r, plain.flags))
        self.assertNotEqual(self.out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
