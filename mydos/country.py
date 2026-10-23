"""The country of INT 21h AH=38h: how dates, times and money are written. Ours is the United States, country 1."""

COUNTRY_CODE = 1
SIZE = 34  # the table of DOS 3 and later
CASE_MAP = (0xF000, 0x0000)  # where the routine that capitalizes a letter is: a bare RETF, so it changes nothing
CASE_MAP_CODE = b"\xCB"


def install(cpu):
    """Put the routine of CASE_MAP in memory, where the BIOS ROM would be."""
    segment, offset = CASE_MAP
    cpu.mem[segment * 16 + offset:segment * 16 + offset + len(CASE_MAP_CODE)] = CASE_MAP_CODE


def information():
    """The 34 bytes of the table: formats and separators, then the pointer to the case routine."""
    table = bytearray(SIZE)
    table[0x00:0x02] = (0).to_bytes(2, "little")  # the date as month/day/year
    table[0x02:0x04] = b"$\0"  # the currency sign, in a field of 5 bytes
    table[0x07:0x09] = b",\0"  # the thousands
    table[0x09:0x0B] = b".\0"  # the decimals
    table[0x0B:0x0D] = b"-\0"  # between the parts of a date
    table[0x0D:0x0F] = b":\0"  # between the parts of the time
    table[0x0F] = 0  # the sign goes before the number, with no space
    table[0x10] = 2  # digits after the decimal point in money
    table[0x11] = 0  # the time on a 12-hour clock
    segment, offset = CASE_MAP
    table[0x12:0x16] = offset.to_bytes(2, "little") + segment.to_bytes(2, "little")
    table[0x16:0x18] = b",\0"  # between the items of a list
    return bytes(table)
