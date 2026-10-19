import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mydos import filesystem
from mydos.config import Mount
from mydos.filesystem import (ACCESS_DENIED, ATTR_ARCHIVE, ATTR_DIRECTORY, ATTR_HIDDEN, ATTR_READONLY, ATTR_SYSTEM,
                              ATTR_VOLUME, FILE_NOT_FOUND, PATH_NOT_FOUND, FsError, HostDir, MemFs, find_ignoring_case,
                              is_dos_name)


def stat_mode(path):
    """The permission bits of a host file."""
    return os.stat(path).st_mode & 0o777


class Contract:
    """Everything every kind of file system has to do. A subclass makes `self.fs` in setUp."""

    def put(self, parts, data=b""):
        f = self.fs.create(parts)
        f.write(data)
        f.close()

    def get(self, parts):
        f = self.fs.open(parts)
        try:
            return f.read()
        finally:
            f.close()

    def assertFails(self, code, function, *arguments):
        with self.assertRaises(FsError) as caught:
            function(*arguments)
        self.assertEqual(caught.exception.code, code)

    # -- the root, files, names --

    def test_the_root_is_an_empty_directory_at_first(self):
        root = self.fs.stat([])
        self.assertEqual((root.name, root.is_dir, root.size), ("", True, 0))
        self.assertEqual(root.attributes, ATTR_DIRECTORY)
        self.assertEqual(self.fs.listdir([]), [])

    def test_a_file_made_has_what_was_written_into_it(self):
        self.put(["HELLO.TXT"], b"hello, world")
        self.assertEqual(self.get(["HELLO.TXT"]), b"hello, world")
        entry = self.fs.stat(["HELLO.TXT"])
        self.assertEqual((entry.name, entry.is_dir, entry.size), ("HELLO.TXT", False, 12))
        self.assertEqual(entry.attributes, ATTR_ARCHIVE)

    def test_names_are_matched_without_regard_to_case_and_shown_in_capitals(self):
        self.put(["readme.txt"], b"x")
        for name in ("README.TXT", "Readme.Txt", "readme.txt"):
            self.assertEqual(self.fs.stat([name]).name, "README.TXT", name)
        self.assertEqual([entry.name for entry in self.fs.listdir([])], ["README.TXT"])

    def test_creating_a_file_that_is_there_empties_it_and_keeps_one_file(self):
        self.put(["A.TXT"], b"long old contents")
        self.put(["a.txt"], b"new")
        self.assertEqual(self.get(["A.TXT"]), b"new")
        self.assertEqual(len(self.fs.listdir([])), 1)

    def test_a_name_that_dos_cannot_have_is_refused(self):
        for name in ("LONGFILENAME.TXT", "A B.TXT", "A.LONG", ".HIDDEN", "A..B", "", "A.B.C", "NAME*", "É.TXT"):
            with self.subTest(name=name):
                self.assertFalse(is_dos_name(name))
                self.assertFails(PATH_NOT_FOUND, self.fs.create, [name])
                self.assertFails(PATH_NOT_FOUND, self.fs.mkdir, [name])

    def test_names_dos_can_have(self):
        for name in ("A", "A.B", "ABCDEFGH", "ABCDEFGH.XYZ", "FILE1.TXT", "A-B_C.1", "~1", "$X", "DIR.D", "!"):
            with self.subTest(name=name):
                self.assertTrue(is_dos_name(name))
                self.put([name], b"1")
                self.assertEqual(self.get([name.lower()]), b"1")

    # -- not found --

    def test_a_file_that_is_not_there_is_file_not_found(self):
        self.assertFails(FILE_NOT_FOUND, self.fs.open, ["NOPE.TXT"])
        self.assertFails(FILE_NOT_FOUND, self.fs.stat, ["NOPE.TXT"])

    def test_a_directory_on_the_way_that_is_not_there_is_path_not_found(self):
        self.assertFails(PATH_NOT_FOUND, self.fs.open, ["NODIR", "A.TXT"])
        self.assertFails(PATH_NOT_FOUND, self.fs.create, ["NODIR", "A.TXT"])
        self.assertFails(PATH_NOT_FOUND, self.fs.listdir, ["NODIR"])
        self.assertFails(PATH_NOT_FOUND, self.fs.mkdir, ["NODIR", "SUB"])

    def test_a_file_in_the_middle_of_a_path_is_path_not_found(self):
        self.put(["FILE.TXT"], b"x")
        self.assertFails(PATH_NOT_FOUND, self.fs.open, ["FILE.TXT", "INNER"])
        self.assertFails(PATH_NOT_FOUND, self.fs.listdir, ["FILE.TXT"])

    # -- directories --

    def test_directories_nest_and_list(self):
        self.fs.mkdir(["GAMES"])
        self.fs.mkdir(["games", "Saves"])
        self.put(["GAMES", "SAVES", "ONE.SAV"], b"123")
        self.put(["GAMES", "README"], b"r")
        self.assertEqual([(e.name, e.is_dir, e.size) for e in self.fs.listdir(["games"])],
                         [("README", False, 1), ("SAVES", True, 0)])
        self.assertEqual([e.name for e in self.fs.listdir(["GAMES", "SAVES"])], ["ONE.SAV"])
        entry = self.fs.stat(["GAMES", "SAVES"])
        self.assertEqual((entry.name, entry.is_dir, entry.attributes), ("SAVES", True, ATTR_DIRECTORY))

    def test_a_listing_is_in_alphabetical_order_and_has_neither_dot_nor_dotdot(self):
        for name in ("ZED", "ALPHA", "MID.TXT", "BETA"):
            self.put([name])
        names = [entry.name for entry in self.fs.listdir([])]
        self.assertEqual(names, ["ALPHA", "BETA", "MID.TXT", "ZED"])
        self.fs.mkdir(["SUB"])
        self.assertEqual(self.fs.listdir(["SUB"]), [])

    def test_a_directory_cannot_be_made_over_something_that_is_there(self):
        self.fs.mkdir(["D"])
        self.put(["F"])
        self.assertFails(ACCESS_DENIED, self.fs.mkdir, ["D"])
        self.assertFails(ACCESS_DENIED, self.fs.mkdir, ["d"])
        self.assertFails(ACCESS_DENIED, self.fs.mkdir, ["F"])

    def test_a_directory_is_not_a_file(self):
        self.fs.mkdir(["D"])
        self.assertFails(ACCESS_DENIED, self.fs.open, ["D"])
        self.assertFails(ACCESS_DENIED, self.fs.create, ["D"])
        self.assertFails(ACCESS_DENIED, self.fs.remove, ["D"])

    def test_rmdir(self):
        self.fs.mkdir(["D"])
        self.fs.rmdir(["d"])
        self.assertEqual(self.fs.listdir([]), [])
        self.assertFails(PATH_NOT_FOUND, self.fs.rmdir, ["D"])  # gone
        self.fs.mkdir(["E"])
        self.put(["E", "X"])
        self.assertFails(ACCESS_DENIED, self.fs.rmdir, ["E"])  # not empty
        self.put(["F"])
        self.assertFails(PATH_NOT_FOUND, self.fs.rmdir, ["F"])  # a file is not a directory
        self.assertFails(ACCESS_DENIED, self.fs.rmdir, [])  # the root stays

    def test_the_root_cannot_be_made_removed_or_renamed(self):
        self.assertFails(ACCESS_DENIED, self.fs.mkdir, [])
        self.assertFails(ACCESS_DENIED, self.fs.create, [])
        self.assertFails(ACCESS_DENIED, self.fs.remove, [])
        self.assertFails(ACCESS_DENIED, self.fs.rename, [], ["X"])

    # -- deleting and renaming --

    def test_remove(self):
        self.put(["A.TXT"], b"x")
        self.fs.remove(["a.txt"])
        self.assertFails(FILE_NOT_FOUND, self.fs.stat, ["A.TXT"])
        self.assertFails(FILE_NOT_FOUND, self.fs.remove, ["A.TXT"])
        self.assertFails(PATH_NOT_FOUND, self.fs.remove, ["NODIR", "A.TXT"])

    def test_rename_a_file(self):
        self.put(["OLD.TXT"], b"data")
        self.fs.rename(["old.txt"], ["new.txt"])
        self.assertEqual(self.get(["NEW.TXT"]), b"data")
        self.assertFails(FILE_NOT_FOUND, self.fs.stat, ["OLD.TXT"])

    def test_rename_into_another_directory_and_a_directory(self):
        self.fs.mkdir(["A"])
        self.fs.mkdir(["B"])
        self.put(["A", "F.TXT"], b"1")
        self.fs.rename(["A", "F.TXT"], ["B", "G.TXT"])
        self.assertEqual(self.get(["B", "G.TXT"]), b"1")
        self.fs.rename(["B"], ["C"])
        self.assertEqual(self.get(["C", "G.TXT"]), b"1")
        self.assertFails(PATH_NOT_FOUND, self.fs.listdir, ["B"])

    def test_rename_mistakes(self):
        self.put(["A"], b"a")
        self.put(["B"], b"b")
        self.fs.mkdir(["D"])
        self.assertFails(ACCESS_DENIED, self.fs.rename, ["A"], ["B"])  # the new name is taken
        self.assertFails(FILE_NOT_FOUND, self.fs.rename, ["NOPE"], ["X"])
        self.assertFails(PATH_NOT_FOUND, self.fs.rename, ["A"], ["NODIR", "X"])
        self.assertFails(PATH_NOT_FOUND, self.fs.rename, ["A"], ["TOO LONG NAME"])
        self.fs.mkdir(["D", "E"])
        self.assertFails(ACCESS_DENIED, self.fs.rename, ["D"], ["D", "E", "D"])  # into itself
        self.assertEqual(self.get(["A"]), b"a")  # nothing was lost

    # -- the file object --

    def test_reading_in_pieces_and_seeking(self):
        self.put(["F"], b"0123456789")
        f = self.fs.open(["F"])
        self.assertEqual((f.read(3), f.tell()), (b"012", 3))
        self.assertEqual(f.read(2), b"34")
        f.seek(7)
        self.assertEqual(f.read(), b"789")
        f.seek(-4, 2)  # from the end
        self.assertEqual(f.read(2), b"67")
        f.seek(-3, 1)  # from here
        self.assertEqual(f.read(1), b"5")
        self.assertEqual(f.seek(0), 0)
        self.assertEqual(f.read(0), b"")
        f.close()

    def test_seeking_before_the_start_is_an_error(self):
        self.put(["F"], b"abc")
        f = self.fs.open(["F"])
        for offset, whence in ((-1, 0), (-4, 2), (-1, 1)):
            with self.subTest(offset=offset, whence=whence):
                with self.assertRaises((ValueError, OSError)):
                    f.seek(offset, whence)
        self.assertEqual(f.read(), b"abc")  # the position is where it was
        f.close()

    def test_reading_at_the_end_gives_nothing(self):
        self.put(["F"], b"abc")
        f = self.fs.open(["F"])
        f.seek(3)
        self.assertEqual(f.read(10), b"")
        f.seek(100)  # beyond the end is allowed, and reads nothing
        self.assertEqual((f.read(), f.tell()), (b"", 100))
        f.close()

    def test_writing_in_the_middle_extends_and_leaves_a_gap_of_zeros(self):
        self.put(["F"], b"0123456789")
        f = self.fs.open(["F"], write=True)
        f.seek(2)
        f.write(b"AB")  # overwrites
        f.seek(12)
        f.write(b"Z")  # beyond the end: two zero bytes between
        f.close()
        self.assertEqual(self.get(["F"]), b"01AB456789\0\0Z")
        self.assertEqual(self.fs.stat(["F"]).size, 13)

    def test_truncate_at_the_position_shortens_or_lengthens(self):
        self.put(["F"], b"0123456789")
        f = self.fs.open(["F"], write=True)
        f.seek(4)
        f.truncate()
        f.close()
        self.assertEqual(self.get(["F"]), b"0123")
        f = self.fs.open(["F"], write=True)
        f.truncate(6)
        f.close()
        self.assertEqual(self.get(["F"]), b"0123\0\0")

    def test_a_file_opened_for_reading_cannot_be_written_to(self):
        self.put(["F"], b"x")
        f = self.fs.open(["F"])
        with self.assertRaises(io.UnsupportedOperation):
            f.write(b"y")
        f.close()

    def test_what_is_written_stays_after_the_file_is_closed_and_opened_again(self):
        f = self.fs.create(["F"])
        f.write(b"abc")
        f.write(b"def")
        f.close()
        self.assertEqual(self.get(["F"]), b"abcdef")

    # -- a disk that cannot be written to --

    def test_a_read_only_disk_can_be_read_and_cannot_be_changed(self):
        self.fs.mkdir(["D"])
        self.put(["D", "F.TXT"], b"data")
        self.fs.readonly = True
        self.assertEqual(self.get(["D", "F.TXT"]), b"data")
        self.assertEqual([e.name for e in self.fs.listdir(["D"])], ["F.TXT"])
        self.assertTrue(self.fs.stat(["D", "F.TXT"]).attributes & ATTR_READONLY)
        self.assertFails(ACCESS_DENIED, self.fs.open, ["D", "F.TXT"], True)
        self.assertFails(ACCESS_DENIED, self.fs.create, ["NEW"])
        self.assertFails(ACCESS_DENIED, self.fs.mkdir, ["NEWDIR"])
        self.assertFails(ACCESS_DENIED, self.fs.rmdir, ["D"])
        self.assertFails(ACCESS_DENIED, self.fs.remove, ["D", "F.TXT"])
        self.assertFails(ACCESS_DENIED, self.fs.rename, ["D", "F.TXT"], ["G.TXT"])
        self.assertEqual(self.get(["D", "F.TXT"]), b"data")  # still all there

    # -- the attributes of a file --

    def skip_if_read_only_cannot_be_seen(self):
        """A hook: HostDir cannot show a read-only file to a user who may write to anything."""

    def test_a_file_can_be_made_read_only_and_writable_again(self):
        self.skip_if_read_only_cannot_be_seen()
        self.put(["F.TXT"], b"data")
        self.fs.set_attributes(["f.txt"], ATTR_READONLY)
        self.assertEqual(self.fs.stat(["F.TXT"]).attributes, ATTR_ARCHIVE | ATTR_READONLY)
        self.assertFails(ACCESS_DENIED, self.fs.open, ["F.TXT"], True)  # not for writing
        self.assertFails(ACCESS_DENIED, self.fs.create, ["F.TXT"])  # not for emptying
        self.assertFails(ACCESS_DENIED, self.fs.remove, ["F.TXT"])  # not for deleting
        self.assertEqual(self.get(["F.TXT"]), b"data")  # for reading it is fine
        self.fs.set_attributes(["F.TXT"], 0)
        self.assertEqual(self.fs.stat(["F.TXT"]).attributes, ATTR_ARCHIVE)
        self.put(["F.TXT"], b"new")  # writable again
        self.assertEqual(self.get(["F.TXT"]), b"new")

    def test_the_other_attribute_bits_are_accepted_and_change_nothing_that_is_seen(self):
        self.put(["F"])
        for bits in (ATTR_HIDDEN, ATTR_SYSTEM, ATTR_ARCHIVE, ATTR_HIDDEN | ATTR_SYSTEM | ATTR_ARCHIVE, 0):
            with self.subTest(bits=bits):
                self.fs.set_attributes(["F"], bits)
                self.assertEqual(self.fs.stat(["F"]).attributes, ATTR_ARCHIVE)

    def test_the_read_only_bit_among_others_is_the_one_that_counts(self):
        self.skip_if_read_only_cannot_be_seen()
        self.put(["F"])
        self.fs.set_attributes(["F"], ATTR_READONLY | ATTR_HIDDEN | ATTR_ARCHIVE)
        self.assertTrue(self.fs.stat(["F"]).attributes & ATTR_READONLY)

    def test_the_attributes_of_a_directory_cannot_be_changed_and_a_file_cannot_become_one(self):
        self.fs.mkdir(["D"])
        self.put(["F"])
        self.assertFails(ACCESS_DENIED, self.fs.set_attributes, ["D"], 0)
        self.assertFails(ACCESS_DENIED, self.fs.set_attributes, [], 0)  # the root is one too
        self.assertFails(ACCESS_DENIED, self.fs.set_attributes, ["F"], ATTR_DIRECTORY)
        self.assertFails(ACCESS_DENIED, self.fs.set_attributes, ["F"], ATTR_VOLUME)
        self.assertEqual(self.fs.stat(["F"]).attributes, ATTR_ARCHIVE)

    def test_the_attributes_of_something_that_is_not_there(self):
        self.assertFails(FILE_NOT_FOUND, self.fs.set_attributes, ["NOPE"], 0)
        self.assertFails(PATH_NOT_FOUND, self.fs.set_attributes, ["NODIR", "F"], 0)

    def test_the_attributes_cannot_be_changed_on_a_disk_that_cannot_be_written_to(self):
        self.put(["F"])
        self.fs.readonly = True
        self.assertFails(ACCESS_DENIED, self.fs.set_attributes, ["F"], ATTR_READONLY)

    def test_a_disk_that_can_be_written_to_says_so_in_the_attributes(self):
        self.put(["F"])
        self.assertFalse(self.fs.stat(["F"]).attributes & ATTR_READONLY)

    def test_the_size_of_the_disk_and_what_is_free(self):
        total, free = self.fs.free_space()
        self.assertTrue(isinstance(total, int) and isinstance(free, int))
        self.assertTrue(0 < free <= total)


class TestHostDir(Contract, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.fs = HostDir(self.root)

    def skip_if_read_only_cannot_be_seen(self):
        if os.geteuid() == 0:
            self.skipTest("the host lets root write to anything, so a read-only file looks writable")

    def test_making_a_file_read_only_takes_the_write_permissions_away_on_the_host(self):
        self.skip_if_read_only_cannot_be_seen()
        self.put(["F.TXT"], b"x")
        os.chmod(self.root / "F.TXT", 0o664)
        self.fs.set_attributes(["F.TXT"], ATTR_READONLY)
        self.assertEqual(stat_mode(self.root / "F.TXT"), 0o444)  # nobody may write
        self.fs.set_attributes(["F.TXT"], 0)
        self.assertEqual(stat_mode(self.root / "F.TXT"), 0o644)  # the owner may, as before; the rest as it was

    def test_other_permission_bits_are_left_alone(self):
        self.skip_if_read_only_cannot_be_seen()
        self.put(["X.SH"], b"x")
        os.chmod(self.root / "X.SH", 0o755)
        self.fs.set_attributes(["X.SH"], ATTR_READONLY)
        self.assertEqual(stat_mode(self.root / "X.SH"), 0o555)
        self.fs.set_attributes(["X.SH"], 0)
        self.assertEqual(stat_mode(self.root / "X.SH"), 0o755)

    def test_new_names_are_made_in_capitals_on_the_host(self):
        self.put(["lower.txt"], b"x")
        self.fs.mkdir(["Dir"])
        self.assertEqual(sorted(os.listdir(self.root)), ["DIR", "LOWER.TXT"])

    def test_a_directory_cannot_be_made_over_one_that_differs_only_in_case(self):
        (self.root / "Games").mkdir()
        self.assertFails(ACCESS_DENIED, self.fs.mkdir, ["GAMES"])
        self.assertEqual(os.listdir(self.root), ["Games"])  # no second directory next to it

    def test_a_file_renamed_gets_its_new_name_in_capitals_on_the_host(self):
        self.put(["old.txt"], b"x")
        self.fs.rename(["OLD.TXT"], ["new.txt"])
        self.assertEqual(os.listdir(self.root), ["NEW.TXT"])

    def test_what_is_written_is_in_the_real_file(self):
        self.put(["F.TXT"], b"on the host")
        self.assertEqual((self.root / "F.TXT").read_bytes(), b"on the host")

    def test_existing_host_files_in_any_case_are_seen_and_changed_under_their_own_name(self):
        (self.root / "Mixed.Txt").write_bytes(b"old")
        self.assertEqual(self.get(["MIXED.TXT"]), b"old")
        self.put(["mixed.txt"], b"new")  # an existing file is reused: no second file in another case
        self.assertEqual(os.listdir(self.root), ["Mixed.Txt"])
        self.assertEqual((self.root / "Mixed.Txt").read_bytes(), b"new")

    def test_two_host_files_that_differ_only_in_case_give_the_first_alphabetically(self):
        (self.root / "Aa").write_bytes(b"first")
        (self.root / "aA").write_bytes(b"second")
        self.assertEqual(self.get(["AA"]), b"first")

    def test_host_names_that_do_not_fit_are_invisible(self):
        for name in ("a long name.txt", "toolongname.txt", "file.html", ".hidden", "a b", "é.txt", "x.y.z"):
            (self.root / name).write_bytes(b"x")
        (self.root / "OK.TXT").write_bytes(b"x")
        (self.root / "dot.d").mkdir()
        (self.root / "long directory name").mkdir()
        self.assertEqual([e.name for e in self.fs.listdir([])], ["DOT.D", "OK.TXT"])
        self.assertFails(FILE_NOT_FOUND, self.fs.stat, ["file.html"])
        self.assertFails(PATH_NOT_FOUND, self.fs.stat, ["long directory name", "x"])

    def test_the_attributes_come_from_the_host(self):
        (self.root / "RO.TXT").write_bytes(b"x")
        (self.root / "RO.TXT").chmod(0o444)
        (self.root / "RW.TXT").write_bytes(b"x")
        if os.access(self.root / "RO.TXT", os.W_OK):
            self.skipTest("the host lets this user write to anything (root)")
        self.assertEqual(self.fs.stat(["RO.TXT"]).attributes, ATTR_ARCHIVE | ATTR_READONLY)
        self.assertEqual(self.fs.stat(["RW.TXT"]).attributes, ATTR_ARCHIVE)
        self.assertFails(ACCESS_DENIED, self.fs.open, ["RO.TXT"], True)  # cannot be opened for writing
        self.assertFails(ACCESS_DENIED, self.fs.remove, ["RO.TXT"])  # and DOS does not delete it either
        self.assertEqual(self.get(["RO.TXT"]), b"x")

    def test_the_time_is_the_time_of_the_host_file(self):
        path = self.root / "T.TXT"
        path.write_bytes(b"x")
        os.utime(path, (1_000_000_000, 1_000_000_000))
        self.assertEqual(self.fs.stat(["T.TXT"]).mtime, 1_000_000_000)

    def test_a_link_that_leads_nowhere_is_left_out_of_a_listing(self):
        (self.root / "GOOD.TXT").write_bytes(b"x")
        os.symlink(self.root / "nowhere", self.root / "BAD.TXT")
        self.assertEqual([e.name for e in self.fs.listdir([])], ["GOOD.TXT"])
        self.assertFails(FILE_NOT_FOUND, self.fs.stat, ["BAD.TXT"])

    def test_the_free_space_is_the_hosts(self):
        total, free = self.fs.free_space()
        self.assertEqual(total, shutil.disk_usage(self.root).total)

    def test_a_name_with_a_slash_cannot_be_used_to_reach_outside(self):
        (self.root.parent / "OUTSIDE.TXT").write_bytes(b"secret")
        self.addCleanup((self.root.parent / "OUTSIDE.TXT").unlink)
        for parts in (["..", "OUTSIDE.TXT"], ["../OUTSIDE.TXT"], [".."], ["."], [""]):
            with self.subTest(parts=parts):
                with self.assertRaises(FsError):
                    self.fs.stat(parts)


class TestMemFs(Contract, unittest.TestCase):
    def setUp(self):
        self.fs = MemFs()

    def test_two_memory_file_systems_do_not_share_anything(self):
        other = MemFs()
        self.put(["F"], b"x")
        self.assertEqual(other.listdir([]), [])

    def test_two_open_files_see_the_same_data(self):
        self.put(["F"], b"abc")
        reader = self.fs.open(["F"])
        writer = self.fs.open(["F"], write=True)
        writer.seek(1)
        writer.write(b"XY")
        self.assertEqual(reader.read(), b"aXY")
        writer.close()
        reader.close()

    def test_the_free_space_shrinks_by_what_is_written(self):
        total, free = self.fs.free_space()
        self.fs.mkdir(["D"])
        self.put(["D", "F"], b"x" * 1000)
        self.put(["G"], b"y" * 24)
        self.assertEqual(self.fs.free_space(), (total, free - 1024))
        self.fs.remove(["G"])
        self.assertEqual(self.fs.free_space(), (total, free - 1000))

    def test_the_time_of_a_file_is_when_it_was_made_and_when_it_was_last_written(self):
        with mock.patch("mydos.filesystem.time.time", return_value=1000.0):
            f = self.fs.create(["F"])
            self.assertEqual(self.fs.stat(["F"]).mtime, 1000.0)
        with mock.patch("mydos.filesystem.time.time", return_value=2000.0):
            f.write(b"x")
            self.assertEqual(self.fs.stat(["F"]).mtime, 2000.0)
        with mock.patch("mydos.filesystem.time.time", return_value=3000.0):
            self.fs.open(["F"]).read()  # reading does not change it
            self.assertEqual(self.fs.stat(["F"]).mtime, 2000.0)
            f.truncate(0)
            self.assertEqual(self.fs.stat(["F"]).mtime, 3000.0)

    def test_the_disk_is_32_megabytes(self):
        self.assertEqual(self.fs.free_space()[0], 32 * 1024 * 1024)


class TestForMount(unittest.TestCase):
    def test_a_directory_mount_is_a_hostdir_and_a_tmpfs_is_a_memfs(self):
        with tempfile.TemporaryDirectory() as directory:
            fs = filesystem.for_mount(Mount("dir", Path(directory), True))
            self.assertIsInstance(fs, HostDir)
            self.assertEqual((fs.root, fs.readonly), (Path(directory), True))
        fs = filesystem.for_mount(Mount("tmpfs", None, False))
        self.assertIsInstance(fs, MemFs)
        self.assertFalse(fs.readonly)
        self.assertTrue(filesystem.for_mount(Mount("tmpfs", None, True)).readonly)


class TestFindingAFileWhateverItsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()

    def test_an_exact_match_wins(self):
        (self.base / "ABC").write_text("1")
        (self.base / "abc").write_text("2")
        self.assertEqual(find_ignoring_case(self.base, "abc"), self.base / "abc")
        self.assertEqual(find_ignoring_case(self.base, "ABC"), self.base / "ABC")

    def test_otherwise_the_case_does_not_matter(self):
        (self.base / "Readme.Txt").write_text("x")
        self.assertEqual(find_ignoring_case(self.base, "README.TXT"), self.base / "Readme.Txt")
        self.assertEqual(find_ignoring_case(self.base, "readme.txt"), self.base / "Readme.Txt")

    def test_two_that_differ_only_in_case_give_the_first_in_alphabetical_order(self):
        (self.base / "Aa").write_text("1")
        (self.base / "aA").write_text("2")
        self.assertEqual(find_ignoring_case(self.base, "AA"), self.base / "Aa")

    def test_the_choice_does_not_depend_on_the_order_the_system_lists_the_files_in(self):
        (self.base / "Aa").write_text("1")
        (self.base / "aA").write_text("2")
        for listing in (["Aa", "aA"], ["aA", "Aa"]):
            with self.subTest(listing=listing), mock.patch.object(os, "listdir", return_value=listing):
                self.assertEqual(find_ignoring_case(self.base, "AA"), self.base / "Aa")

    def test_nothing_found_is_none(self):
        self.assertIsNone(find_ignoring_case(self.base, "nothing"))


if __name__ == "__main__":
    unittest.main()
