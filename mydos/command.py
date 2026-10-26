"""A pocket COMMAND.COM: the few commands of the DOS command interpreter that a program starts it for.

A C program that wants DOS to do something, `system("md C:\\GAME\\DATA")`, starts COMMAND.COM with the switch /C and
the command line, waits for it, and goes on. There is no COMMAND.COM on our disks, and nothing in the machine that
a real one could be of use for, so when a program starts one that is not there (see process.start), the command is
done here, in Python, and the program is told that its child has ended.

Only what a program has been seen to ask for is here. So far that is MD (MKDIR), as `/c md C:\\XCOMA\\tacexe`.
Anything else stops the emulator with a message that says what it was: a command that is not known, a switch other
than /C (a shell that stays, for a person to type in), a line with redirection or a pipe. A command is added when
a program calls for it, as everything else in this machine is.

A command that fails does what the real one does: it says why on the console, in the words of MS-DOS 6, and does
not change the exit code, which is 0, as it is for every internal command (a failing one leaves ERRORLEVEL alone).
"""
import sys

from .filesystem import INVALID_DRIVE, FsError

PROGRAM = "COMMAND.COM"
NOT_ALLOWED = "<>|&"  # redirection and pipes, and a second command: they are for a real shell


def is_command_com(parts):
    """Is this the path (a list of names) of a COMMAND.COM, wherever it is."""
    return bool(parts) and parts[-1].upper() == PROGRAM


def console(text):
    """Show text on the console, which is our stdout, as DOS does for a program that prints."""
    sys.stdout.write(text)
    sys.stdout.flush()


class Shell:
    """The interpreter. `volumes` are the drives it works on; `write` shows a text (default: on our stdout)."""

    def __init__(self, volumes, write=console):
        self.volumes = volumes
        self.write = write
        self.commands = {"MD": self.md, "MKDIR": self.md}

    def say(self, message):
        self.write(message + "\r\n")

    def run(self, line):
        """Do what `COMMAND.COM <line>` does, and return its exit code. `line` is the command tail of the program:
        `/c md C:\\X`, with or without a blank in front."""
        words = line.split()
        if not words or words[0].lower() != "/c":
            raise NotImplementedError(f"COMMAND.COM {line!r}: only /C and a command are done, not a shell that stays")
        command = line.split(None, 1)[1] if len(words) > 1 else ""
        if any(sign in command for sign in NOT_ALLOWED):
            raise NotImplementedError(f"COMMAND.COM /C {command!r}: redirection and pipes are not done")
        if command:
            name, *arguments = command.split()
            handler = self.commands.get(name.upper())
            if handler is None:
                raise NotImplementedError(f"COMMAND.COM /C {command!r}: the command {name.upper()} is not done")
            handler(arguments)
        return 0

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
