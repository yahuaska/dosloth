import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from mydos import config
from mydos.config import ConfigError
from tests.iso_builder import build

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

    def test_the_type_of_the_drive_is_needed(self):
        self.assertRejects("[disks.A]\n", "type", "missing")

    def test_removable_is_the_old_way_and_is_true_or_false(self):
        for value in ('"yes"', "1", "0", "[true]"):
            with self.subTest(value=value):
                self.assertRejects(f"[disks.A]\nremovable = {value}", "removable", "true or false")
        c = self.parse("[disks.A]\nremovable = true\n[disks.B]\nremovable = false\n[mounts]\nB = {type = 'tmpfs'}")
        self.assertEqual([c.disks[n].type for n in (0, 1)], ["floppy", "hdd"])

    def test_the_type_is_floppy_hdd_or_cdrom(self):
        c = self.parse("[disks.A]\ntype = 'floppy'\n[disks.C]\ntype = 'hdd'\n[disks.D]\ntype = 'cdrom'\n"
                       "[mounts]\nC = {type = 'tmpfs'}")
        self.assertEqual([c.disks[n].type for n in (0, 2, 3)], ["floppy", "hdd", "cdrom"])
        for value in ('"tape"', '"HDD"', "1", "true", '""'):
            with self.subTest(value=value):
                self.assertRejects(f"[disks.A]\ntype = {value}", "type", "floppy, hdd, cdrom")

    def test_type_and_removable_together_are_refused(self):
        self.assertRejects("[disks.A]\ntype = 'floppy'\nremovable = true", "type", "removable", "only")

    def test_only_a_hdd_is_not_removable(self):
        c = self.parse("[disks.A]\ntype = 'floppy'\n[disks.C]\ntype = 'hdd'\n[disks.D]\ntype = 'cdrom'\n"
                       "[mounts]\nC = {type = 'tmpfs'}")
        self.assertEqual([c.disks[n].removable for n in (0, 2, 3)], [True, False, True])

    def test_a_cdrom_may_be_left_empty_a_hdd_may_not(self):
        c = self.parse("[disks.A]\ntype = 'floppy'\n[disks.D]\ntype = 'cdrom'\n[disks.C]\ntype = 'hdd'\n"
                       "[mounts]\nC = {type = 'tmpfs'}")
        self.assertIsNone(c.disks[3].mount)
        self.assertRejects("[disks.C]\ntype = 'hdd'\n[disks.D]\ntype = 'cdrom'\n[mounts]\nD = {type = 'dir', "
                           "path = 'games'}", "C", "needs something mounted")

    def test_what_is_mounted_in_a_cdrom_is_read_only_without_saying_so(self):
        c = self.parse("[disks.C]\ntype = 'hdd'\n[disks.D]\ntype = 'cdrom'\n[mounts]\nC = {type = 'tmpfs'}\n"
                       "D = {type = 'dir', path = 'games'}")
        self.assertTrue(c.disks[3].mount.readonly)
        self.assertFalse(c.disks[2].mount.readonly)

    def test_a_cdrom_cannot_be_made_writable_and_cannot_be_memory(self):
        self.assertRejects("[disks.D]\ntype = 'cdrom'\n[mounts]\nD = {type = 'dir', path = 'games', readonly = false}",
                           "mounts.D", "CD-ROM", "written")
        self.assertRejects("[disks.D]\ntype = 'cdrom'\n[mounts]\nD = {type = 'tmpfs'}", "mounts.D", "CD-ROM")
        c = self.parse("[disks.D]\ntype = 'cdrom'\n[mounts]\nD = {type = 'dir', path = 'games', readonly = true}")
        self.assertTrue(c.disks[3].mount.readonly)

    def test_a_disc_image_in_a_cdrom_is_an_iso_mount_with_the_path_of_the_file(self):
        (self.base / "disc.iso").write_bytes(build({"A.TXT": b"x"}))
        c = self.parse("[disks.D]\ntype = 'cdrom'\n[mounts]\nD = {type = 'iso', path = 'disc.iso'}")
        mount = c.disks[3].mount
        self.assertEqual((mount.type, mount.path, mount.readonly), ("iso", self.base / "disc.iso", True))

    def test_an_image_can_be_said_to_be_read_only_but_not_writable(self):
        (self.base / "disc.iso").write_bytes(build({}))
        self.parse("[disks.D]\ntype = 'cdrom'\n[mounts]\nD = {type = 'iso', path = 'disc.iso', readonly = true}")
        self.assertRejects("[disks.D]\ntype = 'cdrom'\n[mounts]\n"
                           "D = {type = 'iso', path = 'disc.iso', readonly = false}", "mounts.D", "CD-ROM", "written")

    def test_an_image_is_for_a_cdrom_drive_only(self):
        (self.base / "disc.iso").write_bytes(build({}))
        for kind in ("floppy", "hdd"):
            self.assertRejects(f"[disks.D]\ntype = '{kind}'\n[mounts]\nD = {{type = 'iso', path = 'disc.iso'}}",
                               "mounts.D", "CD-ROM drive")

    def test_an_image_needs_a_path_that_is_a_file(self):
        (self.base / "disc.iso").write_bytes(build({}))
        self.assertRejects("[disks.D]\ntype = 'cdrom'\n[mounts]\nD = {type = 'iso'}", "mounts.D", "path")
        self.assertRejects("[disks.D]\ntype = 'cdrom'\n[mounts]\nD = {type = 'iso', path = 'games'}",
                           "mounts.D.path", "not a file")
        self.assertRejects("[disks.D]\ntype = 'cdrom'\n[mounts]\nD = {type = 'iso', path = 'missing.iso'}",
                           "mounts.D.path", "not a file")

    def test_a_file_that_is_not_an_image_is_said_when_the_config_is_read(self):
        (self.base / "junk.iso").write_bytes(b"junk" * 20000)
        self.assertRejects("[disks.D]\ntype = 'cdrom'\n[mounts]\nD = {type = 'iso', path = 'junk.iso'}",
                           "mounts.D.path", "volume descriptor")

    def test_a_tmpfs_in_a_cdrom_is_still_refused_and_the_message_names_the_image_too(self):
        self.assertRejects("[disks.D]\ntype = 'cdrom'\n[mounts]\nD = {type = 'tmpfs'}", "image")

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
        self.assertEqual([c.disks[n].type for n in sorted(c.disks)], ["floppy", "floppy", "hdd", "cdrom"])
        self.assertEqual([c.disks[n].removable for n in sorted(c.disks)], [True, True, False, True])
        self.assertEqual([c.disks[n].mount for n in (0, 1, 3)], [None, None, None])
        self.assertEqual(c.disks[2].mount, config.Mount("dir", self.base, False))
        self.assertEqual((c.cwd_drive, c.cwd, c.drive_count), (2, "\\", 4))


if __name__ == "__main__":
    unittest.main()
