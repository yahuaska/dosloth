import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent

# mov ah,19h ; int 21h ; mov ah,4Ch ; int 21h      the exit code is the number of the current drive
CURRENT_DRIVE = bytes([0xB4, 0x19, 0xCD, 0x21, 0xB4, 0x4C, 0xCD, 0x21])
# mov dl,1 ; mov ah,0Eh ; int 21h ; mov ah,4Ch ; int 21h     the exit code is how many drives there are
DRIVE_COUNT = bytes([0xB2, 0x01, 0xB4, 0x0E, 0xCD, 0x21, 0xB4, 0x4C, 0xCD, 0x21])
# an opcode that does not exist, so the emulator stops and leaves a dump
CRASH = bytes([0x0F])


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()

    def program(self, code, name="t.com", directory=None):
        path = (directory or self.dir) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(code)
        return path

    def config(self, text, name="machine.toml"):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def run_emulator(self, *arguments, cwd=None):
        """Run the emulator from a directory (by default the temporary one, where the programs are)."""
        environment = {**os.environ, "PYTHONPATH": str(REPOSITORY)}
        return subprocess.run([sys.executable, "-m", "mydos", *map(str, arguments)], capture_output=True, text=True,
                              timeout=30, cwd=cwd or self.dir, env=environment)

    # a hard disk C: that is the directory of the config, and a memory disk D:
    TWO_DISKS = """
        {cwd}
        [disks.C]
        removable = false
        [disks.D]
        removable = true
        [mounts]
        C = {{ type = "dir", path = "." }}
        D = {{ type = "tmpfs" }}
    """


class TestTheConfig(CommandLineTest):
    def test_the_starting_directory_in_the_config_chooses_the_drive_the_program_starts_on(self):
        config = self.config(self.TWO_DISKS.format(cwd="cwd = 'D:\\'"))
        self.program(CURRENT_DRIVE)
        result = self.run_emulator("-c", config, "C:\\T.COM")  # D:, where the program starts, is empty: name it in full
        self.assertEqual((result.returncode, result.stderr), (3, ""))  # D:

    def test_the_number_of_drives_comes_from_the_config(self):
        config = self.config("[disks.A]\nremovable = true\n[disks.C]\nremovable = false\n[disks.F]\n"
                             "removable = true\n[mounts]\nC = { type = \"dir\", path = \".\" }\n")
        self.program(DRIVE_COUNT)
        result = self.run_emulator("-c", config, "t.com")
        self.assertEqual((result.returncode, result.stderr), (6, ""))  # F: is drive 5, so 6

    def test_a_directory_mount_is_taken_from_the_directory_of_the_config(self):
        config = self.config('[disks.C]\nremovable = false\n[mounts]\nC = { type = "dir", path = "disk" }\n')
        self.program(CURRENT_DRIVE, directory=self.dir / "disk")
        result = self.run_emulator("-c", config, "t.com", cwd=REPOSITORY)  # run from elsewhere: it makes no difference
        self.assertEqual((result.returncode, result.stderr), (2, ""))

    def test_without_a_config_the_built_in_machine_starts_on_c_with_four_drives(self):
        self.program(CURRENT_DRIVE, "cur.com")
        self.program(DRIVE_COUNT, "cnt.com")
        self.assertEqual(self.run_emulator("cur.com").returncode, 2)
        self.assertEqual(self.run_emulator("cnt.com").returncode, 4)

    def test_a_config_that_does_not_exist_stops_before_the_program_with_a_message(self):
        result = self.run_emulator("-c", self.dir / "missing.toml", self.program(CURRENT_DRIVE).name)
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing.toml", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_wrong_config_stops_before_the_program_and_says_what_is_wrong(self):
        config = self.config("[disks.C]\nremovable = false\n")  # a hard disk with nothing mounted in it
        result = self.run_emulator("-c", config, self.program(CURRENT_DRIVE).name)
        self.assertEqual(result.returncode, 2)
        self.assertIn("machine.toml", result.stderr)
        self.assertIn("mounts.C", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_config_that_is_not_toml(self):
        result = self.run_emulator("-c", self.config("this is [not toml"), self.program(CURRENT_DRIVE).name)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_the_help_mentions_the_config_and_the_dos_names(self):
        result = self.run_emulator("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("-c FILE", result.stdout)
        self.assertIn("ESETUP.EXE", result.stdout)


class TestNamingTheProgram(CommandLineTest):
    """The program is named as DOS names a file, and found through the drives, not on the host's path."""

    def test_by_name_from_the_current_directory(self):
        self.program(CURRENT_DRIVE)
        result = self.run_emulator("t.com")
        self.assertEqual((result.returncode, result.stderr), (2, ""))

    def test_whatever_the_case(self):
        self.program(CURRENT_DRIVE, "Mixed.Com")
        for name in ("MIXED.COM", "mixed.com", "Mixed.Com"):
            with self.subTest(name=name):
                self.assertEqual(self.run_emulator(name).returncode, 2)

    def test_in_a_subdirectory_with_either_separator(self):
        self.program(CURRENT_DRIVE, "t.com", directory=self.dir / "games" / "arcade")
        for name in ("games/arcade/t.com", "GAMES\\ARCADE\\T.COM", ".\\games\\arcade\\t.com", "games//arcade/./t.com"):
            with self.subTest(name=name):
                self.assertEqual(self.run_emulator(name).returncode, 2, name)

    def test_with_the_drive_and_from_the_root(self):
        self.program(CURRENT_DRIVE, "t.com", directory=self.dir / "games")
        for name in ("C:\\GAMES\\T.COM", "c:/games/t.com", "\\games\\t.com"):
            with self.subTest(name=name):
                self.assertEqual(self.run_emulator(name).returncode, 2, name)

    def test_the_starting_directory_of_the_config_is_where_a_bare_name_is_looked_for(self):
        self.program(CURRENT_DRIVE, "t.com", directory=self.dir / "games")
        config = self.config(self.TWO_DISKS.format(cwd="cwd = 'C:\\games'"))
        self.assertEqual(self.run_emulator("-c", config, "t.com").returncode, 2)
        self.assertEqual(self.run_emulator("-c", config, "..\\games\\t.com").returncode, 2)

    def test_a_program_that_is_not_there(self):
        result = self.run_emulator("nothing.com")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot run nothing.com", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_directory_is_not_a_program(self):
        (self.dir / "games").mkdir()
        result = self.run_emulator("games")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot run games", result.stderr)

    def test_a_path_on_the_host_that_is_not_on_a_mounted_drive_is_not_found(self):
        path = self.program(CURRENT_DRIVE, directory=self.dir / "elsewhere")
        result = self.run_emulator(path, cwd=REPOSITORY)  # an absolute host path: C:\tmp\... does not exist in DOS
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot run", result.stderr)

    def test_a_drive_with_no_disk_in_it_and_a_drive_that_is_not_there(self):
        config = self.config("[disks.A]\nremovable = true\n[disks.C]\nremovable = false\n"
                             "[mounts]\nC = { type = \"dir\", path = \".\" }\n")
        self.program(CURRENT_DRIVE)
        for name, word in (("A:\\T.COM", "nothing in it"), ("Z:\\T.COM", "no drive Z"), ("..\\..\\T.COM", "above")):
            with self.subTest(name=name):
                result = self.run_emulator("-c", config, name)
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(word, result.stderr)

    def test_the_program_is_started_from_a_read_only_disk_and_from_a_directory_with_a_dollar_in_its_name(self):
        config = self.config('[disks.C]\nremovable = false\n'
                             '[mounts]\nC = { type = "dir", path = ".", readonly = true }\n')
        self.program(CURRENT_DRIVE, "t.com", directory=self.dir / "a$b")
        self.assertEqual(self.run_emulator("-c", config, "a$b\\t.com").returncode, 2)


# What the program sees as its own name: it finds the end of its environment (an empty string after the
# last NAME=value), skips the word after it, and exits with the character that is DISPLACEMENT bytes into the name.
#   mov es,[002Ch] ; xor di,di ; mov al,0 ; mov cx,7FFFh ; cld
#   again: repne scasb ; cmp byte es:[di],0 ; jne again ; mov al,es:[di+3+DISPLACEMENT] ; mov ah,4Ch ; int 21h
def own_name_character(displacement):
    return bytes([0x8E, 0x06, 0x2C, 0x00, 0x33, 0xFF, 0xB0, 0x00, 0xB9, 0xFF, 0x7F, 0xFC, 0xF2, 0xAE,
                  0x26, 0x80, 0x3D, 0x00, 0x75, 0xF8, 0x26, 0x8A, 0x45, 3 + displacement, 0xB4, 0x4C, 0xCD, 0x21])


class TestTheNameTheProgramSees(CommandLineTest):
    """The program gets its full DOS name in the environment, not what was typed on the command line."""

    def test_a_program_in_the_root(self):
        self.program(own_name_character(0))  # C:\T.COM: the first character is the drive letter
        self.assertEqual(self.run_emulator("t.com").returncode, ord("C"))
        self.program(own_name_character(3), "t.com")  # ... and the fourth is the T of T.COM
        self.assertEqual(self.run_emulator("t.com").returncode, ord("T"))

    def test_a_program_in_a_subdirectory_is_named_with_its_directory_in_capitals(self):
        self.program(own_name_character(3), "t.com", directory=self.dir / "games")  # C:\GAMES\T.COM
        self.assertEqual(self.run_emulator("games/t.com").returncode, ord("G"))
        self.program(own_name_character(9), "t.com", directory=self.dir / "games")
        self.assertEqual(self.run_emulator("Games\\t.com").returncode, ord("T"))  # the T after C:\GAMES\

    def test_the_drive_is_the_one_the_program_was_found_on(self):
        config = self.config(self.TWO_DISKS.format(cwd="").replace(
            '        D = { type = "tmpfs" }', '        D = { type = "dir", path = "other" }'))
        self.program(own_name_character(0), "t.com", directory=self.dir / "other")
        self.assertEqual(self.run_emulator("-c", config, "D:\\t.com").returncode, ord("D"))


class TestTheDumpOfACrash(CommandLineTest):
    def test_it_goes_beside_the_program_on_the_host_under_its_real_name(self):
        self.program(CRASH, "Prog.Com", directory=self.dir / "sub")
        result = self.run_emulator("SUB\\PROG.COM", cwd=self.dir)
        self.assertEqual(result.returncode, 1)  # the emulator stopped: an exception, with its traceback
        self.assertIn("NotImplementedError", result.stderr)
        self.assertTrue((self.dir / "sub" / "Prog.Com.crash.json").exists())
        self.assertTrue((self.dir / "sub" / "Prog.Com.crash.mem").exists())
        self.assertEqual(sorted(os.listdir(self.dir)), ["sub"])  # and nowhere else


if __name__ == "__main__":
    unittest.main()
