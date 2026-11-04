import tempfile
import tomllib
import unittest
from pathlib import Path

from dosloth import config
from dosloth.filesystem import DRIVE_NOT_READY, INVALID_DRIVE, PATH_NOT_FOUND, FsError, HostDir, MemFs
from dosloth.volumes import Volumes


class VolumesTest(unittest.TestCase):
    """A machine with an empty floppy drive A:, a hard disk C: that is a host directory, and a tmpfs D:."""

    TEXT = """
        cwd = 'C:\\SETUP'
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
        self.base = Path(self.tmp.name).resolve()
        (self.base / "Setup" / "Sub" / "Deep").mkdir(parents=True)
        (self.base / "Games").mkdir()
        (self.base / "Setup" / "readme.txt").write_text("hi")
        self.volumes = Volumes(config.parse(tomllib.loads(self.TEXT), self.base))

    def resolve(self, text):
        location = self.volumes.resolve(text)
        return location.drive, location.parts

    def assertFails(self, code, function, *arguments):
        with self.assertRaises(FsError) as caught:
            function(*arguments)
        self.assertEqual(caught.exception.code, code)


class TestStartingState(VolumesTest):
    def test_the_current_drive_and_directories_come_from_the_config(self):
        self.assertEqual(self.volumes.current_drive, 2)
        self.assertEqual(self.volumes.directories, {0: [], 2: ["SETUP"], 3: []})
        self.assertEqual(self.volumes.current_directory(), "SETUP")
        self.assertEqual(self.volumes.current_directory(3), "")

    def test_a_drive_with_a_medium_has_a_file_system_and_one_without_has_none(self):
        self.assertIsNone(self.volumes.filesystems[0])
        self.assertIsInstance(self.volumes.filesystems[2], HostDir)
        self.assertIsInstance(self.volumes.filesystems[3], MemFs)

    def test_each_volumes_has_a_memory_disk_of_its_own(self):
        other = Volumes(config.parse(tomllib.loads(self.TEXT), self.base))
        self.volumes.filesystems[3].create(["F"]).close()
        self.assertEqual(other.filesystems[3].listdir([]), [])


class TestResolving(VolumesTest):
    def test_a_name_is_taken_from_the_current_directory_of_the_current_drive(self):
        self.assertEqual(self.resolve("README.TXT"), (2, ["SETUP", "README.TXT"]))
        self.assertEqual(self.resolve("sub\\deep"), (2, ["SETUP", "sub", "deep"]))

    def test_a_path_that_starts_with_a_separator_starts_at_the_root(self):
        self.assertEqual(self.resolve("\\GAMES"), (2, ["GAMES"]))
        self.assertEqual(self.resolve("/games/x"), (2, ["games", "x"]))
        self.assertEqual(self.resolve("\\"), (2, []))

    def test_both_separators_and_repeated_ones(self):
        self.assertEqual(self.resolve("\\A/B\\\\C//D"), (2, ["A", "B", "C", "D"]))
        self.assertEqual(self.resolve("sub/"), (2, ["SETUP", "sub"]))

    def test_a_drive_letter_chooses_the_drive_and_its_own_current_directory(self):
        self.assertEqual(self.resolve("D:file"), (3, ["file"]))  # the root of D:, where its directory is
        self.assertEqual(self.resolve("c:file"), (2, ["SETUP", "file"]))  # lower case is fine, and C: is in SETUP
        self.assertEqual(self.resolve("C:\\file"), (2, ["file"]))
        self.assertEqual(self.resolve("D:\\x\\y"), (3, ["x", "y"]))
        self.assertEqual(self.resolve("D:"), (3, []))  # a drive on its own is its current directory
        self.assertEqual(self.resolve("C:"), (2, ["SETUP"]))

    def test_dot_stays_and_dotdot_goes_up_one(self):
        self.assertEqual(self.resolve("."), (2, ["SETUP"]))
        self.assertEqual(self.resolve(".\\.\\x"), (2, ["SETUP", "x"]))
        self.assertEqual(self.resolve(".."), (2, []))
        self.assertEqual(self.resolve("..\\games"), (2, ["games"]))
        self.assertEqual(self.resolve("sub\\deep\\..\\..\\x"), (2, ["SETUP", "x"]))
        self.assertEqual(self.resolve("\\a\\b\\..\\c"), (2, ["a", "c"]))

    def test_the_path_cannot_go_above_the_root(self):
        for text in ("..\\..", "\\..", "D:..", "\\a\\..\\..", "../../x"):
            with self.subTest(text=text):
                self.assertFails(PATH_NOT_FOUND, self.volumes.resolve, text)

    def test_a_drive_that_does_not_exist_is_error_15(self):
        for text in ("Z:\\x", "B:x", "E:"):
            with self.subTest(text=text):
                self.assertFails(INVALID_DRIVE, self.volumes.resolve, text)

    def test_a_drive_with_nothing_in_it_is_error_21(self):
        self.assertFails(DRIVE_NOT_READY, self.volumes.resolve, "A:\\x")
        self.assertFails(DRIVE_NOT_READY, self.volumes.resolve, "a:")

    def test_an_empty_path_is_error_3(self):
        self.assertFails(PATH_NOT_FOUND, self.volumes.resolve, "")

    def test_the_location_has_the_file_system_of_its_drive(self):
        self.assertIs(self.volumes.resolve("x").filesystem, self.volumes.filesystems[2])
        self.assertIs(self.volumes.resolve("D:x").filesystem, self.volumes.filesystems[3])

    def test_the_full_dos_name_is_in_capitals_with_the_drive(self):
        self.assertEqual(self.volumes.resolve("readme.txt").dos_path(), "C:\\SETUP\\README.TXT")
        self.assertEqual(self.volumes.resolve("d:\\a\\b.c").dos_path(), "D:\\A\\B.C")
        self.assertEqual(self.volumes.resolve("\\").dos_path(), "C:\\")

    def test_resolving_changes_nothing(self):
        self.volumes.resolve("..\\games")
        self.assertEqual((self.volumes.current_drive, self.volumes.directories[2]), (2, ["SETUP"]))

    def test_a_found_file_can_be_opened_through_the_location(self):
        location = self.volumes.resolve("readme.txt")
        with location.filesystem.open(location.parts) as f:
            self.assertEqual(f.read(), b"hi")


class TestChangingDirectory(VolumesTest):
    def test_into_a_directory_below_and_back(self):
        self.volumes.chdir("sub")
        self.assertEqual(self.volumes.directories[2], ["SETUP", "SUB"])  # in capitals
        self.assertEqual(self.volumes.current_directory(), "SETUP\\SUB")
        self.volumes.chdir("deep")
        self.assertEqual(self.volumes.current_directory(), "SETUP\\SUB\\DEEP")
        self.volumes.chdir("..\\..")
        self.assertEqual(self.volumes.current_directory(), "SETUP")
        self.volumes.chdir("\\")
        self.assertEqual(self.volumes.current_directory(), "")

    def test_names_are_matched_without_regard_to_case(self):
        self.volumes.chdir("\\gAmEs")
        self.assertEqual(self.volumes.directories[2], ["GAMES"])

    def test_a_drive_in_the_path_changes_the_directory_of_that_drive_only(self):
        self.volumes.filesystems[3].mkdir(["TMP"])
        self.volumes.chdir("D:\\tmp")
        self.assertEqual(self.volumes.current_directory(3), "TMP")
        self.assertEqual(self.volumes.current_directory(2), "SETUP")  # C: has not moved
        self.assertEqual(self.volumes.current_drive, 2)  # and neither has the current drive

    def test_a_directory_that_is_not_there_is_path_not_found_and_nothing_changes(self):
        self.assertFails(PATH_NOT_FOUND, self.volumes.chdir, "nowhere")
        self.assertFails(PATH_NOT_FOUND, self.volumes.chdir, "\\setup\\nowhere\\deeper")
        self.assertEqual(self.volumes.current_directory(), "SETUP")

    def test_a_file_is_not_a_directory(self):
        self.assertFails(PATH_NOT_FOUND, self.volumes.chdir, "readme.txt")
        self.assertEqual(self.volumes.current_directory(), "SETUP")

    def test_above_the_root_and_on_a_drive_that_cannot_be_used(self):
        self.assertFails(PATH_NOT_FOUND, self.volumes.chdir, "..\\..")
        self.assertFails(DRIVE_NOT_READY, self.volumes.chdir, "A:\\")
        self.assertFails(INVALID_DRIVE, self.volumes.chdir, "Z:\\")

    def test_the_directory_is_kept_per_drive_when_the_current_drive_changes(self):
        self.volumes.chdir("sub")
        self.volumes.current_drive = 3
        self.assertEqual(self.resolve("x"), (3, ["x"]))
        self.volumes.current_drive = 2
        self.assertEqual(self.resolve("x"), (2, ["SETUP", "SUB", "x"]))


if __name__ == "__main__":
    unittest.main()
