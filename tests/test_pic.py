import json
import unittest

from mydos import flags, pic
from mydos.cpu import CPU
from mydos.flat import RealModeBases


class PicTest(unittest.TestCase):
    def setUp(self):
        self.cpu = CPU()
        self.pics = pic.install(self.cpu)
        self.master, self.slave = self.pics

    def out(self, port, value):
        self.cpu.port_out(port, value)

    def get(self, port):
        return self.cpu.port_in(port)


class TestTheMasks(PicTest):
    def test_the_mask_begins_as_dos_leaves_it_and_keeps_what_is_written(self):
        self.assertEqual((self.get(0x21), self.get(0xA1)), (0xB8, 0xFF))
        self.out(0x21, 0)
        self.out(0xA1, 0x55)
        self.assertEqual((self.get(0x21), self.get(0xA1)), (0, 0x55))

    def test_an_initialization_takes_its_bytes_and_not_as_a_mask(self):
        self.out(0x20, 0x11)  # ICW1 with ICW4
        for byte in (0x08, 0x04, 0x01):  # the vector base, the wiring, the mode
            self.out(0x21, byte)
        self.assertEqual(self.get(0x21), 0)  # the mask is cleared by it
        self.out(0x21, 0xFB)
        self.assertEqual(self.get(0x21), 0xFB)

    def test_an_initialization_without_icw4_has_two_bytes_and_the_first_is_the_vector_base(self):
        self.out(0xA0, 0x10)
        self.out(0xA1, 0x58)
        self.out(0xA1, 0x02)
        self.out(0xA1, 0x0F)
        self.assertEqual((self.get(0xA1), self.slave.base), (0x0F, 0x58))
        self.assertEqual(self.pics.vector(9), 0x59)

    def test_the_vectors_are_those_of_the_bios(self):
        self.assertEqual([self.pics.vector(line) for line in (0, 1, 7, 8, 15)], [8, 9, 0x0F, 0x70, 0x77])

    def test_the_state_is_numbers(self):
        json.dumps(self.master.state())


class TestTheLines(PicTest):
    def test_a_line_that_is_open_comes_and_goes_in_service(self):
        self.pics.raise_line(1)
        self.assertEqual(self.pics.pending(), 1)
        self.assertEqual(self.pics.acknowledge(), 1)
        self.assertIsNone(self.pics.pending())
        self.assertEqual((self.master.requests, self.master.in_service), (0, 2))

    def test_a_masked_line_waits_until_it_is_let_through(self):
        self.pics.raise_line(5)
        self.assertIsNone(self.pics.pending())
        self.out(0x21, 0)
        self.assertEqual(self.pics.pending(), 5)

    def test_a_line_in_service_stops_those_of_its_priority_or_lower_until_the_eoi(self):
        self.out(0x21, 0)
        self.pics.raise_line(3)
        self.pics.acknowledge()
        self.pics.raise_line(3)
        self.pics.raise_line(5)
        self.assertIsNone(self.pics.pending())  # the same line, and a lower one
        self.pics.raise_line(1)
        self.assertEqual(self.pics.pending(), 1)  # a higher one comes through
        self.pics.acknowledge()
        self.out(0x20, 0x20)  # the EOI takes the first of the lines in service, the line 1
        self.assertIsNone(self.pics.pending())
        self.out(0x20, 0x63)  # the EOI of the line 3, by name
        self.assertEqual(self.pics.pending(), 3)

    def test_an_eoi_with_nothing_in_service_does_nothing(self):
        self.out(0x20, 0x20)
        self.assertEqual(self.master.in_service, 0)

    def test_the_command_port_reads_the_lines_asked_for_and_those_in_service_as_ocw3_says(self):
        self.out(0x21, 0)
        self.pics.raise_line(4)
        self.assertEqual(self.get(0x20), 0x10)
        self.pics.acknowledge()
        self.assertEqual(self.get(0x20), 0)
        self.out(0x20, 0x0B)  # OCW3: read the lines in service
        self.assertEqual(self.get(0x20), 0x10)
        self.out(0x20, 0x0A)  # and back to the lines asked for
        self.assertEqual(self.get(0x20), 0)

    def test_a_request_that_is_taken_back_is_gone(self):
        self.pics.raise_line(0)
        self.pics.lower_line(0)
        self.assertIsNone(self.pics.pending())


class TestTheSlave(PicTest):
    def test_a_line_of_the_slave_comes_through_line_2_of_the_master(self):
        self.out(0xA1, 0)
        self.pics.raise_line(12)
        self.assertEqual(self.pics.pending(), 12)
        self.assertEqual(self.pics.acknowledge(), 12)
        self.assertEqual((self.slave.in_service, self.master.in_service), (0x10, 0x04))

    def test_with_line_2_masked_in_the_master_nothing_of_the_slave_comes(self):
        self.out(0xA1, 0)
        self.out(0x21, self.get(0x21) | 4)
        self.pics.raise_line(8)
        self.assertIsNone(self.pics.pending())

    def test_the_end_of_a_line_of_the_slave_is_said_to_both(self):
        self.out(0xA1, 0)
        self.pics.raise_line(9)
        self.pics.acknowledge()
        self.pics.end(9)
        self.assertEqual((self.slave.in_service, self.master.in_service), (0, 0))

    def test_a_line_of_the_master_that_is_higher_than_line_2_goes_first(self):
        self.out(0xA1, 0)
        self.out(0x21, 0)
        self.pics.raise_line(8)
        self.pics.raise_line(1)
        self.assertEqual(self.pics.acknowledge(), 1)

    def test_line_2_of_the_master_asked_by_nobody_is_nothing(self):
        self.out(0x21, 0)
        self.pics.raise_line(2)
        self.assertIsNone(self.pics.pending())


CODE = 0x1000
HANDLER = 0x2000


class TestTheCpuTakesThem(PicTest):
    """The CPU between two instructions: NOPs in the program, a handler that ends with IRET at HANDLER:0."""

    def setUp(self):
        super().setUp()
        cpu = self.cpu
        cpu.s[CPU.CS] = CODE
        cpu.s[CPU.SS] = 0x3000
        cpu.r[CPU.SP] = 0x1000
        cpu.ip = 0
        cpu.mem[CODE * 16:CODE * 16 + 64] = b"\x90" * 64
        cpu.mem[HANDLER * 16:HANDLER * 16 + 2] = b"\x90\xCF"  # NOP, IRET
        cpu.flags |= flags.IF
        self.out(0x21, 0)

    def hook(self, number):
        self.cpu.mem[number * 4:number * 4 + 4] = (HANDLER << 16).to_bytes(4, "little")  # HANDLER:0000

    def test_a_line_with_a_handler_in_the_table_calls_it_and_the_iret_comes_back(self):
        cpu = self.cpu
        self.hook(0x09)
        cpu.step()  # NOP
        self.pics.raise_line(1)
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (HANDLER, 1))  # the NOP of the handler is done
        self.assertFalse(cpu.flags & flags.IF)
        cpu.step()  # IRET
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (CODE, 1))
        self.assertTrue(cpu.flags & flags.IF)

    def test_no_second_interrupt_before_the_eoi(self):
        cpu = self.cpu
        self.hook(0x09)
        self.pics.raise_line(1)
        cpu.step()
        cpu.step()  # IRET
        self.pics.raise_line(1)
        cpu.step()
        self.assertEqual(cpu.s[CPU.CS], CODE)
        self.out(0x20, 0x20)
        cpu.step()
        self.assertEqual(cpu.s[CPU.CS], HANDLER)

    def test_with_the_interrupt_flag_clear_it_waits_for_sti(self):
        cpu = self.cpu
        self.hook(0x09)
        cpu.flags &= ~flags.IF
        self.pics.raise_line(1)
        for _ in range(3):
            cpu.step()
        self.assertEqual(cpu.s[CPU.CS], CODE)
        cpu.mem[CODE * 16 + 3] = 0xFB  # STI
        cpu.step()
        cpu.step()  # the instruction after STI: the interrupt comes before it
        self.assertEqual(cpu.s[CPU.CS], HANDLER)

    def test_a_line_that_no_program_took_is_ended_by_the_machine_and_the_program_goes_on(self):
        cpu = self.cpu
        self.pics.raise_line(1)
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (CODE, 1))
        self.assertEqual(self.master.in_service, 0)

    def test_a_python_handler_is_called_and_the_line_ended(self):
        cpu = self.cpu
        calls = []
        cpu.int_handlers[0x09] = calls.append
        self.pics.raise_line(1)
        cpu.step()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.master.in_service, 0)

    def test_the_timer_line_without_a_handler_goes_to_the_user_tick_1ch_if_it_has_one(self):
        cpu = self.cpu
        self.hook(0x1C)
        self.pics.raise_line(0)
        cpu.step()
        self.assertEqual(cpu.s[CPU.CS], HANDLER)
        self.assertEqual(self.master.in_service, 0)  # the interrupt 8 was the BIOS's, and it was ended

    def test_the_timer_line_with_a_handler_of_the_program_leaves_1ch_alone(self):
        cpu = self.cpu
        self.hook(0x08)
        cpu.mem[0x1C * 4:0x1C * 4 + 4] = (0x2100 << 16).to_bytes(4, "little")  # a handler of 1Ch elsewhere
        self.pics.raise_line(0)
        cpu.step()
        self.assertEqual(cpu.s[CPU.CS], HANDLER)
        self.assertEqual(self.master.in_service, 1)  # the program ends it

    def test_in_the_flat_mode_the_handlers_are_those_that_the_program_set_and_the_frame_is_of_doublewords(self):
        cpu = self.cpu
        cpu.use_flat_memory(RealModeBases())
        cpu.pm_vectors[0x09] = (HANDLER, 0)
        cpu.r[CPU.SP] = 0x1000
        self.pics.raise_line(1)
        cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip, cpu.r[CPU.SP]), (HANDLER, 1, 0x1000 - 12))
        self.assertEqual((cpu.rd(0x3000, 0x1000 - 4), cpu.rd(0x3000, 0x1000 - 8)), (flags.IF | 2, CODE))  # eflags, CS
        self.assertFalse(cpu.flags & flags.IF)

    def test_in_the_flat_mode_the_table_at_address_0_is_not_looked_at(self):
        cpu = self.cpu
        self.hook(0x09)
        cpu.use_flat_memory(RealModeBases())
        self.pics.raise_line(1)
        cpu.step()
        self.assertEqual(cpu.s[CPU.CS], CODE)  # taken as one with no handler: ended by the machine
        self.assertEqual(self.master.in_service, 0)

    def test_a_machine_without_controllers_takes_no_interrupt(self):
        cpu = CPU()
        cpu.mem[0:2] = b"\x90\x90"
        cpu.step()
        cpu.step()
        self.assertEqual(cpu.next_event, 1 << 62)

    def test_an_event_source_raises_a_line_at_its_time(self):
        cpu = self.cpu
        seen = []

        def source(now):
            seen.append(now)
            if now >= 5:
                self.pics.raise_line(1)
                return None
            return 5

        cpu.event_sources.append(source)
        cpu.wake()
        self.hook(0x09)
        for _ in range(6):
            cpu.step()
        self.assertEqual(cpu.s[CPU.CS], HANDLER)
        self.assertEqual(seen[0], 0)  # looked at when woken, and at its time


if __name__ == "__main__":
    unittest.main()
