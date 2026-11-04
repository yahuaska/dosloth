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

What is done: the first question only. What the modes are (4F01h), setting one (4F02h), the window of memory of
the card (4F05h) and the rest stop the emulator, so that the first program that needs them says so.

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
