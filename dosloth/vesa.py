"""The VESA BIOS extensions (VBE): how a program finds out what the video card can do beyond the VGA modes.

A program asks with INT 10h AH=4Fh, and AL says what. The answer is always in AX: AL=4Fh says that the function is
known (a BIOS that does not know the extensions leaves AL as it was), AH=00h that it went well. AL=00h is the first
question, "what is this?": the program gives a buffer at ES:DI and the BIOS fills it with the information block.

    00h  4 bytes  the signature "VESA"
    04h  word     the version of VBE, 0200h for 2.0
    06h  dword    a pointer (offset, segment) to a string that says who made the card
    0Ah  dword    the capabilities of the card (bit 0: the DAC can have 8 bits for a color)
    0Eh  dword    a pointer to the list of the modes: words, the number of the mode, the list ends with FFFFh
    12h  word     the memory of the card, in blocks of 64 KB
  and, if the program began the buffer with "VBE2" to say that it knows VBE 2.0, 512 bytes in all:
    14h  word     the version of the software of the BIOS
    16h  dword    a pointer to the name of the maker, 1Ah the name of the product, 1Eh its revision
    22h  222 bytes reserved, and at 100h 256 bytes of data of the maker

The strings and the list of modes are in memory the program can read after the call, in the segment of the video
BIOS, C000h, where a real card has its ROM.

The second question, AL=01h, is "what is this mode?": CX is the number of the mode, and the buffer at ES:DI is filled
with 256 bytes, the information block of the mode:

    00h  word     the attributes: bit 0 the mode can be set, bit 1 the block has more than the old VBE said, bit 3 color,
                  bit 4 graphics, bit 5 not VGA compatible (0: the VGA registers do as they do in the VGA modes),
                  bit 6 no window of memory (0: there is one), bit 7 a linear frame buffer (0: there is none)
    02h  byte     the rights of the window A: bit 0 it is there, bit 1 it can be read, bit 2 it can be written;
    03h  byte     the same for the window B
    04h  word     the granularity: the window is put at a multiple of this many KB
    06h  word     the size of the window, in KB
    08h  word     the segment of the window A, 0Ah  that of the window B (0: none)
    0Ch  dword    a pointer to the function that puts the window in place; 0 says there is none, and a program
                  then does it by INT 10h AX=4F05h
    10h  word     bytes in a line of the screen
    12h  word     width in pixels, 14h  height
    16h  byte     width of a character cell, 17h  its height
    18h  byte     number of planes, 19h  bits in a pixel
    1Ah  byte     number of banks (a window's worth of the screen), 1Bh  the model of the memory (4 is packed pixels),
    1Ch  byte     the size of a bank in KB, 1Dh  the number of the pages of the picture that fit in the memory, less one

A mode that is not there is answered by AX=014Fh: the function is known (4Fh), and it failed (01h).

What is done: the first two questions. Setting a mode (4F02h), the window of memory of the card (4F05h) and the rest
stop the emulator, so that the first program that needs them says so.

The list has one mode, 0101h: 640 by 480 pixels in 256 colors, which X-COM: Apocalypse asks for. We have no such
mode yet.
"""
ROM_SEGMENT = 0xC000
OEM_STRING = (0x0100, b"dosloth VGA\0")
VENDOR = (0x0120, b"dosloth\0")
PRODUCT = (0x0140, b"dosloth VBE\0")
REVISION = (0x0160, b"1.0\0")
MODE_LIST = (0x0200, (0x0101).to_bytes(2, "little") + (0xFFFF).to_bytes(2, "little"))
VERSION = 0x0200
MEMORY_BLOCKS = 16  # of 64 KB: a megabyte, the room for 640 by 480 in 256 colors with some to spare
SOFTWARE_REVISION = 0x0100
MODE = 0x0101
MODE_ATTRIBUTES = 0x001B  # can be set, has the block, color, graphics; it has a window and no linear frame buffer
WINDOW_RIGHTS = 0x07  # there, to be read and to be written
WINDOW_KB = 64  # the size of the window, and the step at which it goes: the VGA window at A000:0000, as it is
WINDOW_SEGMENT = 0xA000
MODE_WIDTH, MODE_HEIGHT = 640, 480
SIZE = 256  # the information block for a program that does not know VBE 2.0
SIZE_2 = 512  # and for one that does


def install(cpu):
    """Put the strings and the list of modes into the segment of the video BIOS."""
    for offset, data in (OEM_STRING, VENDOR, PRODUCT, REVISION, MODE_LIST):
        address = ROM_SEGMENT * 16 + offset
        cpu.mem[address:address + len(data)] = data


def _pointer(offset):
    return (ROM_SEGMENT << 16 | offset).to_bytes(4, "little")  # the offset first, as the 8086 keeps a far pointer


def controller_information(cpu, segment, offset):
    """AX=4F00h: fill the information block at segment:offset. Returns the value for AX."""
    version_2 = bytes(cpu.rb(segment, (offset + i) & 0xFFFF) for i in range(4)) == b"VBE2"
    block = bytearray(SIZE_2 if version_2 else SIZE)
    block[0x00:0x04] = b"VESA"
    block[0x04:0x06] = VERSION.to_bytes(2, "little")
    block[0x06:0x0A] = _pointer(OEM_STRING[0])
    block[0x0A:0x0E] = bytes(4)
    block[0x0E:0x12] = _pointer(MODE_LIST[0])
    block[0x12:0x14] = MEMORY_BLOCKS.to_bytes(2, "little")
    if version_2:
        block[0x14:0x16] = SOFTWARE_REVISION.to_bytes(2, "little")
        block[0x16:0x1A] = _pointer(VENDOR[0])
        block[0x1A:0x1E] = _pointer(PRODUCT[0])
        block[0x1E:0x22] = _pointer(REVISION[0])
    for i, byte in enumerate(block):
        cpu.wb(segment, (offset + i) & 0xFFFF, byte)
    return 0x004F


def mode_information(cpu, segment, offset, mode):
    """AX=4F01h: fill the information block of `mode` at segment:offset. Returns the value for AX."""
    if mode != MODE:
        return 0x014F
    block = bytearray(SIZE)
    block[0x00:0x02] = MODE_ATTRIBUTES.to_bytes(2, "little")
    block[0x02] = WINDOW_RIGHTS
    block[0x04:0x06] = WINDOW_KB.to_bytes(2, "little")
    block[0x06:0x08] = WINDOW_KB.to_bytes(2, "little")
    block[0x08:0x0A] = WINDOW_SEGMENT.to_bytes(2, "little")
    block[0x10:0x12] = MODE_WIDTH.to_bytes(2, "little")
    block[0x12:0x14] = MODE_WIDTH.to_bytes(2, "little")
    block[0x14:0x16] = MODE_HEIGHT.to_bytes(2, "little")
    block[0x16], block[0x17] = 8, 16
    block[0x18], block[0x19] = 1, 8
    block[0x1A] = -(-MODE_WIDTH * MODE_HEIGHT // (WINDOW_KB * 1024))
    block[0x1B] = 4
    block[0x1C] = WINDOW_KB
    block[0x1D] = MEMORY_BLOCKS * 64 // (MODE_WIDTH * MODE_HEIGHT // 1024) - 1
    for i, byte in enumerate(block):
        cpu.wb(segment, (offset + i) & 0xFFFF, byte)
    return 0x004F
