"""The List of Lists of DOS: the table of its inner variables that INT 21h AH=52h gives a pointer to.

Programs that want to know how DOS is set up read it: how many files may be open at once (the system file table,
which is the FILES of CONFIG.SYS), how many drives there are (LASTDRIVE), where the first block of memory is, where
the chain of device drivers begins. DOS keeps the table in its own memory, below the program. We write it there
when DOS starts, and a program gets the address and reads it as it would on a real machine.

The pointer a program receives is ES:BX. The word before it (ES:BX-2) is the segment of the first memory control
block, which comes with the table because that is where the first block was found in DOS 3. What is in the table
(the fields of DOS 4 and later; offsets from BX):
    00h  dword   the first drive parameter block                     (none: FFFF:FFFF)
    04h  dword   the system file table                              (see below)
    08h  dword   the clock device                                   (none)
    0Ch  dword   the console device                                 (none)
    10h  word    the most bytes in a sector of any block device     (512)
    12h  dword   the disk buffers                                   (none)
    16h  dword   the table of current directories                   (none)
    1Ah  dword   the system file control blocks                     (none)
    1Eh  word    how many of them are protected                     (0)
    20h  byte    how many block devices there are                   (floppy and hard disk drives)
    21h  byte    LASTDRIVE: the letters that may be drives          (5 at least, more if the machine has more)
    22h  18 bytes  the header of the NUL device, first of the chain of devices
    34h  byte    how many drives are JOINed                         (0)
A pointer that is FFFF:FFFF says "nothing here", and ends a chain.

The system file table is a chain of tables, each of a header and its entries: a dword for the next table (FFFF:FFFF
for the last), a word for how many entries this table has, and then the entries, 3Bh bytes each. A program adds the
numbers up to know how many files may be open. We make one table of FILES entries, all free (all zeros): the files
of a program are kept by us, in handles.py, and DOS does not show them here.

The first memory control block is made here too: one block, the last, that starts at the environment of the
program and goes to the top of the 640 KB, as the program is the only thing in memory that DOS has given out.
"""
from .psp import ENV_SEG, PSP_SEG

SEGMENT = 0x0070  # where the tables are: 0070:0000, which is physical 0700h, above the data areas of the BIOS
LIST_OFFSET = 0x0002  # the pointer given to a program: ES:BX points at the field after the word of the first MCB
SFT_OFFSET = 0x0100  # the system file table, in the same segment
FILES = 60  # as many files as the table has room for: what Windows 98 sets when CONFIG.SYS says nothing
SFT_ENTRY_SIZE = 0x3B
NONE = 0xFFFFFFFF  # a pointer to nothing, FFFF:FFFF
FIRST_MCB = ENV_SEG - 1  # the control block is the paragraph before the block it describes
MIN_LASTDRIVE = 5  # DOS has room for A: to E: whatever CONFIG.SYS says
MAX_SECTOR = 512


def _put(cpu, offset, value, size):
    address = SEGMENT * 16 + offset
    cpu.mem[address:address + size] = value.to_bytes(size, "little")


def _pointer(segment, offset):
    return segment << 16 | offset


def block_devices(config):
    """How many block devices a machine has: its floppy and hard disk drives (a CD-ROM is the work of MSCDEX)."""
    return sum(1 for disk in config.disks.values() if disk.type != "cdrom")


def write(cpu, config, top_segment):
    """Write the List of Lists, the system file table and the first memory control block into memory.

    `top_segment` is the first segment above the memory the programs may use (the end of the 640 KB)."""
    base = LIST_OFFSET
    _put(cpu, 0x0000, FIRST_MCB, 2)
    _put(cpu, base + 0x00, NONE, 4)
    _put(cpu, base + 0x04, _pointer(SEGMENT, SFT_OFFSET), 4)
    _put(cpu, base + 0x08, NONE, 4)
    _put(cpu, base + 0x0C, NONE, 4)
    _put(cpu, base + 0x10, MAX_SECTOR, 2)
    _put(cpu, base + 0x12, NONE, 4)
    _put(cpu, base + 0x16, NONE, 4)
    _put(cpu, base + 0x1A, NONE, 4)
    _put(cpu, base + 0x1E, 0, 2)
    _put(cpu, base + 0x20, block_devices(config), 1)
    _put(cpu, base + 0x21, max(MIN_LASTDRIVE, config.drive_count), 1)
    _put(cpu, base + 0x22, NONE, 4)  # the NUL device: the next device (none), its attributes (a character device
    _put(cpu, base + 0x26, 0x8004, 2)  # that is the NUL device), the strategy and interrupt routines, its name
    _put(cpu, base + 0x28, 0, 2)
    _put(cpu, base + 0x2A, 0, 2)
    address = SEGMENT * 16 + base + 0x2C
    cpu.mem[address:address + 8] = b"NUL     "
    _put(cpu, base + 0x34, 0, 1)
    _put(cpu, SFT_OFFSET, NONE, 4)
    _put(cpu, SFT_OFFSET + 4, FILES, 2)
    start = SEGMENT * 16 + SFT_OFFSET + 6
    cpu.mem[start:start + FILES * SFT_ENTRY_SIZE] = bytes(FILES * SFT_ENTRY_SIZE)
    mcb = FIRST_MCB * 16  # 'Z': the last block; its owner is the program; its size is all there is up to the top
    size = top_segment - ENV_SEG
    cpu.mem[mcb:mcb + 16] = b"Z" + PSP_SEG.to_bytes(2, "little") + size.to_bytes(2, "little") + bytes(11)
