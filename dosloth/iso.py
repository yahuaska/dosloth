"""A CD-ROM image, an `.iso` file, as the disc in a drive: its files, and its sectors.

An ISO 9660 disc is 2048-byte sectors. The first 16 are the system area, which belongs to no file; then come the
volume descriptors (the primary one, at sector 16, says where the root directory is), and the directories and files,
each in sectors of its own. IsoImage knows the sectors and where the root is; IsoFs is the view of it that DOS has,
the same as a host directory has (see filesystem.py): the names a program sees, its files to read. A program that
reads sectors (MSCDEX, INT 2Fh AH=15h) asks the image itself.

What is read: the primary volume descriptor, with names as DOS knows them (`SETUP.EXE;1` is SETUP.EXE, a name that
DOS cannot have is not there) and no Rock Ridge or Joliet extensions. What is not: images of other kinds
(raw sectors of 2352 bytes, `.bin` and `.cue`), and files in many pieces.

A disc cannot be written to: every change is an ACCESS_DENIED.
"""
import calendar
import os
import struct

from .filesystem import (ACCESS_DENIED, ATTR_DIRECTORY, ATTR_HIDDEN, ATTR_READONLY, FILE_NOT_FOUND, PATH_NOT_FOUND,
                         SECTOR_NOT_FOUND, Entry, FsError, is_dos_name)

SECTOR = 2048
FIRST_DESCRIPTOR = 16  # the sectors before it are the system area
PRIMARY, TERMINATOR = 1, 255  # the types of volume descriptors
MAX_DESCRIPTORS = 32  # how far to look for the primary one
DIRECTORY, HIDDEN = 0x02, 0x01  # bits of the flags of a directory record: it is a directory; it is not to be shown


class IsoError(Exception):
    """The file is not an image this module can read. The message says why."""


class Record:
    """What a directory record says about a file or a directory."""

    def __init__(self, name, is_dir, extent, size, mtime, hidden):
        self.name, self.is_dir, self.hidden = name, is_dir, hidden
        self.extent, self.size, self.mtime = extent, size, mtime

    def entry(self):
        """The Entry a file system gives for it: files of a disc are read-only."""
        attributes = ATTR_DIRECTORY if self.is_dir else ATTR_READONLY
        if self.hidden:
            attributes |= ATTR_HIDDEN
        return Entry(self.name, self.is_dir, 0 if self.is_dir else self.size, self.mtime, attributes)


def dos_name(raw):
    """The name DOS shows for the name of a directory record, or None if DOS cannot have it.

    A file is recorded as `NAME.EXT;1` (the number is the version), and one with no extension as `NAME.`
    """
    name = raw.decode("ascii", "replace").upper().split(";")[0]
    if name.endswith("."):
        name = name[:-1]
    return name if is_dos_name(name) else None


def time_of(date):
    """The time of a 7-byte date of a directory record, in seconds since 1970; 0 if it makes no sense."""
    year, month, day, hour, minute, second, zone = struct.unpack("<6Bb", date)
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 61):
        return 0.0
    try:
        return float(calendar.timegm((1900 + year, month, day, hour, minute, min(second, 59))) - zone * 900)
    except (ValueError, OverflowError):
        return 0.0


def record_at(data, at):
    """The Record in the bytes `data` at `at`, or None for a name nobody can see. Its length is data[at]."""
    extent = int.from_bytes(data[at + 2:at + 6], "little")
    size = int.from_bytes(data[at + 10:at + 14], "little")
    flags, name_length = data[at + 25], data[at + 32]
    raw = data[at + 33:at + 33 + name_length]
    if raw in (b"\0", b"\1"):  # this directory and its parent
        return None
    name = dos_name(raw)
    if name is None:
        return None
    return Record(name, bool(flags & DIRECTORY), extent, size, time_of(data[at + 18:at + 25]), bool(flags & HIDDEN))


class IsoImage:
    """The file of a disc: it reads sectors, and knows where the root directory is.

    The file is opened for each read and closed again, so that an image that is not used holds nothing.
    """

    def __init__(self, path):
        self.path = os.fspath(path)
        try:
            self.sectors = os.path.getsize(self.path) // SECTOR
            descriptor = self.primary_descriptor()
        except OSError as error:
            raise IsoError(f"{self.path} cannot be read: {error.strerror or error}") from None
        if int.from_bytes(descriptor[128:130], "little") != SECTOR:
            raise IsoError(f"{self.path} has blocks that are not {SECTOR} bytes long: not supported")
        # the directory record of the root is in the descriptor, 34 bytes from the 156th
        root_extent = int.from_bytes(descriptor[158:162], "little")
        root_size = int.from_bytes(descriptor[166:170], "little")
        self.root = Record("", True, root_extent, root_size, time_of(descriptor[174:181]), False)
        self.directories = {}  # extent -> the records of the directory there (a disc does not change)

    def read(self, offset, size):
        with open(self.path, "rb") as file:
            file.seek(offset)
            return file.read(size)

    def primary_descriptor(self):
        """The 2048 bytes of the primary volume descriptor. Raises IsoError if the file has none."""
        for number in range(FIRST_DESCRIPTOR, FIRST_DESCRIPTOR + MAX_DESCRIPTORS):
            descriptor = self.read(number * SECTOR, SECTOR)
            if len(descriptor) < SECTOR or descriptor[1:6] != b"CD001":
                break
            if descriptor[0] == PRIMARY:
                return descriptor
            if descriptor[0] == TERMINATOR:
                break
        raise IsoError(f"{self.path} is not an ISO 9660 image of 2048-byte sectors: it has no volume descriptor")

    def read_sectors(self, first, count):
        """`count` sectors from number `first`, as bytes. FsError (27, sector not found) past the end of the disc."""
        if first < 0 or first + count > self.sectors:
            raise FsError(SECTOR_NOT_FOUND, f"the disc has {self.sectors} sectors, not {first + count}")
        return self.read(first * SECTOR, count * SECTOR)

    def records(self, directory):
        """The Records of a directory (a Record): the names DOS can have, in the order of the disc."""
        if directory.extent not in self.directories:
            data = self.read(directory.extent * SECTOR, directory.size)
            found, at = [], 0
            while at < len(data):
                if data[at] == 0:  # no record crosses a sector: the rest of this one is empty
                    at = (at // SECTOR + 1) * SECTOR
                    continue
                if at + 33 > len(data) or at + data[at] > len(data):
                    break
                record = record_at(data, at)
                if record is not None and all(record.name != other.name for other in found):
                    found.append(record)
                at += data[at]
            self.directories[directory.extent] = found
        return self.directories[directory.extent]


class IsoFile:
    """An open file of a disc: it reads from its own position, as a Python file does."""

    def __init__(self, image, record):
        self.image, self.record = image, record
        self.position = 0
        self.closed = False

    def read(self, size=-1):
        left = max(self.record.size - self.position, 0)
        count = left if size is None or size < 0 else min(size, left)
        if count == 0:
            return b""
        data = self.image.read(self.record.extent * SECTOR + self.position, count)
        self.position += len(data)
        return data

    def seek(self, offset, whence=0):
        base = (0, self.position, self.record.size)[whence]
        if base + offset < 0:
            raise ValueError("negative seek position")
        self.position = base + offset
        return self.position

    def tell(self):
        return self.position

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()


class IsoFs:
    """The files of a disc, as DOS asks about them (see filesystem.py): the same questions as of a host directory."""

    readonly = True

    def __init__(self, image):
        self.image = image

    def _find(self, parts):
        """The Record of what `parts` names. The root is a Record with no name."""
        here = self.image.root
        for i, name in enumerate(parts):
            last = i == len(parts) - 1
            if not here.is_dir:
                raise FsError(PATH_NOT_FOUND, f"{parts[i - 1]!r} is not a directory")
            wanted = name.upper()
            found = [record for record in self.image.records(here) if record.name == wanted]
            if not found:
                raise FsError(FILE_NOT_FOUND if last else PATH_NOT_FOUND, f"{name!r} is not there")
            here = found[0]
        return here

    def stat(self, parts):
        record = self._find(parts)
        return Entry("", True, 0, record.mtime, ATTR_DIRECTORY) if not parts else record.entry()

    def listdir(self, parts):
        try:
            record = self._find(parts)
        except FsError as error:
            raise FsError(PATH_NOT_FOUND, str(error)) from None
        if not record.is_dir:
            raise FsError(PATH_NOT_FOUND, f"{parts[-1]!r} is not a directory")
        return sorted((entry.entry() for entry in self.image.records(record)), key=lambda entry: entry.name)

    def open(self, parts, write=False):
        record = self._find(parts)
        if record.is_dir:
            raise FsError(ACCESS_DENIED, f"{parts[-1] if parts else 'the root'!r} is a directory")
        if write:
            raise FsError(ACCESS_DENIED, "a disc cannot be written to")
        return IsoFile(self.image, record)

    def read_sectors(self, first, count):
        return self.image.read_sectors(first, count)

    def _refuse(self, *arguments):
        raise FsError(ACCESS_DENIED, "a disc cannot be written to")

    create = mkdir = rmdir = remove = rename = set_attributes = _refuse

    def free_space(self):
        return self.image.sectors * SECTOR, 0
