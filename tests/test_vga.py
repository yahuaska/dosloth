import unittest

from mydos import vga
from mydos.cpu import CPU
from mydos.vga import Vga


def machine():
    cpu = CPU()
    vga.install(cpu)
    return cpu


class TestIndexedRegisters(unittest.TestCase):
    """The sequencer, the graphics controller and the CRT controller: an index port and a data port each."""

    PAIRS = ((0x3C4, 0x3C5), (0x3CE, 0x3CF), (0x3D4, 0x3D5))

    def test_a_register_gives_back_what_was_written(self):
        for index_port, data_port in self.PAIRS:
            chip = Vga()
            chip.write(index_port, 5)
            chip.write(data_port, 0xA5)
            self.assertEqual(chip.read(data_port), 0xA5, hex(data_port))

    def test_registers_are_told_apart_by_their_index(self):
        for index_port, data_port in self.PAIRS:
            chip = Vga()
            for index in (0, 1, 5):
                chip.write(index_port, index)
                chip.write(data_port, 0x10 + index)
            for index in (0, 1, 5):
                chip.write(index_port, index)
                self.assertEqual(chip.read(data_port), 0x10 + index, f"{data_port:X} register {index}")

    def test_the_three_pairs_do_not_share_registers(self):
        chip = Vga()
        for number, (index_port, data_port) in enumerate(self.PAIRS):
            chip.write(index_port, 3)
            chip.write(data_port, 0x40 + number)
        for number, (index_port, data_port) in enumerate(self.PAIRS):
            chip.write(index_port, 3)
            self.assertEqual(chip.read(data_port), 0x40 + number)

    def test_a_register_never_written_reads_0_and_a_data_write_does_not_change_the_index(self):
        chip = Vga()
        chip.write(0x3CE, 7)
        self.assertEqual(chip.read(0x3CF), 0)
        chip.write(0x3CF, 9)
        self.assertEqual(chip.read(0x3CE), 7)  # the index port still says which register it is

    def test_a_word_out_sets_the_index_from_al_and_the_register_from_ah(self):
        cpu = machine()
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000] = 0xEF  # out dx, ax
        cpu.r[CPU.DX], cpu.r[CPU.AX] = 0x3CE, 0x0305  # register 5 gets 03h
        cpu.step()
        cpu.port_out(0x3CE, 5)
        self.assertEqual(cpu.port_in(0x3CF), 0x03)


class TestOtherPorts(unittest.TestCase):
    def test_a_port_gives_back_the_last_byte_written(self):
        chip = Vga()
        for port in (0x3C0, 0x3C2, 0x3C6, 0x3C8, 0x3C9):
            chip.write(port, 0x11)
            chip.write(port, 0x22)
            self.assertEqual(chip.read(port), 0x22, hex(port))

    def test_a_port_never_written_reads_0(self):
        chip = Vga()
        self.assertEqual([chip.read(port) for port in (0x3C0, 0x3C9, 0x3CC)], [0, 0, 0])

    def test_the_ports_are_kept_apart(self):
        chip = Vga()
        chip.write(0x3C8, 1)
        chip.write(0x3C9, 2)
        self.assertEqual((chip.read(0x3C8), chip.read(0x3C9)), (1, 2))

    def test_an_index_write_is_not_taken_for_a_port_write_of_the_same_value(self):
        chip = Vga()
        chip.write(0x3C4, 2)
        self.assertEqual(chip.read(0x3C5), 0)


class TestStatus(unittest.TestCase):
    def test_both_bits_a_program_waits_for_change_on_every_read(self):
        chip = Vga()
        seen = [chip.read(0x3DA) for _ in range(6)]
        self.assertEqual(seen, [0x09, 0x00, 0x09, 0x00, 0x09, 0x00])
        for before, after in zip(seen, seen[1:]):
            self.assertEqual((before ^ after) & 0x09, 0x09)

    def test_a_wait_for_the_bit_to_be_set_and_then_clear_comes_to_an_end(self):
        chip = Vga()
        reads = 0
        while not chip.read(0x3DA) & 0x08:
            reads += 1
        while chip.read(0x3DA) & 0x08:
            reads += 1
        self.assertLess(reads, 4)

    def test_writing_the_port_does_not_change_what_it_reads(self):
        chip = Vga()
        chip.read(0x3DA)
        chip.write(0x3DA, 0xFF)
        self.assertEqual(chip.read(0x3DA), 0x00)


class TestInstall(unittest.TestCase):
    def test_all_the_ports_from_3c0_to_3df_are_there_and_no_others(self):
        cpu = machine()
        for port in range(0x3C0, 0x3E0):
            cpu.port_out(port, 0)
            cpu.port_in(port)
        for port in (0x3BF, 0x3E0, 0x3B4, 0x3BA):
            with self.assertRaises(NotImplementedError):
                cpu.port_in(port)
            with self.assertRaises(NotImplementedError):
                cpu.port_out(port, 0)

    def test_the_cpu_reads_and_writes_through_the_card(self):
        cpu = machine()
        cpu.port_out(0x3C4, 2)
        cpu.port_out(0x3C5, 0x0F)
        self.assertEqual(cpu.port_in(0x3C5), 0x0F)

    def test_the_video_memory_is_plain_memory(self):
        cpu = machine()
        cpu.wb(0xA000, 0x1234, 0x5A)
        self.assertEqual(cpu.rb(0xA000, 0x1234), 0x5A)

    def test_each_cpu_has_its_own_card(self):
        first, second = machine(), machine()
        first.port_out(0x3C8, 7)
        self.assertEqual(second.port_in(0x3C8), 0)


if __name__ == "__main__":
    unittest.main()
