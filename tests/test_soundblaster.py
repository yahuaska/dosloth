import unittest

from dosloth import soundblaster
from dosloth.cpu import CPU
from dosloth.soundblaster import BASE, SoundBlaster


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
        with self.assertRaisesRegex(NotImplementedError, "no command 15"):
            self.ask(0x15)


class TestTheInterrupt(unittest.TestCase):
    def setUp(self):
        from dosloth import pic
        self.cpu = CPU()
        self.pics = pic.install(self.cpu)
        self.card = soundblaster.install(self.cpu, self.pics)
        self.pics.master.mask = 0

    def test_the_command_f2_raises_line_7(self):
        self.cpu.port_out(BASE + 0xC, 0xF2)
        self.assertEqual(self.pics.pending(), 7)

    def test_a_read_of_the_status_takes_back_an_interrupt_that_was_not_taken(self):
        self.cpu.port_out(BASE + 0xC, 0xF2)
        self.cpu.port_in(BASE + 0xE)
        self.assertIsNone(self.pics.pending())

    def test_a_read_of_the_status_does_not_stop_an_interrupt_in_service(self):
        self.cpu.port_out(BASE + 0xC, 0xF2)
        self.pics.acknowledge()
        self.cpu.port_in(BASE + 0xE)
        self.assertEqual(self.pics.master.in_service, 0x80)

    def test_the_card_without_controllers_just_does_not_interrupt(self):
        card = SoundBlaster()
        card.write(BASE + 0xC, 0xF2)


class TestThePlayingByDma(unittest.TestCase):
    """The card on a clock that the test moves: a byte of the time constant 0 takes 256 microseconds (12,800 instructions)."""

    def setUp(self):
        from dosloth import dma, pic
        self.cpu = CPU()
        self.pics = pic.install(self.cpu)
        self.pics.master.mask = 0
        self.dma = dma.install(self.cpu)
        self.card = soundblaster.install(self.cpu, self.pics, self.dma)

    def ask(self, *bytes_):
        for byte in bytes_:
            self.cpu.port_out(BASE + 0xC, byte)

    def at(self, instructions):
        """Let the time be `instructions` and give the card its events; returns what it says is next."""
        self.cpu.instructions = instructions
        return self.card.events(instructions)

    def test_the_time_constant_is_kept_and_has_one_byte(self):
        self.ask(0x40, 0x64)
        self.assertEqual(self.card.time_constant, 0x64)
        self.assertEqual(self.card.waiting_for, 0)

    def test_the_rate_makes_the_time_constant(self):
        self.ask(0x41, 0x2B, 0x11)  # 11025 Hz, high byte first: 91 microseconds
        self.assertEqual(self.card.time_constant, 256 - 91)

    def test_a_block_size_is_the_bytes_less_one_and_the_playing_is_told_to_the_cpu(self):
        self.ask(0x48, 0xFF, 0x03)
        self.assertEqual(self.card.block_size, 0x400)
        self.cpu.next_event = 1 << 62
        self.ask(0x14, 0x10, 0x00)
        self.assertEqual(self.cpu.next_event, 0)  # woken: the time of its end is to be looked up

    def test_the_output_ends_when_its_time_has_gone_with_the_channel_at_its_end_and_the_interrupt(self):
        self.ask(0x40, 0x64)  # 156 microseconds a byte
        self.ask(0x14, 0x0F, 0x00)  # 16 bytes: 2496 microseconds: 124,800 instructions at 50 million a second
        self.assertEqual(self.card.due, 124800)
        self.assertEqual(self.at(124799), 124800)
        self.assertIsNone(self.pics.pending())
        self.assertIsNone(self.at(124800))
        self.assertEqual(self.pics.pending(), 7)
        self.assertEqual(self.cpu.port_in(0x08), 0x02)  # the channel 1 has reached the end
        self.assertFalse(self.card.playing)

    def test_the_repeated_output_raises_the_interrupt_for_every_block(self):
        self.ask(0x40, 0x00)
        self.ask(0x48, 0x0F, 0x00)  # blocks of 16 bytes: 4096 microseconds
        self.ask(0x1C)
        first = self.card.due
        self.assertEqual(self.at(first), first + first)
        self.assertEqual(self.pics.pending(), 7)
        self.pics.acknowledge()
        self.pics.end(7)
        self.assertEqual(self.at(2 * first), 3 * first)
        self.assertEqual(self.pics.pending(), 7)  # and again

    def test_the_fast_commands_play_the_block_once_and_repeatedly(self):
        self.ask(0x48, 0x0F, 0x00)
        self.ask(0x91)
        self.assertIsNone(self.at(self.card.due))
        self.assertEqual(self.pics.pending(), 7)
        self.ask(0x90)
        self.assertIsNotNone(self.at(self.card.due))
        self.assertTrue(self.card.repeating)

    def test_the_speaker_is_asked_about(self):
        self.ask(0xD8)
        self.ask(0xD1)
        self.ask(0xD8)
        self.assertEqual([self.cpu.port_in(BASE + 0xA) for _ in range(2)], [0x00, 0xFF])

    def test_the_playing_moves_the_registers_of_the_dma_channel(self):
        self.out = self.cpu.port_out
        self.out(0x0C, 0)
        for port, value in ((0x02, 0x00), (0x02, 0x20), (0x03, 0xFF), (0x03, 0x0F), (0x0B, 0x59)):  # a repeated 1000h
            self.out(port, value)
        self.ask(0x40, 0x00)  # a byte a 256 microseconds: 12,800 instructions
        self.ask(0x48, 0xFF, 0x07)
        self.cpu.instructions = 1000
        self.ask(0x1C)
        self.cpu.instructions = 1000 + 12800 * 100
        self.cpu.port_out(0x0C, 0)
        address = self.cpu.port_in(0x02) | self.cpu.port_in(0x02) << 8
        self.assertAlmostEqual(address, 0x2000 + 100, delta=2)
        self.ask(0xD0)  # a halt
        self.cpu.instructions += 10 ** 6
        self.cpu.port_out(0x0C, 0)
        self.assertEqual(self.cpu.port_in(0x02) | self.cpu.port_in(0x02) << 8, address)

    def test_the_card_is_on_the_channel_that_the_program_has_set_up(self):
        self.cpu.port_out(0x0B, 0x5B)  # the channel 3, to the device
        self.cpu.port_out(0x0A, 0x03)  # let through
        self.ask(0x14, 0x0F, 0x00)
        self.assertEqual(self.card.channel, 3)
        self.assertIsNotNone(self.dma.started[3])
        self.at(self.card.due)
        self.assertEqual(self.cpu.port_in(0x08), 0x08)  # the end of the channel 3

    def test_a_halt_stops_it_and_a_continue_goes_on(self):
        self.ask(0x14, 0x0F, 0x00)
        self.ask(0xD0)
        self.assertIsNone(self.at(10 ** 6))
        self.assertIsNone(self.pics.pending())
        self.ask(0xD4)
        self.assertIsNotNone(self.card.due)

    def test_the_end_of_the_repeating_lets_the_block_in_hand_end_and_that_is_all(self):
        self.ask(0x48, 0x0F, 0x00)
        self.ask(0x1C)
        self.ask(0xDA)
        self.assertIsNone(self.at(self.card.due))
        self.assertEqual(self.pics.pending(), 7)
        self.assertFalse(self.card.playing)

    def test_a_reset_forgets_the_arguments_that_came(self):
        self.ask(0x14, 0x10)
        self.cpu.port_out(BASE + 6, 1)
        self.cpu.port_out(BASE + 6, 0)
        self.ask(0xE1)
        self.assertEqual([self.cpu.port_in(BASE + 0xA) for _ in range(3)], [0xAA, 4, 5])

    def test_a_reset_stops_the_playing(self):
        self.ask(0x14, 0x10, 0x00)
        self.cpu.port_out(BASE + 6, 1)
        self.cpu.port_out(BASE + 6, 0)
        self.assertEqual((self.card.playing, self.card.due), (False, None))

    def test_the_state_has_no_controllers_clock_or_functions_in_it(self):
        import json
        state = self.card.state()
        self.assertFalse({"dma", "pics", "clock", "wake"} & set(state))
        json.dumps(state)


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
        json.dumps(SoundBlaster().state())


if __name__ == "__main__":
    unittest.main()
