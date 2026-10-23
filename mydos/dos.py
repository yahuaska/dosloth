import re
import sys
import time
from pathlib import Path

from . import flags
from .config import default as default_config
from .cpu import CPU, Exit, signed
from .filesystem import (ACCESS_DENIED, ATTR_DIRECTORY, ATTR_READONLY, ATTR_VOLUME, FILE_NOT_FOUND, PATH_NOT_FOUND,
                         FsError)
from .handles import Console, HandleTable, NullDevice, OpenFile
from .memory import Blocks
from . import sysvars
from .psp import DTA_OFFSET, PSP_SEG
from .search import Searches, find, record
from .volumes import Volumes

DOS_VERSION = (7, 1)  # (major, minor) reported by INT 21h AH=30h
OEM_MICROSOFT = 0xFF
CONSOLE_INFO = 0x80D3  # IOCTL answer for the console: a character device, standard input and output
DEVICE_INFO = 0x80C0  # the IOCTL answer for the auxiliary and printer devices: a character device, nothing special
TOO_MANY_OPEN_FILES = 4
INVALID_ACCESS_CODE = 0x0C
INVALID_FUNCTION = 1
NEGATIVE_SEEK = 131  # an attempt to move the position of a file to before its start
NO_MORE_FILES = 18
INVALID_HANDLE = 6
MSCDEX_MAGIC = 0xADAD  # what MSCDEX puts in BX to say that it is there
MSCDEX_VERSION = 0x020A  # MSCDEX 2.10, the one of Windows 95
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


def now():
    """The time of the host, in seconds since 1970 with the fractions (a function of its own so tests can fake it)."""
    return time.time()


def take_key(cpu, state):
    """The next character for a program that asks for one without waiting, or None when there is none.

    A key that makes no character (an arrow, F1) comes as two: first 0, then its scan code, as DOS does it.
    """
    if state.pending_scan is not None:
        code, state.pending_scan = state.pending_scan, None
        return code
    keyboard = cpu.keyboard
    if keyboard is None or not keyboard.buffer:
        return None
    word = keyboard.buffer.popleft()
    if word & 0xFF == 0:
        state.pending_scan = word >> 8
        return 0
    return word & 0xFF


def wait_for_key(cpu, state):
    """The next character, waiting for it if there is none yet: what the keyboard functions that wait are made of.

    Waiting is done by the keyboard's waiter (see bios.Keyboard): there is one only when something can press keys,
    a window. Without one, waiting would be for ever, and the emulator stops instead.
    """
    while True:
        key = take_key(cpu, state)
        if key is not None:
            return key
        keyboard = cpu.keyboard
        if keyboard is None or keyboard.waiter is None:
            raise NotImplementedError("INT 21h: the program waits for a key, and no key can be pressed")
        keyboard.waiter()


def put_record(cpu, state, number, entry):
    """Write what a search found into the DTA, for a program to read."""
    segment, offset = state.dta
    for i, byte in enumerate(record(number, entry)):
        cpu.wb(segment, (offset + i) & 0xFFFF, byte)


def find_first(cpu, state):
    """AH=4Eh: look for the files that fit the mask at DS:DX, among the kinds that CX asks for. The first one is put
    in the DTA, and a number there lets AH=4Fh go on with the others. Error 2 if none fits, 3 if the directory is not
    there."""
    text = read_path(cpu)
    where = state.volumes.resolve(text)
    if re.split(r"[\\/:]", text)[-1] in ("", ".", ".."):  # the path ends in a directory, a drive or a dot: no mask
        raise FsError(PATH_NOT_FOUND, "no mask to look for")
    mask = where.parts[-1]
    found = find(where.filesystem.listdir(where.parts[:-1]), mask, cpu.r[CPU.CX])
    if not found:
        raise FsError(FILE_NOT_FOUND, f"nothing fits {mask!r}")
    put_record(cpu, state, state.searches.start(found[1:]), found[0])


def find_next(cpu, state):
    """AH=4Fh: the next file of the search whose number is in the DTA. Error 18 when there are no more."""
    segment, offset = state.dta
    number = cpu.rw(segment, offset)
    entry = state.searches.take(number)
    if entry is None:
        raise FsError(NO_MORE_FILES, "there are no more files")
    put_record(cpu, state, number, entry)


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
        self.dta = (PSP_SEG, DTA_OFFSET)  # where the disk transfer area is, as segment and offset: see AH=1Ah
        self.searches = Searches()  # the file searches that AH=4Eh began and AH=4Fh goes on with
        self.memory = Blocks(MEMORY_TOP_SEG, PSP_SEG, MEMORY_TOP_SEG - PSP_SEG)  # the blocks AH=48h-4Ah deal in
        self.pending_scan = None  # the scan code of a key with no character, to be given by the next read of a key


def boot_drive(config):
    """The number of the drive DOS started from (0 is A:): the first hard disk, or else the first drive."""
    fixed = [number for number, disk in sorted(config.disks.items()) if not disk.removable]
    return fixed[0] if fixed else min(config.disks)


def int21(cpu, state):
    """INT 21h: DOS services, selected by AH.

    So far 06h, 08h, 09h, 0Eh, 19h, 1Ah, 25h, 2Ch, 2Fh, 30h, 33h, 35h, 3Ch, 3Dh, 3Eh, 3Fh, 40h, 42h, 43h, 44h, 4Ah, 4Ch,
    4Eh, 4Fh, 52h, 63h.
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
    elif ah == 0x06:  # direct console input and output, with no checks for Ctrl-C and no waiting
        character = cpu.get8(2)  # DL
        if character != 0xFF:  # print the character as it is; AL is the character too
            sys.stdout.write(bytes([character]).decode("cp437"))
            sys.stdout.flush()
            cpu.set8(0, character)
        else:  # DL = FFh: is there a key? ZF = 1 and AL = 0 if not, else ZF = 0 and AL = the character
            key = take_key(cpu, state)
            cpu.set_flag(flags.ZF, key is None)
            cpu.set8(0, 0 if key is None else key)
    elif ah == 0x08:  # wait for a key and take it, without showing it: AL = the character
        # Ctrl-C is not looked for (the real call would run the Ctrl-C handler, INT 23h): it is a character like any.
        # A key with no character comes as 0 now and its scan code at the next call, as with AH=06h.
        cpu.set8(0, wait_for_key(cpu, state))
    elif ah == 0x0E:  # make drive DL the current one (0 is A:, 1 is B:, ...); AL = how many drives there are
        drive = cpu.get8(2)  # 8-bit register 2 is DL
        if drive in state.config.disks:  # a drive that does not exist is not an error: the call just does nothing
            state.volumes.current_drive = drive
        cpu.set8(0, state.config.drive_count)
    elif ah == 0x19:  # get the current drive into AL: 0 for A:, 1 for B:, 2 for C:, ...
        cpu.set8(0, state.volumes.current_drive)
    elif ah == 0x25:  # set interrupt vector AL to DS:DX
        # The vector table is real memory at 0000:0000, 4 bytes per interrupt: offset, segment. INT goes to the
        # address a program puts there (see CPU.int_), and to our own handler where the table has 0000:0000.
        n = cpu.get8(0)
        cpu.ww(0, n * 4, cpu.r[CPU.DX])
        cpu.ww(0, n * 4 + 2, cpu.s[CPU.DS])
    elif ah == 0x35:  # get interrupt vector AL into ES:BX
        n = cpu.get8(0)
        cpu.r[CPU.BX] = cpu.rw(0, n * 4)
        cpu.s[CPU.ES] = cpu.rw(0, n * 4 + 2)
    elif ah == 0x1A:  # set the disk transfer area to DS:DX: where the results of a file search are put
        state.dta = (cpu.s[CPU.DS], cpu.r[CPU.DX])
    elif ah == 0x2F:  # get the disk transfer area into ES:BX
        cpu.s[CPU.ES], cpu.r[CPU.BX] = state.dta
    elif ah == 0x2C:  # get the time: CH = hour, CL = minute, DH = second, DL = hundredths of a second
        moment = now()
        local = time.localtime(moment)
        cpu.set8(5, local.tm_hour)
        cpu.set8(1, local.tm_min)
        cpu.set8(6, min(local.tm_sec, 59))  # a leap second is shown as the last second of the minute
        cpu.set8(2, int(moment % 1 * 100))
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
            access = cpu.get8(0) & 0x07
            file = where.filesystem.open(where.parts, write=bool(access))
            cpu.r[CPU.AX] = state.handles.allocate(
                OpenFile(file, where.dos_path(), readable=access != 1, writable=access != 0))
            cpu.set_flag(flags.CF, False)
        except FsError as error:
            fail(cpu, error)
    elif ah == 0x3E:  # close the file handle in BX
        if state.handles.close(cpu.r[CPU.BX]):  # the standard handles may be closed too; writing to one then fails
            cpu.set_flag(flags.CF, False)
        else:  # error 06: invalid handle: never opened, or already closed
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 6
    elif ah == 0x3F:  # read CX bytes from the file handle in BX to DS:DX; AX = how many were read
        handle, count = cpu.r[CPU.BX], cpu.r[CPU.CX]
        entry = state.handles.get(handle)
        if entry is None:
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = INVALID_HANDLE
        elif not isinstance(entry, OpenFile):  # the keyboard and the devices: not yet
            raise NotImplementedError(f"INT 21h AH=3F on the handle {handle}: {type(entry).__name__}")
        elif not entry.readable:  # the file was opened to be written only
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = ACCESS_DENIED
        else:
            # Fewer bytes than asked for are normal: the end of the file. At the end it is 0, and that is no error.
            # The file goes on from where the last read left it.
            try:
                data = entry.file.read(count)
            except OSError:
                cpu.set_flag(flags.CF, True)
                cpu.r[CPU.AX] = ACCESS_DENIED
            else:
                for i, byte in enumerate(data):
                    cpu.wb(cpu.s[CPU.DS], (cpu.r[CPU.DX] + i) & 0xFFFF, byte)
                cpu.r[CPU.AX] = len(data)
                cpu.set_flag(flags.CF, False)
    elif ah == 0x40:  # write CX bytes from DS:DX to the file handle in BX; AX = how many were written
        handle, count = cpu.r[CPU.BX], cpu.r[CPU.CX]
        entry = state.handles.get(handle)
        if entry is None:  # error 06: invalid handle: not open
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 6
        elif isinstance(entry, OpenFile) and not entry.writable:  # the file was opened to be read only
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = ACCESS_DENIED
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
    elif ah == 0x42:  # move the position in the file of handle BX; DX:AX = the new position
        # AL = from where: 0 the start of the file, 1 where it is now, 2 the end of the file. CX:DX = how far, a
        # signed number of 32 bits. A position past the end is all right: a read finds nothing there, and a write
        # makes the gap zeros. A position before the start is not: error 131, and the file stays where it was.
        entry = state.handles.get(cpu.r[CPU.BX])
        whence = cpu.get8(0)
        if entry is None:
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = INVALID_HANDLE
        elif not isinstance(entry, OpenFile):  # the console and the devices have no position
            raise NotImplementedError(f"INT 21h AH=42 on the handle {cpu.r[CPU.BX]}: {type(entry).__name__}")
        elif whence > 2:
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = INVALID_FUNCTION
        else:
            before = entry.file.tell()
            base = (0, before, None)[whence]
            if base is None:
                base = entry.file.seek(0, 2)
            target = base + signed(cpu.r[CPU.CX] << 16 | cpu.r[CPU.DX], 32)
            if target < 0:
                entry.file.seek(before)
                cpu.set_flag(flags.CF, True)
                cpu.r[CPU.AX] = NEGATIVE_SEEK
            else:
                target &= 0xFFFFFFFF
                entry.file.seek(target)
                cpu.r[CPU.AX], cpu.r[CPU.DX] = target & 0xFFFF, target >> 16
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
    elif ah == 0x63:  # the table of lead bytes of two-byte characters, for the Chinese, Japanese and Korean pages
        al = cpu.get8(0)
        if al != 0x00:
            raise NotImplementedError(f"INT 21h AH=63 AL={al:02X}")
        # AL=0 asks for a pointer to the table in DS:SI. Ours is the western machine, whose characters all take one
        # byte, so there is no table: error 01 (no such function) is the answer a program takes as "none".
        cpu.set_flag(flags.CF, True)
        cpu.r[CPU.AX] = INVALID_FUNCTION
    elif ah == 0x4E or ah == 0x4F:  # find the first file that fits a mask, and the next ones
        try:
            (find_first if ah == 0x4E else find_next)(cpu, state)
            cpu.set_flag(flags.CF, False)
        except FsError as error:
            fail(cpu, error)
    elif ah == 0x52:  # get the List of Lists, the inner variables of DOS (see sysvars.py), into ES:BX
        cpu.s[CPU.ES], cpu.r[CPU.BX] = sysvars.SEGMENT, sysvars.LIST_OFFSET
    elif ah in (0x51, 0x62):  # BX = the segment of the PSP of the running program (51h is the old name of 62h)
        cpu.r[CPU.BX] = PSP_SEG
    elif ah == 0x48:  # allocate BX paragraphs; AX = the segment of the block
        segment = state.memory.allocate(cpu.r[CPU.BX])
        if segment is None:  # error 08: not enough memory; BX says how big a block there could be
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 8
            cpu.r[CPU.BX] = state.memory.largest()
        else:
            cpu.set_flag(flags.CF, False)
            cpu.r[CPU.AX] = segment
    elif ah == 0x49:  # free the memory block that starts at segment ES
        if state.memory.free(cpu.s[CPU.ES]):
            cpu.set_flag(flags.CF, False)
        else:  # error 09: not a block
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 9
    elif ah == 0x4A:  # resize the memory block that starts at segment ES to BX paragraphs
        done = state.memory.resize(cpu.s[CPU.ES], cpu.r[CPU.BX])
        if done:
            cpu.set_flag(flags.CF, False)
        elif done is None:  # error 09: not a block
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 9
        else:  # error 08: not enough memory; BX says how much there is
            cpu.set_flag(flags.CF, True)
            cpu.r[CPU.AX] = 8
            cpu.r[CPU.BX] = state.memory.room_for(cpu.s[CPU.ES])
    elif ah == 0x4C:  # exit with code in AL
        state.handles.close_all()  # a file left open is closed, and what was written to it is kept
        raise Exit(cpu.get8(0))
    else:
        raise NotImplementedError(f"INT 21h AH={ah:02X}")


def cdrom_drives(config):
    """The numbers of the CD-ROM drives of the machine (0 is A:), the lowest first."""
    return [number for number, disk in sorted(config.disks.items()) if disk.type == "cdrom"]


def int2f(cpu, state):
    """INT 2Fh: the multiplex interrupt. Programs ask here what is installed besides DOS; AH picks the service.

    So far the questions to MSCDEX, the CD-ROM extension of DOS: 1500h, how many CD-ROM drives there are. It is
    there when the machine has a CD-ROM drive, and then it also answers 150Bh and 150Ch. Without a drive there is
    no MSCDEX, and the count is 0.

    And 1600h, "is Windows running in the enhanced mode?" Not here: AL stays 00h, which is what a machine with no
    Windows answers, because nothing answers.
    """
    ax = cpu.r[CPU.AX]
    cdroms = cdrom_drives(state.config)
    if ax == 0x1500:  # BX = how many drives; CX = the first of them (the caller put 0 in BX, and gets 0 if none)
        cpu.r[CPU.BX] = len(cdroms)
        if cdroms:
            cpu.r[CPU.CX] = cdroms[0]
    elif cdroms and ax == 0x150B:  # is the drive in CX a CD-ROM drive? AX = 1 if so, else 0; BX = ADADh: MSCDEX
        cpu.r[CPU.AX] = int(cpu.r[CPU.CX] in cdroms)
        cpu.r[CPU.BX] = MSCDEX_MAGIC
    elif cdroms and ax == 0x150C:  # the version of MSCDEX in BX: the major number in BH, the minor in BL
        cpu.r[CPU.BX] = MSCDEX_VERSION
    elif ax == 0x1600:  # nothing of Windows is installed to answer, so AX is as the program left it
        pass
    else:
        raise NotImplementedError(f"INT 2Fh AX={ax:04X}")


def install(cpu, volumes=None):
    """Hook DOS interrupt handlers into the CPU, for the drives in `volumes` (see volumes.py).

    Without any, the machine is the built-in one: the directory the emulator was started in is the hard disk C:.
    """
    state = DosState(volumes if volumes is not None else Volumes(default_config(Path.cwd())))
    sysvars.write(cpu, state.config, MEMORY_TOP_SEG)  # the tables of DOS, which a program finds with AH=52h
    cpu.int_handlers[0x21] = lambda cpu: int21(cpu, state)
    cpu.int_handlers[0x2F] = lambda cpu: int2f(cpu, state)
    return state  # for whoever wants to look at what DOS holds, the tests in particular
