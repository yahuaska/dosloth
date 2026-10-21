"""The configuration: which drives the emulated machine has, what is in them, and where a program starts.

It is a TOML file. A drive and the medium in it are described apart, as they are in life:

    cwd = 'C:\\SETUP'                      # where the program starts: a drive and a directory inside DOS

    [disks.C]                             # the drive: what kind of device it is: "floppy", "hdd" or "cdrom"
    type = "hdd"
    [disks.A]
    type = "floppy"                       # a floppy drive; nothing is mounted in it: no disk inserted
    [disks.D]
    type = "cdrom"                        # a CD-ROM drive: what is mounted in it cannot be written to

    [mounts]                              # the medium: what is in the drive
    C = { type = "dir", path = "." }      # a directory of the host (relative paths: from the config's directory)
    B = { type = "tmpfs" }                # a file system in memory, gone when the emulator exits
    D = { type = "dir", path = "/mnt/cd" }

`removable = true` (a floppy) and `removable = false` (a hard disk) are the old way to say the same as `type`; a drive
has one of the two, not both.

Every mistake in the file is a ConfigError with a message that says where it is, so a bad config stops the
emulator before any program runs.
"""
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .filesystem import FsError, for_mount

TOP_LEVEL_KEYS = {"cwd", "disks", "mounts"}
DISK_KEYS = {"type", "removable"}
DISK_TYPES = ("floppy", "hdd", "cdrom")
MOUNT_KEYS = {"type", "path", "readonly"}
MOUNT_TYPES = ("dir", "tmpfs")


class ConfigError(Exception):
    """The configuration is wrong. The message says what and where."""


@dataclass(frozen=True)
class Mount:
    """What is in a drive: a host directory (type "dir", with its path) or memory (type "tmpfs")."""
    type: str
    path: Path | None  # the directory of the host, absolute; None for tmpfs
    readonly: bool


@dataclass(frozen=True)
class Disk:
    """A drive: "floppy", "hdd" or "cdrom". `mount` is None when there is no medium in it."""
    letter: str
    type: str
    mount: Mount | None

    @property
    def removable(self):
        """Can the medium be taken out? True for a floppy and a CD-ROM."""
        return self.type != "hdd"


@dataclass(frozen=True)
class Config:
    """The whole machine: its drives by number (0 is A:), and the drive and directory a program starts in."""
    disks: dict
    cwd_drive: int
    cwd: str  # the directory on that drive, as DOS writes it: "\\" for the root, "\\SETUP\\SUB" below it

    @property
    def drive_count(self):
        """What INT 21h AH=0Eh answers: one more than the highest drive number, as there can be gaps."""
        return max(self.disks) + 1


def _table(value, where):
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a table, not {type(value).__name__}")
    return value


def _check_keys(table, allowed, where):
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key {unknown[0]!r} (the keys are {', '.join(sorted(allowed))})")


def _letter(key, where):
    """A drive letter from a key of the file: one letter A-Z in either case. Returns it in capitals."""
    if len(key) != 1 or key.upper() not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        raise ConfigError(f"{where}: {key!r} is not a drive letter (one letter, A to Z)")
    return key.upper()


def _parse_disks(data):
    disks = _table(data.get("disks"), "disks") if "disks" in data else {}
    if not disks:
        raise ConfigError("disks: there must be at least one drive")
    types = {}
    for key, table in disks.items():
        where = f"disks.{key}"
        letter = _letter(key, where)
        if letter in types:
            raise ConfigError(f"{where}: the drive {letter}: is described twice")
        table = _table(table, where)
        _check_keys(table, DISK_KEYS, where)
        if "type" in table and "removable" in table:
            raise ConfigError(f"{where}: 'type' and 'removable' say the same thing, give only 'type'")
        if "type" in table:
            if table["type"] not in DISK_TYPES:
                raise ConfigError(f"{where}.type must be one of {', '.join(DISK_TYPES)}")
            types[letter] = table["type"]
        elif "removable" in table:
            if not isinstance(table["removable"], bool):
                raise ConfigError(f"{where}.removable must be true or false")
            types[letter] = "floppy" if table["removable"] else "hdd"
        else:
            raise ConfigError(f"{where}: 'type' is missing (floppy, hdd or cdrom)")
    return types


def _parse_mount(letter, table, base, disk_type):
    where = f"mounts.{letter}"
    table = _table(table, where)
    _check_keys(table, MOUNT_KEYS, where)
    kind = table.get("type")
    if kind not in MOUNT_TYPES:
        raise ConfigError(f"{where}.type must be one of {', '.join(MOUNT_TYPES)}")
    cdrom = disk_type == "cdrom"
    readonly = table.get("readonly", cdrom)  # nothing can be written to a CD-ROM
    if not isinstance(readonly, bool):
        raise ConfigError(f"{where}.readonly must be true or false")
    if cdrom and not readonly:
        raise ConfigError(f"{where}.readonly: a CD-ROM cannot be written to")
    if cdrom and kind == "tmpfs":
        raise ConfigError(f"{where}: a CD-ROM holds a directory of the host, not a file system in memory")
    if kind == "tmpfs":
        if "path" in table:
            raise ConfigError(f"{where}: a tmpfs is in memory and has no path")
        return Mount("tmpfs", None, readonly)
    if "path" not in table:
        raise ConfigError(f"{where}: a directory mount needs a 'path'")
    if not isinstance(table["path"], str) or not table["path"]:
        raise ConfigError(f"{where}.path must be the name of a directory")
    path = (base / table["path"]).resolve()  # an absolute path stays as it is
    if not path.is_dir():
        raise ConfigError(f"{where}.path: {path} is not a directory")
    return Mount("dir", path, readonly)


def _parse_mounts(data, types, base):
    mounts = _table(data["mounts"], "mounts") if "mounts" in data else {}
    result = {}
    for key, table in mounts.items():
        letter = _letter(key, f"mounts.{key}")
        if letter not in types:
            raise ConfigError(f"mounts.{key}: there is no drive {letter}: in [disks]")
        if letter in result:
            raise ConfigError(f"mounts.{key}: the drive {letter}: is mounted twice")
        result[letter] = _parse_mount(letter, table, base, types[letter])
    for letter, disk_type in types.items():
        if letter not in result and disk_type == "hdd":
            raise ConfigError(f"mounts.{letter}: the drive {letter}: is not removable, so it needs something mounted")
    return result


def _parse_cwd(text, disks):
    """Check the starting directory, `C:\\SETUP` and the like. Returns (drive number, normalized directory)."""
    match = re.fullmatch(r"([A-Za-z]):([\\/].*)?", text)
    if not match:
        raise ConfigError(f"cwd: {text!r} is not a drive and a directory, like 'C:\\SETUP'")
    letter = match.group(1).upper()
    parts = [part for part in re.split(r"[\\/]", match.group(2) or "") if part]
    if letter not in disks:
        raise ConfigError(f"cwd: there is no drive {letter}:")
    mount = disks[letter].mount
    if mount is None:
        raise ConfigError(f"cwd: the drive {letter}: has nothing mounted, a program cannot start there")
    if any(part in (".", "..") for part in parts):
        raise ConfigError(f"cwd: {text!r} must name the directory itself, without . or ..")
    try:
        is_dir = for_mount(mount).stat(parts).is_dir  # the same lookup the programs will get: without regard to case
    except FsError:
        is_dir = False
    if not is_dir:
        raise ConfigError(f"cwd: there is no directory {text!r} on {letter}:")
    shown = [part.upper() for part in parts]
    return ord(letter) - ord("A"), "\\" + "\\".join(shown)


def parse(data, base):
    """Make a Config from what tomllib read. Relative paths are taken from the directory `base`."""
    _check_keys(data, TOP_LEVEL_KEYS, "the file")
    types = _parse_disks(data)
    mounts = _parse_mounts(data, types, base)
    disks = {ord(letter) - ord("A"): Disk(letter, disk_type, mounts.get(letter))
             for letter, disk_type in types.items()}
    by_letter = {disk.letter: disk for disk in disks.values()}
    if "cwd" in data:
        if not isinstance(data["cwd"], str):
            raise ConfigError("cwd must be a string, like 'C:\\SETUP'")
        drive, directory = _parse_cwd(data["cwd"], by_letter)
    else:  # C:, if it has a medium, else the first drive that has one
        mounted = [number for number in sorted(disks) if disks[number].mount is not None]
        if not mounted:
            raise ConfigError("mounts: no drive has anything mounted, so there is nowhere to start")
        drive, directory = (2 if 2 in mounted else mounted[0]), "\\"
    return Config(disks, drive, directory)


def load(path):
    """Read a configuration file. Relative paths inside it are taken from the directory the file is in."""
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read the configuration {path}: {error.strerror}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from None
    try:
        return parse(data, path.resolve().parent)
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from None


def default(base):
    """The machine used when no configuration is given: floppies A: and B:, the hard disk C: with the
    directory `base` in it, the CD-ROM D:, and a start in the root of C:. The drives with no mounts are empty."""
    return parse({
        "disks": {"A": {"type": "floppy"}, "B": {"type": "floppy"}, "C": {"type": "hdd"}, "D": {"type": "cdrom"}},
        "mounts": {"C": {"type": "dir", "path": "."}},
        "cwd": "C:\\",
    }, Path(base))
