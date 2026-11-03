"""INT 21h of a 32-bit program (step 3c2): the functions that the machine does for both modes take their pointers and
counts of 32 bits in the flat mode (EDX, ECX), and give a count in all of EAX. The buffers and the names are placed
above 64 KB, behind a selector that has a base, so that a 16-bit offset or a base that is forgotten shows."""
import unittest

from mydos import flags
from mydos.cpu import CPU, Exit
from mydos.loader import CODE_SELECTOR, DATA_SELECTOR
from tests import test_dos

BASED = 0x2F  # a selector with a base: what is at its offset 0 is at BASE
BASE = 0x00100000
CODE_AT = 0x00200000


class FlatFiles(test_dos.FileFunctionsTest):
    def setUp(self):
        super().setUp()
        self.cpu.enter_flat({CODE_SELECTOR: 0, DATA_SELECTOR: 0, BASED: BASE})
        self.cpu.s[CPU.DS] = BASED
        self.cpu.s[CPU.CS] = CODE_SELECTOR
        self.cpu.mem[CODE_AT:CODE_AT + 2] = b"\xCD\x21"

    def call32(self, eax, ebx=0, ecx=0, edx=0):
        cpu = self.cpu
        for register, value in ((CPU.AX, eax), (CPU.BX, ebx), (CPU.CX, ecx), (CPU.DX, edx)):
            cpu.set32(register, value)
        cpu.ip = CODE_AT
        cpu.step()

    def put(self, offset, data):
        """Bytes at the offset of the selector with the base."""
        self.cpu.mem[BASE + offset:BASE + offset + len(data)] = data

    def get(self, offset, size):
        return bytes(self.cpu.mem[BASE + offset:BASE + offset + size])

    def open_file(self, name, access=0, at=0x00120000):
        self.put(at, name.encode("cp437") + b"\0")
        self.call32(0x3D00 | access, edx=at)
        self.assertIsNone(self.failed())
        return self.cpu.r[CPU.AX]


class TestRealModeIsAsItWas(test_dos.FileFunctionsTest):
    """A program of 16 bits has the high halves of the registers that it left there, and DOS does not look at them."""

    def test_a_name_a_count_and_an_answer_use_the_low_words_only(self):
        cpu = self.cpu
        (self.base / "BIG.DAT").write_bytes(bytes(range(256)) * 4)
        self.call(0x3D00, "BIG.DAT")
        handle = cpu.r[CPU.AX]
        cpu.rh[CPU.DX], cpu.rh[CPU.CX], cpu.rh[CPU.AX] = 0xFFFF, 0x0001, 0x7777
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x3F00, handle, 100, 0x0300
        cpu.ip = 0
        cpu.step()
        self.assertEqual((self.failed(), cpu.r[CPU.AX], cpu.rh[CPU.AX]), (None, 100, 0x7777))  # the count is CX only
        self.assertEqual(bytes(cpu.rb(0x2000, 0x0300 + i) for i in range(4)), bytes([0, 1, 2, 3]))

    def test_a_name_is_read_from_dx_and_not_edx(self):
        self.cpu.rh[CPU.DX] = 0x0001
        self.call(0x3D00, "NOTES.TXT")
        self.assertIsNone(self.failed())
        self.cpu.rh[CPU.DX] = 0x0001  # call() puts the name with a 16-bit offset, and DX is that
        self.call(0x4E00, "NOTES.TXT", cx=0x20)
        self.assertIsNone(self.failed())


class TestTheDta(FlatFiles):
    def test_the_disk_transfer_area_is_an_offset_of_32_bits(self):
        self.call32(0x1AC7, edx=0x00123456)  # AL is of no use to this function
        self.assertEqual(self.state.dta, (BASED, 0x00123456))

    def test_a_search_puts_what_it_found_at_the_dta_whatever_its_address(self):
        (self.base / "A.TXT").write_bytes(b"1234")
        (self.base / "B.TXT").write_bytes(b"12")
        dta, name = 0x00150000, 0x00160000
        self.call32(0x1A00, edx=dta)
        self.put(name, b"*.TXT\0")
        self.call32(0x4E00, ecx=0xABCD0020, edx=name)  # the attributes are the low word of ECX
        found = {}
        while self.failed() is None:
            found[self.get(dta + 0x1E, 12).split(b"\0")[0]] = int.from_bytes(self.get(dta + 0x1A, 4), "little")
            self.call32(0x4F00)
        self.assertEqual(self.failed(), 18)  # no more files
        self.assertEqual(found, {b"A.TXT": 4, b"B.TXT": 2, b"NOTES.TXT": 10})

    def test_a_search_for_a_name_that_is_not_there_is_error_2(self):
        self.put(0x00160000, b"NONE.TXT\0")
        self.call32(0x4E00, ecx=0x20, edx=0x00160000)
        self.assertEqual(self.failed(), 2)


class TestFiles(FlatFiles):
    def setUp(self):
        super().setUp()
        self.big = bytes(i * 7 & 0xFF for i in range(100000))
        (self.base / "BIG.DAT").write_bytes(self.big)

    def test_a_file_is_opened_by_a_name_that_is_above_64_kb(self):
        handle = self.open_file("BIG.DAT")
        self.assertEqual(self.state.handles.get(handle).dos_path, "C:\\BIG.DAT")

    def test_a_file_that_is_not_there_is_error_2_and_the_name_is_looked_for_at_the_whole_offset(self):
        self.put(0x00180000, b"NOPE.DAT\0")
        self.put(0x00000100, b"BIG.DAT\0")  # what a 16-bit offset would find
        self.call32(0x3D00, edx=0x00180000)
        self.assertEqual(self.failed(), 2)

    def test_a_read_of_more_than_64_kb_gives_all_of_eax_and_fills_a_buffer_that_is_above_64_kb(self):
        handle = self.open_file("BIG.DAT")
        buffer = 0x00200000
        self.put(buffer + 100000, b"\xEE" * 16)  # what is beyond the end of the file must be left alone
        self.call32(0x3F00, ebx=handle, ecx=0x00020000, edx=buffer)
        self.assertIsNone(self.failed())
        self.assertEqual(self.cpu.get32(CPU.AX), 100000)  # more than 65535: it does not fit AX
        self.assertEqual(self.get(buffer, 100000), self.big)
        self.assertEqual(self.get(buffer + 100000, 16), b"\xEE" * 16)
        self.call32(0x3F00, ebx=handle, ecx=0x00020000, edx=buffer)
        self.assertEqual((self.failed(), self.cpu.get32(CPU.AX)), (None, 0))  # the end of the file

    def test_a_buffer_that_is_above_16_mb_is_filled_at_that_address(self):
        handle = self.open_file("BIG.DAT")
        buffer = 0x02F00000  # the base is added: this is at 48 MB, and the offset alone would wrap at 16 MB and 64 KB
        self.call32(0x3F00, ebx=handle, ecx=64, edx=buffer)
        self.assertEqual(self.get(buffer, 64), self.big[:64])
        self.assertEqual(self.get(0, 4), bytes(4))
        self.put(0x03100000, b"WRIT")
        (self.base / "OUT2.DAT").write_bytes(b"")
        out = self.open_file("OUT2.DAT", access=1)
        self.call32(0x4000, ebx=out, ecx=4, edx=0x03100000)
        self.state.handles.close_all()
        self.assertEqual((self.base / "OUT2.DAT").read_bytes(), b"WRIT")

    def test_a_read_gives_the_high_half_of_eax_clean(self):
        handle = self.open_file("BIG.DAT")
        self.call32(0xFFFF3F00, ebx=handle, ecx=10, edx=0x00200000)
        self.assertEqual(self.cpu.get32(CPU.AX), 10)

    def test_a_write_takes_a_count_of_more_than_64_kb_from_a_buffer_that_is_above_it(self):
        self.put(0x00210000, b"x" * 70000)
        (self.base / "OUT.DAT").write_bytes(b"")
        handle = self.open_file("OUT.DAT", access=1)
        self.call32(0x4000, ebx=handle, ecx=70000, edx=0x00210000)
        self.assertIsNone(self.failed())
        self.assertEqual(self.cpu.get32(CPU.AX), 70000)
        self.state.handles.close_all()
        self.assertEqual((self.base / "OUT.DAT").read_bytes(), b"x" * 70000)

    def test_a_write_to_the_screen_of_a_buffer_above_64_kb(self):
        import contextlib
        import io
        self.put(0x00220000, b"hello")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.call32(0x4000, ebx=1, ecx=5, edx=0x00220000)
        self.assertEqual((out.getvalue(), self.cpu.get32(CPU.AX)), ("hello", 5))

    def test_a_write_to_the_screen_of_more_than_64_kb_gives_the_count_in_all_of_eax(self):
        import contextlib
        import io
        self.put(0x00230000, b"z" * 70000)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.call32(0x4000, ebx=1, ecx=70000, edx=0x00230000)
        self.assertEqual((len(out.getvalue()), self.cpu.get32(CPU.AX)), (70000, 70000))

    def test_the_position_is_of_32_bits_in_cx_and_dx_and_comes_back_in_dx_and_ax(self):
        handle = self.open_file("BIG.DAT")
        self.call32(0x4200, ebx=handle, ecx=0, edx=0x1000)  # from the start
        self.assertEqual((self.failed(), self.cpu.r[CPU.DX], self.cpu.r[CPU.AX]), (None, 0, 0x1000))
        self.call32(0x4201, ebx=handle, ecx=0, edx=0x00000010 + 0x10000)  # the low word is DX: 16 more, 0 words
        self.assertEqual(self.cpu.r[CPU.AX], 0x1010)
        self.call32(0x4202, ebx=handle, ecx=0, edx=0)  # the end
        self.assertEqual((self.cpu.r[CPU.DX], self.cpu.r[CPU.AX]), (1, 100000 - 65536))
        self.call32(0x4200, ebx=handle, ecx=0x0001, edx=0x0002)  # CX:DX = 10002h
        self.assertEqual((self.cpu.r[CPU.DX], self.cpu.r[CPU.AX]), (1, 2))
        self.call32(0x3F00, ebx=handle, ecx=4, edx=0x00200000)
        self.assertEqual(self.get(0x00200000, 4), self.big[0x10002:0x10006])

    def test_a_file_is_closed_by_the_handle_in_bx_and_asked_about_by_ioctl(self):
        handle = self.open_file("BIG.DAT")
        self.call32(0x4400, ebx=handle)
        self.assertEqual((self.failed(), self.cpu.r[CPU.DX] & 0xFF), (None, 2))  # a file on C:, not a device
        self.call32(0x3E00, ebx=handle)
        self.assertIsNone(self.failed())
        self.call32(0x3E00, ebx=handle)
        self.assertEqual(self.failed(), 6)

    def test_the_functions_that_have_not_been_asked_for_stop_the_emulator_with_the_mode(self):
        for ax in (0x3C00, 0x4100, 0x4300, 0x4700, 0x4800, 0x0900):
            with self.assertRaisesRegex(NotImplementedError, f"INT 21h AX={ax:04X} in the flat mode"):
                self.call32(ax)

    def test_the_check_of_the_input_changes_AL_only_and_says_whether_a_key_waits(self):
        from types import SimpleNamespace
        self.cpu.set32(CPU.AX, 0x12340B02)  # AL is not an argument
        self.cpu.keyboard = SimpleNamespace(buffer=[])
        self.cpu.ip = CODE_AT
        self.cpu.step()
        self.assertEqual(self.cpu.get32(CPU.AX), 0x12340B00)
        self.cpu.keyboard.buffer.append(0x1E61)
        self.cpu.ip = CODE_AT
        self.cpu.step()
        self.assertEqual(self.cpu.get32(CPU.AX), 0x12340BFF)

    def test_a_vector_is_set_to_ds_edx_and_given_back_in_es_ebx(self):
        self.call32(0x250F, edx=0x00123456)
        self.assertIsNone(self.failed())
        self.assertEqual(self.cpu.pm_vectors[0x0F], (BASED, 0x00123456))
        self.call32(0x350F)
        self.assertEqual((self.cpu.s[CPU.ES], self.cpu.get32(CPU.BX)), (BASED, 0x00123456))

    def test_a_vector_that_is_not_set_gives_the_handler_of_the_rom_through_the_code_selector(self):
        self.call32(0x350F)
        self.assertEqual((self.cpu.s[CPU.ES], self.cpu.get32(CPU.BX)), (CODE_SELECTOR, 0xF0010))
        self.call32(0x3560)
        self.assertEqual(self.cpu.get32(CPU.BX), 0xF0001)

    def test_a_vector_that_is_a_function_of_ours_has_no_address_to_give(self):
        with self.assertRaisesRegex(NotImplementedError, "function of the emulator"):
            self.call32(0x3521)

    def test_every_question_whether_a_key_waits_lets_the_time_go_on(self):
        from types import SimpleNamespace
        from mydos import dos32
        self.cpu.keyboard = SimpleNamespace(buffer=[])
        before = self.cpu.instructions
        self.call32(0x0B00)
        self.assertEqual(self.cpu.instructions - before, 1 + dos32.IDLE_INSTRUCTIONS)

    def test_the_other_calls_do_not(self):
        before = self.cpu.instructions
        self.call32(0x3000)
        self.assertEqual(self.cpu.instructions - before, 1)

    def test_the_end_of_the_program_still_ends_the_emulator(self):
        with self.assertRaises(Exit) as stop:
            self.call32(0x4C07)
        self.assertEqual(stop.exception.code, 7)


if __name__ == "__main__":
    unittest.main()
