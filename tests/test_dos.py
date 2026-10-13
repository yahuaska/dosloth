import contextlib
import io
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


class TestWriteToHandle(unittest.TestCase):
    """INT 21h AH=40h: write CX bytes from DS:DX to the handle in BX."""

    def write(self, handle, data, dx=0x0100, ds=0x2000):
        """Run the call with `data` at DS:DX. Returns (cpu, what was printed, what went to the error output)."""
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS], cpu.s[CPU.DS] = 0x1000, ds
        cpu.mem[0x10000:0x10004] = bytes([0xB4, 0x40, 0xCD, 0x21])  # mov ah,40h ; int 21h
        for i, byte in enumerate(data):
            cpu.wb(ds, (dx + i) & 0xFFFF, byte)  # wraps at the end of the segment, as the call will
        cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = handle, len(data), dx
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cpu.step()
            cpu.step()
        return cpu, out.getvalue(), err.getvalue()

    def test_handle_1_is_the_screen(self):
        cpu, out, err = self.write(1, b"Hello\r\n")
        self.assertEqual((out, err), ("Hello\r\n", ""))
        self.assertEqual(cpu.r[CPU.AX], 7)  # how many bytes were written
        self.assertFalse(cpu.get_flag(flags.CF))

    def test_handle_2_goes_to_the_error_output(self):
        cpu, out, err = self.write(2, b"Abnormal program termination\r\n")
        self.assertEqual((out, err), ("", "Abnormal program termination\r\n"))
        self.assertEqual(cpu.r[CPU.AX], 30)

    def test_handle_0_is_the_console_too(self):
        cpu, out, err = self.write(0, b"x")
        self.assertEqual((out, err), ("x", ""))

    def test_nothing_to_write(self):
        cpu, out, err = self.write(1, b"")
        self.assertEqual((out, err, cpu.r[CPU.AX], cpu.get_flag(flags.CF)), ("", "", 0, False))

    def test_the_characters_are_the_ones_of_the_dos_code_page(self):
        cpu, out, err = self.write(1, bytes([0xB0, 0xDB, 0x82]))
        self.assertEqual(out, "\u2591\u2588\u00e9")  # light shade, full block, e acute

    def test_the_text_may_run_over_the_end_of_the_segment(self):
        cpu, out, err = self.write(1, b"WRAP", dx=0xFFFE)  # FFFE, FFFF, 0000, 0001
        self.assertEqual(out, "WRAP")

    def test_exactly_cx_bytes_are_written_zero_bytes_and_dollar_signs_included(self):
        """Unlike AH=09h, which stops at a dollar sign, this call writes as many bytes as it is told."""
        cpu, out, err = self.write(1, b"ab\0cd$ef")
        self.assertEqual((out, cpu.r[CPU.AX]), ("ab\0cd$ef", 8))

    def test_a_long_text_in_one_call(self):
        cpu, out, err = self.write(1, b"A" * 1000)
        self.assertEqual((len(out), cpu.r[CPU.AX]), (1000, 1000))

    def test_a_handle_that_is_not_open_is_error_6_and_prints_nothing(self):
        for handle in (3, 5, 19, 0xFFFF):
            cpu, out, err = self.write(handle, b"text")
            self.assertEqual((out, err), ("", ""), f"handle {handle}")
            self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (True, 6), f"handle {handle}")

    def test_other_registers_are_left_alone(self):
        cpu, out, err = self.write(1, b"abc")
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.s[CPU.DS]), (1, 3, 0x0100, 0x2000))


if __name__ == "__main__":
    unittest.main()
