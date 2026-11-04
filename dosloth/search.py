"""Searching a directory for files by a mask with wildcards: what INT 21h AH=4Eh and AH=4Fh need.

A program asks for the first file that fits a mask (`*.DAT`, `XCOM3.CFG`, `SAVE?.*`), then for the next one, and
so on until DOS says there are no more. Each answer is a record that DOS writes into the program's disk transfer
area (the DTA). The record is 43 bytes:

    00h  21 bytes   DOS keeps its own place in the search here. We put a number in the first two, which says which
                    search it is, and 0 in the rest; the search itself is kept by us (see Searches)
    15h  1 byte     the attributes of the file
    16h  a word     the time of the last change: hours (5 bits), minutes (6), seconds divided by 2 (5)
    18h  a word     the date of the last change: years since 1980 (7 bits), the month (4), the day (5)
    1Ah  a dword    the size in bytes
    1Eh  13 bytes   the name, in capitals, as NAME.EXT, with a 0 after it

The mask is matched the way DOS does it with a name of 8 characters and an extension of 3: `?` is any one character
(or none, at the end), `*` is any number of them up to the end of the name or of the extension. A mask without a
dot that ends with `*` stands for any extension too, so that `*` is the same as `*.*` and `AB*` finds `AB.TXT`.
"""
import time

from .filesystem import ATTR_DIRECTORY, ATTR_HIDDEN, ATTR_SYSTEM, ATTR_VOLUME

RECORD_SIZE = 43
MAX_SEARCHES = 64  # how many searches are kept at once; the oldest is forgotten when a new one starts
ASKED_FOR = ATTR_HIDDEN | ATTR_SYSTEM | ATTR_DIRECTORY  # files of these kinds only come when the program asks for them


def fits(pattern, text, width):
    """Does `text` fit `pattern`, both taken as `width` characters, with blanks after the shorter one?

    In the pattern `?` fits any character, a blank too; `*` makes all of the rest of the characters `?` and what
    follows it is not looked at. The two are compared character by character.
    """
    expanded = ""
    for character in pattern:
        if character == "*":
            expanded += "?" * (width - len(expanded))
            break
        expanded += character
    if len(expanded) > width or len(text) > width:
        return False
    return all(p == "?" or p == t for p, t in zip(expanded.ljust(width), text.ljust(width)))


def mask_matches(mask, name):
    """Does a file called `name` (as NAME.EXT) fit the mask? Capitals and small letters are the same."""
    mask, name = mask.upper(), name.upper()
    if "." in mask:
        pattern_name, _, pattern_extension = mask.partition(".")
    else:
        pattern_name, pattern_extension = mask, "*" if mask.endswith("*") else ""
    file_name, _, file_extension = name.partition(".")
    return fits(pattern_name, file_name, 8) and fits(pattern_extension, file_extension, 3)


def attributes_allow(attributes, asked):
    """May a file of these attributes come to a program that asked for the kinds in `asked` (the CX of the call)?

    Ordinary files always come, and so do read-only and archived ones. Hidden files, system files and directories
    come only if asked for. A search for the volume label (the label bit alone) finds nothing here: no drive of
    ours has one.
    """
    if asked & ATTR_VOLUME and not asked & 0x37:
        return False
    return not attributes & ASKED_FOR & ~asked


def find(entries, mask, asked):
    """The entries of a directory that fit the mask and may come, in the order of the directory."""
    return [entry for entry in entries if mask_matches(mask, entry.name) and attributes_allow(entry.attributes, asked)]


def record(number, entry):
    """The 43 bytes that tell a program about a file: see the top of this file. `number` is the search it is of."""
    when = time.localtime(entry.mtime)
    year = min(max(when.tm_year, 1980), 2107)
    stamp = (when.tm_hour << 11 | when.tm_min << 5 | min(when.tm_sec, 59) // 2) if when.tm_year >= 1980 else 0
    date = (year - 1980) << 9 | when.tm_mon << 5 | when.tm_mday if when.tm_year >= 1980 else 1 << 5 | 1
    data = bytearray(RECORD_SIZE)
    data[0:2] = number.to_bytes(2, "little")
    data[0x15] = entry.attributes & 0xFF
    data[0x16:0x18] = stamp.to_bytes(2, "little")
    data[0x18:0x1A] = date.to_bytes(2, "little")
    data[0x1A:0x1E] = (entry.size & 0xFFFFFFFF).to_bytes(4, "little")
    name = entry.name.encode("ascii", "replace")[:12]
    data[0x1E:0x1E + len(name)] = name
    return bytes(data)


class Searches:
    """The searches that have begun and are not over: what each has still to give.

    A search is the list of entries that fitted when it began (what is added to the directory later is not in
    it), and a number that the program carries about in its DTA.
    """

    def __init__(self):
        self.rest = {}  # number -> the entries not yet given
        self.last = 0

    def start(self, entries):
        """Begin a search that will give these entries. Returns its number, which is never 0."""
        self.last = self.last % 0xFFFF + 1
        self.rest[self.last] = list(entries)
        while len(self.rest) > MAX_SEARCHES:
            del self.rest[next(iter(self.rest))]  # the oldest
        return self.last

    def take(self, number):
        """The next entry of search `number`, or None when it is over or was never begun (it is forgotten then)."""
        entries = self.rest.get(number)
        if not entries:
            self.rest.pop(number, None)
            return None
        entry = entries.pop(0)
        if not entries:
            del self.rest[number]
        return entry
