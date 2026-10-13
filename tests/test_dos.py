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


class TestGetCurrentDrive(unittest.TestCase):
    """INT 21h AH=19h: which drive is the current one?"""

    def test_the_answer_is_drive_c(self):
        cpu = int21(0x1900)
        self.assertEqual(cpu.get8(0), 2)  # 0 is A:, 1 is B:, 2 is C:

    def test_it_agrees_with_the_drive_in_the_environment_the_program_is_given(self):
        from mydos import psp
        drive_letter = psp.environment("x.exe").split(b"=")[1][:1]  # the C of COMSPEC=C:\...
        self.assertEqual(chr(ord("A") + int21(0x1900).get8(0)).encode(), drive_letter)

    def test_other_registers_and_the_flags_are_left_alone(self):
        for carry in (False, True):
            cpu = CPU()
            dos.install(cpu)
            cpu.s[CPU.CS] = 0x1000
            cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
            cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x1900, 0x1111, 0x2222, 0x3333
            cpu.set_flag(flags.CF, carry)
            start_flags = cpu.flags
            cpu.step()
            registers = (cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX])
            self.assertEqual(registers, (0x1902, 0x1111, 0x2222, 0x3333))
            self.assertEqual(cpu.flags, start_flags)  # AH keeps its 19h: only AL is the answer


class TestSelectDrive(unittest.TestCase):
    """INT 21h AH=0Eh: make a drive the current one. AH=19h reads it back."""

    def cpu_with(self, code):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10000 + len(code)] = bytes(code)
        return cpu

    def select(self, cpu, drive):
        """Run `mov dl, drive ; mov ah, 0Eh ; int 21h` on this CPU and leave the answer in the CPU."""
        cpu.mem[0x10100:0x10108] = bytes([0xB2, drive, 0xB4, 0x0E, 0xCD, 0x21, 0x90, 0x90])
        cpu.ip = 0x0100
        for _ in range(3):
            cpu.step()

    def current(self, cpu):
        cpu.mem[0x10200:0x10204] = bytes([0xB4, 0x19, 0xCD, 0x21])
        cpu.ip = 0x0200
        cpu.step()
        cpu.step()
        return cpu.get8(0)

    def test_a_program_starts_on_drive_c(self):
        self.assertEqual(self.current(self.cpu_with([])), 2)

    def test_the_four_drives_can_each_be_selected_and_read_back(self):
        cpu = self.cpu_with([])
        for drive in (0, 1, 2, 3, 1, 3, 0):
            self.select(cpu, drive)
            self.assertEqual(self.current(cpu), drive, f"drive {drive}")

    def test_the_answer_is_how_many_drives_there_are(self):
        cpu = self.cpu_with([])
        for drive in (0, 3, 7, 255):
            self.select(cpu, drive)
            self.assertEqual(cpu.get8(0), 4, f"DL={drive}")

    def test_a_drive_that_does_not_exist_changes_nothing_and_is_not_an_error(self):
        cpu = self.cpu_with([])
        self.select(cpu, 1)
        for drive in (4, 5, 25, 26, 127, 255):
            self.select(cpu, drive)
            self.assertEqual(self.current(cpu), 1, f"DL={drive}")  # still B:
            self.assertFalse(cpu.get_flag(flags.CF))

    def test_the_way_programs_find_out_which_drives_exist(self):
        """As in esetup.exe: remember the drive, select each candidate, read back, compare, put the first one back."""
        cpu = self.cpu_with([])
        original = self.current(cpu)
        existing = []
        for candidate in range(26):
            self.select(cpu, candidate)
            if self.current(cpu) == candidate:
                existing.append(candidate)
        self.select(cpu, original)
        self.assertEqual(existing, [0, 1, 2, 3])
        self.assertEqual(self.current(cpu), original)

    def test_each_cpu_has_its_own_current_drive(self):
        first, second = self.cpu_with([]), self.cpu_with([])
        self.select(first, 0)
        self.assertEqual((self.current(first), self.current(second)), (0, 2))

    def test_dl_ah_and_the_flags_are_left_alone(self):
        for carry in (False, True):
            cpu = self.cpu_with([0xB4, 0x0E, 0xCD, 0x21])
            cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x0E00, 0x1111, 0x2222, 0x0003
            cpu.set_flag(flags.CF, carry)
            start_flags = cpu.flags
            cpu.step()
            cpu.step()
            self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x1111, 0x2222, 0x0003))
            self.assertEqual((cpu.get8(4), cpu.flags), (0x0E, start_flags))


if __name__ == "__main__":
    unittest.main()
