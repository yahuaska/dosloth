"""The commands of the debugger: a step, a step over, going on; and the breakpoints that it keeps."""
import unittest

from mydos.cpu import CPU
from mydos.debugger import BREAKPOINT, EXIT, PAUSE, STEP, Debugger, Pause
from tests.test_breakpoints import machine

# 0000 call 0008 / 0003 mov ax,4C07h / 0006 int 21h / 0008 nop / 0009 nop / 000A ret
CALLS = bytes.fromhex("e8 05 00 b8 07 4c cd 21 90 90 c3")


class TestBreakpointList(unittest.TestCase):
    def test_add_remove_and_list(self):
        debugger = Debugger(machine())
        debugger.add_breakpoint(0x1000, 5)
        debugger.add_breakpoint(None, 2)
        debugger.add_breakpoint(0x2000, 5)
        debugger.add_breakpoint(0x1000, 5)  # the same again changes nothing
        self.assertEqual(debugger.breakpoints(), [(None, 2), (0x1000, 5), (0x2000, 5)])
        self.assertTrue(debugger.remove_breakpoint(0x1000, 5))
        self.assertFalse(debugger.remove_breakpoint(0x1000, 5))
        self.assertFalse(debugger.remove_breakpoint(None, 5))  # a breakpoint at a segment is not one at any
        self.assertFalse(debugger.remove_breakpoint(0x3000, 5))  # nor at another segment
        self.assertEqual(debugger.breakpoints(), [(None, 2), (0x2000, 5)])
        self.assertTrue(debugger.remove_breakpoint(0x2000, 5))
        self.assertTrue(debugger.remove_breakpoint(None, 2))
        self.assertEqual(debugger.breakpoints(), [])
        self.assertEqual(debugger.cpu.breakpoints, {})

    def test_the_breakpoints_the_machine_has_are_listed(self):
        cpu = machine()
        cpu.add_breakpoint(None, 3)
        self.assertEqual(Debugger(cpu).breakpoints(), [(None, 3)])

    def test_a_removed_breakpoint_does_not_stop(self):
        debugger = Debugger(machine())
        debugger.add_breakpoint(None, 1)
        debugger.remove_breakpoint(None, 1)
        self.assertEqual(debugger.go(), type(debugger.exit)(EXIT, 7))


class TestStep(unittest.TestCase):
    def test_one_instruction_at_a_time(self):
        cpu = machine()
        debugger = Debugger(cpu)
        self.assertEqual(debugger.step().reason, STEP)
        self.assertEqual((cpu.ip, cpu.instructions), (1, 1))
        debugger.step()
        self.assertEqual(cpu.ip, 2)

    def test_the_last_step_is_the_exit_and_it_stays(self):
        cpu = machine()
        debugger = Debugger(cpu)
        for _ in range(4):
            self.assertEqual(debugger.step().reason, STEP)
        stop = debugger.step()  # int 21h ends the program
        self.assertEqual((stop.reason, stop.code), (EXIT, 7))
        count = cpu.instructions
        self.assertIs(debugger.step(), stop)
        self.assertIs(debugger.go(), stop)
        self.assertIs(debugger.step_over(), stop)
        self.assertEqual(cpu.instructions, count)

    def test_a_step_does_not_mind_a_breakpoint(self):
        cpu = machine()
        debugger = Debugger(cpu)
        debugger.add_breakpoint(None, 1)
        self.assertEqual(debugger.step().reason, STEP)  # now at the breakpoint
        self.assertEqual(debugger.step().reason, STEP)  # and out of it
        self.assertEqual(cpu.ip, 2)


class TestStepOver(unittest.TestCase):
    def test_what_is_not_a_call_is_a_step(self):
        cpu = machine()
        self.assertEqual(Debugger(cpu).step_over().reason, STEP)
        self.assertEqual((cpu.ip, cpu.instructions), (1, 1))

    def test_a_call_runs_to_the_instruction_after_it(self):
        cpu = machine(CALLS)
        debugger = Debugger(cpu)
        self.assertEqual(debugger.step_over().reason, STEP)
        self.assertEqual(cpu.ip, 3)
        self.assertEqual(cpu.instructions, 4)  # call, nop, nop, ret
        self.assertEqual(cpu.wide(CPU.SP), 0xFFFE)

    def test_a_call_may_be_into_it_by_step(self):
        cpu = machine(CALLS)
        Debugger(cpu).step()
        self.assertEqual(cpu.ip, 8)

    def test_a_breakpoint_inside_stops_the_step_over(self):
        cpu = machine(CALLS)
        debugger = Debugger(cpu)
        debugger.add_breakpoint(None, 9)
        self.assertEqual(debugger.step_over().reason, BREAKPOINT)
        self.assertEqual(cpu.ip, 9)
        self.assertEqual(debugger.step_over().reason, STEP)  # goes on from it
        self.assertEqual(cpu.ip, 10)

    def test_an_int_is_stepped_over_too(self):
        cpu = machine(bytes.fromhex("cd 60 90 b8 07 4c cd 21"))
        cpu.mem[0x180:0x184] = bytes.fromhex("20 00 00 10")  # the handler is code, 1000:0020: nop, nop, iret
        cpu.mem[0x10020:0x10023] = bytes.fromhex("90 90 cf")
        debugger = Debugger(cpu)
        self.assertEqual(debugger.step_over().reason, STEP)
        self.assertEqual((cpu.ip, cpu.instructions), (2, 4))

    def test_a_procedure_that_throws_the_return_address_away_is_not_waited_for(self):
        # 0000 call 0008 / 0003 mov ax,4C07h / 0006 int 21h / 0008 pop ax / 0009 jmp 0006: ends with AL = 3
        cpu = machine(bytes.fromhex("e8 05 00 b8 07 4c cd 21 58 eb fb"))
        stop = Debugger(cpu).step_over()
        self.assertEqual((stop.reason, stop.code), (EXIT, 3))

    def test_the_end_of_the_program_inside_a_call(self):
        cpu = machine(bytes.fromhex("e8 00 00 b8 07 4c cd 21"))  # call 0003: the program ends there
        stop = Debugger(cpu).step_over()
        self.assertEqual((stop.reason, stop.code), (EXIT, 7))

    def test_a_procedure_that_calls_itself_ends_at_the_outer_return(self):
        # 0000 call 0004 / 0003 nop / 0004 dec cx / 0005 jz 000B / 0007 call 0004 / 000A ret / 000B ret
        code = bytes.fromhex("e8 01 00 90 49 74 04 e8 fa ff c3 c3")
        cpu = machine(code)
        cpu.r[CPU.CX] = 3
        debugger = Debugger(cpu)
        stop = debugger.step_over()
        self.assertEqual((stop.reason, cpu.ip, cpu.r[CPU.CX]), (STEP, 3, 0))


class TestGo(unittest.TestCase):
    def test_to_the_end(self):
        stop = Debugger(machine()).go()
        self.assertEqual((stop.reason, stop.code), (EXIT, 7))

    def test_to_a_breakpoint_and_on_to_the_end(self):
        cpu = machine()
        debugger = Debugger(cpu)
        debugger.add_breakpoint(None, 2)
        self.assertEqual(debugger.go().reason, BREAKPOINT)
        self.assertEqual((cpu.ip, cpu.instructions), (2, 2))
        self.assertEqual(debugger.go().reason, EXIT)

    def test_a_pause_from_the_tick(self):
        cpu = machine()
        debugger = Debugger(cpu)

        def tick():
            raise Pause()
        self.assertEqual(debugger.go(2, tick).reason, PAUSE)
        self.assertEqual(cpu.instructions, 2)
        self.assertEqual(debugger.go().reason, EXIT)  # and it goes on from there

    def test_a_pause_while_stepping_over(self):
        cpu = machine(bytes.fromhex("e8 00 00 90 90 90 b8 07 4c cd 21"))
        debugger = Debugger(cpu)

        def tick():
            raise Pause()
        self.assertEqual(debugger.step_over(1, tick).reason, PAUSE)
        self.assertEqual(cpu.instructions, 1)


if __name__ == "__main__":
    unittest.main()
