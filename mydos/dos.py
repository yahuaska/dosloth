import sys

from pathlib import Path

from . import flags
from .config import default as default_config
from .cpu import CPU, Exit
from .filesystem import ACCESS_DENIED, ATTR_DIRECTORY, ATTR_READONLY, ATTR_VOLUME, PATH_NOT_FOUND, FsError
from .handles import Console, HandleTable, NullDevice, OpenFile
from .volumes import Volumes

DOS_VERSION = (7, 1)  # (major, minor) reported by INT 21h AH=30h
OEM_MICROSOFT = 0xFF
CONSOLE_INFO = 0x80D3  # IOCTL answer for the console: a character device, standard input and output
DEVICE_INFO = 0x80C0  # the IOCTL answer for the auxiliary and printer devices: a character device, nothing special
TOO_MANY_OPEN_FILES = 4
INVALID_ACCESS_CODE = 0x0C
MAX_PATH = 260  # how long a path from a program may be, with its ending zero
MEMORY_TOP_SEG = 0xA000  # the end of the 640 KB of ordinary memory; DOS hands out nothing above it


def read_path(cpu):
    """The path in the zero-ended string at DS:DX, which is how DOS functions are given a file name."""
    segment, offset = cpu.s[CPU.DS], cpu.r[CPU.DX]
    characters = bytearray()
    for i in range(MAX_PATH):
        byte = cpu.rb(segment, (offset + i) & 0xFFFF)
        if byte == 0:
            return characters.decode("cp437")
        characters.append(byte)
    raise FsError(PATH_NOT_FOUND, "the path is too long, or has no end")


def fail(cpu, error):
    """Report a failed call the DOS way: the carry flag is set and AX is the error number."""
    cpu.set_flag(flags.CF, True)
    cpu.r[CPU.AX] = error.code


class DosState:
    """What DOS remembers between calls. One of these goes with each CPU."""

    def __init__(self, volumes):
        self.volumes = volumes  # the drives, with the current drive and the current directory of each
        self.config = volumes.config  # the machine: its drives, and where a program starts (see config.py)
        self.handles = HandleTable()  # what the program's file handles stand for
        self.break_checking = False  # the Ctrl-Break flag of AH=33h; DOS starts with it off


def boot_drive(config):
    """The number of the drive DOS started from (0 is A:): the first hard disk, or else the first drive."""
    fixed = [number for number, disk in sorted(config.disks.items()) if not disk.removable]
    return fixed[0] if fixed else min(config.disks)


def int21(cpu, state):
    """INT 21h: DOS services, selected by AH.

    So far 09h, 0Eh, 19h, 25h, 30h, 35h, 33h, 3Ch, 3Dh, 3Eh, 40h, 43h, 44h, 4Ah, 4Ch.
    """
    ah = cpu.get8(4)
    if ah == 0x09:  # print $-terminated string at DS:DX
        off = cpu.r[CPU.DX]
        out = bytearray()
        while (b := cpu.rb(cpu.s[CPU.DS], off)) != 0x24:
            out.append(b)
            off = (off + 1) & 0xFFFF
        sys.stdout.write(out.decode("cp437"))
        sys.stdout.flush()
    elif ah == 0x0E:  # make drive DL the current one (0 is A:, 1 is B:, ...); AL = how many drives there are
        drive = cpu.get8(2)  # 8-bit register 2 is DL
        if drive in state.config.disks:  # a drive that does not exist is not an error: the call just does nothing
            state.volumes.current_drive = drive
        cpu.set8(0, state.config.drive_count)
    elif ah == 0x19:  # get the current drive into AL: 0 for A:, 1 for B:, 2 for C:, ...
        cpu.set8(0, state.volumes.current_drive)
    elif ah == 0x25:  # set interrupt vector AL to DS:DX
        # The vector table is real memory at 0000:0000, 4 bytes per interrupt: offset, segment.
        # Nothing jumps through it yet: INT still calls the Python handlers.
        n = cpu.get8(0)
        cpu.ww(0, n * 4, cpu.r[CPU.DX])
        cpu.ww(0, n * 4 + 2, cpu.s[CPU.DS])
    elif ah == 0x35:  # get interrupt vector AL into ES:BX
        n = cpu.get8(0)
        cpu.r[CPU.BX] = cpu.rw(0, n * 4)
        cpu.s[CPU.ES] = cpu.rw(0, n * 4 + 2)
    elif ah == 0x30:  # get DOS version: AL=major, AH=minor, BH=OEM, BL:CX=serial number
        cpu.set8(0, DOS_VERSION[0])
        cpu.set8(4, DOS_VERSION[1])
        cpu.set8(7, OEM_MICROSOFT)
        cpu.set8(3, 0)
        cpu.r[CPU.CX] = 0
    elif ah == 0x33:  # the Ctrl-Break flag, the drive DOS started from, the real DOS version; AL = what to do
        al = cpu.get8(0)
        if al == 0x00:  # DL = the flag: 1 when DOS looks for Ctrl-Break on every call, not only on console I/O
            cpu.set8(2, int(state.break_checking))
        elif al == 0x01:  # set the flag from DL; any value but 0 means on
            state.break_checking = cpu.get8(2) != 0
        elif al == 0x05:  # DL = the drive DOS started from, counted from 1 (A: is 1)
            cpu.set8(2, boot_drive(state.config) + 1)
        elif al == 0x06:  # the true version, even when SETVER lies to programs: BL = major, BH = minor
            cpu.set8(3, DOS_VERSION[0])
            cpu.set8(7, DOS_VERSION[1])
            cpu.set8(2, 0)  # DL = the revision
            cpu.set8(6, 0)  # DH = where DOS lives: bit 3 the ROM, bit 4 the high memory area; neither here
        else:
            raise NotImplementedError(f"INT 21h AH=33 AL={al:02X}")
    elif ah == 0x3C:  # create (or empty) the file named at DS:DX, with attributes CX; AX = its handle
        try:
            if cpu.r[CPU.CX] & (ATTR_DIRECTORY | ATTR_VOLUME):
                raise FsError(ACCESS_DENIED, "a file cannot be created as a directory or a label")
            where = state.volumes.resolve(read_path(cpu))
            if not state.handles.has_free():
                raise FsError(TOO_MANY_OPEN_FILES, "all the handles are in use")
            file = where.filesystem.create(where.parts)
            if cpu.r[CPU.CX] & ATTR_READONLY:  # the file is read-only from now on, but this handle may still write
                where.filesystem.set_attributes(where.parts, cpu.r[CPU.CX])
            cpu.r[CPU.AX] = state.handles.allocate(OpenFile(file, where.dos_path()))
            cpu.set_flag(flags.CF, False)
        except FsError as error:
            fail(cpu, error)
    elif ah == 0x3D:  # open the file named at DS:DX; AL = the access: 0 read, 1 write, 2 both; AX = its handle
        # Bits 4-6 of AL (how to share the file) and bit 7 (does a child program get the handle) mean nothing
        # to a single program, so they are ignored.
        try:
            if cpu.get8(0) & 0x07 > 2:
                raise FsError(INVALID_ACCESS_CODE, "the access is 0 (read), 1 (write) or 2 (read and write)")
            where = state.volumes.resolve(read_path(cpu))
            if not state.handles.has_free():
                raise FsError(TOO_MANY_OPEN_FILES, "all the handles are in use")
            file = where.filesystem.open(where.parts, write=bool(cpu.get8(0) & 0x07))
            cpu.r[CPU.AX] = state.handles.allocate(OpenFile(file, where.dos_path()))
            cpu.set_flag(flags.CF, False)
        except FsError as error:
            fail(cpu, error)
    elif ah == 0x3E:  # close the file handle in BX
        if state.handles.close(cpu.r[CPU.BX]):  # the standard handles may be closed too; writing to one then fails
            cpu.set_flag(flags.CF, False)
        else:  # error 06: invalid handle: never opened, or already closed
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 6
    elif ah == 0x40:  # write CX bytes from DS:DX to the file handle in BX; AX = how many were written
        handle, count = cpu.r[CPU.BX], cpu.r[CPU.CX]
        entry = state.handles.get(handle)
        if entry is None:  # error 06: invalid handle: not open
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 6
        elif isinstance(entry, OpenFile):
            # A file takes the bytes at its current position. CX = 0 is not an empty write: it cuts the file
            # off at that position.
            data = bytes(cpu.rb(cpu.s[CPU.DS], (cpu.r[CPU.DX] + i) & 0xFFFF) for i in range(count))
            try:
                if count == 0:
                    entry.file.truncate()
                else:
                    entry.file.write(data)
                cpu.r[CPU.AX] = count
                cpu.set_flag(flags.CF, False)
            except (OSError, ValueError):  # error 05: access denied: the file was not opened for writing
                cpu.set_flag(flags.CF, True)
                cpu.r[CPU.AX] = ACCESS_DENIED
        else:
            if isinstance(entry, Console):  # the screen; error output goes to ours. The devices just swallow it.
                data = bytes(cpu.rb(cpu.s[CPU.DS], (cpu.r[CPU.DX] + i) & 0xFFFF) for i in range(count))
                stream = sys.stderr if entry.stream == "stderr" else sys.stdout
                stream.write(data.decode("cp437"))
                stream.flush()
            cpu.r[CPU.AX] = count
            cpu.set_flag(flags.CF, False)
    elif ah == 0x43:  # file attributes, by name at DS:DX: AL=0 get them into CX, AL=1 set them from CX
        al = cpu.get8(0)
        if al not in (0, 1):
            raise NotImplementedError(f"INT 21h AH=43 AL={al:02X}")
        try:
            where = state.volumes.resolve(read_path(cpu))
            if al == 0:
                cpu.r[CPU.CX] = where.filesystem.stat(where.parts).attributes
            else:
                where.filesystem.set_attributes(where.parts, cpu.r[CPU.CX])
            cpu.set_flag(flags.CF, False)
        except FsError as error:
            fail(cpu, error)
    elif ah == 0x44:  # IOCTL: device control through a file handle in BX, subfunction in AL
        al = cpu.get8(0)
        if al == 0x00:
            # Subfunction 0: get device information into DX. C libraries use it to ask "is this handle a
            # terminal or a file?", which decides whether output is buffered. Bit 7 of DX set means a device.
            entry = state.handles.get(cpu.r[CPU.BX])
            if entry is None:  # error 06: invalid handle: not open
                cpu.set_flag(flags.CF, True)
                cpu.r[CPU.AX] = 6
            elif isinstance(entry, OpenFile):  # a file: bit 7 is clear, the low bits are its drive (0 is A:)
                cpu.r[CPU.DX] = ord(entry.dos_path[0]) - ord("A")
                cpu.set_flag(flags.CF, False)
            else:
                cpu.r[CPU.DX] = CONSOLE_INFO if isinstance(entry, Console) else DEVICE_INFO
                cpu.set_flag(flags.CF, False)
        elif al == 0x08:
            # Subfunction 8: is the medium of the drive in BL removable? BL counts from 1 here (0 is the
            # current drive), unlike AH=0Eh and AH=19h. AX = 0 means removable, 1 means fixed.
            number = cpu.get8(3)  # 8-bit register 3 is BL
            drive = state.volumes.current_drive if number == 0 else number - 1
            if drive in state.config.disks:
                cpu.set_flag(flags.CF, False)
                cpu.r[CPU.AX] = 0 if state.config.disks[drive].removable else 1
            else:  # error 0Fh: invalid drive
                cpu.set_flag(flags.CF, True)
                cpu.r[CPU.AX] = 0x0F
        else:
            raise NotImplementedError(f"INT 21h AH=44 AL={al:02X}")
    elif ah == 0x4A:  # resize the memory block that starts at segment ES to BX paragraphs
        # Naive: we keep no list of blocks, only the top of memory. A block can grow or shrink as
        # long as it ends below that top. (DOS error 09, "not a block", cannot be told apart yet.)
        available = max(MEMORY_TOP_SEG - cpu.s[CPU.ES], 0)
        if cpu.r[CPU.BX] <= available:
            cpu.set_flag(flags.CF, False)
        else:  # error 08: not enough memory; BX says how much there is
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 8
            cpu.r[CPU.BX] = available
    elif ah == 0x4C:  # exit with code in AL
        state.handles.close_all()  # a file left open is closed, and what was written to it is kept
        raise Exit(cpu.get8(0))
    else:
        raise NotImplementedError(f"INT 21h AH={ah:02X}")


def install(cpu, volumes=None):
    """Hook DOS interrupt handlers into the CPU, for the drives in `volumes` (see volumes.py).

    Without any, the machine is the built-in one: the directory the emulator was started in is the hard disk C:.
    """
    state = DosState(volumes if volumes is not None else Volumes(default_config(Path.cwd())))
    cpu.int_handlers[0x21] = lambda cpu: int21(cpu, state)
    return state  # for whoever wants to look at what DOS holds, the tests in particular
