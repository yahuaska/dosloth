import unittest

from mydos import soundblaster
from mydos.cpu import CPU
from mydos.soundblaster import BASE, SoundBlaster


class DspTest(unittest.TestCase):
    def setUp(self):
        self.cpu = CPU()
        self.card = soundblaster.install(self.cpu)

    def out(self, port, value):
        self.cpu.port_out(port, value)

    def get(self, port):
        return self.cpu.port_in(port)

    def reset(self):
        self.out(BASE + 6, 1)
        self.out(BASE + 6, 0)

    def ask(self, command):
        self.out(BASE + 0xC, command)


class TestTheDsp(DspTest):
    def test_a_reset_is_answered_with_aa(self):
        self.assertEqual(self.get(BASE + 0xE), 0x00)  # nothing to read
        self.reset()
        self.assertEqual(self.get(BASE + 0xE), 0x80)
        self.assertEqual(self.get(BASE + 0xA), 0xAA)
        self.assertEqual(self.get(BASE + 0xE), 0x00)

    def test_a_zero_without_a_one_is_no_reset(self):
        self.out(BASE + 6, 0)
        self.assertEqual(self.get(BASE + 0xE), 0x00)

    def test_nothing_to_read_gives_ff(self):
        self.assertEqual(self.get(BASE + 0xA), 0xFF)

    def test_the_card_is_ready_for_a_command(self):
        self.assertEqual(self.get(BASE + 0xC) & 0x80, 0)

    def test_the_version_is_4_05(self):
        self.ask(0xE1)
        self.assertEqual([self.get(BASE + 0xA), self.get(BASE + 0xA)], [4, 5])

    def test_the_echo_gives_the_byte_back_turned(self):
        self.ask(0xE0)
        self.ask(0x5A)
        self.assertEqual(self.get(BASE + 0xA), 0xA5)

    def test_a_reset_forgets_an_echo_that_waits_and_the_answers(self):
        self.ask(0xE1)
        self.ask(0xE0)
        self.reset()
        self.ask(0xE1)
        self.assertEqual([self.get(BASE + 0xA) for _ in range(3)], [0xAA, 4, 5])

    def test_the_speaker_is_on_and_off(self):
        self.ask(0xD1)
        self.assertTrue(self.card.speaker)
        self.ask(0xD3)
        self.assertFalse(self.card.speaker)

    def test_another_command_stops_the_emulator(self):
        with self.assertRaisesRegex(NotImplementedError, "no command 14"):
            self.ask(0x14)


class TestTheMixer(DspTest):
    def test_a_register_keeps_what_it_is_given(self):
        self.out(BASE + 4, 0x22)
        self.out(BASE + 5, 0xCC)
        self.out(BASE + 4, 0x3F)
        self.out(BASE + 5, 0xC0)
        self.out(BASE + 4, 0x22)
        self.assertEqual(self.get(BASE + 5), 0xCC)
        self.out(BASE + 4, 0x3F)
        self.assertEqual(self.get(BASE + 5), 0xC0)


class TestTheOtherPlaces(DspTest):
    def test_where_nothing_is_a_read_gives_ff_and_a_write_is_lost(self):
        for base in (0x210, 0x230, 0x240, 0x250, 0x260):
            self.out(base + 6, 1)
            self.out(base + 6, 0)
            self.out(base + 4, 0x22)
            self.assertEqual(self.get(base + 0xE), 0xFF)
            self.assertEqual(self.get(base + 0xA), 0xFF)
        self.assertEqual(self.get(BASE + 0xE), 0x00)  # the card at 220h heard nothing of it

    def test_a_port_beyond_the_places_is_still_unknown(self):
        with self.assertRaises(NotImplementedError):
            self.get(0x270)
        with self.assertRaises(NotImplementedError):
            self.get(0x20F)


class TestTheState(unittest.TestCase):
    def test_all_of_it_is_numbers_and_lists(self):
        import json
        json.dumps(vars(SoundBlaster()))


if __name__ == "__main__":
    unittest.main()
