"""A pocket COMMAND.COM: the few things of the DOS command interpreter that a program starts it for.

A C program that wants DOS to do something, `system("md C:\\GAME\\DATA")`, starts COMMAND.COM with the switch /C and
the command line, waits for it, and goes on. There is no COMMAND.COM on our disks, and nothing in the machine that
a real one could be of use for, so when a program starts one that is not there (see process.start), the command is
done here, in Python, and the program is told that its child has ended.

Two kinds of command are done:
    MD (MKDIR), an internal command of the interpreter, as `/c md C:\\XCOMA\\tacexe`;
    a program, as `/c SETUP.exe child>nul`: the interpreter looks for it where DOS does (the current directory, then
    the directories of PATH; `.COM` before `.EXE`), and what it gives back is a Launch, which process.start carries
    out: the program is started as a child of the one that asked, with the text after its name as its command line,
    and with its output thrown away if the line ends with `>nul`.

Anything else stops the emulator with a message that says what it was: a command that is not known, a switch other
than /C (a shell that stays, for a person to type in), a redirection to a file, `<`, a pipe, a batch file. A
command is added when a program calls for it, as everything else in this machine is.

A command that fails does what the real one does: it says why on the console, in the words of MS-DOS 6, and does
not change the exit code, which is 0, as it is for every internal command (a failing one leaves ERRORLEVEL alone).
"""
import re
import sys
from dataclasses import dataclass

from .filesystem import INVALID_DRIVE, FsError

PROGRAM = "COMMAND.COM"
NOT_ALLOWED = "<>|&"  # redirection and pipes, and a second command: they are for a real shell
NUL_OUTPUT = re.compile(r">\s*nul\s*$", re.IGNORECASE)  # the one redirection that is done: output to the NUL device
PROGRAM_EXTENSIONS = ("COM", "EXE", "BAT")  # what DOS tries, in this order, for a name with no extension
# the other commands that the interpreter has in itself, and that are not done yet: a program is not looked for by
# these names (DOS does not either), and the emulator stops, so that nothing goes unnoticed
INTERNAL = frozenset("""BREAK CALL CD CHCP CHDIR CLS COPY CTTY DATE DEL DIR ECHO ERASE EXIT FOR GOTO IF LH LOADHIGH PATH
                        PAUSE PROMPT RD REM REN RENAME RMDIR SET SHIFT TIME TRUENAME TYPE VER VERIFY VOL""".split())


def is_command_com(parts):
    """Is this the path (a list of names) of a COMMAND.COM, wherever it is."""
    return bool(parts) and parts[-1].upper() == PROGRAM


@dataclass(frozen=True)
class Launch:
    """What running a program comes to: the file (a volumes.Location), the text after its name, which is its command
    line, and whether its output goes to the NUL device. The one that asked for it starts the program."""
    where: object
    tail: str
    null_output: bool


def console(text):
    """Show text on the console, which is our stdout, as DOS does for a program that prints."""
    sys.stdout.write(text)
    sys.stdout.flush()


class Shell:
    """The interpreter. `volumes` are the drives it works on; `write` shows a text (default: on our stdout)."""

    def __init__(self, volumes, write=console, search_path=()):
        self.volumes = volumes
        self.write = write
        self.search_path = list(search_path)  # the directories of PATH, as DOS paths, where a program is looked for
        self.commands = {"MD": self.md, "MKDIR": self.md}

    def say(self, message):
        self.write(message + "\r\n")

    def run(self, line):
        """Do what `COMMAND.COM <line>` does. `line` is the command tail of the program: `/c md C:\\X`, with or
        without a blank in front. Returns the exit code, 0, of a command that is done here; or, for a program, the
        Launch that the caller has to carry out."""
        words = line.split()
        if not words or words[0].lower() != "/c":
            raise NotImplementedError(f"COMMAND.COM {line!r}: only /C and a command are done, not a shell that stays")
        command = line.split(None, 1)[1] if len(words) > 1 else ""
        null_output = bool(NUL_OUTPUT.search(command))
        command = NUL_OUTPUT.sub("", command)
        if any(sign in command for sign in NOT_ALLOWED):
            raise NotImplementedError(f"COMMAND.COM /C {line.split(None, 1)[1]!r}: only the output to NUL is done, "
                                      "no other redirection, no pipes and no second command")
        if not command.strip():
            return 0
        name = command.split()[0]
        handler = self.commands.get(name.upper())
        if handler is not None:
            said, self.write = self.write, (lambda text: None) if null_output else self.write  # >nul: no words
            try:
                handler(command.split()[1:])
            finally:
                self.write = said
            return 0
        if name.upper() in INTERNAL:
            raise NotImplementedError(f"COMMAND.COM /C {command!r}: the command {name.upper()} is not done")
        where = self.find_program(name)
        if where is None:
            self.say("Bad command or file name")
            return 0
        return Launch(where, command[command.index(name) + len(name):].rstrip(), null_output)

    def find_program(self, name):
        """The Location of the program a command word names, or None. A word with a drive or a directory in it is
        taken as it is; a plain name is looked for in the current directory and then in those of PATH. Without an
        extension DOS tries .COM, .EXE and .BAT, in this order, in each directory."""
        component = re.split(r"[\\/:]", name)[-1]
        extension = component.rpartition(".")[2].upper() if "." in component else None
        if extension is not None and extension not in PROGRAM_EXTENSIONS:
            return None
        directories = [""] if re.search(r"[\\/:]", name) else [""] + self.search_path
        for directory in directories:
            for suffix in ([""] if extension else [f".{each}" for each in PROGRAM_EXTENSIONS]):
                path = (directory + "\\" if directory else "") + name + suffix
                try:
                    where = self.volumes.resolve(path)
                    if where.filesystem.stat(where.parts).is_dir:
                        continue
                except FsError:
                    continue
                if path.upper().endswith(".BAT"):
                    raise NotImplementedError(f"COMMAND.COM /C {name}: batch files are not done")
                return where
        return None

    def md(self, arguments):
        """MD path: make a directory. As in MS-DOS 5 and later, the directories on the way that are not there are
        made too (`md A\\B\\C` makes A, then A\\B, then A\\B\\C)."""
        if not arguments:
            return self.say("Required parameter missing")
        if len(arguments) > 1:
            return self.say(f"Too many parameters - {arguments[1]}")
        path = arguments[0]
        try:
            where = self.volumes.resolve(path)
        except FsError as error:
            return self.say("Invalid drive specification" if error.code == INVALID_DRIVE
                            else "Unable to create directory")
        if not where.parts:  # the root: it is there
            return self.say("Unable to create directory")
        for depth in range(1, len(where.parts) + 1):
            names = where.parts[:depth]
            try:
                entry = where.filesystem.stat(names)
            except FsError:
                entry = None
            if entry is None:
                try:
                    where.filesystem.mkdir(names)
                except FsError:
                    return self.say("Unable to create directory")
            elif depth == len(where.parts):
                return self.say(f"A subdirectory or file {path} already exists")
