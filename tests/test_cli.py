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


class TestTheCommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def program(self, code):
        path = self.dir / "t.com"
        path.write_bytes(code)
        return path

    def config(self, text, name="machine.toml"):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def run_emulator(self, *arguments):
        return subprocess.run([sys.executable, "-m", "mydos", *map(str, arguments)], capture_output=True, text=True,
                              timeout=30, cwd=REPOSITORY)

    def test_the_starting_directory_in_the_config_chooses_the_drive_the_program_starts_on(self):
        config = self.config("cwd = 'D:\\'\n[disks.C]\nremovable = false\n[disks.D]\nremovable = true\n"
                             "[mounts]\nC = { type = \"tmpfs\" }\nD = { type = \"tmpfs\" }\n")
        result = self.run_emulator("-c", config, self.program(CURRENT_DRIVE))
        self.assertEqual((result.returncode, result.stderr), (3, ""))  # D:

    def test_the_number_of_drives_comes_from_the_config(self):
        config = self.config("[disks.A]\nremovable = true\n[disks.C]\nremovable = false\n"
                             "[disks.F]\nremovable = true\n[mounts]\nC = { type = \"tmpfs\" }\n")
        result = self.run_emulator("-c", config, self.program(DRIVE_COUNT))
        self.assertEqual((result.returncode, result.stderr), (6, ""))  # F: is drive 5, so 6

    def test_a_directory_mount_is_taken_from_the_directory_of_the_config(self):
        (self.dir / "disk").mkdir()
        config = self.config('[disks.C]\nremovable = false\n[mounts]\nC = { type = "dir", path = "disk" }\n')
        result = self.run_emulator("-c", config, self.program(CURRENT_DRIVE))
        self.assertEqual((result.returncode, result.stderr), (2, ""))  # started, with C: from that directory

    def test_without_a_config_the_built_in_machine_starts_on_c_with_four_drives(self):
        self.assertEqual(self.run_emulator(self.program(CURRENT_DRIVE)).returncode, 2)
        self.assertEqual(self.run_emulator(self.program(DRIVE_COUNT)).returncode, 4)

    def test_a_config_that_does_not_exist_stops_before_the_program_with_a_message(self):
        result = self.run_emulator("-c", self.dir / "missing.toml", self.program(CURRENT_DRIVE))
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing.toml", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_wrong_config_stops_before_the_program_and_says_what_is_wrong(self):
        config = self.config("[disks.C]\nremovable = false\n")  # a hard disk with nothing mounted in it
        result = self.run_emulator("-c", config, self.program(CURRENT_DRIVE))
        self.assertEqual(result.returncode, 2)
        self.assertIn("machine.toml", result.stderr)
        self.assertIn("mounts.C", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_config_that_is_not_toml(self):
        result = self.run_emulator("-c", self.config("this is [not toml"), self.program(CURRENT_DRIVE))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_the_help_mentions_the_config(self):
        result = self.run_emulator("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("-c FILE", result.stdout)


if __name__ == "__main__":
    unittest.main()
