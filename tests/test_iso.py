import calendar
import os
import tempfile
import unittest
from pathlib import Path

from dosloth import iso
from dosloth.filesystem import (ACCESS_DENIED, ATTR_DIRECTORY, ATTR_HIDDEN, ATTR_READONLY, FILE_NOT_FOUND,
                              PATH_NOT_FOUND, SECTOR_NOT_FOUND, FsError)
from tests.iso_builder import DEFAULT_DATE, Item, build

PATTERN = bytes(range(256)) * 8  # one sector of something that is not zeros
TREE = {
    "README.TXT": b"read me\r\n",
    "NOEXT": b"no extension",
    "EMPTY.DAT": b"",
    "BIG.BIN": bytes(range(256)) * 20,  # 5120 bytes: three sectors
    "SECRET.DAT": Item(b"hidden", hidden=True),
    "LONGNAMEFORISO.TXT": b"a name DOS cannot have",
    "GAMES": {"ARCADE": {"PONG.COM": b"pong"}, "CHESS.EXE": b"chess"},
}


class ImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def image_of(self, data, name="disc.iso"):
        path = self.dir / name
        path.write_bytes(data)
        return iso.IsoImage(path)

    def disc(self, tree=TREE, **keywords):
        return iso.IsoFs(self.image_of(build(tree, **keywords)))


class TestTheImage(ImageTest):
    def test_the_sectors_are_counted_and_the_root_is_found_by_the_primary_descriptor(self):
        data = build(TREE)
        image = self.image_of(data)
        self.assertEqual(image.sectors, len(data) // 2048)
        self.assertTrue(image.root.is_dir)
        self.assertGreater(image.root.extent, 17)
        self.assertEqual(image.root.size, 2048)

    def test_a_descriptor_of_another_kind_in_front_of_the_primary_one_is_passed_over(self):
        disc = iso.IsoFs(self.image_of(build(TREE, boot_record=True)))
        self.assertIn("README.TXT", [entry.name for entry in disc.listdir([])])

    def test_what_is_not_an_iso_image_is_said_in_plain_words(self):
        for data in (b"", b"junk" * 10000, bytes(100 * 2048)):
            with self.assertRaisesRegex(iso.IsoError, "volume descriptor"):
                self.image_of(data)

    def test_a_disc_that_ends_before_its_descriptor_is_not_an_image(self):
        with self.assertRaises(iso.IsoError):
            self.image_of(build(TREE)[:16 * 2048 + 100])

    def test_a_descriptor_list_that_ends_before_the_primary_one_is_not_an_image(self):
        data = bytearray(build(TREE))
        data[16 * 2048] = 255  # the terminator comes first
        with self.assertRaisesRegex(iso.IsoError, "volume descriptor"):
            self.image_of(bytes(data))

    def test_a_file_that_is_not_there_is_an_iso_error_too(self):
        with self.assertRaisesRegex(iso.IsoError, "cannot be read"):
            iso.IsoImage(self.dir / "missing.iso")

    def test_blocks_of_another_size_are_refused(self):
        data = bytearray(build(TREE))
        data[16 * 2048 + 128:16 * 2048 + 130] = (512).to_bytes(2, "little")
        with self.assertRaisesRegex(iso.IsoError, "not supported"):
            self.image_of(bytes(data))

    def test_sectors_are_read_as_they_are_on_the_disc(self):
        data = build(TREE, system_area=PATTERN * 3)
        image = self.image_of(data)
        self.assertEqual(image.read_sectors(0, 1), PATTERN)
        self.assertEqual(image.read_sectors(2, 1), PATTERN)
        self.assertEqual(image.read_sectors(3, 13), bytes(13 * 2048))
        self.assertEqual(image.read_sectors(16, 2), data[16 * 2048:18 * 2048])
        self.assertEqual(image.read_sectors(0, 15), data[:15 * 2048])
        self.assertEqual(image.read_sectors(5, 0), b"")

    def test_the_last_sector_can_be_read_and_the_one_after_it_cannot(self):
        data = build(TREE)
        image = self.image_of(data)
        last = image.sectors - 1
        self.assertEqual(image.read_sectors(last, 1), data[-2048:])
        for first, count in ((image.sectors, 1), (last, 2), (0, image.sectors + 1), (-1, 1), (2 ** 32 - 1, 1)):
            with self.assertRaises(FsError) as caught:
                image.read_sectors(first, count)
            self.assertEqual(caught.exception.code, SECTOR_NOT_FOUND, (first, count))

    def test_the_image_holds_no_file_open_between_reads(self):
        image = self.image_of(build(TREE))
        image.read_sectors(0, 1)
        os.remove(image.path)  # nothing keeps it: on a system that cannot remove an open file, this fails
        with self.assertRaises(OSError):
            image.read_sectors(0, 1)


class TestTheNames(ImageTest):
    def names(self, disc, parts=()):
        return [entry.name for entry in disc.listdir(list(parts))]

    def test_the_names_are_as_dos_knows_them_and_in_order(self):
        disc = self.disc()
        self.assertEqual(self.names(disc), ["BIG.BIN", "EMPTY.DAT", "GAMES", "NOEXT", "README.TXT", "SECRET.DAT"])

    def test_a_name_that_dos_cannot_have_is_not_there(self):
        disc = self.disc()
        self.assertNotIn("LONGNAMEFORISO.TXT", self.names(disc))
        with self.assertRaises(FsError) as caught:
            disc.stat(["LONGNAMEFORISO.TXT"])
        self.assertEqual(caught.exception.code, FILE_NOT_FOUND)

    def test_the_version_number_and_the_dot_of_a_file_with_no_extension_are_not_part_of_the_name(self):
        disc = self.disc()
        self.assertEqual(disc.stat(["NOEXT"]).name, "NOEXT")
        self.assertEqual(disc.stat(["README.TXT"]).name, "README.TXT")

    def test_a_name_is_found_whatever_its_case(self):
        disc = self.disc()
        for parts in (["readme.txt"], ["Readme.Txt"], ["games", "arcade", "pong.com"], ["GAMES", "Chess.Exe"]):
            self.assertFalse(disc.stat(parts).is_dir, parts)

    def test_the_directories_are_where_they_are_and_have_their_own_files(self):
        disc = self.disc()
        self.assertEqual(self.names(disc, ["GAMES"]), ["ARCADE", "CHESS.EXE"])
        self.assertEqual(self.names(disc, ["games", "arcade"]), ["PONG.COM"])

    def test_the_first_of_two_versions_of_a_file_is_the_file(self):
        disc = self.disc({"A.TXT": b"one"})
        image = disc.image
        directory = image.records(image.root)
        directory.append(iso.Record("A.TXT", False, 0, 99, 0.0, False))  # a second "A.TXT" would come after
        with disc.open(["A.TXT"]) as file:
            self.assertEqual(file.read(), b"one")

    def test_a_directory_of_many_files_is_read_whole_across_its_sectors(self):
        tree = {f"FILE{number:03d}.TXT": f"file {number}".encode() for number in range(250)}
        disc = self.disc(tree)
        self.assertGreater(disc.image.root.size, 2048 * 2)
        names = self.names(disc)
        self.assertEqual(names, sorted(tree))
        for number in (0, 61, 62, 63, 130, 249):
            with disc.open([f"FILE{number:03d}.TXT"]) as file:
                self.assertEqual(file.read(), f"file {number}".encode())

    def test_a_directory_is_read_once(self):
        disc = self.disc()
        first = disc.image.records(disc.image.root)
        self.assertIs(disc.image.records(disc.image.root), first)

    def test_a_disc_with_nothing_on_it_has_an_empty_root(self):
        disc = self.disc({})
        self.assertEqual(disc.listdir([]), [])
        self.assertTrue(disc.stat([]).is_dir)


class TestWhatTheDiscSaysAboutAFile(ImageTest):
    def test_files_are_read_only_directories_are_directories_and_a_hidden_one_is_hidden(self):
        disc = self.disc()
        self.assertEqual(disc.stat(["README.TXT"]).attributes, ATTR_READONLY)
        self.assertEqual(disc.stat(["GAMES"]).attributes, ATTR_DIRECTORY)
        self.assertEqual(disc.stat(["SECRET.DAT"]).attributes, ATTR_READONLY | ATTR_HIDDEN)
        self.assertEqual(disc.stat([]).attributes, ATTR_DIRECTORY)

    def test_the_size_is_that_of_the_file_and_0_for_a_directory(self):
        disc = self.disc()
        self.assertEqual(disc.stat(["BIG.BIN"]).size, 5120)
        self.assertEqual(disc.stat(["EMPTY.DAT"]).size, 0)
        self.assertEqual(disc.stat(["GAMES"]).size, 0)
        self.assertEqual(disc.stat(["README.TXT"]).size, 9)

    def test_the_time_is_the_one_in_the_record_in_seconds_since_1970(self):
        disc = self.disc()
        self.assertEqual(disc.stat(["README.TXT"]).mtime, calendar.timegm((1997, 3, 14, 12, 30, 45)))

    def test_the_time_zone_of_the_record_is_taken_off(self):
        # 15 minutes a step: +4 is an hour east of Greenwich, so Greenwich is an hour earlier; -8 is two hours west
        for zone, shift in ((4, -3600), (-8, 7200), (0, 0)):
            date = DEFAULT_DATE[:6] + bytes([zone & 0xFF])
            disc = self.disc({"A.TXT": Item(b"x", date=date)})
            self.assertEqual(disc.stat(["A.TXT"]).mtime, calendar.timegm((1997, 3, 14, 12, 30, 45)) + shift, zone)

    def test_a_date_that_makes_no_sense_is_the_start_of_1970(self):
        for date in (bytes(7), bytes([97, 13, 1, 0, 0, 0, 0]), bytes([97, 1, 32, 0, 0, 0, 0]),
                     bytes([97, 1, 1, 24, 0, 0, 0]), bytes([97, 1, 1, 0, 60, 0, 0])):
            disc = self.disc({"A.TXT": Item(b"x", date=date)})
            self.assertEqual(disc.stat(["A.TXT"]).mtime, 0.0, date)

    def test_the_root_has_the_time_of_the_descriptor(self):
        self.assertEqual(self.disc().stat([]).mtime, calendar.timegm((1997, 3, 14, 12, 30, 45)))

    def test_what_is_not_there_is_file_not_found_at_the_end_and_path_not_found_on_the_way(self):
        disc = self.disc()
        for parts, code in ((["NOTHING"], FILE_NOT_FOUND), (["GAMES", "NOTHING"], FILE_NOT_FOUND),
                            (["NOTHING", "X"], PATH_NOT_FOUND), (["README.TXT", "X"], PATH_NOT_FOUND),
                            (["GAMES", "NOTHING", "X"], PATH_NOT_FOUND)):
            with self.assertRaises(FsError) as caught:
                disc.stat(parts)
            self.assertEqual(caught.exception.code, code, parts)

    def test_a_directory_listing_of_what_is_not_a_directory_is_path_not_found(self):
        disc = self.disc()
        for parts in (["README.TXT"], ["NOTHING"], ["GAMES", "NOTHING"]):
            with self.assertRaises(FsError) as caught:
                disc.listdir(parts)
            self.assertEqual(caught.exception.code, PATH_NOT_FOUND, parts)

    def test_the_disc_is_as_big_as_the_image_and_has_no_free_space(self):
        data = build(TREE)
        disc = iso.IsoFs(self.image_of(data))
        self.assertEqual(disc.free_space(), (len(data), 0))


class TestReadingAFile(ImageTest):
    def test_a_file_is_read_whole_or_by_pieces(self):
        disc = self.disc()
        with disc.open(["BIG.BIN"]) as file:
            self.assertEqual(file.read(10), bytes(range(10)))
            self.assertEqual(file.tell(), 10)
            self.assertEqual(file.read(3000), (bytes(range(256)) * 20)[10:3010])
            self.assertEqual(file.read(), (bytes(range(256)) * 20)[3010:])
            self.assertEqual(file.read(), b"")
            self.assertEqual(file.tell(), 5120)

    def test_a_read_across_the_end_of_a_sector_goes_on_in_the_next_one(self):
        disc = self.disc()
        with disc.open(["BIG.BIN"]) as file:
            file.seek(2040)
            self.assertEqual(file.read(16), (bytes(range(256)) * 20)[2040:2056])

    def test_seeking_from_the_start_the_place_and_the_end(self):
        disc = self.disc()
        with disc.open(["README.TXT"]) as file:
            self.assertEqual(file.seek(5), 5)
            self.assertEqual(file.read(2), b"me")
            self.assertEqual(file.seek(-2, 1), 5)
            self.assertEqual(file.seek(-3, 2), 6)
            self.assertEqual(file.read(), b"e\r\n")  # the last three
            self.assertEqual(file.seek(0, 2), 9)

    def test_a_place_past_the_end_is_all_right_and_reads_nothing(self):
        disc = self.disc()
        with disc.open(["README.TXT"]) as file:
            self.assertEqual(file.seek(100), 100)
            self.assertEqual(file.read(5), b"")
            self.assertEqual(file.tell(), 100)

    def test_a_place_before_the_start_is_an_error_and_the_file_stays_where_it_was(self):
        disc = self.disc()
        with disc.open(["README.TXT"]) as file:
            file.seek(4)
            with self.assertRaises(ValueError):
                file.seek(-5, 1)
            self.assertEqual(file.tell(), 4)

    def test_an_empty_file_reads_nothing(self):
        disc = self.disc()
        with disc.open(["EMPTY.DAT"]) as file:
            self.assertEqual(file.read(), b"")
            self.assertEqual(file.read(10), b"")

    def test_files_in_directories_are_read(self):
        disc = self.disc()
        with disc.open(["games", "arcade", "pong.com"]) as file:
            self.assertEqual(file.read(), b"pong")

    def test_two_open_files_have_places_of_their_own(self):
        disc = self.disc()
        first, second = disc.open(["README.TXT"]), disc.open(["README.TXT"])
        first.seek(5)
        self.assertEqual((first.tell(), second.tell(), second.read(4)), (5, 0, b"read"))
        first.close()
        second.close()

    def test_a_file_knows_that_it_is_closed(self):
        disc = self.disc()
        file = disc.open(["README.TXT"])
        self.assertFalse(file.closed)
        file.close()
        self.assertTrue(file.closed)
        with disc.open(["README.TXT"]) as other:
            self.assertFalse(other.closed)
        self.assertTrue(other.closed)

    def test_nothing_that_would_change_the_disc_is_allowed(self):
        disc = self.disc()
        for call in (lambda: disc.open(["README.TXT"], write=True), lambda: disc.create(["NEW.TXT"]),
                     lambda: disc.mkdir(["NEWDIR"]), lambda: disc.rmdir(["GAMES"]),
                     lambda: disc.remove(["README.TXT"]), lambda: disc.rename(["README.TXT"], ["X.TXT"]),
                     lambda: disc.set_attributes(["README.TXT"], 0)):
            with self.assertRaises(FsError) as caught:
                call()
            self.assertEqual(caught.exception.code, ACCESS_DENIED)

    def test_a_directory_cannot_be_opened(self):
        disc = self.disc()
        for parts in ([], ["GAMES"]):
            with self.assertRaises(FsError) as caught:
                disc.open(parts)
            self.assertEqual(caught.exception.code, ACCESS_DENIED)

    def test_the_sectors_are_those_of_the_image(self):
        disc = self.disc(system_area=PATTERN)
        self.assertEqual(disc.read_sectors(0, 1), PATTERN)
        with self.assertRaises(FsError):
            disc.read_sectors(disc.image.sectors, 1)


if __name__ == "__main__":
    unittest.main()
