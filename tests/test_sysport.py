import unittest

from dosloth import sysport, timer
from dosloth.cpu import CPU, Exit


class Clock:
    """A time that the test sets: the timer counts in the ticks it says."""

    def __init__(self):
        self.ticks = 0

    def __call__(self):
        return self.ticks


class PortTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.chip = timer.Timer(self.clock)
        self.port = sysport.SystemPort(self.chip)

    def program_counter_2(self, control, reload):
        """What `out 43h,control ; out 42h,low ; out 42h,high` does, at the time of the clock."""
        self.chip.write(0x43, control)
        self.chip.write(0x42, reload & 0xFF)
        self.chip.write(0x42, reload >> 8)


class TestWhatIsWritten(PortTest):
    def test_the_four_low_bits_are_kept_and_read_back_whatever_the_rest_of_the_byte_is(self):
        for byte in range(256):
            self.port.write(0x61, byte)
            self.assertEqual(self.port.read(0x61) & 0x0F, byte & 0x0F, byte)

    def test_the_bits_that_a_program_does_not_own_are_not_taken_from_what_it_writes(self):
        self.port.write(0x61, 0xFF)
        self.assertEqual(self.port.value, 0x0F)
        self.assertEqual(self.port.read(0x61) & 0xC0, 0)  # the two error bits are always off

    def test_the_port_starts_with_the_speaker_off(self):
        self.assertEqual(self.port.read(0x61) & 0x03, 0)

    def test_a_write_replaces_what_was_there(self):
        self.port.write(0x61, 0x0F)
        self.port.write(0x61, 0x01)
        self.assertEqual(self.port.value, 0x01)


class TestTheRefreshBit(PortTest):
    def test_it_toggles_every_18_ticks_exactly(self):
        for ticks in range(18 * 7):
            self.clock.ticks = ticks
            expected = 0x10 if (ticks // 18) % 2 else 0
            self.assertEqual(self.port.read(0x61) & 0x10, expected, ticks)

    def test_it_is_off_at_the_start_and_on_after_the_first_18_ticks(self):
        self.clock.ticks = 17
        self.assertEqual(self.port.read(0x61) & 0x10, 0)
        self.clock.ticks = 18
        self.assertEqual(self.port.read(0x61) & 0x10, 0x10)
        self.clock.ticks = 36
        self.assertEqual(self.port.read(0x61) & 0x10, 0)

    def test_it_does_not_depend_on_what_was_written(self):
        for byte in (0x00, 0x0F, 0xFF, 0x10):
            self.port.write(0x61, byte)
            self.clock.ticks = 18
            self.assertEqual(self.port.read(0x61) & 0x10, 0x10, byte)
            self.clock.ticks = 0
            self.assertEqual(self.port.read(0x61) & 0x10, 0, byte)


class TestTheOutputOfCounter2(PortTest):
    def test_a_square_wave_is_high_for_the_first_half_of_a_period_and_low_for_the_second(self):
        self.program_counter_2(0xB6, 1000)  # counter 2, both bytes, mode 3
        self.port.write(0x61, 0x03)  # the gate open and the speaker on
        for ticks, high in ((0, 1), (1, 1), (249, 1), (250, 0), (499, 0), (500, 1), (749, 1), (750, 0)):
            self.clock.ticks = ticks
            self.assertEqual(bool(self.port.read(0x61) & 0x20), bool(high), ticks)

    def test_with_the_gate_closed_the_output_stays_high(self):
        self.program_counter_2(0xB6, 1000)
        self.port.write(0x61, 0x02)
        for ticks in (0, 250, 499):
            self.clock.ticks = ticks
            self.assertTrue(self.port.read(0x61) & 0x20, ticks)

    def test_in_mode_0_it_is_low_until_the_count_is_done(self):
        self.program_counter_2(0xB0, 100)
        self.port.write(0x61, 0x01)
        for ticks, high in ((0, 0), (99, 0), (100, 1), (5000, 1)):
            self.clock.ticks = ticks
            self.assertEqual(bool(self.port.read(0x61) & 0x20), bool(high), ticks)

    def test_in_mode_2_it_is_high(self):
        self.program_counter_2(0xB4, 100)
        self.port.write(0x61, 0x01)
        for ticks in (0, 50, 99, 100):
            self.clock.ticks = ticks
            self.assertTrue(self.port.read(0x61) & 0x20, ticks)

    def test_the_time_of_the_count_is_from_when_the_counter_was_loaded(self):
        self.clock.ticks = 400
        self.program_counter_2(0xB0, 100)
        self.port.write(0x61, 0x01)
        self.clock.ticks = 450
        self.assertFalse(self.port.read(0x61) & 0x20)
        self.clock.ticks = 500
        self.assertTrue(self.port.read(0x61) & 0x20)


class TestOnTheCpu(unittest.TestCase):
    def machine(self, code):
        cpu = CPU()
        self.chip = timer.install(cpu)
        self.port = sysport.install(cpu, self.chip)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10000 + len(code)] = code
        return cpu

    def run_to_hlt(self, cpu, limit=2000):
        for _ in range(limit):
            try:
                cpu.step()
            except Exit:
                return cpu
        self.fail("the program did not end")

    def test_the_port_is_wired_for_reading_and_writing(self):
        cpu = self.machine(bytes([0xB0, 0x03, 0xE6, 0x61, 0xE4, 0x61, 0xF4]))  # mov al,3 ; out 61h,al ; in al,61h
        self.run_to_hlt(cpu)
        self.assertEqual(cpu.get8(0) & 0x0F, 0x03)

    def test_a_program_that_waits_on_the_refresh_bit_does_not_wait_for_ever(self):
        # in al,61h ; and al,10h ; mov bl,al ; again: in al,61h ; and al,10h ; cmp al,bl ; je again ; hlt
        code = bytes([0xE4, 0x61, 0x24, 0x10, 0x88, 0xC3, 0xE4, 0x61, 0x24, 0x10, 0x38, 0xD8, 0x74, 0xF8, 0xF4])
        cpu = self.machine(code)
        self.run_to_hlt(cpu, limit=5000)
        self.assertNotEqual(cpu.get8(0), cpu.get8(3))
        # the time a toggle takes: 18 ticks, which are 754 instructions, so a few passes of the loop of 5 instructions
        toggle = sysport.REFRESH_TICKS / timer.TICKS_PER_INSTRUCTION
        self.assertLess(cpu.instructions, toggle + 20)

    def test_a_program_that_waits_for_ten_toggles_waits_ten_times_as_long(self):
        # mov cx,10 ; mov dl,0 ; again: in al,61h ; and al,10h ; cmp al,dl ; je again ; mov dl,al ; loop again ; hlt
        code = bytes([0xB9, 0x0A, 0x00, 0xB2, 0x00, 0xE4, 0x61, 0x24, 0x10, 0x38, 0xD0, 0x74, 0xF8, 0x88, 0xC2,
                      0xE2, 0xF4, 0xF4])
        cpu = self.machine(code)
        self.run_to_hlt(cpu, limit=50000)
        ticks = cpu.instructions * timer.TICKS_PER_INSTRUCTION
        self.assertGreaterEqual(ticks, 9 * sysport.REFRESH_TICKS)  # ten changes of the bit take nine of its periods

    def test_the_sound_routine_of_the_installer_turns_the_speaker_on_and_programs_counter_2(self):
        # in al,61h ; test al,3 ; jne skip ; or al,3 ; out 61h,al ; mov al,B6h ; out 43h,al
        # skip: mov al,bl ; out 42h,al ; mov al,bh ; out 42h,al ; hlt
        code = bytes([0xE4, 0x61, 0xA8, 0x03, 0x75, 0x08, 0x0C, 0x03, 0xE6, 0x61, 0xB0, 0xB6, 0xE6, 0x43,
                      0x8A, 0xC3, 0xE6, 0x42, 0x8A, 0xC7, 0xE6, 0x42, 0xF4])
        cpu = self.machine(code)
        cpu.r[CPU.BX] = 0x0A97  # 1193180 / 440 Hz
        self.run_to_hlt(cpu)
        self.assertEqual(self.port.value & 0x03, 0x03)
        counter = self.chip.counters[2]
        self.assertEqual((counter.reload, counter.mode, counter.access), (0x0A97, 3, 3))

    def test_the_sound_routine_does_not_set_the_mode_again_for_a_speaker_that_is_on(self):
        code = bytes([0xE4, 0x61, 0xA8, 0x03, 0x75, 0x08, 0x0C, 0x03, 0xE6, 0x61, 0xB0, 0xB6, 0xE6, 0x43,
                      0x8A, 0xC3, 0xE6, 0x42, 0x8A, 0xC7, 0xE6, 0x42, 0xF4])
        cpu = self.machine(code)
        self.port.write(0x61, 0x01)  # on already
        cpu.r[CPU.BX] = 0x0500
        self.run_to_hlt(cpu)
        self.assertEqual(self.chip.counters[2].reload, 0x0500)
        self.assertEqual(self.port.value, 0x01)  # untouched: it only reads the port in that case

    def test_nosound_takes_the_speaker_off_and_leaves_the_other_bits(self):
        # in al,61h ; and al,0FCh ; out 61h,al ; hlt
        cpu = self.machine(bytes([0xE4, 0x61, 0x24, 0xFC, 0xE6, 0x61, 0xF4]))
        self.port.write(0x61, 0x0F)
        self.run_to_hlt(cpu)
        self.assertEqual(self.port.value, 0x0C)

    def test_install_puts_the_port_in_the_tables_of_the_cpu(self):
        cpu = CPU()
        chip = timer.install(cpu)
        port = sysport.install(cpu, chip)
        self.assertEqual(cpu.port_readers[0x61], port.read)
        self.assertEqual(cpu.port_writers[0x61], port.write)
        self.assertEqual(sorted(set(cpu.port_readers) & {0x60, 0x61, 0x62}), [0x61])


if __name__ == "__main__":
    unittest.main()
