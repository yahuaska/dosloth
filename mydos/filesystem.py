"""The file systems a drive can have: a directory of the host (HostDir) or a tree kept in memory (MemFs).

The DOS functions never touch the host directly. They ask a file system, and both kinds answer the same
questions in the same way, so a program cannot tell them apart:

    stat(parts)          what is at this path: an Entry, or FsError
    listdir(parts)       the entries of a directory
    open(parts, write)   an existing file, as a file object
    create(parts)        a new file, or an old one emptied, as a file object
    mkdir, rmdir, remove, rename
    set_attributes(parts, bits)   change the read-only bit of a file (the other bits are accepted and ignored)
    free_space()         (the size of the disk, the free space) in bytes

A path is a list of names, the way a DOS path splits at the backslashes: ["SETUP", "README.TXT"]. The empty
list is the root. The caller has already dealt with `.` and `..` and with the current directory, so no name
here is more than a plain name. Names are matched without regard to case, and they are what DOS can have:
at most 8 letters, a dot, at most 3 more (is_dos_name). A host file whose name does not fit is invisible. A new
file or directory gets its name in capitals, as DOS stores it.

A file object has read(), write(), seek(), tell(), truncate() and close(), as a Python file has.

A mistake is an FsError that carries the number DOS would answer with, so the DOS layer can pass it on.
"""
import io
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path

# what DOS answers when something goes wrong
FILE_NOT_FOUND = 2
PATH_NOT_FOUND = 3
ACCESS_DENIED = 5
INVALID_DRIVE = 15
DRIVE_NOT_READY = 21

# the bits of a DOS file's attributes
ATTR_READONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_VOLUME = 0x08
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20

_CHARACTER = r"[A-Za-z0-9!#$%&'()\-@^_`{}~]"
_DOS_NAME = re.compile(rf"{_CHARACTER}{{1,8}}(\.{_CHARACTER}{{1,3}})?")
MEMFS_SIZE = 32 * 1024 * 1024  # the size of the disk a MemFs pretends to be


def is_dos_name(name):
    """Can DOS have a file of this name? Letters, digits and a few signs: 8 characters, then maybe a dot and 3."""
    return _DOS_NAME.fullmatch(name) is not None


class FsError(Exception):
    """A file operation failed. `code` is the error number DOS gives for it."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Entry:
    """What a file system knows about a file or a directory."""
    name: str  # in capitals; empty for the root
    is_dir: bool
    size: int  # in bytes; 0 for a directory
    mtime: float  # the time of the last change, as time.time() gives it
    attributes: int  # ATTR_ bits


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


def _check_settable(attributes):
    """DOS lets a program change the attributes of a file, but not turn a file into a directory or a label."""
    if attributes & (ATTR_DIRECTORY | ATTR_VOLUME):
        raise FsError(ACCESS_DENIED, "the directory and volume label bits cannot be set")


def _parent_and_name(parts):
    """Split a path into the directory it is in and its own name; the root has neither, and cannot be a target."""
    if not parts:
        raise FsError(ACCESS_DENIED, "the root directory cannot be changed this way")
    return parts[:-1], parts[-1]


def _check_name(name):
    if not is_dos_name(name):
        raise FsError(PATH_NOT_FOUND, f"{name!r} is not a name DOS can have")


class HostDir:
    """A directory of the host, seen as the root of a drive. `readonly` makes every change an error."""

    def __init__(self, root, readonly=False):
        self.root = Path(root)
        self.readonly = readonly

    # -- finding things --

    def _child(self, directory, name):
        """The host path of the entry `name` in `directory` as DOS sees it, or None."""
        if not is_dos_name(name):
            return None
        return find_ignoring_case(directory, name)

    def _find(self, parts):
        """The host path of what `parts` names. Raises PATH_NOT_FOUND if a directory on the way is missing,
        FILE_NOT_FOUND if only the last name is."""
        here = self.root
        for i, name in enumerate(parts):
            last = i == len(parts) - 1
            found = self._child(here, name)
            if found is None:
                raise FsError(FILE_NOT_FOUND if last else PATH_NOT_FOUND, f"{name!r} is not there")
            if not last and not found.is_dir():
                raise FsError(PATH_NOT_FOUND, f"{name!r} is not a directory")
            here = found
        return here

    def host_path(self, parts):
        """Where on the host the file or directory is. For a message, or for a file to put beside it."""
        return self._find(parts)

    def _directory(self, parts):
        """The host path of a directory, which must be there."""
        try:
            path = self._find(parts)
        except FsError as error:
            raise FsError(PATH_NOT_FOUND, str(error)) from None
        if not path.is_dir():
            raise FsError(PATH_NOT_FOUND, f"{parts[-1]!r} is not a directory")
        return path

    def _entry(self, path):
        try:
            info = path.stat()
        except OSError:
            raise FsError(FILE_NOT_FOUND, f"{path.name!r} cannot be read") from None
        is_dir = stat.S_ISDIR(info.st_mode)
        attributes = ATTR_DIRECTORY if is_dir else ATTR_ARCHIVE
        if self.readonly or not os.access(path, os.W_OK):
            attributes |= ATTR_READONLY
        return Entry(path.name.upper(), is_dir, 0 if is_dir else info.st_size, info.st_mtime, attributes)

    def _may_change(self):
        if self.readonly:
            raise FsError(ACCESS_DENIED, "this disk cannot be written to")

    # -- the questions --

    def stat(self, parts):
        if not parts:
            return Entry("", True, 0, self.root.stat().st_mtime, ATTR_DIRECTORY)
        return self._entry(self._find(parts))

    def listdir(self, parts):
        directory = self._directory(parts) if parts else self.root
        entries = []
        for name in os.listdir(directory):
            if is_dos_name(name):
                try:
                    entries.append(self._entry(directory / name))
                except FsError:  # a link that leads nowhere
                    continue
        return sorted(entries, key=lambda entry: entry.name)

    def open(self, parts, write=False):
        path = self._find(parts)
        if path.is_dir():
            raise FsError(ACCESS_DENIED, f"{parts[-1]!r} is a directory")
        if write:
            self._may_change()
        try:
            return open(path, "r+b" if write else "rb")
        except OSError:
            raise FsError(ACCESS_DENIED, f"{parts[-1]!r} cannot be opened") from None

    def create(self, parts):
        self._may_change()
        folder, name = _parent_and_name(parts)
        _check_name(name)
        directory = self._directory(folder) if folder else self.root
        existing = self._child(directory, name)
        if existing is not None and existing.is_dir():
            raise FsError(ACCESS_DENIED, f"{name!r} is a directory")
        try:
            return open(existing if existing is not None else directory / name.upper(), "w+b")
        except OSError:
            raise FsError(ACCESS_DENIED, f"{name!r} cannot be created") from None

    def mkdir(self, parts):
        self._may_change()
        folder, name = _parent_and_name(parts)
        _check_name(name)
        directory = self._directory(folder) if folder else self.root
        if self._child(directory, name) is not None:
            raise FsError(ACCESS_DENIED, f"{name!r} is there already")
        try:
            os.mkdir(directory / name.upper())
        except OSError:
            raise FsError(ACCESS_DENIED, f"{name!r} cannot be made") from None

    def rmdir(self, parts):
        self._may_change()
        _parent_and_name(parts)
        path = self._directory(parts)
        try:
            os.rmdir(path)
        except OSError:  # not empty, or not allowed
            raise FsError(ACCESS_DENIED, f"{parts[-1]!r} cannot be removed") from None

    def remove(self, parts):
        self._may_change()
        _parent_and_name(parts)
        path = self._find(parts)
        if path.is_dir() or not os.access(path, os.W_OK):  # DOS does not delete a directory, or a read-only file
            raise FsError(ACCESS_DENIED, f"{parts[-1]!r} cannot be deleted")
        try:
            os.remove(path)
        except OSError:
            raise FsError(ACCESS_DENIED, f"{parts[-1]!r} cannot be deleted") from None

    def rename(self, old, new):
        self._may_change()
        _parent_and_name(old)
        source = self._find(old)
        folder, name = _parent_and_name(new)
        _check_name(name)
        directory = self._directory(folder) if folder else self.root
        if self._child(directory, name) is not None:
            raise FsError(ACCESS_DENIED, f"{name!r} is there already")
        try:
            os.rename(source, directory / name.upper())
        except OSError:
            raise FsError(ACCESS_DENIED, f"{old[-1]!r} cannot be renamed") from None

    def set_attributes(self, parts, attributes):
        """Make a file read-only or writable. Hidden, system and archive are accepted and not kept: the host has
        no such bits, and every file reads as archived."""
        self._may_change()
        path = self._find(parts)
        if path.is_dir():
            raise FsError(ACCESS_DENIED, "the attributes of a directory cannot be changed")
        _check_settable(attributes)
        mode = path.stat().st_mode
        try:
            os.chmod(path, mode & ~0o222 if attributes & ATTR_READONLY else mode | 0o200)
        except OSError:
            raise FsError(ACCESS_DENIED, f"{parts[-1]!r} cannot be changed") from None

    def free_space(self):
        usage = shutil.disk_usage(self.root)
        return usage.total, usage.free


class _File:
    def __init__(self):
        self.data = bytearray()
        self.mtime = time.time()
        self.readonly = False  # the read-only attribute of this file


class _Directory:
    def __init__(self):
        self.children = {}  # the name in capitals -> a _File or a _Directory
        self.mtime = time.time()


class MemFile:
    """An open file of a MemFs. It reads and writes the bytes the file keeps, from its own position."""

    def __init__(self, node, writable):
        self.node = node
        self.writable = writable
        self.position = 0
        self.closed = False

    def read(self, size=-1):
        data = self.node.data
        end = len(data) if size is None or size < 0 else self.position + size
        chunk = bytes(data[self.position:end])
        self.position += len(chunk)
        return chunk

    def write(self, data):
        if not self.writable:
            raise io.UnsupportedOperation("not writable")
        node = self.node
        if self.position > len(node.data):  # writing beyond the end leaves a gap of zeros
            node.data.extend(bytes(self.position - len(node.data)))
        node.data[self.position:self.position + len(data)] = data
        self.position += len(data)
        node.mtime = time.time()
        return len(data)

    def seek(self, offset, whence=0):
        base = (0, self.position, len(self.node.data))[whence]
        if base + offset < 0:
            raise ValueError("negative seek position")
        self.position = base + offset
        return self.position

    def tell(self):
        return self.position

    def truncate(self, size=None):
        if not self.writable:
            raise io.UnsupportedOperation("not writable")
        size = self.position if size is None else size
        data = self.node.data
        if size < len(data):
            del data[size:]
        else:
            data.extend(bytes(size - len(data)))
        self.node.mtime = time.time()
        return size

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()


class MemFs:
    """A file system kept in memory. It is empty when made, and gone when the emulator ends."""

    def __init__(self, readonly=False):
        self.root = _Directory()
        self.readonly = readonly

    def _child(self, directory, name):
        return directory.children.get(name.upper()) if is_dos_name(name) else None

    def _find(self, parts):
        here = self.root
        for i, name in enumerate(parts):
            last = i == len(parts) - 1
            found = self._child(here, name)
            if found is None:
                raise FsError(FILE_NOT_FOUND if last else PATH_NOT_FOUND, f"{name!r} is not there")
            if not last and not isinstance(found, _Directory):
                raise FsError(PATH_NOT_FOUND, f"{name!r} is not a directory")
            here = found
        return here

    def _directory(self, parts):
        try:
            node = self._find(parts)
        except FsError as error:
            raise FsError(PATH_NOT_FOUND, str(error)) from None
        if not isinstance(node, _Directory):
            raise FsError(PATH_NOT_FOUND, f"{parts[-1]!r} is not a directory")
        return node

    def _entry(self, name, node):
        locked = ATTR_READONLY if self.readonly else 0
        if isinstance(node, _Directory):
            return Entry(name.upper(), True, 0, node.mtime, ATTR_DIRECTORY | locked)
        locked |= ATTR_READONLY if node.readonly else 0
        return Entry(name.upper(), False, len(node.data), node.mtime, ATTR_ARCHIVE | locked)

    def _may_change(self):
        if self.readonly:
            raise FsError(ACCESS_DENIED, "this disk cannot be written to")

    def stat(self, parts):
        if not parts:
            return Entry("", True, 0, self.root.mtime, ATTR_DIRECTORY)
        return self._entry(parts[-1], self._find(parts))

    def listdir(self, parts):
        directory = self._directory(parts) if parts else self.root
        return [self._entry(name, node) for name, node in sorted(directory.children.items())]

    def open(self, parts, write=False):
        node = self._find(parts)
        if isinstance(node, _Directory):
            raise FsError(ACCESS_DENIED, f"{parts[-1]!r} is a directory")
        if write:
            self._may_change()
            if node.readonly:
                raise FsError(ACCESS_DENIED, f"{parts[-1]!r} is read-only")
        return MemFile(node, write)

    def create(self, parts):
        self._may_change()
        folder, name = _parent_and_name(parts)
        _check_name(name)
        directory = self._directory(folder) if folder else self.root
        existing = self._child(directory, name)
        if isinstance(existing, _Directory) or (existing is not None and existing.readonly):
            raise FsError(ACCESS_DENIED, f"{name!r} is a directory or read-only")
        node = existing if existing is not None else _File()
        node.data.clear()
        node.mtime = time.time()
        directory.children[name.upper()] = node
        return MemFile(node, True)

    def mkdir(self, parts):
        self._may_change()
        folder, name = _parent_and_name(parts)
        _check_name(name)
        directory = self._directory(folder) if folder else self.root
        if self._child(directory, name) is not None:
            raise FsError(ACCESS_DENIED, f"{name!r} is there already")
        directory.children[name.upper()] = _Directory()

    def rmdir(self, parts):
        self._may_change()
        folder, name = _parent_and_name(parts)
        node = self._directory(parts)
        if node.children:
            raise FsError(ACCESS_DENIED, f"{name!r} is not empty")
        del (self._directory(folder) if folder else self.root).children[name.upper()]

    def remove(self, parts):
        self._may_change()
        folder, name = _parent_and_name(parts)
        node = self._find(parts)
        if isinstance(node, _Directory) or node.readonly:
            raise FsError(ACCESS_DENIED, f"{name!r} is a directory or read-only")
        del (self._directory(folder) if folder else self.root).children[name.upper()]

    def rename(self, old, new):
        self._may_change()
        old_folder, old_name = _parent_and_name(old)
        node = self._find(old)
        folder, name = _parent_and_name(new)
        _check_name(name)
        directory = self._directory(folder) if folder else self.root
        if self._child(directory, name) is not None:
            raise FsError(ACCESS_DENIED, f"{name!r} is there already")
        inside = [part.upper() for part in new][:len(old)] == [part.upper() for part in old]
        if isinstance(node, _Directory) and inside:  # a directory cannot move into itself
            raise FsError(ACCESS_DENIED, f"{old_name!r} cannot be moved into itself")
        del (self._directory(old_folder) if old_folder else self.root).children[old_name.upper()]
        directory.children[name.upper()] = node

    def set_attributes(self, parts, attributes):
        self._may_change()
        node = self._find(parts)
        if isinstance(node, _Directory):
            raise FsError(ACCESS_DENIED, "the attributes of a directory cannot be changed")
        _check_settable(attributes)
        node.readonly = bool(attributes & ATTR_READONLY)

    def free_space(self):
        def used(directory):
            return sum(used(node) if isinstance(node, _Directory) else len(node.data)
                       for node in directory.children.values())
        return MEMFS_SIZE, MEMFS_SIZE - used(self.root)


def for_mount(mount):
    """The file system of a config.Mount: a HostDir for a directory, a fresh MemFs for a tmpfs."""
    if mount.type == "dir":
        return HostDir(mount.path, mount.readonly)
    return MemFs(mount.readonly)
