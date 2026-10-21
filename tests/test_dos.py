import contextlib
import io
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from mydos import config, dos, flags
from mydos.cpu import CPU, Exit
from mydos.handles import OpenFile
from mydos.volumes import Volumes


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


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
        for handle in (5, 19, 20, 0x8000, 0xFFFF):  # 3 and 4 are the auxiliary and printer devices: open
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
        for handle in (5, 19, 20, 0xFFFF):
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


class TestIoctlRemovable(unittest.TestCase):
    """INT 21h AH=44h AL=08h: is the medium of this drive removable? BL counts from 1; 0 is the current drive."""

    def ask(self, bl, current=None, carry=False):
        """Run `mov ax,4408h ; mov bl,BL ; int 21h`, after selecting `current` as the drive if given."""
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        code = []
        if current is not None:
            code += [0xB2, current, 0xB4, 0x0E, 0xCD, 0x21]  # mov dl,current ; mov ah,0Eh ; int 21h
        code += [0xB8, 0x08, 0x44, 0xB3, bl, 0xCD, 0x21]  # mov ax,4408h ; mov bl,BL ; int 21h
        cpu.mem[0x10000:0x10000 + len(code)] = bytes(code)
        cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x2222, 0x3333
        steps = 6 if current is not None else 3
        for i in range(steps):
            cpu.step()
            if i == 2 and current is not None:
                cpu.set_flag(flags.CF, carry)  # the carry before the call, whatever the selection did to it
        return cpu

    def test_the_floppies_are_removable(self):
        for bl in (1, 2):  # A: and B:
            cpu = self.ask(bl)
            self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (False, 0), f"BL={bl}")

    def test_the_hard_disk_is_fixed(self):
        cpu = self.ask(3)  # C:
        self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (False, 1))

    def test_the_cd_rom_is_removable(self):
        cpu = self.ask(4)  # D:
        self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (False, 0))

    def test_bl_0_asks_about_the_current_drive(self):
        for current, answer in ((0, 0), (1, 0), (2, 1), (3, 0)):
            cpu = self.ask(0, current=current)
            self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (False, answer), f"current drive {current}")
        self.assertEqual(self.ask(0).r[CPU.AX], 1)  # a program starts on C:, the hard disk

    def test_a_drive_that_does_not_exist_is_error_0fh(self):
        for bl in (5, 6, 26, 27, 255):  # E: and beyond
            cpu = self.ask(bl)
            self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (True, 0x0F), f"BL={bl}")

    def test_the_carry_is_cleared_on_success(self):
        cpu = self.ask(3, current=2, carry=True)
        self.assertFalse(cpu.get_flag(flags.CF))

    def test_other_registers_are_left_alone(self):
        cpu = self.ask(3)
        self.assertEqual((cpu.r[CPU.BX] & 0xFF, cpu.r[CPU.CX], cpu.r[CPU.DX]), (3, 0x2222, 0x3333))

    def test_the_numbers_of_the_three_functions_do_not_agree_and_the_program_copes(self):
        """esetup.exe selects drive CL (from 0), then asks about drive CL+1 (from 1): the same disk."""
        for drive in range(4):
            cpu = self.ask(drive + 1)
            self.assertEqual(cpu.r[CPU.AX], 0 if drive in (0, 1, 3) else 1, f"drive {drive}")


class TestCloseHandle(unittest.TestCase):
    """INT 21h AH=3Eh: close a file handle."""

    def setUp(self):
        self.cpu = CPU()
        dos.install(self.cpu)
        self.cpu.s[CPU.CS], self.cpu.s[CPU.DS] = 0x1000, 0x2000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        for i, byte in enumerate(b"text"):
            self.cpu.wb(0x2000, 0x0100 + i, byte)

    def call(self, ax, bx=0, cx=0, dx=0):
        """Run one INT 21h with these registers on the CPU of this test; returns (printed, error output)."""
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.ip = 0
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cpu.step()
        return out.getvalue(), err.getvalue()

    def close(self, handle):
        self.call(0x3E00, bx=handle)
        return self.cpu.get_flag(flags.CF), self.cpu.r[CPU.AX] if self.cpu.get_flag(flags.CF) else None

    def test_the_standard_handles_can_be_closed(self):
        for handle in (0, 1, 2):
            self.assertEqual(self.close(handle), (False, None), f"handle {handle}")

    def test_closing_a_handle_twice_is_error_6(self):
        self.assertEqual(self.close(1), (False, None))
        self.assertEqual(self.close(1), (True, 6))

    def test_a_handle_that_was_never_open_is_error_6(self):
        for handle in (5, 19, 20, 255, 0xFFFF):
            self.assertEqual(self.close(handle), (True, 6), f"handle {handle}")

    def test_writing_to_a_closed_handle_fails_and_prints_nothing(self):
        self.assertEqual(self.call(0x4000, bx=1, cx=4, dx=0x0100), ("text", ""))  # open: it prints
        self.close(1)
        self.assertEqual(self.call(0x4000, bx=1, cx=4, dx=0x0100), ("", ""))  # closed: nothing
        self.assertEqual((self.cpu.get_flag(flags.CF), self.cpu.r[CPU.AX]), (True, 6))

    def test_closing_one_handle_leaves_the_others_open(self):
        self.close(1)
        self.assertEqual(self.call(0x4000, bx=2, cx=4, dx=0x0100), ("", "text"))  # stderr still works
        self.assertFalse(self.cpu.get_flag(flags.CF))
        self.call(0x4400, bx=0)  # and so does the IOCTL question about stdin
        self.assertFalse(self.cpu.get_flag(flags.CF))

    def test_the_ioctl_question_about_a_closed_handle_is_error_6(self):
        self.close(0)
        self.call(0x4400, bx=0)
        self.assertEqual((self.cpu.get_flag(flags.CF), self.cpu.r[CPU.AX]), (True, 6))

    def test_closing_everything_the_way_a_program_does_at_exit(self):
        """The C library closes the streams one by one when the program ends."""
        for handle in range(3):
            self.assertEqual(self.close(handle), (False, None), f"handle {handle}")
        for handle in range(3):
            self.assertEqual(self.close(handle), (True, 6), f"handle {handle} again")

    def test_a_carry_set_before_is_cleared_on_success(self):
        self.cpu.set_flag(flags.CF, True)
        self.call(0x3E00, bx=2)
        self.assertFalse(self.cpu.get_flag(flags.CF))

    def test_other_registers_are_left_alone(self):
        self.call(0x3E00, bx=1, cx=0x2222, dx=0x3333)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.get8(4)), (1, 0x2222, 0x3333, 0x3E))

    def test_each_cpu_has_its_own_open_handles(self):
        self.close(1)
        other = CPU()
        dos.install(other)
        other.s[CPU.CS] = 0x1000
        other.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        other.r[CPU.AX], other.r[CPU.BX] = 0x3E00, 1
        other.step()
        self.assertFalse(other.get_flag(flags.CF))  # for the other machine handle 1 was still open


class TestTheDrivesComeFromTheConfig(unittest.TestCase):
    """The drive functions answer for the machine the configuration describes, not for a fixed one."""

    def cpu_for(self, text):
        """A CPU with DOS installed for the machine in `text`; the media are all in memory, so no host path is used."""
        cpu = CPU()
        dos.install(cpu, Volumes(config.parse(tomllib.loads(text), Path.cwd())))
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        return cpu

    def call(self, cpu, ax, bx=0, dx=0):
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.DX] = ax, bx, dx
        cpu.ip = 0
        cpu.step()

    # a floppy A:, a hard disk C:, a floppy F: (with gaps between), starting on F:
    GAPS = """
        cwd = 'F:\\'
        [disks.A]
        removable = true
        [disks.C]
        removable = false
        [disks.F]
        removable = true
        [mounts]
        C = { type = "tmpfs" }
        F = { type = "tmpfs" }
    """

    def test_a_program_starts_on_the_drive_of_the_starting_directory(self):
        cpu = self.cpu_for(self.GAPS)
        self.call(cpu, 0x1900)
        self.assertEqual(cpu.get8(0), 5)  # F:

    def test_only_the_drives_in_the_config_can_be_selected(self):
        cpu = self.cpu_for(self.GAPS)
        for drive, expected in ((0, 0), (1, 0), (2, 2), (3, 2), (4, 2), (5, 5), (6, 5)):  # B: D: E: G: do not exist
            self.call(cpu, 0x0E00, dx=drive)
            self.call(cpu, 0x1900)
            self.assertEqual(cpu.get8(0), expected, f"selected {drive}")

    def test_the_number_of_drives_is_the_highest_number_plus_one(self):
        cpu = self.cpu_for(self.GAPS)
        self.call(cpu, 0x0E00, dx=2)
        self.assertEqual(cpu.get8(0), 6)  # F: is drive 5, and there are gaps, so 6 and not 3

    def test_a_machine_with_only_a_hard_disk(self):
        cpu = self.cpu_for('[disks.C]\nremovable = false\n[mounts]\nC = { type = "tmpfs" }\n')
        self.call(cpu, 0x1900)
        self.assertEqual(cpu.get8(0), 2)
        self.call(cpu, 0x0E00, dx=0)  # A: is not there
        self.assertEqual(cpu.get8(0), 3)  # the highest number is 2
        self.call(cpu, 0x1900)
        self.assertEqual(cpu.get8(0), 2)

    def test_removable_comes_from_the_config(self):
        cpu = self.cpu_for(self.GAPS)
        for bl, removable in ((1, True), (3, False), (6, True)):  # A:, C:, F: numbered from 1 here
            self.call(cpu, 0x4408, bx=bl)
            self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (False, 0 if removable else 1), f"BL={bl}")

    def test_a_drive_that_is_not_in_the_config_is_an_invalid_drive_for_ioctl(self):
        cpu = self.cpu_for(self.GAPS)
        for bl in (2, 4, 5, 7):  # B: D: E: G:
            self.call(cpu, 0x4408, bx=bl)
            self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (True, 0x0F), f"BL={bl}")

    def test_bl_0_means_the_current_drive_of_this_machine(self):
        cpu = self.cpu_for(self.GAPS)  # starts on F:, which is removable
        self.call(cpu, 0x4408, bx=0)
        self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (False, 0))
        self.call(cpu, 0x0E00, dx=2)  # to C:, the hard disk
        self.call(cpu, 0x4408, bx=0)
        self.assertEqual(cpu.r[CPU.AX], 1)

    def test_without_a_config_the_built_in_machine_is_used(self):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        self.call(cpu, 0x0E00, dx=0)
        self.assertEqual(cpu.get8(0), 4)  # A: B: C: D:
        self.call(cpu, 0x1900)
        self.assertEqual(cpu.get8(0), 0)  # the call moved to A:


class FileFunctionsTest(unittest.TestCase):
    """A machine to try the file functions on: C: is a host directory, D: a memory disk, A: has no disk in it."""

    TEXT = """
        [disks.A]
        removable = true
        [disks.C]
        removable = false
        [disks.D]
        removable = true
        [mounts]
        C = { type = "dir", path = "." }
        D = { type = "tmpfs" }
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: self.state.handles.close_all())
        self.base = Path(self.tmp.name).resolve()
        (self.base / "GAMES").mkdir()
        (self.base / "NOTES.TXT").write_bytes(b"some notes")
        self.volumes = Volumes(config.parse(tomllib.loads(self.TEXT), self.base))
        self.cpu = CPU()
        self.state = dos.install(self.cpu, self.volumes)
        self.cpu.s[CPU.CS], self.cpu.s[CPU.DS] = 0x1000, 0x2000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])

    def call(self, ax, name=None, cx=0, bx=0, dx=0x0100):
        """Run INT 21h with `name` as a zero-ended string at DS:DX."""
        cpu = self.cpu
        if name is not None:
            for i, byte in enumerate(name.encode("cp437") + b"\0"):
                cpu.wb(0x2000, (dx + i) & 0xFFFF, byte)
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.ip = 0
        cpu.step()

    def failed(self):
        """None if the last call went well, else the error number it gave."""
        return self.cpu.r[CPU.AX] if self.cpu.get_flag(flags.CF) else None

    def skip_if_root(self):
        if os.geteuid() == 0:
            self.skipTest("the host lets root write to anything, so a read-only file looks writable")


class TestFileAttributes(FileFunctionsTest):
    """INT 21h AH=43h: AL=0 gets the attributes of a file into CX, AL=1 sets them."""

    def get(self, name):
        self.call(0x4300, name)
        return self.failed(), self.cpu.r[CPU.CX]

    def test_a_file_a_directory_and_the_root(self):
        self.assertEqual(self.get("NOTES.TXT"), (None, 0x20))  # archive
        self.assertEqual(self.get("GAMES"), (None, 0x10))  # directory
        self.assertEqual(self.get("\\"), (None, 0x10))  # the root is a directory too

    def test_names_are_matched_without_regard_to_case_and_with_either_separator(self):
        self.assertEqual(self.get("notes.txt"), (None, 0x20))
        self.assertEqual(self.get("C:/Games"), (None, 0x10))
        self.assertEqual(self.get("games\\..\\NOTES.TXT"), (None, 0x20))

    def test_a_read_only_file(self):
        self.skip_if_root()
        (self.base / "LOCKED.TXT").write_bytes(b"x")
        os.chmod(self.base / "LOCKED.TXT", 0o444)
        self.assertEqual(self.get("LOCKED.TXT"), (None, 0x21))

    def test_a_file_on_the_memory_disk(self):
        self.volumes.filesystems[3].create(["F"]).close()
        self.assertEqual(self.get("D:\\F"), (None, 0x20))

    def test_the_current_directory_is_where_a_relative_name_is_looked_for(self):
        self.volumes.filesystems[3].mkdir(["SUB"])
        self.volumes.filesystems[3].create(["SUB", "F"]).close()
        self.assertEqual(self.get("D:F")[0], 2)  # D: is in its root, which has no F
        self.volumes.chdir("D:\\SUB")
        self.assertEqual(self.get("D:F"), (None, 0x20))

    def test_the_errors_are_the_ones_dos_gives(self):
        self.assertEqual(self.get("NOPE.TXT")[0], 2)  # file not found
        self.assertEqual(self.get("GAMES\\NOPE.TXT")[0], 2)
        self.assertEqual(self.get("NODIR\\NOPE.TXT")[0], 3)  # path not found
        self.assertEqual(self.get("NOTES.TXT\\INSIDE")[0], 3)  # a file in the middle of the path
        self.assertEqual(self.get("..\\X")[0], 3)  # above the root
        self.assertEqual(self.get("Z:\\X")[0], 15)  # no such drive
        self.assertEqual(self.get("A:\\X")[0], 21)  # a drive with nothing in it
        self.assertEqual(self.get("LONGFILENAME.TXT")[0], 2)  # a name DOS cannot have is not a file it has
        self.assertEqual(self.get("\u00e9.TXT")[0], 2)

    def test_an_empty_name_and_a_name_with_no_end(self):
        self.assertEqual(self.get("")[0], 3)
        for i in range(300):  # no zero within the longest path
            self.cpu.wb(0x2000, 0x0100 + i, ord("A"))
        self.call(0x4300)
        self.assertEqual(self.failed(), 3)

    def test_the_name_may_run_over_the_end_of_the_segment(self):
        self.call(0x4300, "NOTES.TXT", dx=0xFFFC)  # FFFC, FFFD, ..., FFFF, 0000, ...
        self.assertEqual((self.failed(), self.cpu.r[CPU.CX]), (None, 0x20))

    def test_registers_after_a_good_get(self):
        self.cpu.set_flag(flags.CF, True)
        self.call(0x4300, "NOTES.TXT", cx=0x7777, bx=0x1234)
        cpu = self.cpu
        self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX], cpu.r[CPU.BX]), (False, 0x4300, 0x1234))

    def test_registers_after_a_failed_get(self):
        self.call(0x4300, "NOPE.TXT", cx=0x7777)
        self.assertEqual((self.failed(), self.cpu.r[CPU.CX]), (2, 0x7777))  # CX is not touched when it fails

    def test_setting_read_only_and_clearing_it_on_the_memory_disk(self):
        self.volumes.filesystems[3].create(["F"]).close()
        self.call(0x4301, "D:\\F", cx=0x01)
        self.assertEqual((self.failed(), self.cpu.r[CPU.CX]), (None, 0x01))  # CX is what was given
        self.assertEqual(self.get("D:\\F"), (None, 0x21))
        self.call(0x4301, "D:\\F", cx=0x20)
        self.assertEqual(self.get("D:\\F"), (None, 0x20))

    def test_setting_read_only_on_a_host_file(self):
        self.skip_if_root()
        self.call(0x4301, "NOTES.TXT", cx=0x21)
        self.assertIsNone(self.failed())
        self.assertEqual(stat_mode(self.base / "NOTES.TXT") & 0o222, 0)
        self.call(0x4301, "NOTES.TXT", cx=0x20)
        self.assertEqual(stat_mode(self.base / "NOTES.TXT") & 0o200, 0o200)

    def test_what_cannot_be_set(self):
        self.call(0x4301, "GAMES", cx=0)  # a directory's attributes
        self.assertEqual(self.failed(), 5)
        self.call(0x4301, "NOTES.TXT", cx=0x10)  # a file cannot become a directory
        self.assertEqual(self.failed(), 5)
        self.call(0x4301, "NOPE.TXT", cx=0)
        self.assertEqual(self.failed(), 2)
        self.call(0x4301, "Z:\\X", cx=0)
        self.assertEqual(self.failed(), 15)

    def test_the_other_subfunctions_stop_loudly(self):
        for al in (2, 3, 6, 0xFF):
            with self.assertRaises(NotImplementedError, msg=f"AL={al:02X}"):
                self.call(0x4300 | al, "NOTES.TXT")


class TestCreateFile(FileFunctionsTest):
    """INT 21h AH=3Ch: create a file, or empty one that is there; AX is its handle."""

    def create(self, name, cx=0):
        self.call(0x3C00, name, cx=cx)
        return self.failed(), self.cpu.r[CPU.AX]  # (the error number or None, the handle if all went well)

    def entry(self, handle):
        return self.state.handles.get(handle)

    def test_a_new_file_is_made_empty_on_the_host_and_gets_handle_5(self):
        self.cpu.set_flag(flags.CF, True)
        self.assertEqual(self.create("dummy.out"), (None, 5))
        self.assertEqual((self.base / "DUMMY.OUT").read_bytes(), b"")  # in capitals, as DOS stores it
        self.assertEqual(self.entry(5).dos_path, "C:\\DUMMY.OUT")

    def test_the_handles_count_up(self):
        self.assertEqual([self.create(name)[1] for name in ("A", "B", "C")], [5, 6, 7])

    def test_a_file_that_is_there_is_emptied_and_keeps_its_name(self):
        self.assertEqual(self.create("notes.txt"), (None, 5))
        self.assertEqual(sorted(os.listdir(self.base)), ["GAMES", "NOTES.TXT"])  # no second file
        self.assertEqual((self.base / "NOTES.TXT").read_bytes(), b"")

    def test_on_the_memory_disk_and_with_a_relative_name(self):
        self.assertEqual(self.create("D:\\F"), (None, 5))
        self.assertEqual(self.volumes.filesystems[3].stat(["F"]).size, 0)
        self.volumes.filesystems[3].mkdir(["SUB"])
        self.volumes.chdir("D:\\SUB")
        self.assertEqual(self.create("D:G"), (None, 6))
        self.assertEqual([e.name for e in self.volumes.filesystems[3].listdir(["SUB"])], ["G"])

    def test_the_handle_reads_and_writes_from_the_start_of_the_file(self):
        handle = self.create("DATA.BIN")[1]
        file = self.entry(handle).file
        self.assertEqual(file.tell(), 0)
        file.write(b"hello")
        file.seek(0)
        self.assertEqual(file.read(), b"hello")  # read and write: it was opened for both

    def test_what_was_written_is_in_the_host_file_once_the_handle_is_closed(self):
        handle = self.create("DATA.BIN")[1]
        self.entry(handle).file.write(b"abc")
        self.call(0x3E00, bx=handle)
        self.assertIsNone(self.failed())
        self.assertEqual((self.base / "DATA.BIN").read_bytes(), b"abc")
        self.call(0x3E00, bx=handle)
        self.assertEqual(self.failed(), 6)  # already closed

    def test_closing_the_handle_closes_the_file_object(self):
        handle = self.create("DATA.BIN")[1]
        file = self.entry(handle).file
        self.call(0x3E00, bx=handle)
        self.assertTrue(file.closed)

    def test_attribute_1_makes_the_file_read_only_but_the_handle_can_still_write(self):
        handle = self.create("D:\\RO", cx=1)[1]  # the memory disk, where no user can write to anything
        self.assertEqual(self.volumes.filesystems[3].stat(["RO"]).attributes, 0x21)
        self.assertEqual(self.entry(handle).file.write(b"still writable"), 14)

    def test_attribute_1_on_the_host_takes_the_write_permissions_away_after_the_file_exists(self):
        self.skip_if_root()
        handle = self.create("RO.TXT", cx=1)[1]
        self.assertEqual(stat_mode(self.base / "RO.TXT") & 0o222, 0)
        self.assertEqual(self.entry(handle).file.write(b"x"), 1)  # the open handle may write

    def test_hidden_system_and_archive_attributes_are_accepted(self):
        self.assertEqual(self.create("D:\\H", cx=0x27 & ~0x01)[0], None)
        self.assertEqual(self.volumes.filesystems[3].stat(["H"]).attributes, 0x20)

    def test_a_directory_or_label_attribute_is_refused_and_nothing_is_made(self):
        for attributes in (0x10, 0x08, 0x18):
            self.assertEqual(self.create("NEWFILE", cx=attributes), (5, 5))  # AX is the error, 5 again by chance
        self.assertNotIn("NEWFILE", os.listdir(self.base))
        self.assertIsNone(self.entry(5))

    def test_the_errors_are_the_ones_dos_gives(self):
        self.assertEqual(self.create("NODIR\\X")[0], 3)  # path not found
        self.assertEqual(self.create("TOOLONGNAME.TXT")[0], 3)  # a name DOS cannot have
        self.assertEqual(self.create("")[0], 3)
        self.assertEqual(self.create("GAMES")[0], 5)  # a directory is in the way
        self.assertEqual(self.create("Z:\\X")[0], 15)  # no such drive
        self.assertEqual(self.create("A:\\X")[0], 21)  # nothing in the drive
        self.assertEqual(self.create("..\\X")[0], 3)  # above the root

    def test_an_error_takes_no_handle_and_changes_no_file(self):
        before = sorted(os.listdir(self.base))
        self.create("NODIR\\X")
        self.create("GAMES")
        self.assertEqual(sorted(os.listdir(self.base)), before)
        self.assertEqual(self.create("OK")[1], 5)  # still the first free handle

    def test_a_read_only_file_cannot_be_emptied(self):
        self.volumes.filesystems[3].create(["F"]).close()
        self.volumes.filesystems[3].set_attributes(["F"], 1)
        self.assertEqual(self.create("D:\\F")[0], 5)

    def test_a_read_only_disk_cannot_be_written_to(self):
        text = self.TEXT.replace('D = { type = "tmpfs" }', 'D = { type = "tmpfs", readonly = true }')
        self.state = dos.install(self.cpu, Volumes(config.parse(tomllib.loads(text), self.base)))
        self.assertEqual(self.create("D:\\F")[0], 5)

    def test_twenty_handles_are_all_there_are(self):
        handles = [self.create(f"F{i}")[1] for i in range(15)]
        self.assertEqual(handles, list(range(5, 20)))
        self.assertEqual(self.create("ONEMORE")[0], 4)  # too many open files
        self.assertNotIn("ONEMORE", os.listdir(self.base))  # and the file was not made
        self.call(0x3E00, bx=12)
        self.assertEqual(self.create("ONEMORE"), (None, 12))  # a freed handle is the next one

    def test_a_standard_handle_that_was_closed_is_given_out_again(self):
        self.call(0x3E00, bx=0)
        self.assertEqual(self.create("F")[1], 0)
        self.call(0x3E00, bx=3)
        self.assertEqual(self.create("G")[1], 3)

    def test_the_other_registers_are_left_alone(self):
        self.call(0x3C00, "F", cx=0x0000, bx=0x1234, dx=0x0100)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x1234, 0, 0x0100))

    def test_the_name_may_run_over_the_end_of_the_segment(self):
        self.call(0x3C00, "WRAP.TXT", dx=0xFFFC)
        self.assertEqual((self.failed(), self.cpu.r[CPU.AX]), (None, 5))
        self.assertTrue((self.base / "WRAP.TXT").exists())

    def test_the_end_of_the_program_closes_every_file_and_keeps_what_was_written(self):
        handle = self.create("LEFT.TXT")[1]
        self.entry(handle).file.write(b"left open")
        file = self.entry(handle).file
        with self.assertRaises(Exit):
            self.call(0x4C00)
        self.assertTrue(file.closed)
        self.assertEqual((self.base / "LEFT.TXT").read_bytes(), b"left open")

    def test_the_auxiliary_and_printer_devices_are_open_and_swallow_what_is_written(self):
        for handle in (3, 4):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.call(0x4000, "text", bx=handle, cx=4)
            self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))
            self.assertEqual((self.failed(), self.cpu.r[CPU.AX]), (None, 4))
            self.call(0x4400, bx=handle)
            self.assertEqual((self.failed(), self.cpu.r[CPU.DX]), (None, 0x80C0))

    def test_the_state_install_returns_has_the_table(self):
        names = [type(self.entry(handle)).__name__ for handle in range(6)]
        self.assertEqual(names, ["Console"] * 3 + ["NullDevice"] * 2 + ["NoneType"])


class TestWriteAndAskAboutFiles(FileFunctionsTest):
    """INT 21h AH=40h and AH=44h AL=00 on the handle of a file."""

    def make(self, name="OUT.TXT", cx=0):
        self.call(0x3C00, name, cx=cx)
        return self.cpu.r[CPU.AX]

    def write(self, handle, text):
        self.call(0x4000, text, bx=handle, cx=len(text))
        return self.failed(), self.cpu.r[CPU.AX]

    def test_writing_gives_the_count_and_the_bytes_reach_the_host_file(self):
        handle = self.make()
        self.cpu.set_flag(flags.CF, True)
        self.assertEqual(self.write(handle, "hello"), (None, 5))
        self.call(0x3E00, bx=handle)
        self.assertEqual((self.base / "OUT.TXT").read_bytes(), b"hello")

    def test_each_write_goes_on_after_the_one_before(self):
        handle = self.make()
        self.write(handle, "abc")
        self.write(handle, "def")
        self.call(0x3E00, bx=handle)
        self.assertEqual((self.base / "OUT.TXT").read_bytes(), b"abcdef")

    def test_on_the_memory_disk(self):
        handle = self.make("D:\\F")
        self.assertEqual(self.write(handle, "mem"), (None, 3))
        self.call(0x3E00, bx=handle)
        self.assertEqual(self.volumes.filesystems[3].stat(["F"]).size, 3)

    def test_the_bytes_are_not_changed_whatever_they_are(self):
        handle = self.make()
        self.cpu.mem[0x20100:0x20104] = bytes([0, 0x0D, 0x1A, 0xFF])
        self.call(0x4000, bx=handle, cx=4)
        self.call(0x3E00, bx=handle)
        self.assertEqual((self.base / "OUT.TXT").read_bytes(), bytes([0, 0x0D, 0x1A, 0xFF]))

    def test_the_data_may_run_over_the_end_of_the_segment(self):
        handle = self.make()
        self.cpu.mem[0x2FFFE:0x30000] = b"ab"
        self.cpu.mem[0x20000:0x20002] = b"cd"
        self.call(0x4000, bx=handle, cx=4, dx=0xFFFE)
        self.call(0x3E00, bx=handle)
        self.assertEqual((self.base / "OUT.TXT").read_bytes(), b"abcd")

    def test_zero_bytes_cut_the_file_off_at_the_position(self):
        handle = self.make()
        self.write(handle, "abcdef")
        self.state.handles.get(handle).file.seek(2)
        self.call(0x4000, bx=handle, cx=0)
        self.assertEqual((self.failed(), self.cpu.r[CPU.AX]), (None, 0))
        self.call(0x3E00, bx=handle)
        self.assertEqual((self.base / "OUT.TXT").read_bytes(), b"ab")

    def test_zero_bytes_at_the_end_change_nothing(self):
        handle = self.make("D:\\F")
        self.write(handle, "abc")
        self.call(0x4000, bx=handle, cx=0)
        self.assertEqual(self.volumes.filesystems[3].stat(["F"]).size, 3)

    def test_a_file_that_was_not_opened_for_writing_refuses(self):
        for name in ("NOTES.TXT", "D:\\F"):
            where = self.volumes.resolve(name)
            if where.drive == 3:
                where.filesystem.create(where.parts).close()
            file = where.filesystem.open(where.parts, write=False)
            handle = self.state.handles.allocate(OpenFile(file, where.dos_path()))
            self.assertEqual(self.write(handle, "x"), (5, 5))
            self.call(0x4000, bx=handle, cx=0)
            self.assertEqual(self.failed(), 5)
        self.assertEqual((self.base / "NOTES.TXT").read_bytes(), b"some notes")

    def test_a_closed_handle_is_an_error(self):
        handle = self.make()
        self.call(0x3E00, bx=handle)
        self.assertEqual(self.write(handle, "x"), (6, 6))

    def test_the_other_registers_are_left_alone(self):
        handle = self.make()
        self.call(0x4000, "abcd", bx=handle, cx=4, dx=0x0100)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (handle, 4, 0x0100))

    def test_ioctl_says_a_file_is_not_a_device_and_names_its_drive(self):
        host, memory = self.make("F"), self.make("D:\\G")
        for handle, drive in ((host, 2), (memory, 3)):
            self.cpu.set_flag(flags.CF, True)
            self.call(0x4400, bx=handle)
            self.assertEqual((self.failed(), self.cpu.r[CPU.DX]), (None, drive))

    def test_ioctl_on_a_closed_handle_is_an_error(self):
        handle = self.make()
        self.call(0x3E00, bx=handle)
        self.call(0x4400, bx=handle)
        self.assertEqual(self.failed(), 6)

    def test_ioctl_does_not_touch_the_other_registers(self):
        handle = self.make()
        self.call(0x4400, bx=handle, cx=0x1234)
        self.assertEqual((self.cpu.r[CPU.BX], self.cpu.r[CPU.CX]), (handle, 0x1234))


class TestOpenFile(FileFunctionsTest):
    """INT 21h AH=3Dh: open a file that is there; AL says for what; AX is the handle."""

    def open(self, name, mode=0):
        self.call(0x3D00 | mode, name)
        return self.failed(), self.cpu.r[CPU.AX]

    def entry(self, handle):
        return self.state.handles.get(handle)

    def test_an_existing_file_gets_handle_5_and_its_dos_name(self):
        self.cpu.set_flag(flags.CF, True)
        self.assertEqual(self.open("notes.txt"), (None, 5))
        self.assertEqual(self.entry(5).dos_path, "C:\\NOTES.TXT")

    def test_the_file_is_not_changed_and_is_read_from_the_start(self):
        for mode in (0, 1, 2):
            handle = self.open("NOTES.TXT", mode)[1]
            self.assertEqual(self.entry(handle).file.tell(), 0)
        self.assertEqual((self.base / "NOTES.TXT").read_bytes(), b"some notes")

    def test_mode_0_reads_but_cannot_write(self):
        file = self.entry(self.open("NOTES.TXT", 0)[1]).file
        self.assertEqual(file.read(), b"some notes")
        with self.assertRaises(OSError):
            file.write(b"x")

    def test_modes_1_and_2_write_and_keep_the_rest_of_the_file(self):
        for mode in (1, 2):
            handle = self.open("NOTES.TXT", mode)[1]
            self.assertEqual(self.entry(handle).file.write(b"SOME"), 4)
            self.call(0x3E00, bx=handle)
        self.assertEqual((self.base / "NOTES.TXT").read_bytes(), b"SOME notes")

    def test_the_sharing_and_inheritance_bits_are_ignored(self):
        for mode in (0x10, 0x42, 0x80, 0xF0, 0x91):
            self.assertIsNone(self.open("NOTES.TXT", mode)[0])

    def test_an_access_code_above_2_is_error_12(self):
        for mode in (3, 4, 7, 0x13):
            self.assertEqual(self.open("NOTES.TXT", mode), (0x0C, 0x0C))
        self.assertIsNone(self.entry(5))

    def test_a_file_that_is_not_there_is_not_made(self):
        for mode in (0, 1, 2):
            self.assertEqual(self.open("NEW.TXT", mode), (2, 2))
        self.assertEqual(sorted(os.listdir(self.base)), ["GAMES", "NOTES.TXT"])

    def test_the_errors_are_the_ones_dos_gives(self):
        self.assertEqual(self.open("NODIR\\X")[0], 3)
        self.assertEqual(self.open("TOOLONGNAME.TXT")[0], 2)  # no such file, as it cannot exist
        self.assertEqual(self.open("")[0], 3)
        self.assertEqual(self.open("GAMES")[0], 5)  # a directory is not a file
        self.assertEqual(self.open("GAMES", 2)[0], 5)
        self.assertEqual(self.open("Z:\\X")[0], 15)
        self.assertEqual(self.open("A:\\X")[0], 21)
        self.assertEqual(self.open("..\\X")[0], 3)

    def test_an_error_takes_no_handle(self):
        self.open("NOPE")
        self.open("GAMES")
        self.assertEqual(self.open("NOTES.TXT"), (None, 5))

    def test_a_file_in_a_directory_by_a_relative_path_and_on_the_memory_disk(self):
        (self.base / "GAMES" / "G.TXT").write_bytes(b"g")
        self.assertEqual(self.open("games\\g.txt")[0], None)
        self.volumes.filesystems[3].create(["F"]).close()
        self.assertEqual(self.open("D:\\F", 2)[0], None)
        self.volumes.chdir("C:\\GAMES")
        self.assertEqual(self.open("G.TXT")[0], None)

    def test_a_read_only_file_opens_for_reading_only(self):
        self.volumes.filesystems[3].create(["F"]).close()
        self.volumes.filesystems[3].set_attributes(["F"], 1)
        self.assertIsNone(self.open("D:\\F", 0)[0])
        self.assertEqual(self.open("D:\\F", 1)[0], 5)
        self.assertEqual(self.open("D:\\F", 2)[0], 5)

    def test_a_read_only_host_file_opens_for_reading_only(self):
        self.skip_if_root()
        os.chmod(self.base / "NOTES.TXT", 0o444)
        self.assertIsNone(self.open("NOTES.TXT", 0)[0])
        self.assertEqual(self.open("NOTES.TXT", 1)[0], 5)

    def test_a_read_only_disk_opens_for_reading_only(self):
        text = self.TEXT.replace('path = "."', 'path = ".", readonly = true')
        self.state = dos.install(self.cpu, Volumes(config.parse(tomllib.loads(text), self.base)))
        self.assertIsNone(self.open("NOTES.TXT", 0)[0])
        self.assertEqual(self.open("NOTES.TXT", 2)[0], 5)

    def test_the_same_file_can_be_opened_twice_with_a_position_each(self):
        first, second = self.open("NOTES.TXT")[1], self.open("NOTES.TXT")[1]
        self.assertEqual(self.entry(first).file.read(4), b"some")
        self.assertEqual(self.entry(second).file.read(), b"some notes")

    def test_twenty_handles_are_all_there_are(self):
        handles = [self.open("NOTES.TXT")[1] for _ in range(15)]
        self.assertEqual(handles, list(range(5, 20)))
        self.assertEqual(self.open("NOTES.TXT")[0], 4)
        self.call(0x3E00, bx=7)
        self.assertEqual(self.open("NOTES.TXT"), (None, 7))

    def test_a_closed_standard_handle_is_given_out_again(self):
        self.call(0x3E00, bx=0)
        self.assertEqual(self.open("NOTES.TXT")[1], 0)

    def test_the_other_registers_are_left_alone(self):
        self.call(0x3D00, "NOTES.TXT", cx=0x1234, bx=0x5678, dx=0x0100)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x5678, 0x1234, 0x0100))

    def test_the_name_may_run_over_the_end_of_the_segment(self):
        self.call(0x3D00, "NOTES.TXT", dx=0xFFFC)
        self.assertEqual((self.failed(), self.cpu.r[CPU.AX]), (None, 5))

    def test_a_file_made_by_3ch_can_be_opened_again_after_closing(self):
        self.call(0x3C00, "OUT.TXT")
        handle = self.cpu.r[CPU.AX]
        self.entry(handle).file.write(b"abc")
        self.call(0x3E00, bx=handle)
        handle = self.open("out.txt", 2)[1]
        self.assertEqual(self.entry(handle).file.read(), b"abc")
        self.call(0x4000, "xy", bx=handle, cx=2)
        self.call(0x3E00, bx=handle)
        self.assertEqual((self.base / "OUT.TXT").read_bytes(), b"abcxy")


if __name__ == "__main__":
    unittest.main()
