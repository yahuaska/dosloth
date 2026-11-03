import json
import unittest

from mydos import dma
from mydos.cpu import CPU


class DmaTest(unittest.TestCase):
    def setUp(self):
        self.cpu = CPU()
        self.dma = dma.install(self.cpu)

    def out(self, port, value):
        self.cpu.port_out(port, value)

    def get(self, port):
        return self.cpu.port_in(port)


class TestTheRegisters(DmaTest):
    def test_an_address_and_a_count_are_written_a_byte_at_a_time_low_first(self):
        self.out(0x0C, 0)  # the flip-flop
        self.out(0x02, 0x34)
        self.out(0x02, 0x12)
        self.out(0x03, 0x0F)
        self.out(0x03, 0x00)
        self.out(0x83, 0x05)  # the page of the channel 1
        self.assertEqual((self.dma.address(1), self.dma.count(1)), (0x051234, 0x10))

    def test_the_flip_flop_is_shared_by_all_registers_and_cleared_by_port_0c(self):
        self.out(0x00, 0x11)  # the low byte of the address of the channel 0
        self.out(0x01, 0x22)  # goes on with the high byte of the count: the flip-flop is one
        self.assertEqual((self.dma.addresses[0], self.dma.counts[0]), (0x0011, 0x2200))
        self.out(0x0C, 0)
        self.out(0x00, 0x33)
        self.assertEqual(self.dma.addresses[0], 0x0033)

    def test_what_is_read_is_what_was_written(self):
        self.out(0x0C, 0)
        self.out(0x04, 0xCD)
        self.out(0x04, 0xAB)
        self.out(0x0C, 0)
        self.assertEqual((self.get(0x04), self.get(0x04)), (0xCD, 0xAB))

    def test_the_pages_are_of_the_channels_they_belong_to(self):
        for port, channel in ((0x87, 0), (0x83, 1), (0x81, 2), (0x82, 3)):
            self.out(port, channel + 10)
            self.assertEqual(self.dma.pages[channel], channel + 10)
            self.assertEqual(self.get(port), channel + 10)

    def test_every_channel_is_masked_at_the_start_and_the_masks_are_set_and_cleared(self):
        self.assertEqual(self.dma.masked, [True] * 4)
        self.out(0x0A, 0x01)  # unmask the channel 1
        self.assertEqual(self.dma.masked, [True, False, True, True])
        self.out(0x0A, 0x05)  # mask it again
        self.assertTrue(self.dma.masked[1])
        self.out(0x0E, 0)
        self.assertEqual(self.dma.masked, [False] * 4)
        self.out(0x0F, 0x09)
        self.assertEqual(self.dma.masked, [True, False, False, True])

    def test_the_mode_is_kept_for_the_channel_it_names(self):
        self.out(0x0B, 0x49)
        self.assertEqual(self.dma.modes, [0, 0x49, 0, 0])

    def test_the_status_shows_the_channels_at_their_end_and_a_read_clears_it(self):
        self.dma.finish(1)
        self.dma.finish(3)
        self.assertEqual(self.get(0x08), 0x0A)
        self.assertEqual(self.get(0x08), 0)

    def test_the_command_is_written_to_the_same_port_as_the_status(self):
        self.out(0x08, 0x04)
        self.assertEqual(self.dma.command, 4)

    def test_a_master_clear_forgets_everything(self):
        self.out(0x83, 7)
        self.out(0x0E, 0)
        self.out(0x0D, 0)
        self.assertEqual((self.dma.pages[1], self.dma.masked), (0, [True] * 4))

    def test_the_state_is_numbers_and_lists(self):
        json.dumps(vars(self.dma))

    def test_the_other_ports_of_the_controller_are_unknown(self):
        with self.assertRaises(NotImplementedError):
            self.get(0xC0)


if __name__ == "__main__":
    unittest.main()
