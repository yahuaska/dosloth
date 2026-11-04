import unittest

from dosloth import keys
from dosloth.keys import to_bios


class TestNamedKeys(unittest.TestCase):
    def test_the_keys_a_setup_program_asks_for(self):
        for name, char, expected in (("Return", "\r", (0x1C, 0x0D)), ("Escape", "\x1b", (0x01, 0x1B)),
                                     ("space", " ", (0x39, 0x20)), ("BackSpace", "\b", (0x0E, 0x08)),
                                     ("Tab", "\t", (0x0F, 0x09)), ("KP_Enter", "\r", (0x1C, 0x0D))):
            self.assertEqual(to_bios(name, char), expected, name)

    def test_the_keys_that_type_nothing_have_a_zero_ascii_code(self):
        for name, scan in (("Up", 0x48), ("Down", 0x50), ("Left", 0x4B), ("Right", 0x4D), ("Home", 0x47),
                           ("End", 0x4F), ("Prior", 0x49), ("Next", 0x51), ("Insert", 0x52), ("Delete", 0x53)):
            self.assertEqual(to_bios(name, ""), (scan, 0), name)

    def test_the_function_keys(self):
        for number in range(1, 11):
            self.assertEqual(to_bios(f"F{number}", ""), (0x3A + number, 0), f"F{number}")
        self.assertIsNone(to_bios("F11", ""))

    def test_a_named_key_wins_over_the_character_it_says(self):
        self.assertEqual(to_bios("Return", "\n"), (0x1C, 0x0D))


class TestCharacters(unittest.TestCase):
    def test_letters_have_the_scan_codes_of_the_keyboard_rows(self):
        for row, first in (("qwertyuiop", 0x10), ("asdfghjkl", 0x1E), ("zxcvbnm", 0x2C)):
            for number, letter in enumerate(row):
                self.assertEqual(to_bios(letter, letter), (first + number, ord(letter)), letter)

    def test_a_capital_letter_is_the_same_key_with_another_ascii_code(self):
        self.assertEqual(to_bios("a", "a"), (0x1E, 0x61))
        self.assertEqual(to_bios("A", "A"), (0x1E, 0x41))
        self.assertEqual(to_bios("Z", "Z"), (0x2C, 0x5A))

    def test_digits_and_the_signs_over_them(self):
        for number, digit in enumerate("1234567890"):
            self.assertEqual(to_bios(digit, digit), (0x02 + number, ord(digit)), digit)
        for number, sign in enumerate("!@#$%^&*()"):
            self.assertEqual(to_bios("exclam", sign), (0x02 + number, ord(sign)), sign)

    def test_the_punctuation(self):
        for char, scan in (("-", 0x0C), ("=", 0x0D), ("[", 0x1A), ("]", 0x1B), (";", 0x27), ("'", 0x28), ("`", 0x29),
                           ("\\", 0x2B), (",", 0x33), (".", 0x34), ("/", 0x35), ("_", 0x0C), ("+", 0x0D),
                           ("{", 0x1A), ("}", 0x1B), (":", 0x27), ('"', 0x28), ("~", 0x29), ("|", 0x2B),
                           ("<", 0x33), (">", 0x34), ("?", 0x35)):
            self.assertEqual(to_bios("x", char), (scan, ord(char)), char)

    def test_ctrl_and_a_letter_gives_the_control_character(self):
        self.assertEqual(to_bios("c", "\x03"), (0x2E, 0x03))
        self.assertEqual(to_bios("a", "\x01"), (0x1E, 0x01))
        self.assertEqual(to_bios("z", "\x1a"), (0x2C, 0x1A))

    def test_every_printable_ascii_character_on_a_us_keyboard_has_a_key(self):
        for code in range(0x20, 0x7F):
            char = chr(code)
            result = to_bios("space" if char == " " else "x", char)
            self.assertIsNotNone(result, repr(char))
            self.assertEqual(result[1], code, repr(char))

    def test_the_scan_codes_are_all_different_except_with_and_without_shift(self):
        by_scan = {}
        for char, scan in keys.SCAN_OF_CHARACTER.items():
            by_scan.setdefault(scan, []).append(char)
        self.assertTrue(all(len(chars) == 2 for chars in by_scan.values()))
        self.assertEqual(len(by_scan), 47)  # the keys of the four rows


class TestWhatTheProgramDoesNotSee(unittest.TestCase):
    def test_keys_that_only_change_the_others_are_dropped(self):
        for name in ("Shift_L", "Shift_R", "Control_L", "Alt_L", "Caps_Lock", "Num_Lock", "Meta_L", "Super_L"):
            self.assertIsNone(to_bios(name, ""), name)

    def test_characters_that_are_not_on_a_us_keyboard_are_dropped(self):
        for char in ("é", "я", "€", "\x7f", "\x00"):
            self.assertIsNone(to_bios("x", char), repr(char))

    def test_a_control_character_that_is_not_ctrl_and_a_letter_is_dropped(self):
        self.assertIsNone(to_bios("bracketleft", "\x1d"))  # Ctrl+] is not a letter
        self.assertIsNone(to_bios("x", "\x1b"))
        self.assertIsNone(to_bios("Linefeed", "\n"))  # a control character from a key that is not a letter
        self.assertIsNone(to_bios("Pause", "\x13"))

    def test_two_characters_or_none_are_not_a_key_press(self):
        self.assertIsNone(to_bios("x", ""))
        self.assertIsNone(to_bios("x", "ab"))


if __name__ == "__main__":
    unittest.main()
