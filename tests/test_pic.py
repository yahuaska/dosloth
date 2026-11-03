import unittest

from mydos import pic
from mydos.cpu import CPU


class PicTest(unittest.TestCase):
    def setUp(self):
        self.cpu = CPU()
        self.master, self.slave = pic.install(self.cpu)

    def test_the_mask_begins_as_dos_leaves_it_and_keeps_what_is_written(self):
        self.assertEqual((self.cpu.port_in(0x21), self.cpu.port_in(0xA1)), (0xB8, 0xFF))
        self.cpu.port_out(0x21, 0)
        self.cpu.port_out(0xA1, 0x55)
        self.assertEqual((self.cpu.port_in(0x21), self.cpu.port_in(0xA1)), (0, 0x55))

    def test_the_command_port_says_nothing_is_asked_and_takes_an_eoi(self):
        self.cpu.port_out(0x20, 0x20)
        self.cpu.port_out(0xA0, 0x20)
        self.assertEqual((self.cpu.port_in(0x20), self.cpu.port_in(0xA0)), (0, 0))
        self.assertEqual(self.master.mask, 0xB8)

    def test_an_initialization_takes_its_bytes_and_not_as_a_mask(self):
        self.cpu.port_out(0x20, 0x11)  # ICW1 with ICW4
        for byte in (0x08, 0x04, 0x01):  # the vector base, the wiring, the mode
            self.cpu.port_out(0x21, byte)
        self.assertEqual(self.cpu.port_in(0x21), 0)  # the mask is cleared by it
        self.cpu.port_out(0x21, 0xFB)
        self.assertEqual(self.cpu.port_in(0x21), 0xFB)

    def test_an_initialization_without_icw4_has_two_bytes(self):
        self.cpu.port_out(0xA0, 0x10)
        self.cpu.port_out(0xA1, 0x70)
        self.cpu.port_out(0xA1, 0x02)
        self.cpu.port_out(0xA1, 0x0F)
        self.assertEqual(self.cpu.port_in(0xA1), 0x0F)

    def test_the_state_is_numbers(self):
        import json
        json.dumps(vars(self.master))


if __name__ == "__main__":
    unittest.main()
