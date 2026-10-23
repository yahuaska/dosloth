"""The file control block of old DOS, as far as INT 21h AH=29h needs it: a file name taken apart.

An FCB keeps the drive in its first byte (0 is the current drive, 1 is A:), then the name in 8 bytes and
the extension in 3, each in capitals and padded with blanks. `*` stands for `?` to the end of its field.
"""

SIZE = 12  # the bytes of the FCB that a name goes into: the drive, the name and the extension
SEPARATORS = b":.;,=+"  # the characters that part the pieces of a command line
TERMINATORS = b' \t:.;,=+/\\"[]<>|'  # these end a name or an extension; so does every control character
BLANKS = b" \t"


def _is_terminator(byte):
    return byte < 0x20 or byte in TERMINATORS


def _field(data, at, length):
    """Read a name or an extension that starts at data[at]: its `length` bytes, where it ends, if it had `*` or `?`.

    What is more than `length` characters is passed over and lost. `*` fills the rest with `?`.
    """
    field = bytearray()
    wildcards = False
    while at < len(data) and not _is_terminator(data[at]):
        byte = data[at]
        if byte in b"*?":
            wildcards = True
        if byte == ord("*"):
            field += b"?" * (length - len(field))
        elif len(field) < length:
            field.append(byte - 32 if ord("a") <= byte <= ord("z") else byte)
        at += 1
    return bytes(field).ljust(length, b" "), at, wildcards


def parse(data, control, fcb, drive_exists):
    """Take a file name apart the way AH=29h does.

    `data` is the text from DS:SI, `control` the bits of AL (1: skip separators in front, 2: keep the drive of the
    FCB if the text has none, 4: keep the name if it has none, 8: keep the extension if it has none), `fcb` the 12
    bytes now in the FCB, `drive_exists` tells if a drive number (1 is A:) is there.

    Returns the 12 new bytes of the FCB, how many bytes of `data` were used, and the AL of DOS: 0 for a plain name,
    1 if it has `*` or `?`, and 0FFh if the drive is not there. A backslash ends the name, as in DOS 2: a path is
    not taken apart by this call.
    """
    at = 0
    if control & 1:  # blanks, one separator, and blanks again
        while at < len(data) and data[at] in BLANKS:
            at += 1
        if at < len(data) and data[at] in SEPARATORS:
            at += 1
        while at < len(data) and data[at] in BLANKS:
            at += 1
    drive = fcb[0] if control & 2 else 0
    invalid_drive = False
    if at + 1 < len(data) and data[at + 1] == ord(":") and (data[at] | 0x20) in range(ord("a"), ord("z") + 1):
        drive = (data[at] | 0x20) - ord("a") + 1
        invalid_drive = not drive_exists(drive)
        at += 2
    name_at = at
    name, at, name_wildcards = _field(data, at, 8)
    if at == name_at and control & 4:
        name = bytes(fcb[1:9])
    extension, wildcards = bytes(fcb[9:12]), name_wildcards
    if at < len(data) and data[at] == ord("."):
        extension_at = at + 1
        extension, at, extension_wildcards = _field(data, extension_at, 3)
        wildcards = name_wildcards or extension_wildcards
        if at == extension_at and control & 8:
            extension = bytes(fcb[9:12])
    elif not control & 8:
        extension = b"   "
    code = 0xFF if invalid_drive else int(wildcards)
    return bytes([drive]) + name + extension, at, code
