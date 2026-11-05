import contextlib
import io
import os
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

from dosloth import bios, config, dos, flags, vga
from dosloth.cpu import CPU, Exit
from dosloth.filesystem import SECTOR_NOT_FOUND
from dosloth.handles import OpenFile
from dosloth.volumes import Volumes


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


class TestTheVectors(unittest.TestCase):
    """INT 21h AH=25h and 35h: the table of vectors, and what a vector that has no handler of ours gives."""

    def setUp(self):
        self.cpu = CPU()
        dos.install(self.cpu)
        self.cpu.s[CPU.CS], self.cpu.s[CPU.DS] = 0x1000, 0x2000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])

    def call(self, ax, dx=0):
        self.cpu.r[CPU.AX], self.cpu.r[CPU.DX], self.cpu.ip = ax, dx, 0
        self.cpu.step()
        return self.cpu.s[CPU.ES], self.cpu.r[CPU.BX]

    def test_a_vector_that_is_set_is_given_back(self):
        self.call(0x2560, 0x1234)
        self.assertEqual(self.call(0x3560), (0x2000, 0x1234))

    def test_a_vector_with_no_handler_gives_an_iret_in_the_rom(self):
        where = self.call(0x3560)
        self.assertEqual(where, dos.IRET_AT)
        self.assertEqual(self.cpu.mem[where[0] * 16 + where[1]], 0xCF)

    def test_the_vectors_of_the_controllers_give_a_handler_that_ends_the_interrupt_first(self):
        for number, at, ports in ((0x08, dos.MASTER_EOI_AT, (0x20,)), (0x0D, dos.MASTER_EOI_AT, (0x20,)),
                                  (0x70, dos.SLAVE_EOI_AT, (0xA0, 0x20)), (0x77, dos.SLAVE_EOI_AT, (0xA0, 0x20))):
            where = self.call(0x3500 | number)
            self.assertEqual(where, at, number)
            code = bytes(self.cpu.mem[where[0] * 16 + where[1]:where[0] * 16 + where[1] + 9])
            self.assertEqual([code[i + 1] for i in range(len(code)) if code[i] == 0xE6], list(ports))
            self.assertIn(0xCF, code)

    def test_a_vector_that_is_ours_gives_the_zero_that_it_always_gave(self):
        self.assertEqual(self.call(0x3521), (0, 0))  # INT 21h is a Python function: nothing is in the table


class TestAbsoluteDiskRead(unittest.TestCase):
    """INT 25h: sectors of a disk. This machine has none to give, so it is an error, and the flags stay on the stack."""

    def ask(self, drive, cx=1, dx=0, flags_before=0x0246, buffer=0x0300):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS], cpu.s[CPU.DS], cpu.s[CPU.SS] = 0x1000, 0x2000, 0x4000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x25])
        for i in range(16):
            cpu.wb(0x2000, buffer + i, 0xAA)
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0xCD00 | drive, buffer, cx, dx
        cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.r[CPU.BP], cpu.r[CPU.SP] = 0x4444, 0x5555, 0x6666, 0x0200
        cpu.flags = flags_before
        cpu.step()
        return cpu

    def test_it_is_an_error_a_general_failure_with_the_carry_set(self):
        for drive in (0, 1, 2, 3):  # A:, B:, C:, D:
            cpu = self.ask(drive)
            self.assertTrue(cpu.get_flag(flags.CF), drive)
            self.assertEqual(cpu.r[CPU.AX], 0x000C, drive)

    def test_a_drive_that_is_not_there_is_an_unknown_unit(self):
        for drive in (4, 25, 0xFF):
            cpu = self.ask(drive)
            self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (True, 0x0001), drive)

    def test_the_flags_the_int_pushed_are_left_on_the_stack_and_the_program_takes_them_off(self):
        for before in (0x0246, 0x0247, 0x0002, 0x0A03):
            cpu = self.ask(2, flags_before=before)
            self.assertEqual(cpu.r[CPU.SP], 0x01FE)
            self.assertEqual(cpu.rw(0x4000, 0x01FE), before)  # as they were, with the carry before the call
            self.assertTrue(cpu.get_flag(flags.CF))

    def test_the_buffer_and_the_registers_other_than_ax_are_left_alone(self):
        cpu = self.ask(2, cx=3, dx=7)
        self.assertEqual(bytes(cpu.rb(0x2000, 0x0300 + i) for i in range(16)), bytes([0xAA]) * 16)
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.r[CPU.BP]),
                         (0x0300, 3, 7, 0x4444, 0x5555, 0x6666))
        self.assertEqual((cpu.s[CPU.DS], cpu.s[CPU.CS], cpu.ip), (0x2000, 0x1000, 2))

    def test_the_form_for_big_disks_with_cx_ffff_is_the_same_error(self):
        cpu = self.ask(2, cx=0xFFFF)
        self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (True, 0x000C))

    def test_other_flags_stay_as_they_are(self):
        cpu = self.ask(2, flags_before=0x0246 | flags.DF)
        self.assertTrue(cpu.get_flag(flags.DF) and cpu.get_flag(flags.ZF) and cpu.get_flag(flags.IF))


class TestIoctlControlData(unittest.TestCase):
    """INT 21h AH=44h AL=04h and 05h: control data to and from the driver of a block device. BL counts from 1."""

    def ask(self, al, bl, current=None, buffer=0x0200):
        cpu = CPU()
        dos.install(cpu)
        if current is not None:
            cpu.s[CPU.CS] = 0x1000
            cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
            cpu.r[CPU.AX], cpu.r[CPU.DX] = 0x0E00, current
            cpu.step()
        cpu.s[CPU.CS], cpu.s[CPU.DS] = 0x1000, 0x2000
        cpu.mem[0x10100:0x10102] = bytes([0xCD, 0x21])
        for i in range(16):
            cpu.wb(0x2000, buffer + i, 0xAA)
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x4400 | al, 0x1100 | bl, 13, buffer
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, 0x5555
        cpu.set_flag(flags.CF, False)
        cpu.ip = 0x0100
        cpu.step()
        return cpu

    def test_the_disks_have_no_control_data_and_it_is_error_1_for_both_directions(self):
        for al in (4, 5):
            for bl, name in ((1, "A:"), (2, "B:"), (3, "C:")):
                cpu = self.ask(al, bl)
                self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (True, 1), f"AL={al} {name}")

    def test_bl_0_is_the_current_drive(self):
        self.assertEqual(self.ask(5, 0).r[CPU.AX], 1)  # C:, where a program starts
        self.assertEqual(self.ask(5, 0, current=0).r[CPU.AX], 1)  # A:
        with self.assertRaisesRegex(NotImplementedError, "CD-ROM"):
            self.ask(5, 0, current=3)  # D:

    def test_a_drive_that_is_not_there_is_error_0fh(self):
        for al in (4, 5):
            for bl in (5, 27, 0xFF):
                cpu = self.ask(al, bl)
                self.assertEqual((cpu.get_flag(flags.CF), cpu.r[CPU.AX]), (True, 0x0F), f"AL={al} BL={bl}")

    def test_a_cd_rom_drive_stops_the_emulator_with_a_message_that_names_it(self):
        for al in (4, 5):
            with self.assertRaisesRegex(NotImplementedError, f"AH=44 AL={al:02X} for a CD-ROM drive"):
                self.ask(al, 4)

    def test_the_buffer_is_not_touched_and_the_registers_stay(self):
        cpu = self.ask(5, 3)
        self.assertEqual(bytes(cpu.rb(0x2000, 0x0200 + i) for i in range(16)), bytes([0xAA]) * 16)
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]),
                         (0x1103, 13, 0x0200, 0x4444, 0x5555))
        self.assertEqual((cpu.s[CPU.DS], cpu.ip), (0x2000, 0x0102))

    def test_the_other_subfunctions_are_still_not_done(self):
        for al in (0x02, 0x03, 0x06, 0x07, 0x09, 0x0D):
            with self.assertRaisesRegex(NotImplementedError, f"AH=44 AL={al:02X}"):
                self.ask(al, 3)


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
        from dosloth import psp
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


class TestBreakFlagBootDriveAndVersion(unittest.TestCase):
    """INT 21h AH=33h: the Ctrl-Break flag (AL=0, 1), the boot drive (AL=5), the real DOS version (AL=6)."""

    def setUp(self):
        self.cpu = CPU()
        self.state = dos.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])

    def call(self, ax, dx=0x1111, bx=0x3333, cx=0x2222):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.DX], cpu.r[CPU.BX], cpu.r[CPU.CX] = ax, dx, bx, cx
        cpu.set_flag(flags.CF, True)
        cpu.ip = 0
        cpu.step()

    def test_dos_starts_with_the_flag_off(self):
        self.call(0x3300)
        self.assertEqual(self.cpu.get8(2), 0)
        self.assertEqual(self.cpu.r[CPU.DX], 0x1100)  # only DL is the answer: DH stays
        self.assertTrue(self.cpu.get_flag(flags.CF))  # no error to report: the carry is left as it was

    def test_the_flag_is_set_and_read_back(self):
        self.call(0x3301, dx=0x1101)
        self.call(0x3300, dx=0x1100)
        self.assertEqual(self.cpu.r[CPU.DX], 0x1101)
        self.call(0x3301, dx=0x1100)
        self.call(0x3300, dx=0x1101)
        self.assertEqual(self.cpu.r[CPU.DX], 0x1100)

    def test_any_value_but_zero_in_dl_turns_it_on(self):
        self.call(0x3301, dx=0x1180)
        self.call(0x3300)
        self.assertEqual(self.cpu.get8(2), 1)

    def test_setting_the_flag_changes_no_register(self):
        self.call(0x3301, dx=0x1101)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]),
                         (0x3301, 0x3333, 0x2222, 0x1101))
        self.assertTrue(cpu.get_flag(flags.CF))  # left as it was

    def test_two_machines_have_two_flags(self):
        self.call(0x3301, dx=1)
        other = CPU()
        dos.install(other)
        other.s[CPU.CS] = 0x1000
        other.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        other.r[CPU.AX] = 0x3300
        other.step()
        self.assertEqual(other.get8(2), 0)

    def test_the_boot_drive_is_the_hard_disk_counted_from_1(self):
        self.call(0x3305)
        self.assertEqual(self.cpu.r[CPU.DX], 0x1103)  # C: of the built-in machine; DH stays
        self.assertEqual((self.cpu.r[CPU.AX], self.cpu.r[CPU.BX], self.cpu.r[CPU.CX]), (0x3305, 0x3333, 0x2222))
        self.assertTrue(self.cpu.get_flag(flags.CF))  # no error to report: the carry is left as it was

    def test_the_boot_drive_follows_the_config(self):
        def boot(text):
            return dos.boot_drive(config.parse(tomllib.loads(text), Path(".")))
        self.assertEqual(boot("[disks.A]\nremovable = true\n[disks.E]\nremovable = false\n[disks.D]\n"
                              "removable = false\n[mounts]\nE = {type = 'tmpfs'}\nD = {type = 'tmpfs'}"), 3)
        self.assertEqual(boot("[disks.B]\nremovable = true\n[disks.A]\nremovable = true\n[mounts]\n"
                              "B = {type = 'tmpfs'}"), 0)

    def test_the_true_version_is_the_one_ah_30h_gives(self):
        self.call(0x3306)
        cpu = self.cpu
        self.assertEqual((cpu.get8(3), cpu.get8(7)), dos.DOS_VERSION)  # BL major, BH minor
        self.assertEqual(cpu.r[CPU.DX], 0)  # revision 0, and DOS in neither ROM nor high memory
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.CX]), (0x3306, 0x2222))
        self.assertTrue(cpu.get_flag(flags.CF))  # left as it was

    def test_other_subfunctions_are_not_done_yet(self):
        for al in (0x02, 0x03, 0x04, 0x07, 0xFF):
            with self.assertRaises(NotImplementedError):
                self.call(0x3300 | al)


class TestMultiplex(unittest.TestCase):
    """INT 2Fh, the questions to MSCDEX, the CD-ROM driver."""

    FLOPPIES_ONLY = "[disks.A]\ntype = 'floppy'\n[disks.C]\ntype = 'hdd'\n[mounts]\nC = {type = 'tmpfs'}\n"
    TWO_CDROMS = FLOPPIES_ONLY + "[disks.E]\ntype = 'cdrom'\n[disks.D]\ntype = 'cdrom'\n"

    def machine(self, text=None):
        self.cpu = CPU()
        volumes = Volumes(config.parse(tomllib.loads(text), Path("."))) if text else None
        dos.install(self.cpu, volumes)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x2F])
        return self.cpu

    def call(self, ax, bx=0, cx=0x2222, dx=0x3333):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.ip = 0
        cpu.step()

    def test_the_built_in_machine_has_one_cd_rom_drive_d(self):
        self.machine()
        self.call(0x1500, bx=0x5555)
        self.assertEqual((self.cpu.r[CPU.BX], self.cpu.r[CPU.CX]), (1, 3))  # one drive, and it is D:

    def test_without_a_cd_rom_drive_mscdex_is_not_there(self):
        self.machine(self.FLOPPIES_ONLY)
        self.call(0x1500, bx=0x5555)
        self.assertEqual((self.cpu.r[CPU.BX], self.cpu.r[CPU.CX]), (0, 0x2222))  # CX is left as it was

    def test_two_cd_rom_drives_are_counted_and_the_lowest_is_the_first(self):
        self.machine(self.TWO_CDROMS)
        self.call(0x1500)
        self.assertEqual((self.cpu.r[CPU.BX], self.cpu.r[CPU.CX]), (2, 3))  # D: and E:, though E: is written first

    def test_a_cd_rom_drive_with_no_disc_in_it_is_still_a_drive(self):
        self.machine()  # D: has nothing mounted
        self.call(0x1500)
        self.assertEqual(self.cpu.r[CPU.BX], 1)

    def test_the_other_registers_and_the_flags_are_left_alone_by_1500(self):
        self.machine()
        self.cpu.set_flag(flags.CF, True)
        self.call(0x1500)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.DX]), (0x1500, 0x3333))
        self.assertTrue(cpu.get_flag(flags.CF))
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))  # the call returned after the int

    def test_150b_tells_a_cd_rom_drive_from_the_others_and_says_mscdex_is_there(self):
        self.machine(self.TWO_CDROMS)
        for drive, answer in ((0, 0), (1, 0), (2, 0), (3, 1), (4, 1), (5, 0), (25, 0)):
            self.call(0x150B, cx=drive)
            self.assertEqual((self.cpu.r[CPU.AX], self.cpu.r[CPU.BX]), (answer, 0xADAD), f"drive {drive}")
            self.assertEqual(self.cpu.r[CPU.CX], drive)

    def test_1600_says_windows_is_not_running_with_or_without_a_cd_rom_drive(self):
        for text in (None, self.FLOPPIES_ONLY, self.TWO_CDROMS):
            self.machine(text)
            self.cpu.set_flag(flags.CF, True)
            self.call(0x1600, bx=0x5555)
            cpu = self.cpu
            self.assertEqual(cpu.get8(0), 0x00)  # AL=00h: not running (a value 80h would say so too)
            self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]),
                             (0x1600, 0x5555, 0x2222, 0x3333))
            self.assertTrue(cpu.get_flag(flags.CF))
            self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_4a11_says_there_is_no_disk_compression_with_or_without_a_cd_rom_drive(self):
        for text in (None, self.FLOPPIES_ONLY, self.TWO_CDROMS):
            self.machine(text)
            cpu = self.cpu
            cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, 0x5555
            cpu.set_flag(flags.CF, True)
            self.call(0x4A11, bx=0)
            self.assertEqual(cpu.r[CPU.AX], 0x4A11)  # a driver would have made it 0
            self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0, 0x2222, 0x3333))
            self.assertEqual((cpu.r[CPU.SI], cpu.r[CPU.DI]), (0x4444, 0x5555))
            self.assertTrue(cpu.get_flag(flags.CF))
            self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_4a11_with_the_carry_clear_stays_clear(self):
        self.machine()
        self.cpu.set_flag(flags.CF, False)
        self.call(0x4A11, bx=0)
        self.assertFalse(self.cpu.get_flag(flags.CF))

    def test_the_other_functions_of_the_disk_compression_get_no_answer_either(self):
        self.machine()
        for bx in (1, 2, 0x10, 0xFFFF):
            self.call(0x4A11, bx=bx)
            self.assertEqual((self.cpu.r[CPU.AX], self.cpu.r[CPU.BX]), (0x4A11, bx))

    def test_the_questions_next_to_4a11_are_still_not_known(self):
        self.machine()
        for ax in (0x4A10, 0x4A12, 0x4A13, 0x4A14, 0x4A15):
            with self.assertRaises(NotImplementedError, msg=hex(ax)):
                self.call(ax)

    def test_1600_is_the_only_one_of_the_windows_questions_that_is_answered(self):
        self.machine()
        for ax in (0x1601, 0x1605, 0x1606, 0x1607, 0x160A, 0x1680, 0x1681, 0x1683, 0x4680, 0x4A33):
            with self.assertRaises(NotImplementedError, msg=hex(ax)):
                self.call(ax)

    def test_150c_gives_the_version_2_10(self):
        self.machine()
        self.call(0x150C, bx=0)
        self.assertEqual(self.cpu.r[CPU.BX], 0x020A)

    def drive_letters(self, buffer=0x0200, fill=0xAA, size=8, es=0x3000):
        cpu = self.cpu
        for i in range(size):
            cpu.wb(es, (buffer + i) & 0xFFFF, fill)
        cpu.s[CPU.ES] = es
        self.call(0x150D, bx=buffer)
        return bytes(cpu.rb(es, (buffer + i) & 0xFFFF) for i in range(size))

    def test_150d_gives_the_number_of_each_cd_rom_drive_and_nothing_more(self):
        self.machine()
        self.assertEqual(self.drive_letters(), bytes([3]) + bytes([0xAA]) * 7)  # D:

    def test_150d_lists_the_drives_from_the_lowest_though_e_is_written_before_d(self):
        self.machine(self.TWO_CDROMS)
        self.assertEqual(self.drive_letters(), bytes([3, 4]) + bytes([0xAA]) * 6)

    def test_150d_agrees_with_what_1500_counts(self):
        self.machine(self.TWO_CDROMS)
        self.call(0x1500)
        count = self.cpu.r[CPU.BX]
        letters = self.drive_letters()
        self.assertEqual(count, 2)
        self.assertEqual(letters[count:], bytes([0xAA]) * (8 - count))

    def test_150d_leaves_the_registers_and_the_flags_alone(self):
        self.machine(self.TWO_CDROMS)
        cpu = self.cpu
        cpu.set_flag(flags.CF, True)
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, 0x5555
        self.drive_letters()
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x150D, 0x0200, 0x2222, 0x3333))
        self.assertEqual((cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.s[CPU.ES]), (0x4444, 0x5555, 0x3000))
        self.assertTrue(cpu.get_flag(flags.CF))

    def test_150d_may_go_round_the_end_of_the_segment(self):
        self.machine(self.TWO_CDROMS)
        self.assertEqual(self.drive_letters(buffer=0xFFFF, size=3), bytes([3, 4, 0xAA]))
        self.assertEqual(self.cpu.rb(0x3000, 0), 4)

    def test_150d_with_a_disc_or_without_is_the_same(self):
        self.machine("[disks.C]\ntype = 'hdd'\n[mounts]\nC = { type = 'tmpfs' }\n[disks.D]\ntype = 'cdrom'\n")
        self.assertEqual(self.drive_letters()[:2], bytes([3, 0xAA]))

    def test_150b_150c_and_150d_are_not_answered_where_there_is_no_mscdex(self):
        self.machine(self.FLOPPIES_ONLY)
        for ax in (0x150B, 0x150C, 0x150D):
            with self.assertRaises(NotImplementedError):
                self.call(ax)

    def test_the_other_services_are_not_done_yet(self):
        self.machine()
        for ax in (0x1501, 0x1510, 0x1601, 0x1680, 0x160A, 0x4300, 0x4680, 0x0000):
            with self.assertRaises(NotImplementedError):
                self.call(ax)


class TestSwitchCharacter(unittest.TestCase):
    """INT 21h AH=37h: the sign that begins an option on a command line."""

    def setUp(self):
        self.cpu = CPU()
        dos.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])

    def call(self, al, bx=0x3333, cx=0x2222, dx=0x1111):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x3700 | al, bx, cx, dx
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, 0x5555
        cpu.ip = 0
        cpu.step()

    def test_al_0_gives_the_slash_in_dl_and_nothing_else_changes(self):
        self.cpu.set_flag(flags.CF, True)
        self.call(0)
        cpu = self.cpu
        self.assertEqual(cpu.r[CPU.DX], 0x112F)  # DL is "/", DH as it was
        self.assertEqual(cpu.r[CPU.AX], 0x3700)  # AL = 0: all went well
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.SI], cpu.r[CPU.DI]), (0x3333, 0x2222, 0x4444, 0x5555))
        self.assertTrue(cpu.get_flag(flags.CF))
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_the_answer_does_not_depend_on_what_dl_held(self):
        for dl in (0x00, 0x2D, 0x2F, 0xFF):
            self.call(0, dx=0x5500 | dl)
            self.assertEqual(self.cpu.get8(2), 0x2F, dl)

    def test_the_other_subfunctions_stop_the_emulator_with_their_number(self):
        for al in (1, 2, 3, 0xFF):
            with self.assertRaisesRegex(NotImplementedError, f"AH=37 AL={al:02X}"):
                self.call(al)


class TestGetDate(unittest.TestCase):
    """INT 21h AH=2Ah: the date, from the clock of the host."""

    def setUp(self):
        self.cpu = CPU()
        dos.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        self.real_now = dos.now
        self.addCleanup(setattr, dos, "now", self.real_now)

    def on(self, year, month, day, hour=12, minute=0, second=0):
        """Make the clock of the host show this local date."""
        dos.now = lambda: time.mktime((year, month, day, hour, minute, second, 0, 0, -1))

    def call(self, ax=0x2A00, bx=0x3333, cx=0x2222, dx=0x1111):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.ip = 0
        cpu.step()
        return cpu.r[CPU.CX], cpu.get8(6), cpu.get8(2), cpu.get8(0)  # year, month, day, day of the week

    def test_the_year_month_day_and_day_of_the_week_come_in_cx_dh_dl_al(self):
        self.on(2026, 10, 22)  # a Thursday
        self.assertEqual(self.call(), (2026, 10, 22, 4))

    def test_every_day_of_the_week_has_its_number_with_sunday_0(self):
        for day, number in zip(range(18, 25), range(7)):  # 2026-10-18 is a Sunday
            self.on(2026, 10, day)
            self.assertEqual(self.call()[3], number, day)

    def test_the_first_and_the_last_day_of_a_year(self):
        self.on(2026, 1, 1)
        self.assertEqual(self.call(), (2026, 1, 1, 4))
        self.on(2026, 12, 31)
        self.assertEqual(self.call(), (2026, 12, 31, 4))

    def test_the_29th_of_february_of_a_leap_year(self):
        self.on(2024, 2, 29)
        self.assertEqual(self.call(), (2024, 2, 29, 4))

    def test_the_first_and_the_last_day_dos_has(self):
        self.on(1980, 1, 1)
        self.assertEqual(self.call(), (1980, 1, 1, 2))  # a Tuesday
        self.on(2099, 12, 31)
        self.assertEqual(self.call(), (2099, 12, 31, 4))

    def test_the_time_of_the_day_does_not_matter_to_the_date(self):
        for hour, minute, second in ((0, 0, 0), (12, 30, 15), (23, 59, 59)):
            self.on(2026, 10, 22, hour, minute, second)
            self.assertEqual(self.call(), (2026, 10, 22, 4), (hour, minute, second))

    def test_ah_and_the_other_registers_stay(self):
        self.on(2026, 10, 22)
        cpu = self.cpu
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, 0x5555
        cpu.set_flag(flags.CF, True)
        self.call(bx=0x3333)
        self.assertEqual(cpu.get8(4), 0x2A)  # AH
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.SI], cpu.r[CPU.DI]), (0x3333, 0x4444, 0x5555))
        self.assertTrue(cpu.get_flag(flags.CF))
        self.assertEqual(cpu.r[CPU.CX], 2026)  # a whole word, not a byte

    def test_it_is_the_local_date_not_the_utc_date(self):
        if not hasattr(time, "tzset"):
            self.skipTest("this system cannot change its time zone")
        old = os.environ.get("TZ")

        def restore():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.addCleanup(restore)
        os.environ["TZ"] = "Pacific/Auckland"  # twelve hours ahead of Greenwich or more: another day than UTC's
        time.tzset()
        dos.now = lambda: 1761091200.0  # 2025-10-22 00:00:00 UTC, which is the 22nd, 13:00 at Auckland: the same day
        self.assertEqual(self.call()[:3], (2025, 10, 22))
        dos.now = lambda: 1761091200.0 + 12 * 3600  # 12:00 UTC on the 22nd is the 23rd, 01:00 at Auckland
        self.assertEqual(self.call()[:3], (2025, 10, 23))


class TestGetTime(unittest.TestCase):
    """INT 21h AH=2Ch: the time of day, from the clock of the host."""

    def setUp(self):
        self.cpu = CPU()
        dos.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        self.real_now = dos.now
        self.addCleanup(setattr, dos, "now", self.real_now)

    def at(self, hour, minute, second, fraction=0.0):
        """Make the clock of the host show this local time on some day."""
        dos.now = lambda: time.mktime((2026, 10, 22, hour, minute, second, 0, 0, -1)) + fraction

    def call(self, ax=0x2C00, bx=0x3333, cx=0x2222, dx=0x1111):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.ip = 0
        cpu.step()
        return cpu.get8(5), cpu.get8(1), cpu.get8(6), cpu.get8(2)  # CH, CL, DH, DL

    def test_the_hour_minute_second_and_hundredths_come_in_ch_cl_dh_dl(self):
        self.at(13, 45, 27, 0.565)
        self.assertEqual(self.call(), (13, 45, 27, 56))

    def test_midnight_and_the_last_moment_of_the_day(self):
        self.at(0, 0, 0)
        self.assertEqual(self.call(), (0, 0, 0, 0))
        self.at(23, 59, 59, 0.999)
        self.assertEqual(self.call(), (23, 59, 59, 99))  # never 100

    def test_the_hundredths_are_cut_not_rounded(self):
        for fraction, hundredths in ((0.0, 0), (0.007, 0), (0.013, 1), (0.019, 1), (0.5, 50), (0.994, 99)):
            self.at(8, 30, 15, fraction)
            self.assertEqual(self.call()[3], hundredths, fraction)

    def test_it_is_the_local_time_not_the_utc_time(self):
        if not hasattr(time, "tzset"):
            self.skipTest("this system cannot change its time zone")
        old = os.environ.get("TZ")

        def restore():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

        self.addCleanup(restore)
        os.environ["TZ"] = "XXX-3"  # three hours ahead of UTC, wherever the test runs
        time.tzset()
        self.at(7, 5, 3)
        self.assertEqual(self.call()[:3], (7, 5, 3))

    def test_time_goes_on_between_two_calls(self):
        self.at(10, 0, 0)
        first = self.call()
        self.at(10, 0, 1, 0.25)
        self.assertEqual((first, self.call()), ((10, 0, 0, 0), (10, 0, 1, 25)))

    def test_the_other_registers_and_the_flags_stay(self):
        self.at(1, 2, 3, 0.04)
        self.cpu.set_flag(flags.CF, True)
        self.cpu.set_flag(flags.ZF, True)
        self.call(ax=0x2C77)
        cpu = self.cpu
        self.assertEqual((cpu.get8(0), cpu.r[CPU.BX]), (0x77, 0x3333))
        self.assertTrue(cpu.get_flag(flags.CF) and cpu.get_flag(flags.ZF))
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_the_real_clock_gives_a_time_that_is_there(self):
        dos.now = self.real_now
        hour, minute, second, hundredths = self.call()
        self.assertTrue(0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60 and 0 <= hundredths < 100)
        local = time.localtime()
        self.assertLessEqual(abs(hour * 60 + minute - (local.tm_hour * 60 + local.tm_min)) % 1440, 1)

    def test_a_leap_second_is_shown_as_the_last_second_of_the_minute(self):
        from unittest import mock
        leap = time.struct_time((2016, 12, 31, 23, 59, 60, 5, 366, 0))  # some systems have a second 60
        with mock.patch("dosloth.dos.time.localtime", return_value=leap):
            self.assertEqual(self.call()[:3], (23, 59, 59))


class TestReadFile(FileFunctionsTest):
    """INT 21h AH=3Fh: read from a file handle into memory at DS:DX; AX is how many bytes came."""

    BUFFER = 0x0100  # where the bytes go; the segment is the one of DS, 2000h

    def open(self, name, mode=0):
        self.call(0x3D00 | mode, name)
        self.assertIsNone(self.failed())
        return self.cpu.r[CPU.AX]

    def read(self, handle, count, offset=None):
        """Read and give back (the error or None, how many bytes came)."""
        self.call(0x3F00, bx=handle, cx=count, dx=self.BUFFER if offset is None else offset)
        return self.failed(), self.cpu.r[CPU.AX]

    def memory(self, offset, count):
        return bytes(self.cpu.rb(0x2000, (offset + i) & 0xFFFF) for i in range(count))

    def test_the_bytes_come_to_ds_dx_and_ax_says_how_many(self):
        handle = self.open("NOTES.TXT")
        self.cpu.set_flag(flags.CF, True)
        self.assertEqual(self.read(handle, 10), (None, 10))
        self.assertEqual(self.memory(self.BUFFER, 10), b"some notes")

    def test_only_as_many_bytes_as_there_are_come_and_then_none_which_is_no_error(self):
        handle = self.open("NOTES.TXT")
        self.assertEqual(self.read(handle, 100), (None, 10))
        self.assertEqual(self.read(handle, 100), (None, 0))
        self.assertEqual(self.read(handle, 100), (None, 0))

    def test_the_end_of_the_file_leaves_the_buffer_as_it_was(self):
        handle = self.open("NOTES.TXT")
        self.read(handle, 10)
        self.cpu.mem[0x20100:0x20104] = b"keep"
        self.read(handle, 4)
        self.assertEqual(self.memory(self.BUFFER, 4), b"keep")

    def test_each_read_goes_on_where_the_one_before_stopped(self):
        handle = self.open("NOTES.TXT")
        pieces = []
        for count in (4, 4, 4, 4):
            error, got = self.read(handle, count)
            pieces.append(self.memory(self.BUFFER, got))
        self.assertEqual(pieces, [b"some", b" not", b"es", b""])

    def test_nothing_is_read_for_a_count_of_0_and_the_buffer_is_left_alone(self):
        handle = self.open("NOTES.TXT")
        self.cpu.mem[0x20100:0x20102] = b"ab"
        self.assertEqual(self.read(handle, 0), (None, 0))
        self.assertEqual(self.memory(self.BUFFER, 2), b"ab")
        self.assertEqual(self.read(handle, 4), (None, 4))  # and the position did not move
        self.assertEqual(self.memory(self.BUFFER, 4), b"some")

    def test_the_bytes_before_and_after_the_buffer_are_not_touched(self):
        handle = self.open("NOTES.TXT")
        self.cpu.mem[0x200FF] = ord("[")
        self.cpu.mem[0x20103] = ord(")")
        self.read(handle, 3)
        self.assertEqual(self.memory(0x00FF, 5), b"[som)")  # the three bytes are between the two that were there

    def test_the_buffer_may_run_over_the_end_of_the_segment(self):
        handle = self.open("NOTES.TXT")
        self.assertEqual(self.read(handle, 4, offset=0xFFFE), (None, 4))
        self.assertEqual(self.memory(0xFFFE, 2), b"so")
        self.assertEqual(self.memory(0x0000, 2), b"me")

    def test_a_file_made_by_3ch_and_written_is_read_back_after_going_to_its_start(self):
        self.call(0x3C00, "D:\\F")
        handle = self.cpu.r[CPU.AX]
        self.call(0x4000, "hello", bx=handle, cx=5)
        self.assertEqual(self.read(handle, 5), (None, 0))  # the position is at the end
        self.state.handles.get(handle).file.seek(0)
        self.assertEqual(self.read(handle, 5), (None, 5))
        self.assertEqual(self.memory(self.BUFFER, 5), b"hello")

    def test_a_file_on_the_memory_disk(self):
        self.call(0x3C00, "D:\\F")
        handle = self.cpu.r[CPU.AX]
        self.call(0x4000, "abcdef", bx=handle, cx=6)
        self.call(0x3E00, bx=handle)
        handle = self.open("D:\\F")
        self.assertEqual(self.read(handle, 4), (None, 4))
        self.assertEqual(self.memory(self.BUFFER, 4), b"abcd")

    def test_a_long_file_comes_in_pieces_and_a_count_of_65535_is_possible(self):
        data = bytes(i * 7 & 0xFF for i in range(70000))
        self.call(0x3C00, "D:\\BIG")
        handle = self.cpu.r[CPU.AX]
        self.state.handles.get(handle).file.write(data)
        self.state.handles.get(handle).file.seek(0)
        self.assertEqual(self.read(handle, 0xFFFF, offset=0), (None, 0xFFFF))
        self.assertEqual(self.memory(0, 0xFFFF), data[:0xFFFF])
        self.assertEqual(self.read(handle, 0xFFFF, offset=0), (None, 70000 - 0xFFFF))
        self.assertEqual(self.memory(0, 70000 - 0xFFFF), data[0xFFFF:])

    def test_two_handles_of_one_file_have_a_position_each(self):
        first, second = self.open("NOTES.TXT"), self.open("NOTES.TXT")
        self.read(first, 4)
        self.assertEqual(self.read(second, 10), (None, 10))
        self.assertEqual(self.memory(self.BUFFER, 10), b"some notes")

    def test_reading_does_not_change_the_file(self):
        handle = self.open("NOTES.TXT", 2)
        self.read(handle, 10)
        self.call(0x3E00, bx=handle)
        self.assertEqual((self.base / "NOTES.TXT").read_bytes(), b"some notes")

    def test_a_file_opened_for_reading_and_writing_can_be_read(self):
        self.assertEqual(self.read(self.open("NOTES.TXT", 2), 4), (None, 4))

    def test_a_file_opened_to_be_written_only_cannot_be_read(self):
        handle = self.open("NOTES.TXT", 1)
        self.cpu.mem[0x20100:0x20104] = b"keep"
        self.assertEqual(self.read(handle, 4), (5, 5))
        self.assertEqual(self.memory(self.BUFFER, 4), b"keep")

    def test_the_handles_that_are_not_open_are_error_6(self):
        handle = self.open("NOTES.TXT")
        self.call(0x3E00, bx=handle)
        for number in (handle, 6, 19, 20, 100, 0xFFFF):
            self.assertEqual(self.read(number, 4), (6, 6), number)

    def test_the_keyboard_and_the_devices_are_not_done_yet(self):
        for number in (0, 1, 2, 3, 4):
            with self.assertRaises(NotImplementedError, msg=number):
                self.read(number, 4)

    def test_the_other_registers_are_left_alone_and_the_flags_too(self):
        handle = self.open("NOTES.TXT")
        self.cpu.set_flag(flags.ZF, True)
        self.call(0x3F00, bx=handle, cx=4, dx=self.BUFFER)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (handle, 4, self.BUFFER))
        self.assertTrue(cpu.get_flag(flags.ZF))

    def test_the_bytes_go_through_the_video_window_when_the_buffer_is_in_it(self):
        vga.install(self.cpu)
        handle = self.open("NOTES.TXT")
        self.cpu.s[CPU.DS] = 0xA000
        self.call(0x3F00, bx=handle, cx=4, dx=0x0010)
        self.assertEqual(self.cpu.r[CPU.AX], 4)
        for plane in self.cpu.video.planes:  # the registers as they start: every plane takes the byte
            self.assertEqual(bytes(plane[0x10:0x14]), b"some")
        self.assertEqual(self.cpu.mem[0xA0010:0xA0014], bytes(4))  # and the plain memory behind is not used

    def test_a_file_that_cannot_be_read_is_an_access_error_and_leaves_the_buffer_alone(self):
        class Broken:
            def read(self, count):
                raise OSError(5, "input/output error")

            def close(self):
                pass

        handle = self.state.handles.allocate(OpenFile(Broken(), "C:\\BROKEN"))
        self.cpu.mem[0x20100:0x20104] = b"keep"
        self.cpu.set_flag(flags.CF, False)
        self.assertEqual(self.read(handle, 4), (5, 5))
        self.assertEqual(self.memory(self.BUFFER, 4), b"keep")

    def test_an_error_leaves_the_position_alone(self):
        handle = self.open("NOTES.TXT", 1)
        self.read(handle, 4)
        self.state.handles.get(handle).file.seek(2)
        self.read(handle, 4)
        self.assertEqual(self.state.handles.get(handle).file.tell(), 2)


class TestOpenModesAndWriting(FileFunctionsTest):
    """What the way a file was opened lets a program do with it."""

    def open(self, name, mode):
        self.call(0x3D00 | mode, name)
        return self.cpu.r[CPU.AX]

    def test_the_handle_remembers_what_it_may_do(self):
        for mode, readable, writable in ((0, True, False), (1, False, True), (2, True, True)):
            entry = self.state.handles.get(self.open("NOTES.TXT", mode))
            self.assertEqual((entry.readable, entry.writable), (readable, writable), mode)

    def test_a_file_that_was_made_may_be_read_and_written(self):
        self.call(0x3C00, "NEW.TXT")
        entry = self.state.handles.get(self.cpu.r[CPU.AX])
        self.assertEqual((entry.readable, entry.writable), (True, True))

    def test_a_file_opened_to_be_read_only_refuses_a_write_and_stays_as_it_was(self):
        handle = self.open("NOTES.TXT", 0)
        self.call(0x4000, "XXXX", bx=handle, cx=4)
        self.assertEqual(self.failed(), 5)
        self.call(0x4000, "XXXX", bx=handle, cx=0)  # even a count of 0, which would cut the file off
        self.assertEqual(self.failed(), 5)
        self.call(0x3E00, bx=handle)
        self.assertEqual((self.base / "NOTES.TXT").read_bytes(), b"some notes")

    def test_the_handle_decides_not_the_file_object(self):
        # a file object that would take the write, on a handle that was not opened to write
        file = self.volumes.filesystems[3].create(["F"])
        handle = self.state.handles.allocate(OpenFile(file, "D:\\F", readable=True, writable=False))
        self.call(0x4000, "data", bx=handle, cx=4)
        self.assertEqual(self.failed(), 5)
        self.assertEqual(self.volumes.filesystems[3].stat(["F"]).size, 0)  # nothing went in
        self.call(0x4000, "data", bx=handle, cx=0)
        self.assertEqual(self.failed(), 5)

    def test_a_file_opened_to_be_written_may_be_written(self):
        handle = self.open("NOTES.TXT", 1)
        self.call(0x4000, "XXXX", bx=handle, cx=4)
        self.assertIsNone(self.failed())
        self.call(0x3E00, bx=handle)
        self.assertEqual((self.base / "NOTES.TXT").read_bytes(), b"XXXX notes")


class TestLeadByteTable(unittest.TestCase):
    """INT 21h AH=63h AL=00h: the table of lead bytes of two-byte characters. This machine has none."""

    def setUp(self):
        self.cpu = CPU()
        dos.install(self.cpu)
        self.cpu.s[CPU.CS], self.cpu.s[CPU.DS], self.cpu.s[CPU.ES] = 0x1000, 0x2000, 0x3000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])

    def call(self, ax=0x6300):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, 0x1111, 0x2222, 0x3333
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, 0x5555
        cpu.ip = 0
        cpu.step()

    def test_the_answer_is_that_there_is_no_such_function_with_the_carry_flag_set(self):
        self.cpu.set_flag(flags.CF, False)
        self.call()
        self.assertTrue(self.cpu.get_flag(flags.CF))
        self.assertEqual(self.cpu.r[CPU.AX], 1)

    def test_the_other_registers_and_flags_stay_and_so_does_the_pointer_the_caller_would_have_got(self):
        self.cpu.set_flag(flags.ZF, True)
        self.cpu.set_flag(flags.SF, False)
        self.call()
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]),
                         (0x1111, 0x2222, 0x3333, 0x4444, 0x5555))
        self.assertEqual((cpu.s[CPU.DS], cpu.s[CPU.ES]), (0x2000, 0x3000))
        self.assertTrue(cpu.get_flag(flags.ZF))
        self.assertFalse(cpu.get_flag(flags.SF))
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_the_old_ah_al_do_not_matter_but_only_al_0_is_done(self):
        self.call(0x6300)
        for al in (0x01, 0x02, 0x03, 0x80, 0xFF):
            with self.assertRaises(NotImplementedError, msg=hex(al)):
                self.call(0x6300 | al)

    def test_the_idiom_of_the_game_ends_with_a_null_pointer_and_so_no_table(self):
        # mov ax,6300h ; int 21h ; jae +6 ; xor si,si ; xor ax,ax ; mov ds,ax ; (there) mov di,ds
        code = bytes([0xB8, 0x00, 0x63, 0xCD, 0x21, 0x73, 0x06, 0x31, 0xF6, 0x31, 0xC0, 0x8E, 0xD8, 0x8C, 0xDF])
        self.cpu.mem[0x10000:0x10000 + len(code)] = code
        self.cpu.ip = 0
        for _ in range(7):  # mov, int, jae (not taken), xor, xor, mov ds, mov di
            self.cpu.step()
        self.assertEqual((self.cpu.r[CPU.SI], self.cpu.r[CPU.DI], self.cpu.s[CPU.DS]), (0, 0, 0))


class TestDiskTransferArea(unittest.TestCase):
    """INT 21h AH=1Ah (set the DTA to DS:DX) and AH=2Fh (get it into ES:BX)."""

    def setUp(self):
        self.cpu = CPU()
        self.state = dos.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])

    def call(self, ax, ds=0x2000, dx=0x1111, es=0x3000, bx=0x2222, cpu=None):
        cpu = cpu or self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.DX], cpu.r[CPU.BX], cpu.r[CPU.CX] = ax, dx, bx, 0x4444
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x5555, 0x6666
        cpu.s[CPU.DS], cpu.s[CPU.ES] = ds, es
        cpu.ip = 0
        cpu.step()

    def get(self):
        self.call(0x2F00)
        return self.cpu.s[CPU.ES], self.cpu.r[CPU.BX]

    def test_it_starts_at_the_end_of_the_psp_where_the_command_line_is(self):
        self.assertEqual(self.get(), (0x1000, 0x0080))

    def test_what_was_set_is_what_comes_back(self):
        for segment, offset in ((0x2000, 0x1111), (0x0000, 0x0000), (0xFFFF, 0xFFFF), (0x1234, 0x0080),
                                (0x8000, 0x0100)):
            self.call(0x1A00, ds=segment, dx=offset)
            self.assertEqual(self.get(), (segment, offset))

    def test_a_second_set_takes_the_place_of_the_first(self):
        self.call(0x1A00, ds=0x2000, dx=0x0100)
        self.call(0x1A00, ds=0x3000, dx=0x0200)
        self.assertEqual(self.get(), (0x3000, 0x0200))

    def test_getting_it_does_not_change_it_or_the_registers_that_are_not_its_answer(self):
        self.call(0x1A00, ds=0x2000, dx=0x0100)
        self.get()
        self.assertEqual(self.get(), (0x2000, 0x0100))
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.DX], cpu.r[CPU.CX], cpu.r[CPU.SI], cpu.r[CPU.DI]), (0x1111, 0x4444, 0x5555, 0x6666))
        self.assertEqual(cpu.s[CPU.DS], 0x2000)  # DS is what the call put in it, not the DTA

    def test_setting_it_changes_no_register_and_no_flag(self):
        self.cpu.flags = 0x0FD7
        self.call(0x1A00, ds=0x2000, dx=0x0100)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]),
                         (0x1A00, 0x2222, 0x4444, 0x0100, 0x5555, 0x6666))
        self.assertEqual((cpu.s[CPU.DS], cpu.s[CPU.ES], cpu.flags), (0x2000, 0x3000, 0x0FD7))

    def test_getting_it_changes_no_flag(self):
        self.cpu.flags = 0x0FD7
        self.get()
        self.assertEqual(self.cpu.flags, 0x0FD7)

    def test_it_is_the_ds_of_the_call_that_counts_not_the_es(self):
        self.call(0x1A00, ds=0x2000, dx=0x0100, es=0x7000)
        self.assertEqual(self.get(), (0x2000, 0x0100))

    def test_each_machine_has_its_own(self):
        other = CPU()
        dos.install(other)
        other.s[CPU.CS] = 0x1000
        other.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        self.call(0x1A00, ds=0x2000, dx=0x0100)
        self.call(0x2F00, cpu=other)
        self.assertEqual((other.s[CPU.ES], other.r[CPU.BX]), (0x1000, 0x0080))

    def test_the_state_holds_it_as_a_pair(self):
        self.call(0x1A00, ds=0x2000, dx=0x0100)
        self.assertEqual(self.state.dta, (0x2000, 0x0100))


class TestFindFiles(FileFunctionsTest):
    """INT 21h AH=4Eh (find the first file that fits a mask) and AH=4Fh (the next one)."""

    DTA = (0x5000, 0x0100)

    def setUp(self):
        super().setUp()
        self.state.dta = self.DTA
        self.when = time.mktime((1997, 6, 20, 13, 45, 58, 0, 0, -1))
        for name, data in (("AAA.DAT", b"12345"), ("BBB.DAT", b""), ("CCC.TXT", b"abc"), ("readme", b"r")):
            (self.base / name).write_bytes(data)
            os.utime(self.base / name, (self.when, self.when))

    def first(self, mask, attributes=0):
        self.call(0x4E00, mask, cx=attributes)
        return self.failed()

    def next(self):
        self.call(0x4F00)
        return self.failed()

    def memory(self):
        return bytes(self.cpu.mem[0x50100:0x50100 + 43])

    def found(self):
        """(name, size, attributes) of what the DTA holds."""
        data = self.memory()
        return data[0x1E:].split(b"\0")[0].decode(), int.from_bytes(data[0x1A:0x1E], "little"), data[0x15]

    def all(self, mask, attributes=0):
        """The names of every file that fits, going on with AH=4Fh until there are no more."""
        names = []
        error = self.first(mask, attributes)
        while error is None:
            names.append(self.found()[0])
            error = self.next()
        self.assertEqual(error, 18)
        return names

    def test_the_first_file_is_in_the_dta_with_its_name_size_attributes_time_and_date(self):
        self.cpu.set_flag(flags.CF, True)
        self.assertIsNone(self.first("aaa.dat"))
        self.assertEqual(self.found(), ("AAA.DAT", 5, 0x20))
        data = self.memory()
        self.assertEqual(int.from_bytes(data[0x16:0x18], "little"), 13 << 11 | 45 << 5 | 29)
        self.assertEqual(int.from_bytes(data[0x18:0x1A], "little"), (1997 - 1980) << 9 | 6 << 5 | 20)

    def test_every_file_that_fits_comes_once_in_the_order_of_the_directory(self):
        self.assertEqual(self.all("*.*"), ["AAA.DAT", "BBB.DAT", "CCC.TXT", "NOTES.TXT", "README"])
        self.assertEqual(self.all("*.DAT"), ["AAA.DAT", "BBB.DAT"])
        self.assertEqual(self.all("*"), ["AAA.DAT", "BBB.DAT", "CCC.TXT", "NOTES.TXT", "README"])
        self.assertEqual(self.all("?B?.D*"), ["BBB.DAT"])
        self.assertEqual(self.all("README"), ["README"])

    def test_a_directory_comes_only_when_the_attributes_say_so(self):
        self.assertNotIn("GAMES", self.all("*.*"))
        self.assertIn("GAMES", self.all("*.*", 0x10))
        self.first("GAMES", 0x10)
        self.assertEqual(self.found(), ("GAMES", 0, 0x10))
        self.assertEqual(self.first("GAMES", 0), 2)

    def test_no_file_that_fits_is_error_2_and_the_dta_is_not_touched(self):
        self.cpu.mem[0x50100:0x50104] = b"KEEP"
        self.assertEqual(self.first("XCOM3.CFG"), 2)  # as xcomapoc.exe asks for it
        self.assertEqual(self.cpu.r[CPU.AX], 2)
        self.assertEqual(self.cpu.mem[0x50100:0x50104], b"KEEP")

    def test_after_the_last_file_every_call_gives_error_18(self):
        self.first("AAA.DAT")
        self.assertEqual(self.next(), 18)
        self.assertEqual(self.cpu.r[CPU.AX], 18)
        self.assertEqual(self.next(), 18)

    def test_the_dta_keeps_the_last_file_after_the_error_18(self):
        self.first("AAA.DAT")
        self.next()
        self.assertEqual(self.found()[0], "AAA.DAT")

    def test_a_search_in_a_directory_by_a_relative_path_an_absolute_path_and_a_drive(self):
        (self.base / "GAMES" / "G1.DAT").write_bytes(b"g")
        self.assertEqual(self.all("games\\*.dat"), ["G1.DAT"])
        self.assertEqual(self.all("C:\\GAMES\\*.*"), ["G1.DAT"])
        self.assertEqual(self.all("\\games\\*"), ["G1.DAT"])
        self.assertEqual(self.all("C:\\*.TXT"), ["CCC.TXT", "NOTES.TXT"])
        self.volumes.chdir("C:\\GAMES")
        self.assertEqual(self.all("*.DAT"), ["G1.DAT"])
        self.assertEqual(self.all("..\\*.TXT"), ["CCC.TXT", "NOTES.TXT"])
        self.assertEqual(self.all("C:*.DAT"), ["G1.DAT"])

    def test_the_memory_disk_too(self):
        for name in ("B2.TXT", "A1.TXT"):
            self.volumes.filesystems[3].create([name]).close()
        self.assertEqual(self.all("D:*.TXT"), ["A1.TXT", "B2.TXT"])
        self.assertEqual(self.all("D:\\*.*"), ["A1.TXT", "B2.TXT"])

    def test_an_empty_directory_has_nothing_and_that_is_error_2(self):
        self.assertEqual(self.first("GAMES\\*.*"), 2)

    def test_a_directory_that_is_not_there_is_error_3_and_so_is_a_file_used_as_a_directory(self):
        self.assertEqual(self.first("NOPE\\*.*"), 3)
        self.assertEqual(self.first("NOTES.TXT\\*.*"), 3)
        self.assertEqual(self.first("GAMES\\NOPE\\*.*"), 3)

    def test_no_mask_is_error_3(self):
        for path in ("C:\\", "C:", "GAMES\\", "\\", "C:\\GAMES\\"):
            self.assertEqual(self.first(path), 3, path)
        self.assertEqual(self.first(""), 3)

    def test_a_path_with_a_forward_slash_or_a_drive_or_dots_at_its_end_has_no_mask_either(self):
        for path in ("GAMES/", "C:/", "/", ".", "..", "GAMES\\..", "GAMES\\.", "C:."):
            self.assertEqual(self.first(path), 3, path)

    def test_a_drive_letter_alone_means_no_mask_even_when_the_current_directory_is_not_the_root(self):
        self.volumes.chdir("C:\\GAMES")
        self.assertEqual(self.first("C:"), 3)
        self.assertEqual(self.first("D:"), 3)

    def test_the_drive_errors(self):
        self.assertEqual(self.first("Z:*.*"), 15)
        self.assertEqual(self.first("A:*.*"), 21)
        self.assertEqual(self.first("..\\*.*"), 3)

    def test_the_mask_is_found_whatever_its_case_and_the_names_come_in_capitals(self):
        self.assertEqual(self.all("ReadMe"), ["README"])
        self.assertEqual(self.all("notes.*"), ["NOTES.TXT"])

    def test_a_file_whose_name_dos_cannot_have_is_not_found(self):
        (self.base / "a_very_long_file_name.dat").write_bytes(b"x")
        self.assertNotIn("A_VERY_LONG_FILE_NAME.DAT", self.all("*.*"))
        self.assertEqual(self.first("A_VERY*"), 2)

    def test_the_search_is_a_snapshot_so_files_made_after_it_began_are_not_in_it(self):
        self.first("*.DAT")  # AAA.DAT comes; BBB.DAT is still to come
        (self.base / "ZZZ.DAT").write_bytes(b"z")
        names = [self.found()[0]]
        while self.next() is None:
            names.append(self.found()[0])
        self.assertEqual(names, ["AAA.DAT", "BBB.DAT"])

    def test_the_number_of_the_search_in_the_dta_is_what_lets_4fh_go_on_wherever_the_dta_is_now(self):
        self.first("*.DAT")
        self.cpu.mem[0x60200:0x60200 + 43] = self.memory()  # the program copies the DTA to another place
        self.state.dta = (0x6000, 0x0200)
        self.assertIsNone(self.next())
        self.assertEqual(bytes(self.cpu.mem[0x60200 + 0x1E:0x60200 + 0x25]), b"BBB.DAT")

    def test_4fh_with_a_dta_that_holds_no_search_is_error_18(self):
        for garbage in (bytes(43), bytes([0xFF] * 43), bytes(range(43))):
            self.cpu.mem[0x50100:0x50100 + 43] = garbage
            self.assertEqual(self.next(), 18)

    def test_two_searches_at_once_each_with_a_dta_of_its_own(self):
        self.first("*.DAT")
        one = self.memory()
        self.state.dta = (0x6000, 0x0000)
        self.first("*.TXT")
        two = bytes(self.cpu.mem[0x60000:0x60000 + 43])
        self.assertEqual(two[0x1E:0x25], b"CCC.TXT")
        self.state.dta = self.DTA
        self.cpu.mem[0x50100:0x50100 + 43] = one
        self.next()
        self.assertEqual(self.found()[0], "BBB.DAT")
        self.state.dta = (0x6000, 0x0000)
        self.cpu.mem[0x60000:0x60000 + 43] = two
        self.next()
        self.assertEqual(bytes(self.cpu.mem[0x60000 + 0x1E:0x60000 + 0x27]), b"NOTES.TXT")

    def test_a_search_of_one_machine_is_not_known_to_another(self):
        self.first("*.DAT")
        other = CPU()
        other_state = dos.install(other)
        other_state.dta = self.DTA
        other.s[CPU.CS], other.s[CPU.DS] = 0x1000, 0x2000
        other.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        other.mem[0x50100:0x50100 + 43] = self.memory()  # the DTA of the search that was begun here
        other.r[CPU.AX] = 0x4F00
        other.step()
        self.assertTrue(other.get_flag(flags.CF))
        self.assertEqual(other.r[CPU.AX], 18)

    def test_the_dta_that_is_set_is_the_one_written_to_and_the_old_one_is_left_alone(self):
        self.state.dta = (0x6000, 0x0000)
        self.cpu.mem[0x50100:0x50104] = b"KEEP"
        self.first("AAA.DAT")
        self.assertEqual(self.cpu.mem[0x50100:0x50104], b"KEEP")
        self.assertEqual(bytes(self.cpu.mem[0x6001E:0x60025]), b"AAA.DAT")

    def test_the_dta_may_run_over_the_end_of_its_segment(self):
        self.state.dta = (0x5000, 0xFFF0)
        self.first("*.DAT")
        data = bytes(self.cpu.rb(0x5000, (0xFFF0 + i) & 0xFFFF) for i in range(43))
        self.assertEqual(data[0x1E:0x25], b"AAA.DAT")
        self.assertIsNone(self.next())
        data = bytes(self.cpu.rb(0x5000, (0xFFF0 + i) & 0xFFFF) for i in range(43))
        self.assertEqual(data[0x1E:0x25], b"BBB.DAT")

    def test_the_default_dta_is_in_the_psp(self):
        self.state.dta = (0x1000, 0x0080)
        self.first("AAA.DAT")
        self.assertEqual(bytes(self.cpu.mem[0x10080 + 0x1E:0x10080 + 0x25]), b"AAA.DAT")

    def test_the_registers_are_left_alone_and_the_flags_but_the_carry_too(self):
        self.cpu.set_flag(flags.ZF, True)
        self.call(0x4E00, "AAA.DAT", cx=0x0007, bx=0x1234, dx=0x0100)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x4E00, 0x1234, 7, 0x0100))
        self.assertTrue(cpu.get_flag(flags.ZF))
        self.assertFalse(cpu.get_flag(flags.CF))

    def test_the_mask_may_run_over_the_end_of_the_segment_it_is_in(self):
        self.call(0x4E00, "AAA.DAT", dx=0xFFFA)
        self.assertIsNone(self.failed())
        self.assertEqual(self.found()[0], "AAA.DAT")

    def test_a_mask_with_no_end_is_error_3(self):
        for i in range(300):
            self.cpu.wb(0x2000, 0x0100 + i, ord("A"))
        self.call(0x4E00, None)
        self.assertEqual(self.failed(), 3)

    def test_the_label_search_finds_nothing(self):
        self.assertEqual(self.first("*.*", 0x08), 2)

    def test_a_search_for_everything_that_has_all_the_kinds_asked_for_finds_directories_too(self):
        self.assertEqual(self.all("*.*", 0x37)[0:2], ["AAA.DAT", "BBB.DAT"])
        self.assertIn("GAMES", self.all("*.*", 0x37))

    def test_a_long_directory_is_gone_through_to_the_end(self):
        for number in range(300):
            self.volumes.filesystems[3].create([f"F{number:03d}.DAT"]).close()
        names = self.all("D:*.DAT")
        self.assertEqual(len(names), 300)
        self.assertEqual((names[0], names[-1]), ("F000.DAT", "F299.DAT"))
        self.assertEqual(names, sorted(names))


class TestDirectConsole(unittest.TestCase):
    """INT 21h AH=06h: print the character in DL, or if DL is FFh, take a key if there is one, without waiting."""

    def setUp(self):
        self.cpu = CPU()
        self.keyboard = bios.install(self.cpu)
        self.state = dos.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])

    def call(self, dl, dh=0x00, al=0x77, cpu=None):
        cpu = cpu or self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.DX] = 0x0600 | al, dh << 8 | dl
        cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x1111, 0x2222, 0x3333, 0x4444
        cpu.ip = 0
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cpu.step()
        return out.getvalue(), err.getvalue()

    def press(self, scan, ascii_code):
        self.keyboard.press(scan, ascii_code)

    def test_a_character_goes_to_the_output_as_it_is_and_al_is_the_character(self):
        out, err = self.call(ord("A"))
        self.assertEqual((out, err), ("A", ""))
        self.assertEqual(self.cpu.get8(0), ord("A"))

    def test_the_controls_are_not_acted_on_or_changed(self):
        for code in (0x0D, 0x0A, 0x08, 0x07, 0x09, 0x1B, 0x00, 0x03, 0x1A):
            out, _ = self.call(code)
            self.assertEqual(out, chr(code), hex(code))

    def test_the_codes_over_127_are_the_characters_of_the_pc(self):
        for code, character in ((0x80, "Ç"), (0xB0, "░"), (0xC4, "─"), (0xDB, "█"), (0xE1, "ß"), (0xFE, "■"),
                                (0xFD, "²"), (0x9B, "¢")):
            self.assertEqual(self.call(code)[0], character, hex(code))

    def test_every_code_but_ff_is_printed_and_ff_is_not(self):
        for code in range(255):
            out, _ = self.call(code)
            self.assertEqual(out, bytes([code]).decode("cp437"), code)
        self.assertEqual(self.call(0xFF)[0], "")

    def test_the_high_byte_of_dx_does_not_matter(self):
        self.assertEqual(self.call(ord("Q"), dh=0xFF)[0], "Q")
        self.assertEqual(self.call(0xFF, dh=0x41)[0], "")

    def test_printing_changes_no_register_but_al_and_no_flag(self):
        self.cpu.flags = 0x0FD7
        self.call(ord("x"))
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.SI], cpu.r[CPU.DI]), (0x1111, 0x2222, 0x3333, 0x4444))
        self.assertEqual((cpu.get8(4), cpu.r[CPU.DX]), (0x06, ord("x")))
        self.assertEqual(cpu.flags, 0x0FD7)

    def test_the_output_is_flushed_at_once_so_that_a_prompt_is_seen(self):
        class Spy(io.StringIO):
            flushed = 0

            def flush(self):
                self.flushed += 1

        spy = Spy()
        with contextlib.redirect_stdout(spy):
            self.cpu.r[CPU.AX], self.cpu.r[CPU.DX] = 0x0600, ord("z")
            self.cpu.ip = 0
            self.cpu.step()
        self.assertEqual((spy.getvalue(), spy.flushed), ("z", 1))

    def test_with_no_key_pressed_zf_is_set_and_al_is_0_and_nothing_is_printed(self):
        self.cpu.set_flag(flags.ZF, False)
        out, err = self.call(0xFF, al=0x77)
        self.assertEqual((out, err), ("", ""))
        self.assertTrue(self.cpu.get_flag(flags.ZF))
        self.assertEqual(self.cpu.get8(0), 0)

    def test_with_a_key_pressed_zf_is_clear_and_al_is_its_character_and_the_key_is_taken(self):
        self.press(0x1E, 0x61)
        self.cpu.set_flag(flags.ZF, True)
        out, _ = self.call(0xFF)
        self.assertEqual(out, "")
        self.assertFalse(self.cpu.get_flag(flags.ZF))
        self.assertEqual(self.cpu.get8(0), 0x61)
        self.assertEqual(len(self.keyboard.buffer), 0)
        self.call(0xFF)
        self.assertTrue(self.cpu.get_flag(flags.ZF))

    def test_the_keys_come_in_the_order_they_were_pressed(self):
        for scan, ascii_code in ((0x1E, 0x61), (0x30, 0x62), (0x1C, 0x0D)):
            self.press(scan, ascii_code)
        got = []
        for _ in range(4):
            self.call(0xFF)
            got.append(None if self.cpu.get_flag(flags.ZF) else self.cpu.get8(0))
        self.assertEqual(got, [0x61, 0x62, 0x0D, None])

    def test_a_key_with_no_character_comes_as_0_and_then_its_scan_code(self):
        self.press(0x48, 0x00)  # the up arrow
        self.press(0x1E, 0x61)
        got = []
        for _ in range(4):
            self.call(0xFF)
            got.append(None if self.cpu.get_flag(flags.ZF) else self.cpu.get8(0))
        self.assertEqual(got, [0x00, 0x48, 0x61, None])

    def test_the_scan_code_waits_for_the_next_read_even_when_nothing_else_is_pressed_in_between(self):
        self.press(0x3B, 0x00)  # F1
        self.call(0xFF)
        self.assertFalse(self.cpu.get_flag(flags.ZF))
        for _ in range(3):  # printing and other asks in between do not lose it
            self.call(ord("."))
        self.call(0xFF)
        self.assertEqual((self.cpu.get_flag(flags.ZF), self.cpu.get8(0)), (False, 0x3B))
        self.call(0xFF)
        self.assertTrue(self.cpu.get_flag(flags.ZF))

    def test_the_key_of_the_bios_is_the_same_key_dos_takes(self):
        self.press(0x1E, 0x61)
        self.press(0x30, 0x62)
        self.call(0xFF)  # DOS takes the first
        cpu = self.cpu
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x16])
        cpu.r[CPU.AX] = 0x0000  # and the BIOS gets the second
        cpu.ip = 0
        cpu.step()
        self.assertEqual(cpu.r[CPU.AX], 0x3062)

    def test_asking_changes_no_register_but_al_and_the_zero_flag_and_not_the_other_flags(self):
        self.press(0x1E, 0x61)
        self.cpu.flags = 0x0FD7 & ~flags.ZF
        self.call(0xFF)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.SI], cpu.r[CPU.DI]), (0x1111, 0x2222, 0x3333, 0x4444))
        self.assertEqual((cpu.get8(4), cpu.get8(2)), (0x06, 0xFF))
        self.assertEqual(cpu.flags, 0x0FD7 & ~flags.ZF)  # a key was there: ZF clear as it was
        self.call(0xFF)  # none now
        self.assertEqual(cpu.flags, 0x0FD7 | flags.ZF)

    def test_a_machine_with_no_bios_has_no_keys(self):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        self.call(0xFF, cpu=cpu)
        self.assertTrue(cpu.get_flag(flags.ZF))
        self.assertEqual(cpu.get8(0), 0)

    def test_each_machine_has_its_own_half_read_key(self):
        self.press(0x48, 0x00)
        self.call(0xFF)  # this one has the scan code waiting
        other = CPU()
        bios.install(other)
        dos.install(other)
        other.s[CPU.CS] = 0x1000
        other.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        self.call(0xFF, cpu=other)
        self.assertTrue(other.get_flag(flags.ZF))

    def test_the_scan_code_zero_after_a_key_with_no_character_is_not_taken_for_no_key(self):
        self.press(0x00, 0x00)  # a key with scan code 0 and no character: odd, but it must come as 0, 0
        self.call(0xFF)
        self.assertEqual((self.cpu.get_flag(flags.ZF), self.cpu.get8(0)), (False, 0))
        self.call(0xFF)
        self.assertEqual((self.cpu.get_flag(flags.ZF), self.cpu.get8(0)), (False, 0))
        self.call(0xFF)
        self.assertTrue(self.cpu.get_flag(flags.ZF))


class TestInputStatus(unittest.TestCase):
    """INT 21h AH=0Bh: is there a character to read? AL = FFh or 00h, and nothing is taken."""

    def setUp(self):
        self.cpu = CPU()
        self.keyboard = bios.install(self.cpu)
        self.state = dos.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])

    def call(self, ah=0x0B, al=0x77, cpu=None):
        cpu = cpu or self.cpu
        cpu.r[CPU.AX] = ah << 8 | al
        cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x1111, 0x2222, 0x3333
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, 0x5555
        cpu.ip = 0
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cpu.step()
        return cpu.get8(0)

    def test_with_no_key_it_is_00(self):
        self.assertEqual(self.call(), 0x00)

    def test_with_a_key_it_is_ff_and_the_key_stays(self):
        self.keyboard.press(0x1E, 0x61)
        self.assertEqual(self.call(), 0xFF)
        self.assertEqual(self.call(), 0xFF)  # asking again changes nothing
        self.assertEqual(list(self.keyboard.buffer), [0x1E61])
        self.assertEqual(self.call(ah=0x08), 0x61)  # the key is still there to read
        self.assertEqual(self.call(), 0x00)

    def test_it_follows_the_keys_that_are_read_one_by_one(self):
        for scan, ascii_code in ((0x1E, 0x61), (0x30, 0x62)):
            self.keyboard.press(scan, ascii_code)
        answers = []
        for _ in range(3):
            answers.append(self.call())
            if answers[-1]:
                self.call(ah=0x08)
        self.assertEqual(answers, [0xFF, 0xFF, 0x00])

    def test_a_key_with_no_character_is_one_character_that_is_zero_and_another_that_is_its_scan_code(self):
        self.keyboard.press(0x3B, 0x00)  # F1
        self.assertEqual(self.call(), 0xFF)
        self.assertEqual(self.call(ah=0x08), 0x00)  # the first half
        self.assertEqual(self.call(), 0xFF)  # the scan code is still to be read, though the buffer is empty
        self.assertEqual(self.call(ah=0x08), 0x3B)
        self.assertEqual(self.call(), 0x00)

    def test_a_scan_code_that_waits_is_there_with_other_keys_in_the_buffer_or_without(self):
        self.state.pending_scan = 0x48
        self.assertEqual(self.call(), 0xFF)
        self.state.pending_scan = 0x00  # a scan code of 0 is still a character
        self.assertEqual(self.call(), 0xFF)

    def test_the_other_registers_and_the_flags_are_left_alone(self):
        self.cpu.set_flag(flags.CF, True)
        self.call()
        cpu = self.cpu
        self.assertEqual(cpu.get8(4), 0x0B)  # AH
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]),
                         (0x1111, 0x2222, 0x3333, 0x4444, 0x5555))
        self.assertTrue(cpu.get_flag(flags.CF))

    def test_a_machine_with_no_bios_has_no_keys(self):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        self.assertEqual(self.call(cpu=cpu), 0x00)

    def test_it_does_not_wait_and_does_not_need_anything_that_could_press_a_key(self):
        self.assertIsNone(self.keyboard.waiter)
        self.assertEqual(self.call(), 0x00)  # where AH=08h would stop the emulator


class TestConsoleInputWithoutEcho(unittest.TestCase):
    """INT 21h AH=08h: wait for a key and take it, without showing it."""

    def setUp(self):
        self.cpu = CPU()
        self.keyboard = bios.install(self.cpu)
        self.state = dos.install(self.cpu)
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])

    def call(self, cpu=None, al=0x77):
        cpu = cpu or self.cpu
        cpu.r[CPU.AX] = 0x0800 | al
        cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x1111, 0x2222, 0x3333
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, 0x5555
        cpu.ip = 0
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cpu.step()
        return cpu.get8(0), out.getvalue() + err.getvalue()

    def test_the_key_that_is_there_is_taken_and_not_shown(self):
        self.keyboard.press(0x1E, 0x61)
        self.assertEqual(self.call(), (0x61, ""))
        self.assertEqual(len(self.keyboard.buffer), 0)

    def test_the_keys_come_in_the_order_they_were_pressed(self):
        for scan, ascii_code in ((0x1E, 0x61), (0x30, 0x62), (0x1C, 0x0D)):
            self.keyboard.press(scan, ascii_code)
        self.assertEqual([self.call()[0] for _ in range(3)], [0x61, 0x62, 0x0D])

    def test_ctrl_c_is_a_character_like_any_other_for_now(self):
        self.keyboard.press(0x2E, 0x03)
        self.assertEqual(self.call()[0], 0x03)

    def test_with_no_key_and_nothing_that_could_press_one_the_emulator_stops(self):
        with self.assertRaises(NotImplementedError):
            self.call()

    def test_a_machine_with_no_bios_cannot_wait_either(self):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        with self.assertRaises(NotImplementedError):
            self.call(cpu=cpu)

    def test_it_waits_through_the_waiter_when_there_is_one_and_takes_the_key_that_came(self):
        calls = []

        def waiter():
            calls.append(len(self.keyboard.buffer))
            self.keyboard.press(0x1C, 0x0D)

        self.keyboard.waiter = waiter
        self.assertEqual(self.call(), (0x0D, ""))
        self.assertEqual(calls, [0])
        self.assertEqual(len(self.keyboard.buffer), 0)

    def test_it_does_not_wait_when_a_key_is_already_there(self):
        self.keyboard.waiter = lambda: self.fail("it should not wait")
        self.keyboard.press(0x1E, 0x61)
        self.assertEqual(self.call()[0], 0x61)

    def test_a_waiter_that_comes_back_with_no_key_is_called_again(self):
        calls = []

        def waiter():
            calls.append(1)
            if len(calls) == 3:
                self.keyboard.press(0x1E, 0x7A)

        self.keyboard.waiter = waiter
        self.assertEqual(self.call()[0], 0x7A)
        self.assertEqual(len(calls), 3)

    def test_a_key_with_no_character_comes_as_0_and_then_its_scan_code_with_no_waiting_for_the_second(self):
        calls = []
        self.keyboard.waiter = lambda: calls.append(1) or self.keyboard.press(0x48, 0x00)  # the up arrow
        self.assertEqual(self.call()[0], 0x00)
        self.assertEqual(self.call()[0], 0x48)
        self.assertEqual(len(calls), 1)  # the second half was there already

    def test_the_second_half_of_a_key_may_be_taken_by_the_other_keyboard_function_of_dos(self):
        self.keyboard.press(0x3B, 0x00)  # F1
        self.assertEqual(self.call()[0], 0x00)
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.DX] = 0x0600, 0x00FF  # AH=06h, DL=FFh: is there a key?
        cpu.ip = 0
        cpu.step()
        self.assertEqual((cpu.get_flag(flags.ZF), cpu.get8(0)), (False, 0x3B))

    def test_no_register_is_changed_but_al_and_no_flag(self):
        self.keyboard.press(0x1E, 0x61)
        self.cpu.flags = 0x0FD7
        self.call()
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]),
                         (0x1111, 0x2222, 0x3333, 0x4444, 0x5555))
        self.assertEqual((cpu.get8(4), cpu.flags), (0x08, 0x0FD7))
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 2))

    def test_the_old_al_does_not_matter(self):
        for al in (0x00, 0x77, 0xFF):
            self.keyboard.press(0x1E, 0x61)
            self.assertEqual(self.call(al=al)[0], 0x61)

    def test_each_machine_has_its_own_keys_and_its_own_half_read_key(self):
        self.keyboard.press(0x48, 0x00)
        self.call()  # the scan code is waiting in this machine
        other = CPU()
        other_keyboard = bios.install(other)
        dos.install(other)
        other.s[CPU.CS] = 0x1000
        other.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        other_keyboard.press(0x1E, 0x61)
        self.assertEqual(self.call(cpu=other)[0], 0x61)


class TestSeek(FileFunctionsTest):
    """INT 21h AH=42h: move the position in the file of a handle; DX:AX is the new one."""

    def open(self, name="NOTES.TXT", mode=2):
        self.call(0x3D00 | mode, name)
        self.assertIsNone(self.failed())
        return self.cpu.r[CPU.AX]

    def seek(self, handle, whence, distance):
        """Seek and give back (the error or None, the position as DX:AX)."""
        distance &= 0xFFFFFFFF
        self.call(0x4200 | whence, bx=handle, cx=distance >> 16, dx=distance & 0xFFFF)
        return self.failed(), self.cpu.r[CPU.DX] << 16 | self.cpu.r[CPU.AX]

    def read(self, handle, count):
        self.call(0x3F00, bx=handle, cx=count, dx=0x0100)
        return bytes(self.cpu.rb(0x2000, 0x0100 + i) for i in range(self.cpu.r[CPU.AX]))

    def test_from_the_start(self):
        handle = self.open()
        self.assertEqual(self.seek(handle, 0, 4), (None, 4))
        self.assertEqual(self.read(handle, 3), b" no")
        self.assertEqual(self.seek(handle, 0, 0), (None, 0))
        self.assertEqual(self.read(handle, 4), b"some")

    def test_from_where_it_is_with_a_distance_in_either_direction(self):
        handle = self.open()
        self.read(handle, 3)
        self.assertEqual(self.seek(handle, 1, 0), (None, 3))  # where am I?
        self.assertEqual(self.seek(handle, 1, 2), (None, 5))
        self.assertEqual(self.seek(handle, 1, -2), (None, 3))
        self.assertEqual(self.read(handle, 2), b"e ")

    def test_from_the_end(self):
        handle = self.open()
        self.assertEqual(self.seek(handle, 2, 0), (None, 10))
        self.assertEqual(self.seek(handle, 2, -3), (None, 7))
        self.assertEqual(self.read(handle, 10), b"tes")
        self.assertEqual(self.seek(handle, 2, -10), (None, 0))

    def test_asking_where_it_is_does_not_move_it(self):
        handle = self.open()
        self.read(handle, 4)
        for _ in range(3):
            self.assertEqual(self.seek(handle, 1, 0), (None, 4))
        self.assertEqual(self.read(handle, 2), b" n")

    def test_the_idiom_for_the_size_of_a_file_and_back(self):
        handle = self.open()
        self.read(handle, 2)
        self.assertEqual(self.seek(handle, 2, 0), (None, 10))  # the size
        self.assertEqual(self.seek(handle, 0, 0), (None, 0))  # back to the start

    def test_past_the_end_is_all_right_and_a_read_there_finds_nothing(self):
        handle = self.open()
        self.assertEqual(self.seek(handle, 2, 5), (None, 15))
        self.assertEqual(self.read(handle, 4), b"")
        self.assertEqual(self.seek(handle, 0, 1000), (None, 1000))
        self.assertEqual(self.read(handle, 4), b"")

    def test_a_write_past_the_end_makes_the_gap_zeros(self):
        for name in ("D:\\GAP", "GAP.BIN"):
            self.call(0x3C00, name)
            handle = self.cpu.r[CPU.AX]
            self.assertEqual(self.seek(handle, 0, 5), (None, 5))
            self.call(0x4000, "x", bx=handle, cx=1)
            self.assertEqual(self.seek(handle, 2, 0), (None, 6))
            self.assertEqual(self.seek(handle, 0, 0), (None, 0))
            self.assertEqual(self.read(handle, 10), b"\0\0\0\0\0x", name)

    def test_before_the_start_is_error_131_and_the_file_stays_where_it_was(self):
        handle = self.open()
        self.read(handle, 4)
        for whence, distance in ((0, -1), (0, -0x7FFFFFFF - 1), (1, -5), (2, -11), (2, -0x7FFFFFFF)):
            self.assertEqual(self.seek(handle, whence, distance)[0], 131, (whence, distance))
            self.assertEqual(self.cpu.r[CPU.AX], 131)
            self.assertEqual(self.seek(handle, 1, 0), (None, 4), (whence, distance))

    def test_exactly_the_start_and_exactly_the_end_are_all_right(self):
        handle = self.open()
        self.read(handle, 4)
        self.assertEqual(self.seek(handle, 1, -4), (None, 0))
        self.assertEqual(self.seek(handle, 2, -10), (None, 0))
        self.assertEqual(self.seek(handle, 1, 10), (None, 10))

    def test_big_distances_come_back_in_dx_and_ax(self):
        self.call(0x3C00, "D:\\BIG")
        handle = self.cpu.r[CPU.AX]
        self.assertEqual(self.seek(handle, 0, 0x00012345), (None, 0x00012345))
        self.assertEqual((self.cpu.r[CPU.DX], self.cpu.r[CPU.AX]), (0x0001, 0x2345))
        self.assertEqual(self.seek(handle, 1, 0x00010000), (None, 0x00022345))
        self.assertEqual(self.seek(handle, 1, -0x00020000), (None, 0x00002345))

    def test_a_position_over_4_gb_goes_round_to_the_start_of_32_bits(self):
        self.call(0x3C00, "D:\\WRAP")
        handle = self.cpu.r[CPU.AX]
        self.seek(handle, 0, 0x7FFFFFFF)
        self.seek(handle, 1, 0x7FFFFFFF)  # 0xFFFFFFFE
        self.assertEqual(self.seek(handle, 1, 0), (None, 0xFFFFFFFE))
        self.assertEqual(self.seek(handle, 1, 4), (None, 2))  # 0x100000002
        self.assertEqual((self.cpu.r[CPU.DX], self.cpu.r[CPU.AX]), (0, 2))

    def test_a_whence_that_is_not_0_1_or_2_is_error_1_and_the_file_stays(self):
        handle = self.open()
        self.read(handle, 3)
        for whence in (3, 4, 0x7F, 0xFF):
            self.assertEqual(self.seek(handle, whence, 2)[0], 1, whence)
            self.assertEqual(self.cpu.r[CPU.AX], 1)
        self.assertEqual(self.seek(handle, 1, 0), (None, 3))

    def test_the_handles_that_are_not_open_are_error_6(self):
        handle = self.open()
        self.call(0x3E00, bx=handle)
        for number in (handle, 6, 19, 20, 0xFFFF):
            self.assertEqual(self.seek(number, 0, 0)[0], 6, number)

    def test_the_console_and_the_devices_have_no_position_it_is_0_and_it_is_not_an_error(self):
        for number in (0, 1, 2, 3, 4):
            for whence in (0, 1, 2):
                self.assertEqual(self.seek(number, whence, 5), (None, 0), (number, whence))

    def test_a_way_to_seek_that_does_not_exist_is_error_1_for_the_devices_too(self):
        for number in (0, 3, 4):
            self.assertEqual(self.seek(number, 3, 0)[0], 1, number)

    def test_each_handle_of_a_file_has_a_position_of_its_own(self):
        first, second = self.open(), self.open()
        self.seek(first, 0, 6)
        self.assertEqual(self.seek(second, 1, 0), (None, 0))
        self.assertEqual(self.read(second, 4), b"some")
        self.assertEqual(self.read(first, 4), b"otes")

    def test_it_works_on_a_file_of_the_memory_disk_as_on_one_of_the_host(self):
        self.call(0x3C00, "D:\\M")
        handle = self.cpu.r[CPU.AX]
        self.call(0x4000, "abcdefgh", bx=handle, cx=8)
        self.assertEqual(self.seek(handle, 1, 0), (None, 8))
        self.assertEqual(self.seek(handle, 0, 2), (None, 2))
        self.assertEqual(self.read(handle, 3), b"cde")
        self.assertEqual(self.seek(handle, 2, -1), (None, 7))

    def test_the_registers_bx_cx_and_the_flags_but_the_carry_are_left_alone(self):
        handle = self.open()
        self.cpu.set_flag(flags.ZF, True)
        self.cpu.set_flag(flags.CF, True)
        self.call(0x4200, bx=handle, cx=0, dx=3)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX]), (handle, 0))
        self.assertTrue(cpu.get_flag(flags.ZF))
        self.assertFalse(cpu.get_flag(flags.CF))
        self.assertEqual((cpu.r[CPU.DX], cpu.r[CPU.AX]), (0, 3))
        self.assertEqual(cpu.get8(4), 0)  # AH = 0: AX holds the position

    def test_a_failed_seek_leaves_dx_alone(self):
        handle = self.open()
        self.call(0x4200, bx=handle, cx=0xFFFF, dx=0xFFFF)
        self.assertEqual(self.cpu.r[CPU.DX], 0xFFFF)  # what it was: the call put the distance there
        self.assertTrue(self.cpu.get_flag(flags.CF))


if __name__ == "__main__":
    unittest.main()


class TestReadSectorsOfTheDisc(unittest.TestCase):
    """INT 2Fh AX=1508h: sectors of the CD-ROM in CX, DX of them from the number in SI:DI, to ES:BX."""

    SYSTEM = bytes(range(256)) * 8 * 4  # the first four sectors: something that is not zeros

    def setUp(self):
        from tests.iso_builder import build
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.image = build({"A.TXT": b"abc"}, system_area=self.SYSTEM)
        (self.base / "disc.iso").write_bytes(self.image)
        (self.base / "files").mkdir()
        self.machine(f"""
            [disks.C]
            type = 'hdd'
            [disks.D]
            type = 'cdrom'
            [disks.E]
            type = 'cdrom'
            [disks.F]
            type = 'cdrom'
            [mounts]
            C = {{ type = 'tmpfs' }}
            D = {{ type = 'iso', path = 'disc.iso' }}
            E = {{ type = 'dir', path = 'files' }}
        """)

    def machine(self, text):
        self.cpu = CPU()
        dos.install(self.cpu, Volumes(config.parse(tomllib.loads(text), self.base)))
        self.cpu.s[CPU.CS] = 0x1000
        self.cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x2F])

    def read(self, drive=3, count=1, first=0, buffer=0x0100, fill=0xAA, size=3 * 2048 + 64):
        cpu = self.cpu
        for i in range(size):
            cpu.wb(0x3000, (buffer + i) & 0xFFFF, fill)
        cpu.r[CPU.AX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x1508, drive, count
        cpu.r[CPU.BX], cpu.r[CPU.SI], cpu.r[CPU.DI] = buffer, first >> 16, first & 0xFFFF
        cpu.s[CPU.ES] = 0x3000
        cpu.ip = 0
        cpu.step()
        return bytes(cpu.rb(0x3000, (buffer + i) & 0xFFFF) for i in range(size))

    def failed(self):
        return self.cpu.r[CPU.AX] if self.cpu.get_flag(flags.CF) else None

    def test_sectors_are_put_where_es_bx_says_and_nothing_after_them(self):
        got = self.read(count=2, first=1)
        self.assertIsNone(self.failed())
        self.assertEqual(got[:4096], self.image[2048:6144])
        self.assertEqual(got[4096:], bytes([0xAA]) * (len(got) - 4096))

    def test_the_first_sector_is_the_one_in_si_di_with_si_the_high_word(self):
        self.assertEqual(self.read(first=16)[:6], b"\x01CD001")  # the primary volume descriptor
        self.assertIsNone(self.failed())
        self.read(first=0x10000 | 16)  # SI = 1: 65552, far beyond a disc of 26 sectors
        self.assertEqual(self.failed(), SECTOR_NOT_FOUND)

    def test_the_system_area_is_read_as_the_disc_has_it(self):
        got = self.read(count=3, first=0)
        self.assertEqual(got[:3 * 2048], self.SYSTEM[:3 * 2048])

    def test_fifteen_sectors_from_the_start_as_the_installer_asks(self):
        got = self.read(count=15, first=0, size=15 * 2048)
        self.assertIsNone(self.failed())
        self.assertEqual(got, self.image[:15 * 2048])

    def test_the_other_registers_stay_and_the_carry_is_clear(self):
        self.cpu.set_flag(flags.CF, True)
        self.read(count=1, first=2)
        cpu = self.cpu
        self.assertFalse(cpu.get_flag(flags.CF))
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.BX]), (0x1508, 3, 1, 0x0100))
        self.assertEqual((cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.s[CPU.ES]), (0, 2, 0x3000))

    def test_no_sectors_is_all_right_and_writes_nothing(self):
        got = self.read(count=0)
        self.assertIsNone(self.failed())
        self.assertEqual(set(got), {0xAA})

    def test_the_last_sector_of_the_disc_is_there_and_the_next_one_is_not(self):
        last = len(self.image) // 2048 - 1
        self.assertEqual(self.read(first=last)[:2048], self.image[-2048:])
        self.assertIsNone(self.failed())
        self.read(first=last + 1)
        self.assertEqual(self.failed(), SECTOR_NOT_FOUND)
        self.read(first=last, count=2)
        self.assertEqual(self.failed(), SECTOR_NOT_FOUND)

    def test_a_failed_read_writes_nothing(self):
        got = self.read(first=len(self.image) // 2048 - 1, count=2)
        self.assertEqual(set(got), {0xAA})

    def test_the_buffer_may_go_round_the_end_of_the_segment(self):
        got = self.read(count=1, first=16, buffer=0xFFF0, size=2048)
        self.assertEqual(got, self.image[16 * 2048:17 * 2048])
        self.assertEqual(self.cpu.rb(0x3000, 0), self.image[16 * 2048 + 16])

    def test_a_drive_that_is_not_a_cd_rom_is_error_15(self):
        for drive in (2, 4 + 3, 0):  # the hard disk C:, a drive that is not there, a drive A: that is not there
            self.read(drive=drive)
            self.assertEqual(self.failed(), 15, drive)

    def test_a_cd_rom_drive_with_no_disc_in_it_is_not_ready(self):
        self.read(drive=5)
        self.assertEqual(self.failed(), 21)

    def test_a_cd_rom_that_is_a_directory_has_no_sectors_and_says_so(self):
        with self.assertRaisesRegex(NotImplementedError, r"1508h.*directory.*type = \"iso\""):
            self.read(drive=4)

    def test_without_any_cd_rom_drive_the_call_is_not_known(self):
        self.machine("[disks.C]\ntype = 'hdd'\n[mounts]\nC = { type = 'tmpfs' }\n")
        with self.assertRaisesRegex(NotImplementedError, "AX=1508"):
            self.read()

    def test_the_files_of_the_same_disc_are_there_as_files(self):
        cpu = self.cpu
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10010:0x10012] = bytes([0xCD, 0x21])
        for i, byte in enumerate(b"D:\\A.TXT\0"):
            cpu.wb(0x2000, 0x0100 + i, byte)
        cpu.s[CPU.DS] = 0x2000
        cpu.r[CPU.AX], cpu.r[CPU.DX] = 0x3D00, 0x0100
        cpu.ip = 0x10
        cpu.step()
        self.assertFalse(cpu.get_flag(flags.CF))
        handle = cpu.r[CPU.AX]
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x3F00, handle, 10, 0x0200
        cpu.ip = 0x10
        cpu.step()
        self.assertEqual((cpu.r[CPU.AX], bytes(cpu.rb(0x2000, 0x200 + i) for i in range(3))), (3, b"abc"))


class Disk:
    """A file system that says how big it is, and nothing else."""

    def __init__(self, size, free):
        self.sizes = (size, free)

    def free_space(self):
        return self.sizes


class TestDiskSpace(FileFunctionsTest):
    """INT 21h AH=36h: sectors to a cluster, free clusters, bytes to a sector, clusters in all."""

    CLUSTER = 512 * 64

    def ask(self, drive, bx=0x1111, cx=0x2222, dx=None):
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX] = 0x3600, bx, cx
        cpu.r[CPU.DX] = 0x3300 | drive if dx is None else dx
        cpu.ip = 0
        cpu.step()
        return tuple(cpu.r[i] for i in (CPU.AX, CPU.BX, CPU.CX, CPU.DX))

    def disk(self, drive, size, free):
        self.volumes.filesystems[drive] = Disk(size, free)

    def test_a_disk_is_sectors_of_512_bytes_and_clusters_of_64_of_them(self):
        self.disk(2, 100 * self.CLUSTER, 40 * self.CLUSTER)
        self.assertEqual(self.ask(3), (64, 40, 512, 100))  # C:

    def test_what_does_not_fill_a_cluster_is_not_counted(self):
        self.disk(2, 100 * self.CLUSTER + self.CLUSTER - 1, 40 * self.CLUSTER + self.CLUSTER - 1)
        self.assertEqual(self.ask(3)[1::2], (40, 100))
        self.disk(2, self.CLUSTER - 1, self.CLUSTER - 1)
        self.assertEqual(self.ask(3), (64, 0, 512, 0))

    def test_a_big_disk_is_shown_as_just_under_2_gb_and_the_free_space_is_cut_with_it(self):
        self.disk(2, 232 * 2 ** 30, 41 * 2 ** 30)
        ax, bx, cx, dx = self.ask(3)
        self.assertEqual((dx, bx), (65535, 65535))
        self.assertEqual(dx * ax * cx, 2 ** 31 - 2 ** 15)  # 2 GB less a cluster

    def test_the_free_space_never_passes_the_whole(self):
        self.disk(2, 10 * self.CLUSTER, 20 * self.CLUSTER)
        self.assertEqual(self.ask(3)[1::2], (10, 10))

    def test_a_disk_that_is_just_at_the_limit(self):
        self.disk(2, 65535 * self.CLUSTER, 65535 * self.CLUSTER)
        self.assertEqual(self.ask(3)[1::2], (65535, 65535))
        self.disk(2, 65536 * self.CLUSTER, 65536 * self.CLUSTER)
        self.assertEqual(self.ask(3)[1::2], (65535, 65535))
        self.disk(2, 65534 * self.CLUSTER, 65534 * self.CLUSTER)
        self.assertEqual(self.ask(3)[1::2], (65534, 65534))

    def test_drive_0_is_the_current_drive_and_a_number_is_that_drive_whatever_is_current(self):
        self.disk(2, 100 * self.CLUSTER, 10 * self.CLUSTER)
        self.disk(3, 50 * self.CLUSTER, 5 * self.CLUSTER)
        self.volumes.current_drive = 3
        self.assertEqual(self.ask(0)[1::2], (5, 50))  # D:
        self.assertEqual(self.ask(3)[1::2], (10, 100))  # C:
        self.volumes.current_drive = 2
        self.assertEqual(self.ask(0)[1::2], (10, 100))
        self.assertEqual(self.ask(4)[1::2], (5, 50))

    def test_a_drive_that_is_not_there_or_has_nothing_in_it_is_ffff_and_the_rest_is_left(self):
        for drive in (1, 10, 27):  # A: has no disk in it, J: and Z: are not drives
            self.assertEqual(self.ask(drive), (0xFFFF, 0x1111, 0x2222, 0x3300 | drive), drive)

    def test_a_directory_of_the_host_gives_what_the_host_says(self):
        import shutil
        real = shutil.disk_usage(self.base)
        ax, bx, cx, dx = self.ask(3)
        self.assertEqual((ax, cx), (64, 512))
        self.assertEqual(dx, min(real.total // self.CLUSTER, 65535))
        self.assertLessEqual(bx, dx)

    def test_a_memory_disk_is_32_megabytes_and_the_files_in_it_take_space(self):
        self.assertEqual(self.ask(4), (64, 1024, 512, 1024))  # D: is a tmpfs
        with self.volumes.filesystems[3].create(["F.DAT"]) as file:
            file.write(bytes(3 * self.CLUSTER))
        self.assertEqual(self.ask(4)[1::2], (1024 - 3, 1024))

    def test_the_registers_other_than_the_four_and_the_flags_are_left_alone(self):
        self.disk(2, 100 * self.CLUSTER, 40 * self.CLUSTER)
        cpu = self.cpu
        cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.s[CPU.ES] = 0x4444, 0x5555, 0x3000
        cpu.set_flag(flags.CF, True)
        self.ask(3)
        self.assertEqual((cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.s[CPU.DS], cpu.s[CPU.ES]), (0x4444, 0x5555, 0x2000, 0x3000))
        self.assertTrue(cpu.get_flag(flags.CF))


class TestMakeDirectory(FileFunctionsTest):
    """INT 21h AH=39h: make a directory."""

    def mkdir(self, name, **registers):
        self.call(0x3900, name, **registers)
        return self.failed()

    def test_a_directory_is_made_on_the_host_in_capitals_and_the_carry_is_clear(self):
        self.cpu.set_flag(flags.CF, True)
        self.assertIsNone(self.mkdir("xcoma"))
        self.assertFalse(self.cpu.get_flag(flags.CF))
        self.assertTrue((self.base / "XCOMA").is_dir())
        self.assertEqual(sorted(os.listdir(self.base)), ["GAMES", "NOTES.TXT", "XCOMA"])

    def test_it_is_empty_and_the_program_can_go_into_it(self):
        self.mkdir("XCOMA")
        self.assertEqual(os.listdir(self.base / "XCOMA"), [])
        self.call(0x3B00, "XCOMA")
        self.assertIsNone(self.failed())
        self.assertEqual(self.volumes.current_directory(), "XCOMA")

    def test_a_relative_path_is_from_the_current_directory_and_an_absolute_one_from_the_root(self):
        self.call(0x3B00, "GAMES")
        self.assertIsNone(self.mkdir("ARCADE"))
        self.assertTrue((self.base / "GAMES" / "ARCADE").is_dir())
        self.assertIsNone(self.mkdir("\\TOP"))
        self.assertTrue((self.base / "TOP").is_dir())
        self.assertIsNone(self.mkdir("..\\SIBLING"))
        self.assertTrue((self.base / "SIBLING").is_dir())

    def test_a_path_with_the_drive_names_the_drive(self):
        self.assertIsNone(self.mkdir("C:\\GAMES\\SAVES"))
        self.assertTrue((self.base / "GAMES" / "SAVES").is_dir())
        self.assertIsNone(self.mkdir("d:\\TEMP"))
        self.assertEqual([entry.name for entry in self.volumes.filesystems[3].listdir([])], ["TEMP"])

    def test_a_directory_that_is_there_and_a_file_of_that_name_are_error_5(self):
        self.assertEqual(self.mkdir("GAMES"), 5)
        self.assertEqual(self.mkdir("games"), 5)
        self.assertEqual(self.mkdir("NOTES.TXT"), 5)
        self.assertEqual(sorted(os.listdir(self.base)), ["GAMES", "NOTES.TXT"])

    def test_a_parent_that_is_not_there_is_error_3_and_nothing_is_made(self):
        self.assertEqual(self.mkdir("NOTHING\\SUB"), 3)
        self.assertEqual(self.mkdir("NOTES.TXT\\SUB"), 3)  # the parent is a file
        self.assertEqual(self.mkdir("..\\X"), 3)  # above the root
        self.assertEqual(sorted(os.listdir(self.base)), ["GAMES", "NOTES.TXT"])

    def test_a_name_dos_cannot_have_is_error_3(self):
        for name in ("TOOLONGNAME", "A*B", "A?B", "NAME.LONG", "A B"):
            self.assertEqual(self.mkdir(name), 3, name)
        self.assertEqual(sorted(os.listdir(self.base)), ["GAMES", "NOTES.TXT"])

    def test_the_root_and_an_empty_name_cannot_be_made(self):
        self.assertEqual(self.mkdir("\\"), 5)
        self.assertEqual(self.mkdir(""), 3)

    def test_a_drive_that_is_not_there_or_has_nothing_in_it_is_an_error_of_the_drive(self):
        self.assertEqual(self.mkdir("A:\\X"), 21)
        self.assertEqual(self.mkdir("Z:\\X"), 15)

    def test_a_disc_cannot_be_written_to(self):
        from dosloth.iso import IsoFs, IsoImage
        from tests.iso_builder import build
        (self.base / "disc.iso").write_bytes(build({"A.TXT": b"abc"}))
        self.volumes.filesystems[3] = IsoFs(IsoImage(self.base / "disc.iso"))
        self.assertEqual(self.mkdir("D:\\NEW"), 5)

    def test_the_registers_other_than_ax_are_left_alone(self):
        self.cpu.r[CPU.SI], self.cpu.r[CPU.DI] = 0x4444, 0x5555
        self.assertIsNone(self.mkdir("NEWDIR", bx=0x1111, cx=0x2222))
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]),
                         (0x1111, 0x2222, 0x0100, 0x4444, 0x5555))

    def test_the_name_is_read_from_ds_dx_round_the_end_of_the_segment(self):
        self.assertIsNone(self.mkdir("WRAPPED", dx=0xFFF8))
        self.assertTrue((self.base / "WRAPPED").is_dir())


class TestChangeDirectory(FileFunctionsTest):
    """INT 21h AH=3Bh: make a directory the current one."""

    def setUp(self):
        super().setUp()
        (self.base / "GAMES" / "ARCADE").mkdir()
        (self.base / "GAMES" / "ARCADE" / "PONG.COM").write_bytes(b"pong")

    def cd(self, name):
        self.call(0x3B00, name)
        return self.failed()

    def current(self, drive=None):
        """The current directory, as the file system layer has it, as DOS writes it."""
        return self.volumes.current_directory(drive)

    def test_a_directory_becomes_the_current_one_and_the_carry_is_clear(self):
        self.cpu.set_flag(flags.CF, True)
        self.assertIsNone(self.cd("GAMES"))
        self.assertFalse(self.cpu.get_flag(flags.CF))
        self.assertEqual(self.current(), "GAMES")

    def test_what_the_program_reads_back_with_47h_is_what_it_set(self):
        self.cd("games\\arcade")
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.DX], cpu.r[CPU.SI] = 0x4700, 0, 0x0300  # the current drive, a buffer at DS:SI
        cpu.ip = 0
        cpu.step()
        text = bytes(cpu.rb(0x2000, 0x0300 + i) for i in range(14))
        self.assertEqual(text.split(b"\0")[0], b"GAMES\\ARCADE")

    def test_the_names_are_stored_in_capitals_whatever_was_written(self):
        self.cd("Games\\Arcade")
        self.assertEqual(self.current(), "GAMES\\ARCADE")

    def test_a_relative_path_goes_from_the_current_directory_and_dot_dot_goes_up(self):
        self.cd("GAMES")
        self.assertIsNone(self.cd("ARCADE"))
        self.assertEqual(self.current(), "GAMES\\ARCADE")
        self.assertIsNone(self.cd(".."))
        self.assertEqual(self.current(), "GAMES")
        self.assertIsNone(self.cd(".\\ARCADE\\.."))
        self.assertEqual(self.current(), "GAMES")

    def test_a_path_from_the_root_does_not_depend_on_where_it_is(self):
        self.cd("GAMES\\ARCADE")
        self.assertIsNone(self.cd("\\GAMES"))
        self.assertEqual(self.current(), "GAMES")
        self.assertIsNone(self.cd("\\"))
        self.assertEqual(self.current(), "")

    def test_a_path_with_the_drive_changes_the_directory_of_that_drive_and_not_the_current_drive(self):
        self.volumes.current_drive = 3  # D:
        self.assertIsNone(self.cd("C:\\GAMES"))
        self.assertEqual(self.volumes.current_drive, 3)
        self.assertEqual(self.current(2), "GAMES")
        self.assertEqual(self.current(3), "")

    def test_what_is_not_there_is_error_3_and_the_directory_stays(self):
        self.cd("GAMES")
        for name in ("NOTHING", "GAMES\\NOTHING", "NOTHING\\ARCADE"):
            self.assertEqual(self.cd(name), 3, name)
        self.assertEqual(self.current(), "GAMES")

    def test_a_file_is_not_a_directory_and_is_error_3(self):
        self.assertEqual(self.cd("NOTES.TXT"), 3)
        self.assertEqual(self.cd("GAMES\\ARCADE\\PONG.COM"), 3)
        self.assertEqual(self.current(), "")

    def test_an_empty_name_and_a_path_above_the_root_are_error_3(self):
        self.assertEqual(self.cd(""), 3)
        self.assertEqual(self.cd(".."), 3)
        self.assertEqual(self.cd("\\.."), 3)

    def test_a_drive_that_is_not_there_or_has_nothing_in_it_is_an_error_of_the_drive(self):
        self.assertEqual(self.cd("A:\\X"), 21)
        self.assertEqual(self.cd("Z:\\X"), 15)

    def test_a_memory_disk_directory_can_be_made_current_too(self):
        self.volumes.filesystems[3].mkdir(["TEMP"])
        self.assertIsNone(self.cd("D:\\TEMP"))
        self.assertEqual(self.current(3), "TEMP")

    def test_the_registers_other_than_ax_are_left_alone(self):
        self.cpu.r[CPU.SI], self.cpu.r[CPU.DI] = 0x4444, 0x5555
        self.call(0x3B00, "GAMES", bx=0x1111, cx=0x2222)
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]),
                         (0x1111, 0x2222, 0x0100, 0x4444, 0x5555))

    def test_the_name_is_read_from_ds_dx_round_the_end_of_the_segment(self):
        self.call(0x3B00, "GAMES", dx=0xFFFA)
        self.assertEqual(self.current(), "GAMES")


class TestDeleteFile(FileFunctionsTest):
    """INT 21h AH=41h: delete a file."""

    def delete(self, name, **registers):
        self.call(0x4100, name, **registers)
        return self.failed()

    def test_a_file_is_gone_from_the_host_and_the_carry_is_clear(self):
        self.cpu.set_flag(flags.CF, True)
        self.assertIsNone(self.delete("NOTES.TXT"))
        self.assertFalse(self.cpu.get_flag(flags.CF))
        self.assertFalse((self.base / "NOTES.TXT").exists())
        self.assertTrue((self.base / "GAMES").is_dir())  # and nothing else

    def test_a_file_in_a_directory_and_a_name_in_other_case_and_with_the_drive(self):
        (self.base / "GAMES" / "SAVE.DAT").write_bytes(b"x")
        self.assertIsNone(self.delete("games\\save.dat"))
        self.assertEqual(os.listdir(self.base / "GAMES"), [])
        self.assertIsNone(self.delete("c:\\notes.txt"))
        self.assertFalse((self.base / "NOTES.TXT").exists())

    def test_the_file_a_program_just_made_can_be_deleted_after_it_is_closed(self):
        self.call(0x3C00, "XXYXZ.$%!")
        handle = self.cpu.r[CPU.AX]
        self.call(0x3E00, bx=handle)
        self.assertTrue((self.base / "XXYXZ.$%!").exists())
        self.assertIsNone(self.delete("XXYXZ.$%!"))
        self.assertFalse((self.base / "XXYXZ.$%!").exists())

    def test_a_file_that_is_not_there_is_error_2_and_a_path_that_is_not_there_is_error_3(self):
        self.assertEqual(self.delete("NOTHING.TXT"), 2)
        self.assertEqual(self.delete("GAMES\\NOTHING.TXT"), 2)
        self.assertEqual(self.delete("NOTHING\\NOTES.TXT"), 3)
        self.assertEqual(self.delete("NOTES.TXT\\X"), 3)
        self.assertTrue((self.base / "NOTES.TXT").exists())

    def test_a_directory_is_error_5_and_stays(self):
        self.assertEqual(self.delete("GAMES"), 5)
        self.assertEqual(self.delete("\\"), 5)  # the root has no name to delete
        self.assertTrue((self.base / "GAMES").is_dir())

    def test_a_read_only_file_is_error_5_and_stays_until_it_is_made_writable(self):
        self.skip_if_root()
        (self.base / "NOTES.TXT").chmod(0o444)
        self.assertEqual(self.delete("NOTES.TXT"), 5)
        self.assertTrue((self.base / "NOTES.TXT").exists())
        self.call(0x4301, "NOTES.TXT", cx=0)
        self.assertIsNone(self.delete("NOTES.TXT"))
        self.assertFalse((self.base / "NOTES.TXT").exists())

    def test_a_wildcard_is_not_expanded_and_deletes_nothing(self):
        for name in ("*.TXT", "NOTES.*", "N?TES.TXT"):
            self.assertIn(self.delete(name), (2, 3), name)
        self.assertTrue((self.base / "NOTES.TXT").exists())

    def test_a_file_in_a_memory_disk_is_deleted_there(self):
        self.call(0x3C00, "D:\\TEMP.DAT")
        self.call(0x3E00, bx=self.cpu.r[CPU.AX])
        self.assertEqual([entry.name for entry in self.volumes.filesystems[3].listdir([])], ["TEMP.DAT"])
        self.assertIsNone(self.delete("D:\\TEMP.DAT"))
        self.assertEqual(self.volumes.filesystems[3].listdir([]), [])

    def test_a_drive_that_is_not_there_or_has_nothing_in_it_is_an_error_of_the_drive(self):
        self.assertEqual(self.delete("A:\\X.TXT"), 21)
        self.assertEqual(self.delete("Z:\\X.TXT"), 15)

    def test_a_disc_cannot_be_written_to(self):
        from tests.iso_builder import build
        (self.base / "disc.iso").write_bytes(build({"A.TXT": b"abc"}))
        from dosloth.iso import IsoFs, IsoImage
        self.volumes.filesystems[3] = IsoFs(IsoImage(self.base / "disc.iso"))
        self.assertEqual(self.delete("D:\\A.TXT"), 5)

    def test_the_registers_other_than_ax_and_the_flags_are_left_alone(self):
        self.cpu.r[CPU.SI], self.cpu.r[CPU.DI] = 0x4444, 0x5555
        self.assertIsNone(self.delete("NOTES.TXT", bx=0x1111, cx=0x2222))
        cpu = self.cpu
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI]),
                         (0x1111, 0x2222, 0x0100, 0x4444, 0x5555))

    def test_the_name_is_read_from_ds_dx_round_the_end_of_the_segment(self):
        self.assertIsNone(self.delete("NOTES.TXT", dx=0xFFFA))
        self.assertFalse((self.base / "NOTES.TXT").exists())


class TestGetCurrentDirectory(FileFunctionsTest):
    """INT 21h AH=47h: the current directory of a drive, written at DS:SI without the drive and the first backslash."""

    def ask(self, drive, si=0x0300, fill=0xAA):
        for i in range(80):
            self.cpu.wb(0x2000, (si + i) & 0xFFFF, fill)
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x4700, 0x1111, 0x2222, 0x0300 | drive
        cpu.r[CPU.SI], cpu.r[CPU.DI] = si, 0x4444
        cpu.ip = 0
        cpu.step()

    def written(self, si=0x0300):
        """The bytes at DS:SI up to the zero, and the byte after it."""
        text = bytearray()
        for i in range(80):
            byte = self.cpu.rb(0x2000, (si + i) & 0xFFFF)
            if byte == 0:
                return bytes(text), self.cpu.rb(0x2000, (si + i + 1) & 0xFFFF)
            text.append(byte)
        self.fail("no zero")

    def test_the_root_is_an_empty_string(self):
        self.volumes.directories[2] = []
        self.ask(3)
        self.assertIsNone(self.failed())
        self.assertEqual(self.written(), (b"", 0xAA))

    def test_a_directory_is_written_in_capitals_without_the_first_backslash(self):
        self.volumes.chdir("GAMES")
        self.ask(3)
        self.assertEqual(self.written(), (b"GAMES", 0xAA))

    def test_drive_0_is_the_current_drive_and_a_number_is_that_drive(self):
        self.volumes.chdir("GAMES")
        self.volumes.current_drive = 3
        self.ask(0)
        self.assertEqual(self.written(), (b"", 0xAA))
        self.volumes.current_drive = 3
        self.ask(3)  # C:, whatever the current drive is
        self.assertEqual(self.written(), (b"GAMES", 0xAA))

    def test_the_directory_is_that_of_the_drive_asked_not_of_the_current_one(self):
        self.volumes.chdir("GAMES")
        self.volumes.directories[3] = ["A", "B"]
        self.volumes.current_drive = 2
        self.ask(4)
        self.assertEqual(self.written(), (b"A\\B", 0xAA))
        self.ask(0)
        self.assertEqual(self.written(), (b"GAMES", 0xAA))

    def test_the_answer_is_ax_0100_and_the_other_registers_stay(self):
        self.ask(3)
        cpu = self.cpu
        self.assertEqual(cpu.r[CPU.AX], 0x0100)
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x1111, 0x2222, 0x0303))
        self.assertEqual((cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.s[CPU.DS]), (0x0300, 0x4444, 0x2000))
        self.assertFalse(cpu.get_flag(flags.CF))

    def test_a_good_call_clears_the_carry_flag(self):
        self.cpu.set_flag(flags.CF, True)
        self.ask(3)
        self.assertFalse(self.cpu.get_flag(flags.CF))

    def test_a_drive_that_is_not_there_is_error_15(self):
        self.ask(10)
        self.assertEqual(self.failed(), 15)
        self.assertEqual(self.cpu.rb(0x2000, 0x0300), 0xAA)

    def test_a_drive_with_nothing_in_it_is_not_ready(self):
        self.ask(1)
        self.assertEqual(self.failed(), 21)
        self.assertEqual(self.cpu.rb(0x2000, 0x0300), 0xAA)

    def test_the_buffer_may_go_round_the_end_of_the_segment(self):
        self.volumes.chdir("GAMES")
        self.ask(3, si=0xFFFD)
        self.assertEqual(self.written(si=0xFFFD), (b"GAMES", 0xAA))
        self.assertEqual(self.cpu.rb(0x2000, 0x0002), 0)

    def test_a_long_path_is_written_whole(self):
        self.volumes.directories[2] = ["ABCDEFGH"] * 7 + ["IJ"]
        self.ask(3)
        self.assertEqual(self.written(), (b"\\".join([b"ABCDEFGH"] * 7 + [b"IJ"]), 0xAA))


class TestCountryInformation(FileFunctionsTest):
    """INT 21h AH=38h: how dates, times and money are written. The United States is the only country."""

    def ask(self, al=0, bx=0x1111, dx=0x0300, si=0x4444, fill=0xAA):
        for i in range(48):
            self.cpu.wb(0x2000, (dx + i) & 0xFFFF, fill)
        cpu = self.cpu
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x3800 | al, bx, 0x2222, dx
        cpu.r[CPU.SI] = si
        cpu.ip = 0
        cpu.step()
        return bytes(cpu.rb(0x2000, (dx + i) & 0xFFFF) for i in range(48))

    def test_the_table_has_the_formats_of_the_united_states(self):
        table = self.ask()
        self.assertIsNone(self.failed())
        self.assertEqual(table[0x00:0x02], b"\0\0")  # month, day, year
        self.assertEqual(table[0x02:0x07], b"$\0\0\0\0")
        self.assertEqual(table[0x07:0x09], b",\0")
        self.assertEqual(table[0x09:0x0B], b".\0")
        self.assertEqual(table[0x0B:0x0D], b"-\0")
        self.assertEqual(table[0x0D:0x0F], b":\0")
        self.assertEqual(table[0x0F:0x12], bytes([0, 2, 0]))  # the sign before, two digits, 12 hours
        self.assertEqual(table[0x16:0x18], b",\0")
        self.assertEqual(table[0x18:0x22], bytes(10))

    def test_the_table_is_34_bytes_and_nothing_after_it_is_written(self):
        table = self.ask()
        self.assertEqual(table[34:], bytes([0xAA]) * 14)

    def test_the_case_routine_is_a_far_pointer_to_a_return(self):
        table = self.ask()
        offset, segment = int.from_bytes(table[0x12:0x14], "little"), int.from_bytes(table[0x14:0x16], "little")
        self.assertEqual(self.cpu.rb(segment, offset), 0xCB)

    def test_bx_is_the_country_code_and_the_other_registers_stay(self):
        self.ask()
        cpu = self.cpu
        self.assertEqual(cpu.r[CPU.BX], 1)
        self.assertEqual((cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.s[CPU.DS]), (0x2222, 0x0300, 0x4444, 0x2000))
        self.assertFalse(cpu.get_flag(flags.CF))

    def test_a_country_can_be_asked_for_by_al_or_by_bx_with_al_ff(self):
        self.assertEqual(self.ask(al=1)[0x02], ord("$"))
        self.assertIsNone(self.failed())
        self.assertEqual(self.ask(al=0xFF, bx=1)[0x02], ord("$"))
        self.assertIsNone(self.failed())

    def test_another_country_is_error_2_and_nothing_is_written(self):
        for al, bx in ((7, 0), (0xFE, 0), (0xFF, 7), (0xFF, 0)):
            table = self.ask(al=al, bx=bx)
            self.assertEqual(self.failed(), 2, (al, bx))
            self.assertEqual(table, bytes([0xAA]) * 48)

    def test_the_table_may_go_round_the_end_of_the_segment(self):
        table = self.ask(dx=0xFFF0)
        self.assertEqual(table[0x02], ord("$"))
        self.assertEqual(table[0x10], 2)
        self.assertEqual(self.cpu.rb(0x2000, 0x0000), 2)  # the byte at 0x10 of the table is at the start of the segment
        self.assertEqual(table[0x16:0x18], b",\0")
        self.assertEqual(table[34:], bytes([0xAA]) * 14)

    def test_dx_ffff_sets_the_country_and_writes_nothing(self):
        cpu = self.cpu
        cpu.wb(0x2000, 0xFFFF, 0xAA)
        self.ask(al=1, dx=0xFFFF)
        self.assertIsNone(self.failed())
        self.assertEqual(cpu.rb(0x2000, 0xFFFF), 0xAA)
        self.assertEqual(cpu.r[CPU.BX], 0x1111)

    def test_setting_another_country_is_error_2(self):
        self.ask(al=7, dx=0xFFFF)
        self.assertEqual(self.failed(), 2)
        self.ask(al=0xFF, bx=7, dx=0xFFFF)
        self.assertEqual(self.failed(), 2)
        self.ask(al=0xFF, bx=1, dx=0xFFFF)
        self.assertIsNone(self.failed())
