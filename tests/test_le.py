import struct
import unittest
from pathlib import Path

from mydos import le
from tests.le_builder import F, Obj, PAGE, RW, RX, build_bound, build_program, mz_stub

REPOSITORY = Path(__file__).resolve().parent.parent
CODE = bytes(range(256)) * 17  # 4352 bytes: a page and some more
DATA = b"\xAA" * 100
BASES = {1: 0x400000, 2: 0x500000}


def program(fixups=(), **keywords):
    """An LE program of two objects: the code (3 pages, the last mostly zeros) and the data (2 pages)."""
    objects = [Obj(CODE, size=0x2100, base=0x10000, flags=RX), Obj(DATA, size=0x1800, base=0x30000, flags=RW)]
    return build_program(objects, entry=(1, 0x20), stack=(2, 0x1800), fixups=fixups, **keywords)


def word(data, at, size=4):
    return int.from_bytes(data[at:at + size], "little")


class TestTheHeader(unittest.TestCase):
    def setUp(self):
        self.image = le.LeImage(program())

    def test_what_the_header_says_about_the_program(self):
        image = self.image
        self.assertEqual((image.cpu, image.page_count, image.page_size), (2, 5, PAGE))
        self.assertEqual((image.entry_object, image.entry_offset), (1, 0x20))
        self.assertEqual((image.stack_object, image.stack_offset), (2, 0x1800))
        self.assertEqual(image.import_modules, 0)

    def test_the_objects_with_their_sizes_bases_and_pages(self):
        code, data = self.image.objects
        self.assertEqual((code.number, code.size, code.base), (1, 0x2100, 0x10000))
        self.assertEqual((code.first_page, code.page_count), (1, 3))
        self.assertEqual((data.number, data.size, data.base), (2, 0x1800, 0x30000))
        self.assertEqual((data.first_page, data.page_count), (4, 2))

    def test_the_flags_of_an_object_say_what_it_is(self):
        code, data = self.image.objects
        self.assertTrue(code.executable and code.big and not code.writable)
        self.assertTrue(data.writable and data.big and not data.executable)

    def test_the_entry_address_is_the_base_of_its_object_and_the_offset(self):
        self.assertEqual(self.image.entry_address, 0x10020)

    def test_an_object_that_is_not_there_is_an_error(self):
        for number in (0, 3, -1):
            with self.assertRaisesRegex(le.LeError, "no object"):
                self.image.object(number)

    def test_the_cpus_386_486_and_pentium_are_taken_and_the_286_is_not(self):
        for cpu in (2, 3, 4):
            self.assertEqual(le.LeImage(program(cpu=cpu)).cpu, cpu)
        for cpu in (0, 1, 5):
            with self.assertRaisesRegex(le.LeError, "cpu"):
                le.LeImage(program(cpu=cpu))

    def test_a_page_size_that_is_not_a_power_of_two_is_an_error(self):
        data = bytearray(program())
        header = word(data, 0x3C) + 0x28
        data[header:header + 4] = (3000).to_bytes(4, "little")
        with self.assertRaisesRegex(le.LeError, "power of two"):
            le.LeImage(bytes(data))

    def test_a_program_that_imports_from_other_modules_is_an_error(self):
        with self.assertRaisesRegex(le.LeError, "imports from 2 other modules"):
            le.LeImage(program(import_modules=2))

    def test_not_a_program_is_said_so(self):
        for data in (b"", b"MZ", b"MZ" + bytes(100), b"\x00" * 200, program()[:0x90], b"PE" + program()[2:]):
            with self.assertRaises(le.LeError):
                le.LeImage(data)

    def test_a_header_that_is_not_where_the_stub_says_is_an_error(self):
        data = bytearray(program())
        data[0x3C:0x40] = (0x100).to_bytes(4, "little")
        with self.assertRaisesRegex(le.LeError, "no LE header"):
            le.LeImage(bytes(data))

    def test_a_big_endian_file_is_not_one_of_ours(self):
        with self.assertRaises(le.LeError):
            le.LeImage(program(byte_order=1))

    def test_a_file_that_ends_with_the_header_has_no_objects_and_is_said_to_have_none(self):
        data = program()
        with self.assertRaisesRegex(le.LeError, "table of objects"):
            le.LeImage(data[:0x80 + 0xB0])  # the whole header that is read, and nothing after it
        with self.assertRaisesRegex(le.LeError, "no LE header"):
            le.LeImage(data[:0x80 + 0xB0 - 1])

    def test_the_tables_that_go_beyond_the_end_of_the_file_are_errors(self):
        data = program()
        with self.assertRaises(le.LeError):
            le.LeImage(data[:0x80 + 0xC4 + 30])  # the table of objects is cut
        with self.assertRaises(le.LeError):
            le.LeImage(data[:0x80 + 0xC4 + 48 + 10])  # the map of the pages is cut


class TestThePages(unittest.TestCase):
    def test_an_object_is_its_pages_cut_or_filled_with_zeros_to_its_virtual_size(self):
        image = le.LeImage(program())
        code = image.object_bytes(1)
        self.assertEqual(len(code), 0x2100)
        self.assertEqual(code[:len(CODE)], CODE)
        self.assertEqual(code[len(CODE):], bytes(0x2100 - len(CODE)))
        data = image.object_bytes(2)
        self.assertEqual((len(data), data[:100], data[100:]), (0x1800, DATA, bytes(0x1800 - 100)))

    def test_the_virtual_size_may_be_less_than_what_the_pages_hold(self):
        image = le.LeImage(build_program([Obj(b"\x11" * 5000, size=4200), Obj(b"x", size=1)]))
        self.assertEqual(image.object_bytes(1), b"\x11" * 4200)

    def test_an_object_with_a_virtual_size_that_is_more_than_its_data_has_zeros_after_it(self):
        image = le.LeImage(build_program([Obj(b"abc", size=10000), Obj(b"x", size=1)]))
        data = image.object_bytes(1)
        self.assertEqual((data[:3], len(data), any(data[3:])), (b"abc", 10000, False))

    def test_the_last_page_of_the_file_may_be_short(self):
        image = le.LeImage(build_program([Obj(b"\x01" * 5000), Obj(b"\x02" * 300)], last_page_size=300))
        self.assertEqual(image.object_bytes(2), b"\x02" * 300)
        self.assertEqual(image.page_bytes(image.page_count - 1), b"\x02" * 300 + bytes(PAGE - 300))

    def test_a_page_beyond_the_end_of_the_file_is_an_error(self):
        data = build_program([Obj(b"\x01" * 5000), Obj(b"\x02" * 300)])
        image = le.LeImage(data[:-1000])
        with self.assertRaisesRegex(le.LeError, "beyond the end"):
            image.object_bytes(2)

    def test_a_zero_filled_page_is_zeros_whatever_the_file_has_there(self):
        image = le.LeImage(build_program([Obj(b"\x07" * 8192), Obj(b"x", size=1)], page_kinds={1: le.PAGE_ZERO_FILLED}))
        self.assertEqual(image.object_bytes(1), b"\x07" * 4096 + bytes(4096))

    def test_the_pages_that_are_not_stored_plainly_are_errors(self):
        for kind in (le.PAGE_ITERATED, le.PAGE_INVALID, le.PAGE_RANGE):
            image = le.LeImage(build_program([Obj(b"\x07" * 8192), Obj(b"x", size=1)], page_kinds={1: kind}))
            with self.assertRaisesRegex(le.LeError, "not done"):
                image.object_bytes(1)

    def test_the_page_data_is_found_from_the_start_of_the_program_not_of_the_file(self):
        plain = program()
        bound = build_bound(plain, extender_size=0x400, kernel_size=0x1000)
        start = le.locate(bound)
        self.assertEqual(le.LeImage(bound, start).object_bytes(1), le.LeImage(plain).object_bytes(1))
        self.assertEqual(le.LeImage(bound, start).object_bytes(2), le.LeImage(plain).object_bytes(2))


class TestTheFixups(unittest.TestCase):
    def fixups(self, *specs, **keywords):
        return le.LeImage(program(specs, **keywords)).fixups()

    def test_a_32_bit_offset_fixup_is_read_whole(self):
        (f,) = self.fixups(F(le.SOURCE_OFFSET_32, 2, 0x10, obj=1, at=0x20))
        self.assertEqual(f, le.Fixup(page=0, source=7, offset=0x20, target_object=2, target_offset=0x10, additive=0))

    def test_the_page_and_the_offset_in_it_are_where_the_place_is(self):
        (f,) = self.fixups(F(le.SOURCE_OFFSET_32, 1, 0, obj=1, at=PAGE * 2 + 0x30))
        self.assertEqual((f.page, f.offset), (2, 0x30))
        (f,) = self.fixups(F(le.SOURCE_OFFSET_32, 1, 0, obj=2, at=PAGE + 8))
        self.assertEqual((f.page, f.offset), (4, 8))

    def test_the_fixups_come_in_the_order_of_the_pages(self):
        got = self.fixups(F(7, 1, 4, obj=2, at=PAGE), F(7, 1, 8, obj=1, at=0), F(7, 2, 12, obj=1, at=PAGE + 4))
        self.assertEqual([(f.page, f.target_offset) for f in got], [(0, 8), (1, 12), (4, 4)])

    def test_the_two_bytes_form_of_the_target_offset(self):
        (f,) = self.fixups(F(le.SOURCE_OFFSET_16, 2, 0x1234, obj=1, at=4, wide=False))
        self.assertEqual((f.source, f.target_offset), (5, 0x1234))

    def test_a_target_object_as_a_word(self):
        (f,) = self.fixups(F(7, 2, 5, obj=1, at=4, big_object=True))
        self.assertEqual(f.target_object, 2)

    def test_the_additive_in_a_word_and_in_a_dword(self):
        (f,) = self.fixups(F(7, 2, 5, obj=1, at=4, additive=0x77, wide_additive=False))
        self.assertEqual(f.additive, 0x77)
        (f,) = self.fixups(F(7, 2, 5, obj=1, at=4, additive=0x12345678, wide_additive=True))
        self.assertEqual(f.additive, 0x12345678)

    def test_the_width_of_the_additive_is_its_own_and_not_that_of_the_target_offset(self):
        got = self.fixups(F(7, 2, 5, obj=1, at=4, wide=False, additive=0x12345678, wide_additive=True),
                          F(7, 2, 6, obj=1, at=8, wide=True, additive=0x77, wide_additive=False),
                          F(7, 2, 7, obj=1, at=12, wide=False, additive=0x55, wide_additive=False),
                          F(7, 1, 9, obj=1, at=16))
        self.assertEqual([(f.target_offset, f.additive) for f in got], [(5, 0x12345678), (6, 0x77), (7, 0x55), (9, 0)])

    def test_a_selector_fixup_has_no_target_offset(self):
        (f,) = self.fixups(F(le.SOURCE_SELECTOR, 2, obj=1, at=8))
        self.assertEqual((f.source, f.target_object, f.target_offset), (2, 2, 0))

    def test_the_pointer_kinds(self):
        got = self.fixups(F(le.SOURCE_POINTER_16_16, 2, 0x44, obj=1, at=8, wide=False),
                          F(le.SOURCE_POINTER_16_32, 2, 0x55, obj=1, at=16))
        self.assertEqual([(f.source, f.target_offset) for f in got], [(3, 0x44), (6, 0x55)])

    def test_a_record_with_a_list_of_places_is_a_fixup_for_each_place(self):
        got = self.fixups(F(7, 1, 0x40, page=0, sources=[0x10, 0x20, 0x30]))
        self.assertEqual([(f.page, f.offset, f.target_offset) for f in got], [(0, 0x10, 0x40), (0, 0x20, 0x40),
                                                                           (0, 0x30, 0x40)])

    def test_a_list_of_places_with_the_additive_goes_on_to_the_next_record(self):
        got = self.fixups(F(7, 1, 0x40, page=0, sources=[0x10, 0x20], additive=5), F(7, 2, 9, obj=1, at=0x40))
        self.assertEqual([(f.offset, f.target_offset, f.additive) for f in got], [(0x10, 0x40, 5), (0x20, 0x40, 5),
                                                                                (0x40, 9, 0)])

    def test_a_place_that_begins_in_the_page_before_has_a_negative_offset(self):
        (f,) = self.fixups(F(7, 1, 0, page=1, offset=-2))
        self.assertEqual((f.page, f.offset), (1, -2))

    def test_the_fixups_of_a_program_with_none_are_an_empty_list(self):
        self.assertEqual(self.fixups(), [])

    def test_a_fixup_to_another_module_or_by_the_entry_table_is_an_error(self):
        for bits in (1, 2, 3):
            with self.assertRaisesRegex(le.LeError, "another module"):
                self.fixups(F(7, 1, 0, obj=1, at=4, flag_bits=bits))

    def test_a_fixup_to_an_alias_is_an_error(self):
        with self.assertRaisesRegex(le.LeError, "alias"):
            self.fixups(F(7, 1, 0, obj=1, at=4, source_bits=0x10))

    def test_the_table_of_the_fixups_that_is_cut_is_an_error(self):
        data = program((F(7, 1, 0, obj=1, at=4),))
        image = le.LeImage(data)
        image.data = data[:image.header_at + image._fixup_page_table + 8]
        with self.assertRaises(le.LeError):
            image.fixups()


class TestLoading(unittest.TestCase):
    def load(self, *specs, selector_of=None, bases=BASES, **keywords):
        return le.LeImage(program(specs, **keywords)).load(bases, selector_of)

    def test_a_32_bit_offset_is_the_base_of_the_target_object_and_the_offset(self):
        images = self.load(F(7, 2, 0x10, obj=1, at=0x20))
        self.assertEqual(word(images[1], 0x20), 0x500010)

    def test_the_bytes_around_the_place_are_not_touched(self):
        images = self.load(F(7, 2, 0x10, obj=1, at=0x20))
        original = le.LeImage(program()).object_bytes(1)
        self.assertEqual(images[1][:0x20] + images[1][0x24:], original[:0x20] + original[0x24:])

    def test_the_objects_without_a_fixup_are_as_they_are_in_the_file(self):
        images = self.load(F(7, 2, 0x10, obj=1, at=0x20))
        self.assertEqual(images[2], le.LeImage(program()).object_bytes(2))

    def test_the_fixups_in_the_second_object_and_in_later_pages_are_at_the_right_place(self):
        images = self.load(F(7, 1, 0x44, obj=2, at=PAGE + 0x10), F(7, 2, 0x88, obj=1, at=PAGE * 2 + 8))
        self.assertEqual(word(images[2], PAGE + 0x10), 0x400044)
        self.assertEqual(word(images[1], PAGE * 2 + 8), 0x500088)

    def test_the_additive_is_added_to_the_target(self):
        images = self.load(F(7, 2, 0x10, obj=1, at=0x20, additive=0x100))
        self.assertEqual(word(images[1], 0x20), 0x500110)

    def test_a_place_that_begins_in_the_page_before_goes_where_it_begins(self):
        images = self.load(F(7, 2, 0x10, page=1, offset=-2))
        self.assertEqual(word(images[1], PAGE - 2), 0x500010)

    def test_a_list_of_places_writes_the_same_address_to_each_of_them(self):
        images = self.load(F(7, 2, 0x10, page=0, sources=[0x10, 0x20, 0x30]))
        self.assertEqual([word(images[1], at) for at in (0x10, 0x20, 0x30)], [0x500010] * 3)

    def test_a_self_relative_offset_is_from_the_end_of_the_place_to_the_target(self):
        images = self.load(F(le.SOURCE_RELATIVE_32, 1, 0x1000, obj=1, at=0x40))
        self.assertEqual(word(images[1], 0x40), 0x1000 - 0x40 - 4)  # the same object: the bases cancel

    def test_a_self_relative_offset_to_another_object_counts_the_bases(self):
        images = self.load(F(le.SOURCE_RELATIVE_32, 2, 0x10, obj=1, at=0x40))
        self.assertEqual(word(images[1], 0x40), (0x500010 - (0x400040 + 4)) & 0xFFFFFFFF)

    def test_a_self_relative_offset_that_is_negative_is_a_dword_of_two_s_complement(self):
        images = self.load(F(le.SOURCE_RELATIVE_32, 1, 0, obj=1, at=0x1000))
        self.assertEqual(word(images[1], 0x1000), (0 - 0x1000 - 4) & 0xFFFFFFFF)

    def test_a_16_bit_offset_is_the_low_word_of_the_address(self):
        images = self.load(F(le.SOURCE_OFFSET_16, 2, 0x1234, obj=1, at=0x20, wide=False))
        self.assertEqual(word(images[1], 0x20, 2), (0x500000 + 0x1234) & 0xFFFF)
        self.assertEqual(images[1][0x22:0x24], le.LeImage(program()).object_bytes(1)[0x22:0x24])  # two bytes, no more

    def test_a_byte_is_the_low_byte_and_one_byte(self):
        images = self.load(F(le.SOURCE_BYTE, 2, 0x34, obj=1, at=0x20, wide=False))
        original = le.LeImage(program()).object_bytes(1)
        self.assertEqual(images[1][0x20], 0x34)
        self.assertEqual(images[1][0x21], original[0x21])

    def test_a_selector_is_what_the_loader_says_the_object_has_and_two_bytes_of_it(self):
        images = self.load(F(le.SOURCE_SELECTOR, 2, obj=1, at=0x20), selector_of=lambda number: 0x100 + number)
        original = le.LeImage(program()).object_bytes(1)
        self.assertEqual(word(images[1], 0x20, 2), 0x102)
        self.assertEqual(images[1][0x22:0x24], original[0x22:0x24])

    def test_a_pointer_16_16_and_16_32(self):
        images = self.load(F(le.SOURCE_POINTER_16_16, 2, 0x44, obj=1, at=0x20, wide=False),
                           F(le.SOURCE_POINTER_16_32, 2, 0x55, obj=1, at=0x40), selector_of=lambda number: 0x77)
        self.assertEqual(word(images[1], 0x20), 0x0077 << 16 | (0x500044 & 0xFFFF))
        self.assertEqual(word(images[1], 0x40), 0x500055)
        self.assertEqual(word(images[1], 0x44, 2), 0x77)  # the selector after the offset
        original = le.LeImage(program()).object_bytes(1)
        self.assertEqual(images[1][0x24:0x26], original[0x24:0x26])  # the 16:16 pointer is four bytes
        self.assertEqual(images[1][0x46:0x48], original[0x46:0x48])  # and the 16:32 one is six

    def test_a_selector_without_a_way_to_know_one_is_an_error(self):
        for kind, wide in ((le.SOURCE_SELECTOR, True), (le.SOURCE_POINTER_16_32, True),
                           (le.SOURCE_POINTER_16_16, False)):
            with self.assertRaisesRegex(le.LeError, "selector"):
                self.load(F(kind, 2, 0, obj=1, at=0x20, wide=wide))

    def test_a_fixup_that_goes_beyond_its_object_is_an_error(self):
        with self.assertRaisesRegex(le.LeError, "beyond"):
            self.load(F(7, 1, 0, page=2, offset=0x1FF))  # object 1 ends at 0x2100: this place ends at 0x2203
        with self.assertRaisesRegex(le.LeError, "beyond"):
            self.load(F(7, 1, 0, page=0, offset=-2))

    def test_the_value_is_cut_to_the_size_of_the_place(self):
        images = self.load(F(7, 2, 0xFFF0, obj=1, at=8), bases={1: 0x400000, 2: 0xFFFFF000})
        self.assertEqual(word(images[1], 8), (0xFFFFF000 + 0xFFF0) & 0xFFFFFFFF)

    def test_the_bases_the_loader_gives_are_the_ones_used_whatever_the_file_says(self):
        for bases in ({1: 0x10000, 2: 0x30000}, {1: 0x200000, 2: 0x7000000}):
            images = self.load(F(7, 2, 0x10, obj=1, at=0x20), bases=bases)
            self.assertEqual(word(images[1], 0x20), bases[2] + 0x10)

    def test_the_load_does_not_change_the_file_or_the_objects_of_a_second_load(self):
        image = le.LeImage(program((F(7, 2, 0x10, obj=1, at=0x20),)))
        first = image.load(BASES)
        second = image.load({1: 0x400000, 2: 0x600000})
        self.assertEqual(word(first[1], 0x20), 0x500010)
        self.assertEqual(word(second[1], 0x20), 0x600010)
        self.assertEqual(image.object_bytes(1)[0x20:0x24], CODE[0x20:0x24])


class TestFindingTheProgram(unittest.TestCase):
    def test_a_plain_program_starts_at_the_start_of_the_file(self):
        self.assertEqual(le.locate(program()), 0)

    def test_a_program_bound_to_the_extender_starts_after_the_kernel(self):
        plain = program()
        bound = build_bound(plain, extender_size=0x200, kernel_size=0x300)
        self.assertEqual(le.locate(bound), 0x500)
        self.assertEqual(le.LeImage(bound, 0x500).entry_address, 0x10020)

    def test_the_things_in_the_kernel_that_look_like_a_program_are_passed_over(self):
        bound = build_bound(program(), decoys=True)
        self.assertEqual(le.locate(bound), 0x500)
        self.assertEqual(le.locate(build_bound(program(), decoys=False)), 0x500)

    def test_a_decoy_that_has_a_stub_that_leads_to_an_le_that_is_not_one_is_passed_over_as_well(self):
        extender = mz_stub(0x200, 0x1000000)
        decoy = mz_stub(0x100, 0x40)  # leads to the bytes right after the stub, that are not an LE
        bound = extender + b"BW" + bytes(0x10) + decoy + b"\xAA" * 100 + program()
        self.assertEqual(le.locate(bound), 0x200 + 2 + 0x10 + 0x100 + 100)

    def test_is_bound_is_true_for_the_extender_and_false_for_the_plain_program(self):
        self.assertTrue(le.is_bound(build_bound(program())))
        self.assertFalse(le.is_bound(program()))

    def test_is_le_program_is_true_for_both_and_false_for_the_rest(self):
        self.assertTrue(le.is_le_program(program()))
        self.assertTrue(le.is_le_program(build_bound(program())))
        for data in (b"", b"\xB8\x00\x4C\xCD\x21", mz_stub(0x100, 0x40), b"MZ" + bytes(100), b"ZM" + bytes(0x80)):
            self.assertFalse(le.is_le_program(data), data[:8])

    def test_a_dos_exe_that_has_a_number_for_e_lfanew_that_is_not_an_le_is_not_one(self):
        exe = bytearray(mz_stub(0x200, 0x100))
        exe[0x100:0x104] = b"NE\0\0"
        self.assertIsNone(le.locate(bytes(exe)))

    def test_a_program_after_a_stub_that_has_no_kernel_is_not_a_bound_one(self):
        extender = mz_stub(0x200, 0x1000000)
        self.assertIsNone(le.locate(extender + b"XX" + bytes(0x10) + program()))
        self.assertEqual(le.locate(extender + b"BW" + bytes(0x10) + program()), 0x200 + 2 + 0x10)

    def test_a_dos_16_m_program_without_the_program_after_it_has_none(self):
        extender = mz_stub(0x200, 0x1000000)
        self.assertIsNone(le.locate(extender + b"BW" + bytes(0x100)))
        self.assertIsNone(le.locate(extender + b"XX" + bytes(0x100)))  # no kernel: not bound

    def test_the_stub_size_of_a_file_with_a_whole_last_page_is_read_right(self):
        stub = bytearray(mz_stub(0x400, 0x1000000))
        stub[2:4] = (0).to_bytes(2, "little")  # cblp 0: the last page is whole
        stub[4:6] = (2).to_bytes(2, "little")
        bound = bytes(stub) + b"BW" + bytes(0x10) + program()
        self.assertEqual(le.locate(bound), 0x400 + 2 + 0x10)

    def test_a_file_that_is_cut_does_not_break_it(self):
        bound = build_bound(program())
        for length in (0, 1, 2, 0x3F, 0x40, 0x41, 0x1FF, 0x200, 0x201, 0x4FF, 0x500, 0x520, len(bound) - 1):
            le.locate(bound[:length])  # a result or None, not an exception


@unittest.skipUnless((REPOSITORY / "XCOMA" / "UFOEXE" / "SMKP.EXE").exists(), "the game is not installed in XCOMA/")
class TestTheGame(unittest.TestCase):
    """The programs of the game as they are: bound to DOS/4G, and Watcom's LE inside them."""

    def image(self, name):
        data = (REPOSITORY / "XCOMA" / name).read_bytes()
        return data, le.LeImage(data, le.locate(data))

    def test_smkp_is_found_after_the_extender(self):
        data, image = self.image("UFOEXE/SMKP.EXE")
        self.assertTrue(le.is_bound(data))
        self.assertEqual(le.locate(data), 0x26654)
        self.assertEqual(image.header_at, 0x292E4)
        self.assertEqual((image.cpu, image.page_count, image.page_size), (2, 21, 0x1000))
        code, data_object = image.objects
        self.assertEqual((code.base, code.size, code.executable, code.big), (0x10000, 0x11380, True, True))
        self.assertEqual((data_object.base, data_object.size, data_object.writable), (0x30000, 0xFA70, True))
        self.assertEqual((image.entry_object, image.entry_offset, image.stack_object, image.stack_offset),
                         (1, 0x6CB0, 2, 0xFA70))

    def test_the_big_programs_are_found_too(self):
        for name, start, pages, bases in (("UFOEXE/UFO2P.EXE", 0x352A4, 303, (0x10000, 0xD0000)),
                                          ("TACEXE/TACP.EXE", 0x352A4, 666, (0x10000, 0xE0000))):
            _, image = self.image(name)
            self.assertEqual((image.start, image.page_count, tuple(obj.base for obj in image.objects)),
                             (start, pages, bases), name)

    def test_the_code_at_the_entry_is_the_start_of_the_runtime_of_watcom(self):
        _, image = self.image("UFOEXE/SMKP.EXE")
        code = image.object_bytes(1)
        self.assertEqual(code[image.entry_offset:image.entry_offset + 2], b"\xEB\x76")
        self.assertIn(b"WATCOM C/C++32 Run-Time system", code[image.entry_offset:image.entry_offset + 64])

    def test_the_fixups_are_all_internal_offsets_and_the_program_loads(self):
        _, image = self.image("UFOEXE/SMKP.EXE")
        fixups = image.fixups()
        self.assertEqual(len(fixups), 2299)
        self.assertEqual({f.source for f in fixups}, {le.SOURCE_OFFSET_32})
        self.assertEqual({f.target_object for f in fixups}, {1, 2})
        images = image.load({1: 0x200000, 2: 0x400000})
        self.assertEqual((len(images[1]), len(images[2])), (0x11380, 0xFA70))
        # every address that a fixup wrote is inside the object it names
        sizes = {1: 0x11380, 2: 0xFA70}
        bases = {1: 0x200000, 2: 0x400000}
        for f in fixups[:500]:
            owner = image.objects[0] if f.page < 18 else image.objects[1]
            where = (f.page - (owner.first_page - 1)) * image.page_size + f.offset
            value = word(images[owner.number], where)
            low = bases[f.target_object]
            self.assertTrue(low <= value <= low + sizes[f.target_object] + 0x10000)

    def test_the_selectors_of_the_big_programs_are_asked_for(self):
        _, image = self.image("UFOEXE/UFO2P.EXE")
        kinds = {f.source for f in image.fixups()}
        self.assertTrue({le.SOURCE_SELECTOR, le.SOURCE_POINTER_16_32} <= kinds)
        with self.assertRaisesRegex(le.LeError, "selector"):
            image.load({1: 0x200000, 2: 0x400000})
        image.load({1: 0x200000, 2: 0x800000}, selector_of=lambda number: 0x20 + number)

    def test_a_plain_dos_program_of_the_game_is_not_an_le_program(self):
        data = (REPOSITORY / "XCOMA" / "XCOMAPOC.EXE").read_bytes()
        self.assertFalse(le.is_le_program(data))


if __name__ == "__main__":
    unittest.main()
