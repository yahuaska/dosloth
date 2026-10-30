"""Breakpoints: the address as it is written, and what CPU.run does with it."""
import unittest

from mydos import breakpoints
from mydos.breakpoints import BreakpointHit
from mydos.cpu import CPU

# three nops, then mov ax,4C07h / int 21h: the program ends with 7 (the handler of INT 21h is set by the tests)
PROGRAM = bytes.fromhex("90 90 90 b8 07 4c cd 21")


def machine(code=PROGRAM):
    cpu = CPU()
    cpu.mem[0x10000:0x10000 + len(code)] = code
    cpu.s[CPU.CS] = cpu.s[CPU.DS] = cpu.s[CPU.SS] = 0x1000
    cpu.r[CPU.SP] = 0xFFFE

    def dos(cpu):
        from mydos.cpu import Exit
        raise Exit(cpu.get8(0))
    cpu.int_handlers[0x21] = dos
    return cpu


class TestParse(unittest.TestCase):
    def test_a_segment_and_an_offset_in_hex(self):
        self.assertEqual(breakpoints.parse("000F:0010E060"), (0x0F, 0x10E060))
        self.assertEqual(breakpoints.parse("1000:0102"), (0x1000, 0x102))
        self.assertEqual(breakpoints.parse("f:a"), (0xF, 0xA))  # the case and the zeros in front do not matter

    def test_the_offset_alone_is_any_segment(self):
        self.assertEqual(breakpoints.parse("10E060"), (None, 0x10E060))
        self.assertEqual(breakpoints.parse("0x10e060"), (None, 0x10E060))

    def test_the_largest_that_there_are(self):
        self.assertEqual(breakpoints.parse("FFFF:FFFFFFFF"), (0xFFFF, 0xFFFFFFFF))

    def test_what_is_not_an_address_says_so(self):
        for text in ("", ":", "x", "1000:", ":0100", "10:0G", "1000:01 00", "1000:0100:5"):
            with self.assertRaisesRegex(ValueError, "is not an address: it is written as SEGMENT:OFFSET"):
                breakpoints.parse(text)

    def test_too_much_is_said_so(self):
        with self.assertRaisesRegex(ValueError, "segment .* more than FFFF"):
            breakpoints.parse("10000:0")
        with self.assertRaisesRegex(ValueError, "offset .* more than FFFFFFFF"):
            breakpoints.parse("1000:100000000")
        for text in ("-1", "-1:0", "1000:-5"):
            with self.assertRaisesRegex(ValueError, "is not an address"):
                breakpoints.parse(text)


class TestStopping(unittest.TestCase):
    def test_a_program_with_no_breakpoint_runs_to_its_end(self):
        self.assertEqual(machine().run(), 7)

    def test_it_stops_before_the_instruction_at_the_breakpoint_and_says_where_and_when(self):
        cpu = machine()
        cpu.add_breakpoint(0x1000, 3)
        with self.assertRaises(BreakpointHit) as stop:
            cpu.run()
        self.assertEqual(str(stop.exception), "breakpoint at 1000:0003 (instruction 3)")
        self.assertEqual((cpu.ip, cpu.start_ip, cpu.instructions), (3, 3, 3))  # the 3 nops are done, the next is not
        self.assertEqual(cpu.r[CPU.AX], 0)

    def test_a_program_that_is_stopped_goes_on_from_there_and_is_not_stopped_again_at_once(self):
        cpu = machine()
        cpu.add_breakpoint(0x1000, 3)
        with self.assertRaises(BreakpointHit):
            cpu.run()
        self.assertEqual(cpu.run(), 7)  # the first instruction is let go: it is the one at the breakpoint

    def test_a_breakpoint_that_is_come_to_again_stops_again(self):
        cpu = machine(bytes.fromhex("90 eb fd"))  # nop / jmp -3 (to the nop): for ever
        cpu.add_breakpoint(0x1000, 0)
        counts = []
        for _ in range(3):
            with self.assertRaises(BreakpointHit):
                cpu.run()
            counts.append(cpu.instructions)
            self.assertEqual(cpu.ip, 0)
        self.assertEqual(counts, [2, 4, 6])  # let go from it, round the loop, and it is there again

    def test_only_the_segment_that_was_asked_for_stops_it(self):
        cpu = machine()
        cpu.add_breakpoint(0x2000, 3)
        self.assertEqual(cpu.run(), 7)

    def test_with_no_segment_any_one_stops_it(self):
        cpu = machine()
        cpu.add_breakpoint(None, 3)
        with self.assertRaises(BreakpointHit):
            cpu.run()
        cpu = machine()
        cpu.s[CPU.CS] = 0x0F00
        cpu.mem[0xF000:0xF000 + len(PROGRAM)] = PROGRAM
        cpu.add_breakpoint(None, 3)
        with self.assertRaises(BreakpointHit) as stop:
            cpu.run()
        self.assertIn("breakpoint at 0F00:0003", str(stop.exception))

    def test_a_breakpoint_at_any_segment_is_not_made_less_by_one_at_a_segment(self):
        cpu = machine()
        cpu.add_breakpoint(None, 3)
        cpu.add_breakpoint(0x2000, 3)
        with self.assertRaises(BreakpointHit):
            cpu.run()
        cpu = machine()
        cpu.add_breakpoint(0x2000, 3)
        cpu.add_breakpoint(None, 3)
        with self.assertRaises(BreakpointHit):
            cpu.run()

    def test_several_segments_at_one_offset_all_stop_it(self):
        for segment in (0x2000, 0x1000):
            cpu = machine()
            cpu.add_breakpoint(0x2000, 3)
            cpu.add_breakpoint(0x1000, 3)
            cpu.s[CPU.CS] = segment
            cpu.mem[segment * 16:segment * 16 + len(PROGRAM)] = PROGRAM
            with self.assertRaises(BreakpointHit):
                cpu.run()

    def test_the_first_of_several_breakpoints_that_the_program_comes_to_is_the_one_that_stops_it(self):
        cpu = machine()
        cpu.add_breakpoint(0x1000, 6)
        cpu.add_breakpoint(0x1000, 1)
        with self.assertRaises(BreakpointHit) as stop:
            cpu.run()
        self.assertEqual((cpu.ip, str(stop.exception)), (1, "breakpoint at 1000:0001 (instruction 1)"))

    def test_the_breakpoint_is_at_the_start_of_an_instruction_not_in_the_middle_of_one(self):
        cpu = machine()
        cpu.add_breakpoint(0x1000, 4)  # inside the mov ax,imm16 that begins at 3
        self.assertEqual(cpu.run(), 7)

    def test_a_32_bit_program_has_its_address_with_8_digits(self):
        from mydos.flat import Bases
        cpu = machine()
        cpu.enter_flat(Bases({0x0F: 0}))
        cpu.s[CPU.CS] = cpu.s[CPU.DS] = cpu.s[CPU.SS] = 0x0F
        cpu.mem[0x200000:0x200000 + 3] = bytes.fromhex("90 90 90")
        cpu.ip = 0x200000
        cpu.set32(CPU.SP, 0x300000)
        cpu.add_breakpoint(None, 0x200002)
        with self.assertRaises(BreakpointHit) as stop:
            cpu.run()
        self.assertEqual(str(stop.exception), "breakpoint at 000F:00200002 (instruction 2)")

    def test_the_tick_is_called_as_often_as_it_is_without_breakpoints(self):
        code = bytes([0x90] * 20) + bytes.fromhex("b8 07 4c cd 21")  # 20 nops, then the end: 23 instructions
        ticks = []
        cpu = machine(code)
        cpu.run(5, lambda: ticks.append(cpu.instructions))
        plain = list(ticks)
        ticks.clear()
        cpu = machine(code)
        cpu.add_breakpoint(0x1000, 0x7000)  # one that is never come to
        cpu.run(5, lambda: ticks.append(cpu.instructions))
        self.assertEqual((ticks, ticks == plain), ([5, 10, 15, 20], True))


if __name__ == "__main__":
    unittest.main()
