"""From a key of the host to the two numbers the BIOS gives a program: the scan code and the ASCII code.

A program that asks INT 16h for a key gets one word: the scan code of the key in the high byte, the ASCII code in the
low byte. The scan code names the key on the keyboard of an IBM PC (the same for A and Shift+A), the ASCII code the
character it typed (not the same). Keys that type no character, the arrows and F1-F10 and the like, have 0 for it.

The keys are named here the way Tk names them (`Return`, `Left`, `F1`), together with the character it says the key
typed. The scan codes are those of a US keyboard; characters that are not on one are dropped, as are the keys that
only change what the others do (Shift, Ctrl, Alt, the locks) and the combinations with Alt.

A program that reads the keyboard itself (port 60h, see kbc.py) sees every key go down and up, those that change the
others too: `scan_code` gives the scan code of a key for that, and whether it is one of the grey block, which sends
E0h first.
"""

# the keys that do not type a character on their own, or whose character is a control character
NAMED_KEYS = {
    "Return": (0x1C, 0x0D), "KP_Enter": (0x1C, 0x0D), "Escape": (0x01, 0x1B), "BackSpace": (0x0E, 0x08),
    "Tab": (0x0F, 0x09), "space": (0x39, 0x20),
    "F1": (0x3B, 0), "F2": (0x3C, 0), "F3": (0x3D, 0), "F4": (0x3E, 0), "F5": (0x3F, 0),
    "F6": (0x40, 0), "F7": (0x41, 0), "F8": (0x42, 0), "F9": (0x43, 0), "F10": (0x44, 0),
    "Home": (0x47, 0), "Up": (0x48, 0), "Prior": (0x49, 0), "Left": (0x4B, 0), "Right": (0x4D, 0),
    "End": (0x4F, 0), "Down": (0x50, 0), "Next": (0x51, 0), "Insert": (0x52, 0), "Delete": (0x53, 0),
}

# the keys of the typewriter part, each with what it types without Shift and with it, in the order of the scan codes
ROWS = [
    (0x02, "1234567890-=", "!@#$%^&*()_+"),
    (0x10, "qwertyuiop[]", 'QWERTYUIOP{}'),
    (0x1E, "asdfghjkl;'`", 'ASDFGHJKL:"~'),
    (0x2B, "\\zxcvbnm,./", "|ZXCVBNM<>?"),
]


def _scan_codes():
    codes = {}
    for first, plain, shifted in ROWS:
        for number, (low, high) in enumerate(zip(plain, shifted)):
            scan = first + number
            codes[low] = codes[high] = scan
    return codes


SCAN_OF_CHARACTER = _scan_codes()
LETTERS = "abcdefghijklmnopqrstuvwxyz"


def to_bios(keysym, char):
    """The (scan code, ASCII code) of a key press, or None when the program is not to see it.

    `keysym` is the name Tk gives the key, `char` the character it typed (an empty string when none).
    """
    if keysym in NAMED_KEYS:
        return NAMED_KEYS[keysym]
    if len(char) != 1:
        return None
    code = ord(char)
    if char in SCAN_OF_CHARACTER:  # a letter, digit or sign, with or without Shift
        return SCAN_OF_CHARACTER[char], code
    if 1 <= code <= 26 and keysym.lower() in LETTERS and len(keysym) == 1:  # Ctrl+A is the character 1, and so on
        return SCAN_OF_CHARACTER[keysym.lower()], code
    return None


# the keys that type nothing and the BIOS does not give, but a program that reads the keyboard itself sees
MODIFIER_KEYS = {
    "Shift_L": 0x2A, "Shift_R": 0x36, "Control_L": 0x1D, "Alt_L": 0x38, "Option_L": 0x38, "Caps_Lock": 0x3A,
}
# the keys of the grey block: their codes come after E0h (the arrows and the rest share the codes of the keypad)
EXTENDED_KEYS = {
    "Control_R": 0x1D, "Alt_R": 0x38, "Option_R": 0x38, "KP_Enter": 0x1C,
    "Up": 0x48, "Down": 0x50, "Left": 0x4B, "Right": 0x4D,
    "Home": 0x47, "End": 0x4F, "Prior": 0x49, "Next": 0x51, "Insert": 0x52, "Delete": 0x53,
}


def scan_code(keysym, char):
    """(scan code, extended) of a key as the keyboard sends it, or None for a key that the PC has not."""
    if keysym in EXTENDED_KEYS:
        return EXTENDED_KEYS[keysym], True
    if keysym in MODIFIER_KEYS:
        return MODIFIER_KEYS[keysym], False
    key = to_bios(keysym, char)
    return None if key is None else (key[0], False)
