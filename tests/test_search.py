import time
import unittest

from dosloth import search
from dosloth.filesystem import ATTR_ARCHIVE, ATTR_DIRECTORY, ATTR_HIDDEN, ATTR_READONLY, ATTR_SYSTEM, ATTR_VOLUME, Entry
from dosloth.search import MAX_SEARCHES, RECORD_SIZE, Searches, attributes_allow, find, mask_matches, record


def entry(name, size=0, attributes=ATTR_ARCHIVE, mtime=0.0):
    return Entry(name, bool(attributes & ATTR_DIRECTORY), size, mtime, attributes)


class TestMasks(unittest.TestCase):
    def check(self, rows):
        for mask, name, expected in rows:
            self.assertEqual(mask_matches(mask, name), expected, f"{mask} against {name}")

    def test_a_name_with_no_wildcard_must_be_the_same_whatever_the_case(self):
        self.check([("XCOM3.CFG", "XCOM3.CFG", True), ("xcom3.cfg", "XCOM3.CFG", True),
                    ("XCOM3.CFG", "xcom3.cfg", True), ("XCOM3.CFG", "XCOM3.CF", False),
                    ("XCOM3.CFG", "XCOM.CFG", False), ("XCOM3.CFG", "XCOM3.CFGX", False),
                    ("XCOM3.CFG", "XCOM33.CFG", False), ("A", "A", True), ("A", "B", False), ("A.B", "A.B", True),
                    ("A.B", "A", False), ("A.B", "A.BC", False)])

    def test_star_dot_star_and_a_lone_star_find_everything(self):
        names = ["A", "A.B", "ABCDEFGH", "ABCDEFGH.XYZ", "X.TXT", "NOEXT", "1.2"]
        for mask in ("*.*", "*", "????????.???", "*.???"):
            for name in names:
                self.assertTrue(mask_matches(mask, name), f"{mask} against {name}")

    def test_star_dot_alone_finds_the_names_with_no_extension_only(self):
        self.check([("*.", "NOEXT", True), ("*.", "A", True), ("*.", "A.B", False), ("*.", "X.TXT", False)])

    def test_the_extension_and_the_name_are_matched_apart(self):
        self.check([("*.CFG", "XCOM3.CFG", True), ("*.CFG", "XCOM3.TXT", False), ("*.CFG", "CFG", False),
                    ("*.C*", "A.CFG", True), ("*.C*", "A.C", True), ("*.C*", "A.XCFG", False), ("*.C*", "A", False),
                    ("NOTES.*", "NOTES.TXT", True), ("NOTES.*", "NOTES", True), ("NOTES.*", "NOTES2.TXT", False),
                    ("X*.TXT", "X.TXT", True), ("X*.TXT", "XYZ.TXT", True), ("X*.TXT", "YXZ.TXT", False)])

    def test_a_mask_with_no_dot_and_no_star_means_a_name_with_no_extension(self):
        self.check([("NOTES", "NOTES", True), ("NOTES", "NOTES.TXT", False), ("NOTES.", "NOTES", True),
                    ("NOTES.", "NOTES.TXT", False)])

    def test_a_star_at_the_end_with_no_dot_means_any_extension_too(self):
        self.check([("AB*", "AB", True), ("AB*", "AB.TXT", True), ("AB*", "ABCDEF.TXT", True), ("AB*", "A.TXT", False),
                    ("AB*", "XAB.TXT", False)])

    def test_a_question_mark_is_one_character_or_none_at_the_end(self):
        self.check([("?.TXT", "A.TXT", True), ("?.TXT", "AB.TXT", False), ("A??.TXT", "A.TXT", True),
                    ("A??.TXT", "AB.TXT", True), ("A??.TXT", "ABC.TXT", True), ("A??.TXT", "ABCD.TXT", False),
                    ("A?C.TXT", "ABC.TXT", True), ("A?C.TXT", "AC.TXT", False), ("A?C.TXT", "AXD.TXT", False),
                    ("NOTES.T?T", "NOTES.TXT", True), ("NOTES.T?T", "NOTES.TT", False), ("NOTES.???", "NOTES", True),
                    ("NOTES.???", "NOTES.A", True), ("NOTES.?", "NOTES.AB", False)])

    def test_a_mask_is_cut_at_its_first_dot_and_the_star_ends_the_part_it_is_in(self):
        self.check([("A.*.C", "A.TXT", True), ("A.*.C", "B.TXT", False), ("A.B.C", "A.B", False)])

    def test_a_pattern_longer_than_the_name_or_the_extension_never_fits_even_with_question_marks(self):
        self.check([("A.????", "A.TXT", False), ("?????????", "ABCDEFGH", False), ("A.????", "A", False),
                    ("????????.????", "ABCDEFGH.XYZ", False), ("A????????", "A", False)])

    def test_what_follows_a_star_in_the_name_is_not_looked_at(self):
        self.check([("A*B", "AXYZ", True), ("A*B", "BXYZ", False), ("A*B.TXT", "AQ.TXT", True), ("*.T*T", "A.TQ", True),
                    ("*.T*T", "A.XYZ", False)])

    def test_a_mask_too_long_for_a_name_finds_nothing_and_so_does_one_with_many_dots(self):
        self.check([("ABCDEFGHI.TXT", "ABCDEFGH.TXT", False), ("ABCDEFGHI", "ABCDEFGH", False),
                    ("A.TEXT", "A.TEX", False), ("A.B.C", "A.B", False), ("A.B.C", "A", False)])

    def test_eight_and_three_characters_are_the_most_a_name_has(self):
        self.check([("ABCDEFGH.XYZ", "ABCDEFGH.XYZ", True), ("????????.???", "ABCDEFGH.XYZ", True),
                    ("ABCDEFG?.XY?", "ABCDEFGH.XYZ", True), ("ABCDEFG?.XY?", "ABCDEFG.XY", True)])

    def test_every_name_of_a_directory_is_found_by_its_own_name_and_by_the_name_with_question_marks(self):
        names = ["A", "A.B", "ABCDEFGH.XYZ", "XCOM3.CFG", "1.2", "ZZ.Z"]
        for name in names:
            self.assertTrue(mask_matches(name, name), name)
            stem, dot, extension = name.partition(".")
            made = "?" * len(stem) + dot + "?" * len(extension)
            self.assertTrue(mask_matches(made, name), made)
            for other in names:
                if other != name:
                    self.assertFalse(mask_matches(name, other), f"{name} against {other}")


class TestAttributes(unittest.TestCase):
    ORDINARY, READONLY = ATTR_ARCHIVE, ATTR_READONLY | ATTR_ARCHIVE

    def test_ordinary_and_read_only_files_always_come_whatever_is_asked(self):
        for asked in (0, ATTR_READONLY, ATTR_ARCHIVE, ATTR_HIDDEN, ATTR_DIRECTORY, 0x37, 0xFFFF):
            if asked & ATTR_VOLUME:
                continue
            self.assertTrue(attributes_allow(self.ORDINARY, asked), hex(asked))
            self.assertTrue(attributes_allow(self.READONLY, asked), hex(asked))
            self.assertTrue(attributes_allow(0, asked), hex(asked))

    def test_hidden_system_and_directories_come_only_when_asked_for(self):
        for kind in (ATTR_HIDDEN, ATTR_SYSTEM, ATTR_DIRECTORY):
            self.assertFalse(attributes_allow(kind, 0), hex(kind))
            self.assertFalse(attributes_allow(kind | ATTR_ARCHIVE, ATTR_READONLY | ATTR_ARCHIVE), hex(kind))
            self.assertTrue(attributes_allow(kind, kind), hex(kind))
            self.assertTrue(attributes_allow(kind | ATTR_ARCHIVE, kind), hex(kind))
            self.assertTrue(attributes_allow(kind, 0x37), hex(kind))

    def test_each_kind_has_to_be_asked_for_by_itself(self):
        self.assertFalse(attributes_allow(ATTR_HIDDEN | ATTR_SYSTEM, ATTR_HIDDEN))
        self.assertFalse(attributes_allow(ATTR_HIDDEN | ATTR_SYSTEM, ATTR_SYSTEM))
        self.assertTrue(attributes_allow(ATTR_HIDDEN | ATTR_SYSTEM, ATTR_HIDDEN | ATTR_SYSTEM))
        self.assertFalse(attributes_allow(ATTR_DIRECTORY, ATTR_HIDDEN | ATTR_SYSTEM))

    def test_asking_for_the_label_alone_finds_no_file(self):
        for attributes in (self.ORDINARY, ATTR_DIRECTORY, ATTR_HIDDEN, 0):
            self.assertFalse(attributes_allow(attributes, ATTR_VOLUME), hex(attributes))

    def test_asking_for_the_label_and_other_kinds_finds_those_kinds(self):
        self.assertTrue(attributes_allow(self.ORDINARY, ATTR_VOLUME | ATTR_HIDDEN))
        self.assertTrue(attributes_allow(ATTR_HIDDEN, ATTR_VOLUME | ATTR_HIDDEN))
        self.assertFalse(attributes_allow(ATTR_DIRECTORY, ATTR_VOLUME | ATTR_HIDDEN))
        self.assertTrue(attributes_allow(ATTR_DIRECTORY, ATTR_VOLUME | ATTR_DIRECTORY))


class TestFind(unittest.TestCase):
    ENTRIES = [entry("A.DAT"), entry("B.DAT", attributes=ATTR_ARCHIVE | ATTR_READONLY), entry("C.TXT"),
               entry("GAMES", attributes=ATTR_DIRECTORY), entry("HID.DAT", attributes=ATTR_HIDDEN)]

    def names(self, mask, asked):
        return [e.name for e in find(self.ENTRIES, mask, asked)]

    def test_the_order_of_the_directory_is_kept_and_only_what_fits_comes(self):
        self.assertEqual(self.names("*.DAT", 0), ["A.DAT", "B.DAT"])
        self.assertEqual(self.names("*.*", 0), ["A.DAT", "B.DAT", "C.TXT"])
        self.assertEqual(self.names("C.TXT", 0), ["C.TXT"])
        self.assertEqual(self.names("NOPE.*", 0), [])

    def test_the_kinds_asked_for_add_to_the_ordinary_files(self):
        self.assertEqual(self.names("*.*", ATTR_DIRECTORY), ["A.DAT", "B.DAT", "C.TXT", "GAMES"])
        self.assertEqual(self.names("*.DAT", ATTR_HIDDEN), ["A.DAT", "B.DAT", "HID.DAT"])
        self.assertEqual(self.names("*", 0x17), ["A.DAT", "B.DAT", "C.TXT", "GAMES", "HID.DAT"])

    def test_an_empty_directory_gives_nothing(self):
        self.assertEqual(find([], "*.*", 0x37), [])


class TestTheRecord(unittest.TestCase):
    def test_the_size_and_the_places_of_the_fields(self):
        moment = time.mktime((1997, 6, 20, 13, 45, 58, 0, 0, -1))
        found = entry("XCOM3.CFG", size=0x01020304, attributes=ATTR_ARCHIVE | ATTR_READONLY, mtime=moment)
        data = record(0x1234, found)
        self.assertEqual(len(data), RECORD_SIZE)
        self.assertEqual(RECORD_SIZE, 43)
        self.assertEqual(data[0:2], b"\x34\x12")  # the number of the search
        self.assertEqual(data[2:0x15], bytes(19))  # DOS's own bytes: nothing of ours
        self.assertEqual(data[0x15], 0x21)
        self.assertEqual(int.from_bytes(data[0x16:0x18], "little"), 13 << 11 | 45 << 5 | 29)  # 58 seconds is 29 twos
        self.assertEqual(int.from_bytes(data[0x18:0x1A], "little"), (1997 - 1980) << 9 | 6 << 5 | 20)
        self.assertEqual(data[0x1A:0x1E], b"\x04\x03\x02\x01")
        self.assertEqual(data[0x1E:0x28], b"XCOM3.CFG\0")
        self.assertEqual(data[0x28:], bytes(3))

    def test_the_longest_name_has_a_zero_after_it(self):
        data = record(1, entry("ABCDEFGH.XYZ"))
        self.assertEqual(data[0x1E:0x2B], b"ABCDEFGH.XYZ\0")
        self.assertEqual(len(data), 43)

    def test_a_directory_has_its_bit_and_size_0(self):
        data = record(1, entry("GAMES", attributes=ATTR_DIRECTORY))
        self.assertEqual((data[0x15], data[0x1A:0x1E]), (0x10, bytes(4)))

    def test_the_seconds_are_halved_and_never_more_than_29_a_minute(self):
        for second, stored in ((0, 0), (1, 0), (2, 1), (58, 29), (59, 29)):
            moment = time.mktime((2001, 3, 4, 5, 6, second, 0, 0, -1))
            data = record(1, entry("A", mtime=moment))
            self.assertEqual(int.from_bytes(data[0x16:0x18], "little") & 0x1F, stored, second)

    def test_a_file_dated_after_2107_gets_2107_with_its_month_and_day(self):
        data = record(1, entry("A", mtime=time.mktime((2200, 3, 9, 1, 2, 4, 0, 0, -1))))
        self.assertEqual(int.from_bytes(data[0x18:0x1A], "little"), 127 << 9 | 3 << 5 | 9)

    def test_a_leap_second_does_not_overflow_the_field(self):
        from unittest import mock
        leap = time.struct_time((1999, 12, 31, 23, 59, 60, 4, 365, 0))
        with mock.patch("dosloth.search.time.localtime", return_value=leap):
            data = record(1, entry("A"))
        self.assertEqual(int.from_bytes(data[0x16:0x18], "little"), 23 << 11 | 59 << 5 | 29)

    def test_the_date_and_time_of_the_first_and_the_last_moment_dos_can_hold(self):
        first = record(1, entry("A", mtime=time.mktime((1980, 1, 1, 0, 0, 0, 0, 0, -1))))
        self.assertEqual((int.from_bytes(first[0x16:0x18], "little"), int.from_bytes(first[0x18:0x1A], "little")),
                         (0, 1 << 5 | 1))
        last = record(1, entry("A", mtime=time.mktime((2107, 12, 31, 23, 59, 58, 0, 0, -1))))
        self.assertEqual(int.from_bytes(last[0x18:0x1A], "little"), 127 << 9 | 12 << 5 | 31)

    def test_a_file_older_than_1980_gets_the_first_day_of_1980(self):
        data = record(1, entry("A", mtime=time.mktime((1970, 5, 6, 7, 8, 9, 0, 0, -1))))
        self.assertEqual(int.from_bytes(data[0x18:0x1A], "little"), 1 << 5 | 1)
        self.assertEqual(int.from_bytes(data[0x16:0x18], "little"), 0)

    def test_a_size_over_4_gb_is_cut_to_32_bits_and_the_attributes_to_a_byte(self):
        data = record(1, entry("A", size=(1 << 32) + 5, attributes=0x1FF))
        self.assertEqual((data[0x1A:0x1E], data[0x15]), (b"\x05\x00\x00\x00", 0xFF))


class TestSearches(unittest.TestCase):
    def test_the_entries_come_in_order_and_then_none_and_the_search_is_forgotten(self):
        searches = Searches()
        number = searches.start([entry("A"), entry("B"), entry("C")])
        self.assertEqual([searches.take(number).name, searches.take(number).name, searches.take(number).name],
                         ["A", "B", "C"])
        self.assertIsNone(searches.take(number))
        self.assertNotIn(number, searches.rest)

    def test_a_search_is_forgotten_the_moment_its_last_entry_is_given(self):
        searches = Searches()
        number = searches.start([entry("A"), entry("B")])
        searches.take(number)
        self.assertIn(number, searches.rest)
        searches.take(number)  # the last one
        self.assertEqual(searches.rest, {})

    def test_sixty_four_searches_are_kept_and_the_65th_makes_the_first_go(self):
        searches = Searches()
        numbers = [searches.start([entry("X")]) for _ in range(64)]
        self.assertEqual(len(searches.rest), 64)
        self.assertTrue(all(number in searches.rest for number in numbers))
        searches.start([entry("Y")])
        self.assertEqual(len(searches.rest), 64)
        self.assertNotIn(numbers[0], searches.rest)
        self.assertIn(numbers[1], searches.rest)
        self.assertEqual(MAX_SEARCHES, 64)

    def test_the_numbers_are_not_0_and_are_all_different(self):
        searches = Searches()
        numbers = [searches.start([entry("A")]) for _ in range(10)]
        self.assertEqual(len(set(numbers)), 10)
        self.assertNotIn(0, numbers)

    def test_two_searches_go_on_independently(self):
        searches = Searches()
        first = searches.start([entry("A1"), entry("A2")])
        second = searches.start([entry("B1"), entry("B2")])
        self.assertEqual(searches.take(first).name, "A1")
        self.assertEqual(searches.take(second).name, "B1")
        self.assertEqual(searches.take(second).name, "B2")
        self.assertEqual(searches.take(first).name, "A2")

    def test_a_number_that_was_never_given_finds_nothing(self):
        searches = Searches()
        for number in (0, 1, 5, 0xFFFF):
            self.assertIsNone(searches.take(number))

    def test_an_empty_search_gives_nothing(self):
        searches = Searches()
        self.assertIsNone(searches.take(searches.start([])))
        self.assertEqual(searches.rest, {})

    def test_the_entries_are_taken_from_a_copy(self):
        searches = Searches()
        mine = [entry("A"), entry("B")]
        number = searches.start(mine)
        searches.take(number)
        self.assertEqual([e.name for e in mine], ["A", "B"])

    def test_the_oldest_search_is_forgotten_when_there_are_too_many(self):
        searches = Searches()
        numbers = [searches.start([entry("X")]) for _ in range(MAX_SEARCHES + 5)]
        self.assertEqual(len(searches.rest), MAX_SEARCHES)
        self.assertIsNone(searches.take(numbers[0]))
        self.assertEqual(searches.take(numbers[-1]).name, "X")

    def test_the_numbers_come_round_before_65536_and_skip_0(self):
        searches = Searches()
        searches.last = 0xFFFE
        self.assertEqual(searches.start([entry("A")]), 0xFFFF)
        self.assertEqual(searches.start([entry("A")]), 1)


if __name__ == "__main__":
    unittest.main()
