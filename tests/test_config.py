import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from mydos import config
from mydos.config import ConfigError

REPOSITORY = Path(__file__).resolve().parent.parent


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        (self.base / "games").mkdir()
        (self.base / "Setup" / "Sub").mkdir(parents=True)  # mixed case, as a Linux user might have it

    def parse(self, text):
        return config.parse(tomllib.loads(text), self.base)

    def assertRejects(self, text, *words):
        """The text is a bad configuration, and the message mentions every one of `words`."""
        with self.assertRaises(ConfigError) as caught:
            self.parse(text)
        for word in words:
            self.assertIn(word, str(caught.exception))

    GOOD = """
        [disks.C]
        removable = false
        [mounts]
        C = { type = "dir", path = "games" }
    """


class TestAGoodConfig(ConfigTest):
    def test_the_drives_the_mounts_and_the_start(self):
        c = self.parse("""
            cwd = 'C:\\'
            [disks.A]
            removable = true
            [disks.b]
            removable = true
            [disks.C]
            removable = false
            [disks.D]
            removable = true
            [mounts]
            C = { type = "dir", path = "games" }
            D = { type = "dir", path = ".", readonly = true }
            b = { type = "tmpfs" }
        """)
        self.assertEqual(sorted(c.disks), [0, 1, 2, 3])
        self.assertEqual([c.disks[n].letter for n in sorted(c.disks)], ["A", "B", "C", "D"])  # the "b" became B
        self.assertEqual([c.disks[n].removable for n in sorted(c.disks)], [True, True, False, True])
        self.assertIsNone(c.disks[0].mount)  # nothing in A:
        self.assertEqual(c.disks[1].mount, config.Mount("tmpfs", None, False))
        self.assertEqual(c.disks[2].mount, config.Mount("dir", self.base / "games", False))
        self.assertEqual(c.disks[3].mount, config.Mount("dir", self.base, True))
        self.assertEqual((c.cwd_drive, c.cwd), (2, "\\"))  # the games directory is the root of C:

    def test_a_relative_path_is_taken_from_the_directory_given_not_from_where_the_emulator_runs(self):
        here = os.getcwd()
        os.chdir(REPOSITORY)
        self.addCleanup(os.chdir, here)
        c = self.parse(self.GOOD)
        self.assertEqual(c.disks[2].mount.path, self.base / "games")

    def test_an_absolute_path_stays_as_it_is(self):
        c = self.parse(f"""
            [disks.C]
            removable = false
            [mounts]
            C = {{ type = "dir", path = "{self.base / 'games'}" }}
        """)
        self.assertEqual(c.disks[2].mount.path, self.base / "games")

    def test_the_number_of_drives_is_the_highest_number_plus_one(self):
        c = self.parse("""
            [disks.A]
            removable = true
            [disks.C]
            removable = false
            [disks.F]
            removable = true
            [mounts]
            C = { type = "tmpfs" }
        """)
        self.assertEqual((sorted(c.disks), c.drive_count), ([0, 2, 5], 6))


class TestTheStartingDirectory(ConfigTest):
    TWO = """
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

    def cwd(self, line):
        c = self.parse(line + self.TWO)
        return c.cwd_drive, c.cwd

    def test_the_ways_to_write_it(self):
        self.assertEqual(self.cwd("cwd = 'C:\\'"), (2, "\\"))
        self.assertEqual(self.cwd("cwd = 'C:'"), (2, "\\"))
        self.assertEqual(self.cwd("cwd = 'c:/'"), (2, "\\"))
        self.assertEqual(self.cwd("cwd = 'C:\\Setup'"), (2, "\\SETUP"))
        self.assertEqual(self.cwd("cwd = 'C:/setup/sub'"), (2, "\\SETUP\\SUB"))
        self.assertEqual(self.cwd("cwd = 'C:\\SETUP\\\\SUB\\'"), (2, "\\SETUP\\SUB"))  # doubled and trailing slashes

    def test_the_case_of_the_host_directory_does_not_matter(self):
        self.assertEqual(self.cwd("cwd = 'C:\\sEtUp\\sUb'"), (2, "\\SETUP\\SUB"))  # the host has Setup/Sub

    def test_a_tmpfs_is_empty_so_only_its_root_exists(self):
        self.assertEqual(self.cwd("cwd = 'D:\\'"), (3, "\\"))

    def test_without_cwd_the_start_is_the_root_of_c(self):
        self.assertEqual(self.cwd(""), (2, "\\"))

    def test_without_cwd_and_without_a_mounted_c_it_is_the_first_drive_with_a_medium(self):
        c = self.parse("""
            [disks.A]
            removable = true
            [disks.D]
            removable = true
            [disks.E]
            removable = true
            [mounts]
            D = { type = "tmpfs" }
            E = { type = "tmpfs" }
        """)
        self.assertEqual((c.cwd_drive, c.cwd), (3, "\\"))

    def test_a_floppy_before_c_does_not_take_the_start_away_from_it(self):
        c = self.parse("""
            [disks.A]
            removable = true
            [disks.B]
            removable = true
            [disks.C]
            removable = false
            [mounts]
            A = { type = "tmpfs" }
            B = { type = "tmpfs" }
            C = { type = "tmpfs" }
        """)
        self.assertEqual((c.cwd_drive, c.cwd), (2, "\\"))

    def test_nonsense_is_rejected(self):
        for text, words in (("cwd = 5", ("cwd", "string")), ("cwd = 'C'", ("cwd", "not a drive")),
                            ("cwd = '\\SETUP'", ("cwd",)), ("cwd = 'CC:\\'", ("cwd",)),
                            ("cwd = 'C:SETUP'", ("cwd",)), ("cwd = ''", ("cwd",)),
                            ("cwd = 'Z:\\'", ("cwd", "no drive Z")),
                            ("cwd = 'A:\\'", ("cwd", "nothing mounted")),
                            ("cwd = 'C:\\Nowhere'", ("cwd", "Nowhere")),
                            ("cwd = 'C:\\Setup\\Nowhere'", ("cwd", "Nowhere")),
                            ("cwd = 'D:\\Anything'", ("cwd", "Anything")),
                            ("cwd = 'C:\\Setup\\..'", ("cwd", "..")), ("cwd = 'C:\\.'", ("cwd",))):
            with self.subTest(text=text):
                self.assertRejects(text + self.TWO, *words)

    def test_a_file_is_not_a_directory(self):
        (self.base / "readme.txt").write_text("hello")
        self.assertRejects("cwd = 'C:\\README.TXT'" + self.TWO, "cwd", "README.TXT")

    def test_with_nothing_mounted_anywhere_there_is_nowhere_to_start(self):
        self.assertRejects("[disks.A]\nremovable = true", "no drive has anything mounted")


class TestTheDisks(ConfigTest):
    def test_there_must_be_a_disks_table_with_a_drive_in_it(self):
        self.assertRejects("", "disks", "at least one")
        self.assertRejects("[disks]", "disks", "at least one")
        self.assertRejects("disks = 5", "disks", "table")

    def test_a_drive_letter_is_one_letter(self):
        for key in ("AB", "1", "é", "_", "AA", ""):
            with self.subTest(key=key):
                self.assertRejects(f'[disks."{key}"]\nremovable = true', "drive letter")  # quoted: TOML allows any key

    def test_a_drive_cannot_be_described_twice_in_two_cases(self):
        self.assertRejects('[disks.a]\nremovable = true\n[disks.A]\nremovable = true', "twice")

    def test_removable_is_needed_and_is_true_or_false(self):
        self.assertRejects("[disks.A]\n", "removable", "missing")
        for value in ('"yes"', "1", "0", "[true]"):
            with self.subTest(value=value):
                self.assertRejects(f"[disks.A]\nremovable = {value}", "removable", "true or false")

    def test_a_disk_is_a_table_with_known_keys(self):
        self.assertRejects("[disks]\nA = 5", "disks.A", "table")
        self.assertRejects("[disks.A]\nremovable = true\ncolour = 1", "colour")

    def test_the_top_level_has_known_keys_only(self):
        # the key goes first: written after a table header it would be a key of that table
        self.assertRejects("speed = 3\n" + self.GOOD, "speed", "unknown")


class TestTheMounts(ConfigTest):
    BASE = """
        [disks.A]
        removable = true
        [disks.C]
        removable = false
        [disks.D]
        removable = false
    """

    def mounts(self, text, c_and_d=True):
        extra = "\n[mounts]\n" + ('D = { type = "tmpfs" }\n' if c_and_d else "")
        return self.BASE + extra + text

    def test_a_hard_disk_needs_a_medium_and_a_floppy_does_not(self):
        self.assertRejects(self.BASE, "mounts.C", "needs something mounted")  # C: and D: are hard disks
        self.assertRejects(self.mounts('C = { type = "tmpfs" }', c_and_d=False), "mounts.D", "needs something mounted")
        c = self.parse(self.mounts('C = { type = "tmpfs" }'))
        self.assertIsNone(c.disks[0].mount)  # A: is removable, and empty

    def test_a_mount_needs_a_drive_in_disks(self):
        self.assertRejects(self.mounts('C = { type = "tmpfs" }\nE = { type = "tmpfs" }'), "mounts.E", "no drive E")

    def test_a_drive_is_not_mounted_twice(self):
        self.assertRejects(self.mounts('C = { type = "tmpfs" }\nc = { type = "tmpfs" }'), "mounted twice")

    def test_the_mount_key_is_a_drive_letter(self):
        self.assertRejects(self.mounts('CC = { type = "tmpfs" }'), "drive letter")

    def test_the_type_is_dir_or_tmpfs(self):
        for table in ("{}", '{ type = "ramdisk" }', '{ type = 5 }', '{ path = "." }'):
            with self.subTest(table=table):
                self.assertRejects(self.mounts(f"C = {table}"), "mounts.C.type", "dir", "tmpfs")

    def test_a_dir_needs_a_path_that_names_a_directory(self):
        self.assertRejects(self.mounts('C = { type = "dir" }'), "mounts.C", "needs a 'path'")
        self.assertRejects(self.mounts('C = { type = "dir", path = "" }'), "mounts.C.path")
        self.assertRejects(self.mounts('C = { type = "dir", path = 5 }'), "mounts.C.path")
        self.assertRejects(self.mounts('C = { type = "dir", path = "nowhere" }'),
                           "mounts.C.path", "nowhere", "not a directory")

    def test_a_file_is_not_a_directory_to_mount(self):
        (self.base / "file.txt").write_text("x")
        self.assertRejects(self.mounts('C = { type = "dir", path = "file.txt" }'), "file.txt", "not a directory")

    def test_a_tmpfs_has_no_path(self):
        self.assertRejects(self.mounts('C = { type = "tmpfs", path = "." }'), "mounts.C", "no path")

    def test_readonly_is_true_or_false_and_false_by_default(self):
        self.assertRejects(self.mounts('C = { type = "tmpfs", readonly = "yes" }'), "mounts.C.readonly")
        c = self.parse(self.mounts('C = { type = "tmpfs", readonly = true }'))
        self.assertTrue(c.disks[2].mount.readonly)
        c = self.parse(self.mounts('C = { type = "tmpfs" }'))
        self.assertFalse(c.disks[2].mount.readonly)

    def test_unknown_keys_in_a_mount_are_rejected(self):
        self.assertRejects(self.mounts('C = { type = "tmpfs", size = 5 }'), "mounts.C", "size")

    def test_the_mounts_must_be_a_table_of_tables(self):
        self.assertRejects("mounts = 5\n" + self.BASE, "mounts", "table")
        self.assertRejects(self.mounts('C = "."'), "mounts.C", "table")


class TestLoadingAFile(ConfigTest):
    def write(self, text, name="machine.toml"):
        path = self.base / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_file_is_read_and_its_relative_paths_are_from_its_own_directory(self):
        (self.base / "inner").mkdir()
        (self.base / "inner" / "disk").mkdir()
        text = '[disks.C]\nremovable = false\n[mounts]\nC = { type = "dir", path = "disk" }\n'
        path = self.write(text, "inner/m.toml")
        self.assertEqual(config.load(path).disks[2].mount.path, self.base / "inner" / "disk")

    def test_the_file_name_is_in_every_message(self):
        path = self.write("[disks.C]\nremovable = false\n")  # a hard disk with nothing mounted
        with self.assertRaises(ConfigError) as caught:
            config.load(path)
        self.assertIn("machine.toml", str(caught.exception))
        self.assertIn("mounts.C", str(caught.exception))

    def test_a_file_that_is_not_toml(self):
        path = self.write("this is [not toml")
        with self.assertRaises(ConfigError) as caught:
            config.load(path)
        self.assertIn("machine.toml", str(caught.exception))

    def test_a_file_that_is_not_text(self):
        path = self.base / "binary.toml"
        path.write_bytes(b"\xff\xfe\x00\x01")
        with self.assertRaises(ConfigError):
            config.load(path)

    def test_a_file_that_is_not_there(self):
        with self.assertRaises(ConfigError) as caught:
            config.load(self.base / "missing.toml")
        self.assertIn("missing.toml", str(caught.exception))

    def test_the_example_in_the_repository_is_a_good_configuration(self):
        c = config.load(REPOSITORY / "config.example.toml")
        self.assertEqual([c.disks[n].letter for n in sorted(c.disks)], ["A", "B", "C", "D"])
        self.assertEqual((c.cwd_drive, c.cwd), (2, "\\"))
        self.assertEqual(c.disks[2].mount.path, REPOSITORY)
        self.assertTrue(c.disks[3].mount.readonly)
        self.assertEqual(c.disks[1].mount.type, "tmpfs")
        self.assertIsNone(c.disks[0].mount)


class TestTheBuiltInMachine(ConfigTest):
    def test_four_drives_with_the_hard_disk_in_the_directory_given(self):
        c = config.default(self.base)
        self.assertEqual([c.disks[n].letter for n in sorted(c.disks)], ["A", "B", "C", "D"])
        self.assertEqual([c.disks[n].removable for n in sorted(c.disks)], [True, True, False, True])
        self.assertEqual([c.disks[n].mount for n in (0, 1, 3)], [None, None, None])
        self.assertEqual(c.disks[2].mount, config.Mount("dir", self.base, False))
        self.assertEqual((c.cwd_drive, c.cwd, c.drive_count), (2, "\\", 4))


class TestFindingAFileWhateverItsCase(ConfigTest):
    def test_an_exact_match_wins(self):
        (self.base / "ABC").write_text("1")
        (self.base / "abc").write_text("2")
        self.assertEqual(config.find_ignoring_case(self.base, "abc"), self.base / "abc")
        self.assertEqual(config.find_ignoring_case(self.base, "ABC"), self.base / "ABC")

    def test_otherwise_the_case_does_not_matter(self):
        (self.base / "Readme.Txt").write_text("x")
        self.assertEqual(config.find_ignoring_case(self.base, "README.TXT"), self.base / "Readme.Txt")
        self.assertEqual(config.find_ignoring_case(self.base, "readme.txt"), self.base / "Readme.Txt")

    def test_two_that_differ_only_in_case_give_the_first_in_alphabetical_order(self):
        (self.base / "Aa").write_text("1")
        (self.base / "aA").write_text("2")
        self.assertEqual(config.find_ignoring_case(self.base, "AA"), self.base / "Aa")

    def test_the_choice_does_not_depend_on_the_order_the_system_lists_the_files_in(self):
        (self.base / "Aa").write_text("1")
        (self.base / "aA").write_text("2")
        for listing in (["Aa", "aA"], ["aA", "Aa"]):
            with self.subTest(listing=listing), mock.patch.object(config.os, "listdir", return_value=listing):
                self.assertEqual(config.find_ignoring_case(self.base, "AA"), self.base / "Aa")

    def test_nothing_found_is_none(self):
        self.assertIsNone(config.find_ignoring_case(self.base, "nothing"))


if __name__ == "__main__":
    unittest.main()
