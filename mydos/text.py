"""The text screen of the BIOS: characters kept in memory, a cursor for each page, and what INT 10h does with them.

In a text mode the screen is memory. Each place on it holds two bytes, the character and its attribute (the colors:
the low 4 bits are the color of the character, the next 3 the color behind it, the top bit blinking or a bright
background). The places go line after line from the start of a page, 80 to a line, and the page is 4096 bytes, so
that there is room for 8 pages from B8000h. Only the page the card shows (INT 10h AH=05h) is seen, but a program may
write to the others.

The BIOS also keeps in its data area at 0040:0000 what it has to remember: the mode and the size of the screen, a
cursor (a column and a row) for each page, and the shape of the cursor, the first and the last of its scan lines.

Only mode 03h (80 by 25) is a text mode here. In the graphics mode the same calls would have to draw characters
pixel by pixel with a font, and they stop the emulator instead.
"""
BDA_VIDEO_MODE = 0x449  # a byte
BDA_COLUMNS = 0x44A  # a word: the characters in a line
BDA_CURSORS = 0x450  # 8 words, one for each page: the column in the low byte, the row in the high byte
BDA_CURSOR_SHAPE = 0x460  # a word: the first scan line of the cursor in the high byte, the last in the low byte
BDA_PAGE = 0x462  # a byte: the page of the screen that is shown
BDA_ROWS = 0x484  # a byte: the number of rows less one
BDA_CHARACTER_HEIGHT = 0x485  # a word: the scan lines of a character

TEXT_BASE = 0xB8000  # where the characters of the text modes are
PAGE_BYTES = 0x1000
PAGES = 8
TEXT_MODE = 0x03  # 80 by 25 characters in 16 colors: what DOS is in before a program starts
GRAPHICS_MODE = 0x12  # 640 by 480 pixels in 16 colors
BLANK_CELL = bytes([0x20, 0x07])  # a space, light grey on black
DEFAULT_CURSOR_SHAPE = 0x0607  # the last two of the 8 scan lines of a character of the old displays


def set_up(cpu, mode):
    """What the BIOS puts in its data area for a video mode: the size of the screen, the cursor of every page at
    the top left, the usual shape of the cursor."""
    cpu.mem[BDA_VIDEO_MODE] = mode
    cpu.mem[BDA_COLUMNS:BDA_COLUMNS + 2] = (80).to_bytes(2, "little")
    cpu.mem[BDA_CURSORS:BDA_CURSORS + 2 * PAGES] = bytes(2 * PAGES)
    cpu.mem[BDA_CURSOR_SHAPE:BDA_CURSOR_SHAPE + 2] = DEFAULT_CURSOR_SHAPE.to_bytes(2, "little")
    cpu.mem[BDA_PAGE] = 0
    cpu.mem[BDA_ROWS] = 24 if mode == TEXT_MODE else 29  # 25 rows of 16 scan lines, or 480 scan lines of 16
    cpu.mem[BDA_CHARACTER_HEIGHT:BDA_CHARACTER_HEIGHT + 2] = (16).to_bytes(2, "little")


def check_page(page, what):
    if page >= PAGES:
        raise NotImplementedError(f"{what}: there is no page {page}")


def get_cursor(cpu, page):
    """(row, column) of the cursor of a page."""
    word = cpu.rw(0x40, BDA_CURSORS - 0x400 + 2 * page)
    return word >> 8, word & 0xFF


def set_cursor(cpu, page, row, column):
    """Put the cursor of a page where the program says, even somewhere outside the screen: some programs hide the
    cursor that way."""
    cpu.mem[BDA_CURSORS + 2 * page] = column & 0xFF
    cpu.mem[BDA_CURSORS + 2 * page + 1] = row & 0xFF


def cursor_shape(cpu):
    return cpu.rw(0x40, BDA_CURSOR_SHAPE - 0x400)


def set_cursor_shape(cpu, shape):
    cpu.mem[BDA_CURSOR_SHAPE:BDA_CURSOR_SHAPE + 2] = (shape & 0xFFFF).to_bytes(2, "little")


def require_text(cpu, what):
    if cpu.mem[BDA_VIDEO_MODE] != TEXT_MODE:
        raise NotImplementedError(f"{what} in the graphics mode {cpu.mem[BDA_VIDEO_MODE]:02X}")


def size(cpu):
    """(columns, rows) of the screen."""
    return cpu.rw(0x40, BDA_COLUMNS - 0x400), cpu.mem[BDA_ROWS] + 1


def cell_address(cpu, page, row, column, what):
    """Where in memory the place at this row and column of a page is. The place has to be on the screen."""
    columns, rows = size(cpu)
    if row >= rows or column >= columns:
        raise NotImplementedError(f"{what} at row {row}, column {column}: outside the screen of {columns} by {rows}")
    return TEXT_BASE + page * PAGE_BYTES + (row * columns + column) * 2


def read_cell(cpu, page, what):
    """(character, attribute) at the cursor of a page."""
    row, column = get_cursor(cpu, page)
    address = cell_address(cpu, page, row, column, what)
    return cpu.mem[address], cpu.mem[address + 1]


def write_cells(cpu, page, character, attribute, count, what):
    """Write the character `count` times from the cursor on, to the end of the line and on into the next, and no
    further than the end of the page. The attribute is not changed when `attribute` is None. The cursor stays."""
    columns, rows = size(cpu)
    row, column = get_cursor(cpu, page)
    address = cell_address(cpu, page, row, column, what)
    end = TEXT_BASE + page * PAGE_BYTES + columns * rows * 2
    for _ in range(count):
        if address >= end:
            break
        cpu.mem[address] = character
        if attribute is not None:
            cpu.mem[address + 1] = attribute
        address += 2
