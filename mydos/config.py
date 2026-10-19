"""The configuration: which drives the emulated machine has, what is in them, and where a program starts.

It is a TOML file. A drive and the medium in it are described apart, as they are in life:

    cwd = 'C:\\SETUP'                      # where the program starts: a drive and a directory inside DOS

    [disks.C]                             # the drive: what kind of device it is
    removable = false
    [disks.A]
    removable = true                      # a floppy drive; nothing is mounted in it: no disk inserted

    [mounts]                              # the medium: what is in the drive
    C = { type = "dir", path = "." }      # a directory of the host (relative paths: from the config's directory)
    B = { type = "tmpfs" }                # a file system in memory, gone when the emulator exits
    D = { type = "dir", path = "/mnt/cd", readonly = true }

Every mistake in the file is a ConfigError with a message that says where it is, so a bad config stops the
emulator before any program runs.
"""
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

TOP_LEVEL_KEYS = {"cwd", "disks", "mounts"}
DISK_KEYS = {"removable"}
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
    """A drive. `mount` is None when there is no medium in it."""
    letter: str
    removable: bool
    mount: Mount | None


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


def find_ignoring_case(directory, name):
    """The entry of a host directory called `name`, whatever the case, or None.

    DOS does not tell ABC from abc, and Linux does. An exact match wins; failing that, the first of the
    entries that differ only in case, in alphabetical order.
    """
    exact = directory / name
    if exact.exists():
        return exact
    wanted = name.upper()
    for entry in sorted(os.listdir(directory)):
        if entry.upper() == wanted:
            return directory / entry
    return None


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
    removable = {}
    for key, table in disks.items():
        where = f"disks.{key}"
        letter = _letter(key, where)
        if letter in removable:
            raise ConfigError(f"{where}: the drive {letter}: is described twice")
        table = _table(table, where)
        _check_keys(table, DISK_KEYS, where)
        if "removable" not in table:
            raise ConfigError(f"{where}: 'removable' is missing (true for a floppy or a CD-ROM, false for a hard disk)")
        if not isinstance(table["removable"], bool):
            raise ConfigError(f"{where}.removable must be true or false")
        removable[letter] = table["removable"]
    return removable


def _parse_mount(letter, table, base):
    where = f"mounts.{letter}"
    table = _table(table, where)
    _check_keys(table, MOUNT_KEYS, where)
    kind = table.get("type")
    if kind not in MOUNT_TYPES:
        raise ConfigError(f"{where}.type must be one of {', '.join(MOUNT_TYPES)}")
    readonly = table.get("readonly", False)
    if not isinstance(readonly, bool):
        raise ConfigError(f"{where}.readonly must be true or false")
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


def _parse_mounts(data, removable, base):
    mounts = _table(data["mounts"], "mounts") if "mounts" in data else {}
    result = {}
    for key, table in mounts.items():
        letter = _letter(key, f"mounts.{key}")
        if letter not in removable:
            raise ConfigError(f"mounts.{key}: there is no drive {letter}: in [disks]")
        if letter in result:
            raise ConfigError(f"mounts.{key}: the drive {letter}: is mounted twice")
        result[letter] = _parse_mount(letter, table, base)
    for letter, is_removable in removable.items():
        if letter not in result and not is_removable:
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
    shown = []
    here = mount.path
    for part in parts:
        found = find_ignoring_case(here, part) if here is not None else None
        if found is None or not found.is_dir():
            raise ConfigError(f"cwd: there is no directory {part!r} on {letter}: in {text!r}")
        here = found
        shown.append(part.upper())
    return ord(letter) - ord("A"), "\\" + "\\".join(shown)


def parse(data, base):
    """Make a Config from what tomllib read. Relative paths are taken from the directory `base`."""
    _check_keys(data, TOP_LEVEL_KEYS, "the file")
    removable = _parse_disks(data)
    mounts = _parse_mounts(data, removable, base)
    disks = {ord(letter) - ord("A"): Disk(letter, is_removable, mounts.get(letter))
             for letter, is_removable in removable.items()}
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
        "disks": {"A": {"removable": True}, "B": {"removable": True},
                  "C": {"removable": False}, "D": {"removable": True}},
        "mounts": {"C": {"type": "dir", "path": "."}},
        "cwd": "C:\\",
    }, Path(base))
