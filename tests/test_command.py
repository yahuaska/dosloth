import contextlib
import io
import tempfile
import tomllib
import unittest
from pathlib import Path

from dosloth import command, config
from dosloth.volumes import Volumes
from tests.iso_builder import build

TEXT = """
    [disks.A]
    type = 'floppy'
    [disks.C]
    type = 'hdd'
    [disks.D]
    type = 'hdd'
    [disks.E]
    type = 'cdrom'
    [mounts]
    C = { type = "dir", path = "." }
    D = { type = "tmpfs" }
    E = { type = "iso", path = "disc.iso" }
"""


class ShellTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        (self.base / "disc.iso").write_bytes(build({"A.TXT": b"abc"}))
        (self.base / "GAMES").mkdir()
        (self.base / "NOTES.TXT").write_bytes(b"notes")
        self.volumes = Volumes(config.parse(tomllib.loads(TEXT), self.base))
        self.said = []
        self.shell = command.Shell(self.volumes, self.said.append)

    def run_line(self, line):
        """Run a command tail; the exit code, and what was said as one text."""
        code = self.shell.run(line)
        return code, "".join(self.said)

    def listing(self, directory=None):
        return sorted(path.name for path in (directory or self.base).iterdir() if path.name != "disc.iso")


class TestMd(ShellTest):
    def test_a_directory_is_made_in_capitals_and_nothing_is_said(self):
        self.assertEqual(self.run_line("/c md C:\\xcoma"), (0, ""))
        self.assertTrue((self.base / "XCOMA").is_dir())
        self.assertEqual(self.listing(), ["GAMES", "NOTES.TXT", "XCOMA"])

    def test_the_line_the_installer_sends(self):
        self.shell.run("/c md C:\\GAMES\\tacexe")
        self.assertTrue((self.base / "GAMES" / "TACEXE").is_dir())

    def test_the_switch_the_command_and_the_blanks_are_taken_as_they_come(self):
        for index, line in enumerate(("/c md C:\\A", "/C MD C:\\B", " /c  md   C:\\C", "/C Md C:\\D", "/c MKDIR C:\\E",
                                      "\t/c\tmkdir\tC:\\F ")):
            self.assertEqual(self.shell.run(line), 0, line)
        self.assertEqual(self.listing(), ["A", "B", "C", "D", "E", "F", "GAMES", "NOTES.TXT"])

    def test_the_directories_on_the_way_that_are_not_there_are_made_too(self):
        self.assertEqual(self.run_line("/c md C:\\ONE\\TWO\\THREE"), (0, ""))
        self.assertTrue((self.base / "ONE" / "TWO" / "THREE").is_dir())

    def test_the_ones_that_are_there_are_left_as_they_are(self):
        (self.base / "GAMES" / "KEEP.TXT").write_bytes(b"keep")
        self.assertEqual(self.run_line("/c md C:\\GAMES\\NEW\\DEEP"), (0, ""))
        self.assertEqual((self.base / "GAMES" / "KEEP.TXT").read_bytes(), b"keep")
        self.assertTrue((self.base / "GAMES" / "NEW" / "DEEP").is_dir())

    def test_a_relative_path_is_from_the_current_directory_of_the_drive(self):
        self.volumes.chdir("GAMES")
        self.shell.run("/c md SAVES")
        self.assertTrue((self.base / "GAMES" / "SAVES").is_dir())
        self.shell.run("/c md ..\\TOP")
        self.assertTrue((self.base / "TOP").is_dir())

    def test_another_drive_is_named_by_its_letter(self):
        self.assertEqual(self.run_line("/c md d:\\TEMP\\X"), (0, ""))
        self.assertEqual([entry.name for entry in self.volumes.filesystems[3].listdir([])], ["TEMP"])
        self.assertEqual([entry.name for entry in self.volumes.filesystems[3].listdir(["TEMP"])], ["X"])

    def test_a_directory_that_is_there_is_said_with_the_path_as_it_was_typed_and_exit_code_0(self):
        self.assertEqual(self.run_line("/c md C:\\games"), (0, "A subdirectory or file C:\\games already exists\r\n"))
        self.assertEqual(self.listing(), ["GAMES", "NOTES.TXT"])

    def test_a_file_of_that_name_is_the_same_thing_for_dos(self):
        self.assertEqual(self.run_line("/c md notes.txt")[1], "A subdirectory or file notes.txt already exists\r\n")

    def test_a_file_on_the_way_is_in_the_way(self):
        self.assertEqual(self.run_line("/c md C:\\NOTES.TXT\\SUB"), (0, "Unable to create directory\r\n"))
        self.assertEqual(self.listing(), ["GAMES", "NOTES.TXT"])

    def test_a_name_dos_cannot_have_is_unable(self):
        self.assertEqual(self.run_line("/c md C:\\TOOLONGNAME")[1], "Unable to create directory\r\n")
        self.said.clear()
        self.assertEqual(self.run_line("/c md C:\\A*B")[1], "Unable to create directory\r\n")

    def test_the_root_and_a_path_above_it_are_unable(self):
        for path in ("C:\\", "\\", "..", "C:\\.."):
            self.said.clear()
            self.assertEqual(self.run_line(f"/c md {path}")[1], "Unable to create directory\r\n", path)

    def test_a_drive_that_is_not_there_is_an_invalid_drive_specification(self):
        self.assertEqual(self.run_line("/c md Z:\\X"), (0, "Invalid drive specification\r\n"))

    def test_a_drive_with_nothing_in_it_is_unable_and_so_is_a_disc(self):
        self.assertEqual(self.run_line("/c md A:\\X")[1], "Unable to create directory\r\n")
        self.said.clear()
        self.assertEqual(self.run_line("/c md E:\\X")[1], "Unable to create directory\r\n")

    def test_no_path_and_two_paths_are_said_in_the_words_of_dos(self):
        self.assertEqual(self.run_line("/c md"), (0, "Required parameter missing\r\n"))
        self.said.clear()
        self.assertEqual(self.run_line("/c md C:\\A C:\\B"), (0, "Too many parameters - C:\\B\r\n"))
        self.assertEqual(self.listing(), ["GAMES", "NOTES.TXT"])  # nothing is made

    def test_every_message_ends_a_line_the_dos_way(self):
        self.run_line("/c md")
        self.assertTrue(self.said[0].endswith("\r\n"))


class TestWhatIsNotDone(ShellTest):
    def test_an_internal_command_that_is_not_done_stops_with_its_name(self):
        for line, name in (("/c dir C:\\", "DIR"), ("/c copy a b", "COPY"), ("/c echo hi", "ECHO"), ("/c Del x", "DEL"),
                           ("/c set A=1", "SET"), ("/c cd games", "CD")):
            with self.assertRaisesRegex(NotImplementedError, f"the command {name} is not done"):
                self.shell.run(line)

    def test_every_internal_command_of_the_list_stops_and_none_is_looked_for_as_a_program(self):
        (self.base / "DIR.COM").write_bytes(b"\xCD\x20")  # a file of the name of a command: DOS runs the command
        for name in sorted(command.INTERNAL):
            with self.assertRaisesRegex(NotImplementedError, f"the command {name} is not done"):
                self.shell.run(f"/c {name.lower()} x")

    def test_the_message_has_the_whole_line(self):
        with self.assertRaisesRegex(NotImplementedError, r"COMMAND.COM /C 'copy a b'"):
            self.shell.run("/c copy a b")

    def test_a_shell_that_stays_is_not_done(self):
        for line in ("", " ", "/k md C:\\X", "/p", "/e:512", "md C:\\X", "/y /c md C:\\X"):
            with self.assertRaisesRegex(NotImplementedError, "only /C"):
                self.shell.run(line)
        self.assertEqual(self.listing(), ["GAMES", "NOTES.TXT"])

    def test_every_redirection_but_the_output_to_nul_and_the_pipes_are_not_done(self):
        for line in ("/c md C:\\X > file", "/c md C:\\X >> nul", "/c md C:\\X < in", "/c md C:\\X | more",
                     "/c md C:\\X & md C:\\Y", "/c md C:\\X >nul x", "/c prog >nul >nul",
                     "/c prog <nul", "/c prog >nuls", "/c prog >con"):
            with self.assertRaisesRegex(NotImplementedError, "only the output to NUL is done"):
                self.shell.run(line)
        self.assertEqual(self.listing(), ["GAMES", "NOTES.TXT"])

    def test_c_alone_does_nothing_and_succeeds(self):
        self.assertEqual(self.run_line("/c"), (0, ""))
        self.assertEqual(self.run_line("/c   "), (0, ""))
        self.assertEqual(self.run_line("/c >nul"), (0, ""))


class TestOutputToNul(ShellTest):
    def test_an_internal_command_with_the_output_to_nul_works_and_says_nothing(self):
        for line in ("/c md C:\\QUIET >nul", "/c md C:\\QUIET2 > nul", "/c md C:\\QUIET3>NUL"):
            self.assertEqual(self.run_line(line), (0, ""), line)
        self.assertEqual(self.listing(), ["GAMES", "NOTES.TXT", "QUIET", "QUIET2", "QUIET3"])

    def test_even_what_goes_wrong_is_not_said(self):
        self.assertEqual(self.run_line("/c md C:\\GAMES >nul"), (0, ""))
        self.assertEqual(self.run_line("/c md >nul"), (0, ""))

    def test_the_words_come_back_for_the_next_command(self):
        self.shell.run("/c md C:\\GAMES >nul")
        self.assertEqual(self.run_line("/c md C:\\GAMES")[1], "A subdirectory or file C:\\GAMES already exists\r\n")


class TestRunningAProgram(ShellTest):
    def setUp(self):
        super().setUp()
        for name in ("SETUP.EXE", "TOOL.COM", "TOOL.EXE", "BOTH.EXE", "BOTH.COM", "ONLY.EXE", "BATCH.BAT", "DATA.TXT"):
            (self.base / name).write_bytes(b"x")
        (self.base / "GAMES" / "GAME.EXE").write_bytes(b"x")
        (self.base / "GAMES" / "TOOL.EXE").write_bytes(b"x")

    def launch(self, line, search_path=()):
        self.shell = command.Shell(self.volumes, self.said.append, search_path)
        return self.shell.run(line)

    def test_the_line_the_installer_sends_gives_the_program_its_tail_and_the_output_to_nul(self):
        got = self.launch("/c SETUP.exe child>nul")
        self.assertIsInstance(got, command.Launch)
        self.assertEqual(got.where.dos_path(), "C:\\SETUP.EXE")
        self.assertEqual((got.tail, got.null_output), (" child", True))
        self.assertEqual(self.said, [])

    def test_the_forms_of_the_output_to_nul_and_the_ones_without_it(self):
        for line, tail, null in (("/c setup>nul", "", True), ("/c setup >nul", "", True), ("/c setup > nul", "", True),
                                 ("/c setup >NUL", "", True), ("/c setup   >  Nul   ", "", True),
                                 ("/c setup a b>nul", " a b", True), ("/c setup a b", " a b", False),
                                 ("/c setup", "", False), ("/c setup /x /y", " /x /y", False),
                                 ("/c  setup   a  b ", "   a  b", False)):
            got = self.launch(line)
            self.assertEqual((got.tail, got.null_output), (tail, null), line)

    def test_the_command_word_may_be_in_any_case(self):
        self.assertEqual(self.launch("/c Setup.Exe").where.dos_path(), "C:\\SETUP.EXE")
        self.assertEqual(self.launch("/c setup").where.dos_path(), "C:\\SETUP.EXE")

    def test_a_name_with_no_extension_tries_com_then_exe_then_bat(self):
        self.assertEqual(self.launch("/c both").where.dos_path(), "C:\\BOTH.COM")
        self.assertEqual(self.launch("/c tool").where.dos_path(), "C:\\TOOL.COM")
        self.assertEqual(self.launch("/c only").where.dos_path(), "C:\\ONLY.EXE")
        with self.assertRaisesRegex(NotImplementedError, "batch files are not done"):
            self.launch("/c batch")

    def test_a_name_with_an_extension_is_that_file_and_no_other(self):
        self.assertEqual(self.launch("/c tool.exe").where.dos_path(), "C:\\TOOL.EXE")
        with self.assertRaisesRegex(NotImplementedError, "batch files"):
            self.launch("/c batch.bat")

    def test_a_file_that_is_not_a_program_by_its_extension_is_a_bad_command(self):
        self.assertEqual(self.launch("/c data.txt"), 0)
        self.assertEqual("".join(self.said), "Bad command or file name\r\n")

    def test_what_is_not_there_is_a_bad_command_with_exit_code_0(self):
        self.assertEqual(self.launch("/c nothing"), 0)
        self.assertEqual(self.launch("/c nothing.exe"), 0)
        self.assertEqual("".join(self.said), "Bad command or file name\r\n" * 2)

    def test_a_directory_is_not_a_program(self):
        (self.base / "TOOLS.EXE").mkdir()
        self.assertEqual(self.launch("/c tools.exe"), 0)
        self.assertEqual("".join(self.said), "Bad command or file name\r\n")

    def test_the_current_directory_comes_first_and_then_the_directories_of_path(self):
        self.volumes.chdir("GAMES")
        self.assertEqual(self.launch("/c game", ["C:\\"]).where.dos_path(), "C:\\GAMES\\GAME.EXE")  # here
        self.assertEqual(self.launch("/c setup", ["C:\\"]).where.dos_path(), "C:\\SETUP.EXE")  # in PATH
        self.assertEqual(self.launch("/c tool.exe", ["C:\\"]).where.dos_path(), "C:\\GAMES\\TOOL.EXE")  # here wins
        self.assertEqual(self.launch("/c tool", ["C:\\"]).where.dos_path(), "C:\\GAMES\\TOOL.EXE")  # the name is first
        self.assertEqual(self.launch("/c setup"), 0)  # no PATH: not found

    def test_path_is_gone_through_in_order_and_a_directory_with_a_bad_name_is_passed_over(self):
        got = self.launch("/c game", ["Z:\\NOWHERE", "C:\\MISSING", "C:\\GAMES;", "C:\\GAMES\\"])
        self.assertEqual(got.where.dos_path(), "C:\\GAMES\\GAME.EXE")

    def test_a_word_with_a_directory_or_a_drive_is_taken_as_it_is_and_not_looked_for_in_path(self):
        self.assertEqual(self.launch("/c games\\game", ["C:\\"]).where.dos_path(), "C:\\GAMES\\GAME.EXE")
        self.assertEqual(self.launch("/c C:\\games\\game.exe").where.dos_path(), "C:\\GAMES\\GAME.EXE")
        self.assertEqual(self.launch("/c c:setup").where.dos_path(), "C:\\SETUP.EXE")
        self.assertEqual(self.launch("/c ..\\setup", ["C:\\GAMES"]), 0)  # above the root, and PATH is not tried

    def test_a_dot_in_the_name_of_a_directory_is_not_an_extension(self):
        (self.base / "APPS.OLD").mkdir()
        (self.base / "APPS.OLD" / "RUN.EXE").write_bytes(b"x")
        self.assertEqual(self.launch("/c apps.old\\run").where.dos_path(), "C:\\APPS.OLD\\RUN.EXE")
        self.assertEqual(self.launch("/c c:apps.old\\run").where.dos_path(), "C:\\APPS.OLD\\RUN.EXE")

    def test_a_directory_of_path_may_end_with_a_backslash_or_not(self):
        for directory in ("C:\\GAMES", "C:\\GAMES\\"):
            self.assertEqual(self.launch("/c game", [directory]).where.dos_path(), "C:\\GAMES\\GAME.EXE", directory)

    def test_a_program_on_another_drive(self):
        with self.volumes.filesystems[3].create(["TEMP.COM"]) as file:
            file.write(b"x")
        got = self.launch("/c d:temp")
        self.assertEqual(got.where.dos_path(), "D:\\TEMP.COM")

    def test_an_internal_command_is_done_even_if_a_program_has_its_name(self):
        (self.base / "MD.COM").write_bytes(b"x")
        self.assertEqual(self.launch("/c md C:\\VIAMD"), 0)
        self.assertTrue((self.base / "VIAMD").is_dir())

    def test_the_tail_keeps_the_blanks_the_way_they_were_typed(self):
        self.assertEqual(self.launch("/c setup  a   b").tail, "  a   b")


    def test_two_as_an_argument_before_the_redirection_is_an_argument_as_in_dos(self):
        got = self.launch("/c setup 2>nul")
        self.assertEqual((got.tail, got.null_output), (" 2", True))


class TestTheConsole(unittest.TestCase):
    def test_by_default_what_is_said_goes_to_stdout_as_it_is(self):
        with tempfile.TemporaryDirectory() as directory:
            volumes = Volumes(config.parse(tomllib.loads("[disks.C]\ntype = 'hdd'\n[mounts]\n"
                                                         "C = { type = 'tmpfs' }\n"), Path(directory)))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                command.Shell(volumes).run("/c md")
        self.assertEqual(out.getvalue(), "Required parameter missing\r\n")

    def test_the_console_is_flushed_so_that_the_message_is_seen_at_once(self):
        class Stream:
            def __init__(self):
                self.events = []

            def write(self, text):
                self.events.append(("write", text))

            def flush(self):
                self.events.append(("flush",))

        stream = Stream()
        with contextlib.redirect_stdout(stream):
            command.console("hello")
        self.assertEqual(stream.events, [("write", "hello"), ("flush",)])

    def test_the_path_of_a_command_com_is_known_wherever_it_is(self):
        for parts in (["COMMAND.COM"], ["DOS", "Command.Com"], ["a", "b", "command.com"]):
            self.assertTrue(command.is_command_com(parts), parts)
        for parts in ([], ["COMMAND.COM", "X"], ["COMMAND.EXE"], ["MYCOMMAND.COM"], ["COMMAND"]):
            self.assertFalse(command.is_command_com(parts), parts)


if __name__ == "__main__":
    unittest.main()
