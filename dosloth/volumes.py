"""The drives as a program uses them: which file system is behind a letter, which directory is current on each.

DOS keeps a current directory for every drive, and one drive is the current one. A path from a program can
name a drive (`D:\\GAMES\\RUN.EXE`), start at the root (`\\GAMES`) or at the current directory (`GAMES`), and
use `.` and `..`. resolve() turns such a path into the file system it lives on and the plain list of names
from that file system's root, which is what filesystem.py asks for.
"""
import re
from dataclasses import dataclass

from .filesystem import DRIVE_NOT_READY, INVALID_DRIVE, PATH_NOT_FOUND, FsError, for_mount


@dataclass(frozen=True)
class Location:
    """A place on a drive: the drive, its file system, and the names from the root of it to the place."""
    drive: int
    filesystem: object
    parts: list

    def dos_path(self):
        """The full name, the way DOS writes it: `C:\\SETUP\\README.TXT`."""
        return chr(ord("A") + self.drive) + ":\\" + "\\".join(part.upper() for part in self.parts)


class Volumes:
    """The drives of one machine, made from its configuration.

    A drive with something mounted gets a file system of its own (a tmpfs starts empty, and is lost when the
    emulator ends). A drive with nothing in it has none, and a path on it is an error: the drive is not ready.
    """

    def __init__(self, config):
        self.config = config
        self.filesystems = {number: for_mount(disk.mount) if disk.mount is not None else None
                            for number, disk in config.disks.items()}
        self.current_drive = config.cwd_drive
        self.directories = {number: [] for number in config.disks}  # the current directory of each drive, as names
        self.directories[config.cwd_drive] = [name for name in config.cwd.split("\\") if name]

    def resolve(self, text):
        """The Location a path names. `text` is a path as a program gives it, with `\\` or `/`.

        An FsError says what is wrong: the drive is not there (15), has nothing in it (21), or the path goes
        above the root of the drive (3). Whether the place exists is for the file system to say.
        """
        if not text:
            raise FsError(PATH_NOT_FOUND, "an empty path")
        drive = self.current_drive
        match = re.match(r"([A-Za-z]):", text)
        if match:
            drive = ord(match.group(1).upper()) - ord("A")
            text = text[2:]
        if drive not in self.config.disks:
            raise FsError(INVALID_DRIVE, f"there is no drive {chr(ord('A') + drive)}:")
        filesystem = self.filesystems[drive]
        if filesystem is None:
            raise FsError(DRIVE_NOT_READY, f"the drive {chr(ord('A') + drive)}: has nothing in it")
        parts = [] if text[:1] in ("\\", "/") else list(self.directories[drive])
        for name in re.split(r"[\\/]", text):
            if name in ("", "."):
                continue
            if name == "..":
                if not parts:
                    raise FsError(PATH_NOT_FOUND, "the path goes above the root of the drive")
                parts.pop()
            else:
                parts.append(name)
        return Location(drive, filesystem, parts)

    def chdir(self, text):
        """Make a directory the current one of its drive. It has to be there. The current drive does not change."""
        location = self.resolve(text)
        try:
            is_dir = location.filesystem.stat(location.parts).is_dir
        except FsError as error:  # DOS says "path not found" for a directory that is not there, not "file not found"
            raise FsError(PATH_NOT_FOUND, str(error)) from None
        if not is_dir:
            raise FsError(PATH_NOT_FOUND, f"{text!r} is not a directory")
        self.directories[location.drive] = [name.upper() for name in location.parts]

    def current_directory(self, drive=None):
        """The current directory of a drive (default: the current drive), as INT 21h AH=47h gives it: no drive
        letter and no backslash in front, so `GAMES\\SAVES`, and an empty string for the root."""
        return "\\".join(self.directories[self.current_drive if drive is None else drive])
