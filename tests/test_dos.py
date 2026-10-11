import unittest

from mydos import dos, flags
from mydos.cpu import CPU


def int21(ax, bx=0, dx=0x1111):
    """Run `int 21h` with these registers and give back the CPU afterwards."""
    cpu = CPU()
    dos.install(cpu)
    cpu.s[CPU.CS] = 0x1000
    cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
    cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.DX] = ax, bx, dx
    cpu.r[CPU.CX] = 0x2222
    cpu.step()
    return cpu


class TestIoctlGetDeviceInfo(unittest.TestCase):
    """INT 21h AH=44h AL=00: what kind of thing is this file handle?"""

    def test_the_standard_handles_are_the_console(self):
        for handle in (0, 1, 2):
            cpu = int21(0x4400, bx=handle)
            self.assertEqual(cpu.r[CPU.DX], 0x80D3, f"handle {handle}")
            self.assertTrue(cpu.r[CPU.DX] & 0x80)  # bit 7: a device, not a file
            self.assertFalse(cpu.get_flag(flags.CF), f"handle {handle}")

    def test_a_handle_that_is_not_open_is_error_6(self):
        for handle in (3, 5, 19, 0x8000, 0xFFFF):
            cpu = int21(0x4400, bx=handle)
            self.assertTrue(cpu.get_flag(flags.CF), f"handle {handle}")
            self.assertEqual(cpu.r[CPU.AX], 6, f"handle {handle}")
            self.assertEqual(cpu.r[CPU.DX], 0x1111)  # nothing was written to DX

    def test_other_registers_are_left_alone(self):
        cpu = int21(0x4400, bx=1)
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX]), (1, 0x2222))
        self.assertEqual(cpu.get8(4), 0x44)  # AH still says 44h

    def test_a_carry_set_before_is_cleared_on_success(self):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        cpu.r[CPU.AX], cpu.r[CPU.BX] = 0x4400, 1
        cpu.set_flag(flags.CF, True)
        cpu.step()
        self.assertFalse(cpu.get_flag(flags.CF))

    def test_the_other_subfunctions_stop_loudly(self):
        for al in (0x01, 0x02, 0x06, 0x07, 0x0D):
            with self.assertRaises(NotImplementedError, msg=f"AL={al:02X}"):
                int21(0x4400 | al, bx=1)


if __name__ == "__main__":
    unittest.main()
