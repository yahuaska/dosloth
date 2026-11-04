import unittest

from dosloth import timer
from dosloth.cpu import CPU
from dosloth.timer import Timer


class Clock:
    """A pretend time, in timer ticks, that the test moves by hand."""

    def __init__(self):
        self.ticks = 0

    def __call__(self):
        return self.ticks


def make():
    clock = Clock()
    return Timer(clock), clock


def program(chip, channel, mode, reload, access=3):
    """What a program does to set a counter up: a control byte, then the reload value."""
    chip.write(0x43, channel << 6 | access << 4 | mode << 1)
    port = 0x40 + channel
    if access == 1:
        chip.write(port, reload & 0xFF)
    elif access == 2:
        chip.write(port, reload >> 8)
    else:
        chip.write(port, reload & 0xFF)
        chip.write(port, reload >> 8)


def latch_and_read(chip, channel=0):
    """The way esetup.exe reads a counter: latch it, then read the low and the high byte."""
    chip.write(0x43, channel << 6)
    low = chip.read(0x40 + channel)
    high = chip.read(0x40 + channel)
    return high << 8 | low


class TestCounting(unittest.TestCase):
    def test_mode_2_counts_down_to_1_and_starts_again(self):
        chip, clock = make()
        program(chip, 0, 2, 1000)
        for ticks, want in ((0, 1000), (1, 999), (500, 500), (999, 1), (1000, 1000), (1001, 999), (2999, 1)):
            clock.ticks = ticks
            self.assertEqual(latch_and_read(chip), want, f"tick {ticks}")

    def test_mode_3_counts_down_by_two_in_half_the_time(self):
        chip, clock = make()
        program(chip, 0, 3, 1000)
        for ticks, want in ((0, 1000), (1, 998), (2, 996), (499, 2), (500, 1000), (501, 998)):
            clock.ticks = ticks
            self.assertEqual(latch_and_read(chip), want, f"tick {ticks}")

    def test_mode_0_goes_on_through_zero(self):
        chip, clock = make()
        program(chip, 0, 0, 100)
        for ticks, want in ((0, 100), (99, 1), (100, 0), (101, 0xFFFF), (200, 0xFF9C)):
            clock.ticks = ticks
            self.assertEqual(latch_and_read(chip), want, f"tick {ticks}")

    def test_a_reload_of_zero_means_65536(self):
        chip, clock = make()
        program(chip, 0, 2, 0)
        for ticks, want in ((0, 0), (1, 0xFFFF), (65535, 1), (65536, 0)):  # 65536 shows as 0
            clock.ticks = ticks
            self.assertEqual(latch_and_read(chip), want, f"tick {ticks}")

    def test_the_state_the_bios_leaves_is_the_18_2_hz_system_clock(self):
        chip, clock = make()  # not programmed at all
        for ticks in (0, 1, 1000, 32767, 32768, 100000):
            clock.ticks = ticks
            self.assertEqual(latch_and_read(chip), (65536 - 2 * (ticks % 32768)) & 0xFFFF, f"tick {ticks}")

    def test_loading_a_counter_restarts_it_from_the_moment_of_the_load(self):
        chip, clock = make()
        clock.ticks = 5123  # not a multiple of the reload value, or a counter that never restarted would look right
        program(chip, 0, 2, 1000)
        self.assertEqual(latch_and_read(chip), 1000)
        clock.ticks = 5133
        self.assertEqual(latch_and_read(chip), 990)

    def test_the_three_counters_are_independent(self):
        chip, clock = make()
        program(chip, 0, 2, 1000)
        program(chip, 1, 2, 300)
        program(chip, 2, 3, 200)
        clock.ticks = 10
        self.assertEqual([latch_and_read(chip, c) for c in range(3)], [990, 290, 180])


class TestLatchAndReadAccess(unittest.TestCase):
    def test_a_latched_value_stays_put_while_time_goes_on(self):
        chip, clock = make()
        program(chip, 0, 2, 1000)
        clock.ticks = 100
        chip.write(0x43, 0x00)  # latch
        clock.ticks = 300
        self.assertEqual((chip.read(0x40), chip.read(0x40)), (900 & 0xFF, 900 >> 8))
        # once both bytes are read the counter is live again
        self.assertEqual(latch_and_read(chip), 700)

    def test_a_second_latch_does_not_replace_one_that_is_still_unread(self):
        chip, clock = make()
        program(chip, 0, 2, 1000)
        clock.ticks = 100
        chip.write(0x43, 0x00)
        clock.ticks = 400
        chip.write(0x43, 0x00)  # ignored: the first one has not been read yet
        self.assertEqual(chip.read(0x40) | chip.read(0x40) << 8, 900)

    def test_an_unlatched_counter_is_read_live(self):
        chip, clock = make()
        program(chip, 0, 2, 1000)
        clock.ticks = 100
        low = chip.read(0x40)
        clock.ticks = 300  # time passes between the two reads, so the halves do not fit together
        high = chip.read(0x40)
        self.assertEqual((low, high), (900 & 0xFF, 700 >> 8))

    def test_low_byte_only_access(self):
        chip, clock = make()
        program(chip, 0, 2, 200, access=1)
        clock.ticks = 5
        self.assertEqual([chip.read(0x40), chip.read(0x40)], [195, 195])  # always the low byte
        chip.write(0x43, 0x00)
        self.assertEqual(chip.read(0x40), 195)  # a latch gives just that one byte

    def test_high_byte_only_access(self):
        chip, clock = make()
        program(chip, 0, 2, 0x4000, access=2)  # reload = 0x4000
        clock.ticks = 0
        self.assertEqual(chip.read(0x40), 0x40)
        chip.write(0x43, 0x00)
        self.assertEqual(chip.read(0x40), 0x40)

    def test_the_low_byte_alone_does_not_load_a_counter_in_low_high_mode(self):
        chip, clock = make()
        program(chip, 0, 2, 1000)
        chip.write(0x43, 0x34)  # counter 0, low then high, mode 2
        chip.write(0x40, 0x10)  # only the low byte so far: nothing changes
        self.assertEqual(latch_and_read(chip), 1000)
        chip.write(0x40, 0x00)  # the high byte completes it: reload = 0x0010
        self.assertEqual(latch_and_read(chip), 16)

    def test_the_control_byte_chooses_the_counter_by_its_top_bits(self):
        chip, clock = make()
        program(chip, 2, 2, 777)
        chip.write(0x43, 0x80)  # latch counter 2
        self.assertEqual(chip.read(0x42) | chip.read(0x42) << 8, 777)


class TestWhatIsNotDone(unittest.TestCase):
    def test_these_stop_the_emulator_instead_of_being_ignored(self):
        chip, clock = make()
        for control, what in ((0xC2, "read-back"), (0x31, "BCD"), (0x32, "mode 1"), (0x38, "mode 4"), (0x3A, "mode 5")):
            with self.assertRaises(NotImplementedError, msg=what):
                chip.write(0x43, control)

    def test_modes_6_and_7_are_modes_2_and_3(self):
        chip, clock = make()
        chip.write(0x43, 0x3C)  # counter 0, low then high, "mode 6"
        self.assertEqual(chip.counters[0].mode, 2)
        chip.write(0x43, 0x3E)
        self.assertEqual(chip.counters[0].mode, 3)


class TestOnTheCpu(unittest.TestCase):
    def test_the_cpu_counts_instructions_as_it_starts_them(self):
        cpu = CPU()
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10003] = bytes([0x90, 0x90, 0x90])
        for _ in range(3):
            cpu.step()
        self.assertEqual(cpu.instructions, 3)

    def test_install_gives_the_timer_for_whoever_wants_to_look_at_it(self):
        cpu = CPU()
        chip = timer.install(cpu)
        self.assertIsInstance(chip, timer.Timer)
        self.assertEqual(len(chip.counters), 3)
        cpu.instructions = 100
        self.assertEqual(chip.now(), int(100 * timer.TICKS_PER_INSTRUCTION))
        cpu.instructions = timer.INSTRUCTIONS_PER_SECOND
        self.assertEqual(chip.now(), timer.PIT_FREQUENCY)  # a second of instructions is a second of ticks

    def test_the_ports_are_wired_and_the_control_port_cannot_be_read(self):
        cpu = CPU()
        timer.install(cpu)
        self.assertEqual(sorted(cpu.port_readers), [0x40, 0x41, 0x42])
        self.assertEqual(sorted(cpu.port_writers), [0x40, 0x41, 0x42, 0x43])
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xE4, 0x43])  # in al, 43h
        with self.assertRaises(NotImplementedError):
            cpu.step()

    def test_the_sequence_from_esetup_reads_the_counter_at_the_moment_of_the_latch(self):
        """pushf ; cli ; mov al,0 ; out 43h,al ; in al,40h ; mov ah,al ; in al,40h ; xchg ah,al ; popf"""
        code = bytes([0x9C, 0xFA, 0xB0, 0x00, 0xE6, 0x43, 0xE4, 0x40, 0x8A, 0xE0, 0xE4, 0x40, 0x86, 0xE0, 0x9D])
        for ticks_per_instruction in (1, 4, 7):
            cpu = CPU()
            timer.install(cpu, ticks_per_instruction)
            cpu.s[CPU.CS] = cpu.s[CPU.SS] = 0x1000
            cpu.r[CPU.SP] = 0x8000
            cpu.mem[0x10000:0x10000 + len(code)] = code
            for _ in range(4):  # pushf, cli, mov al,0 and the OUT: the latch happens in the fourth
                cpu.step()
            time_of_the_latch = cpu.instructions * ticks_per_instruction
            for _ in range(5):  # in, mov, in, xchg, popf
                cpu.step()
            want = (65536 - 2 * (time_of_the_latch % 32768)) & 0xFFFF
            self.assertEqual(cpu.r[CPU.AX], want, f"{ticks_per_instruction} ticks per instruction")

    def test_two_runs_of_the_same_program_read_the_same_value(self):
        values = []
        for _ in range(2):
            cpu = CPU()
            timer.install(cpu)
            cpu.s[CPU.CS] = 0x1000
            cpu.mem[0x10000:0x10008] = bytes([0xB0, 0x00, 0xE6, 0x43, 0xE4, 0x40, 0xE4, 0x40])
            for _ in range(4):
                cpu.step()
            values.append(cpu.r[CPU.AX])
        self.assertEqual(values[0], values[1])


class Lines:
    """Pretend controllers: they only count the requests of the lines."""

    def __init__(self):
        self.raised = []

    def raise_line(self, line):
        self.raised.append(line)


class TestTheInterrupt(unittest.TestCase):
    def setUp(self):
        self.chip, self.clock = make()
        self.chip.ticks_per_instruction = 4
        self.lines = Lines()

    def test_the_line_is_raised_at_the_end_of_the_count_and_the_next_one_is_told(self):
        program(self.chip, 0, 2, 1000)
        self.assertEqual(self.chip.events(0, self.lines), 250)  # 1000 ticks, 4 a instruction
        self.assertEqual(self.lines.raised, [])
        self.clock.ticks = 1000
        self.assertEqual(self.chip.events(250, self.lines), 500)
        self.assertEqual(self.lines.raised, [0])

    def test_the_end_of_the_count_that_is_not_a_whole_instruction_is_at_the_next_instruction(self):
        program(self.chip, 0, 2, 1001)
        self.assertEqual(self.chip.events(0, self.lines), 251)

    def test_a_few_ends_that_went_by_make_one_request(self):
        program(self.chip, 0, 3, 100)
        self.clock.ticks = 450
        self.chip.events(112, self.lines)
        self.assertEqual(self.lines.raised, [0])
        self.chip.events(112, self.lines)
        self.assertEqual(self.lines.raised, [0])  # asked once, not again before the next end
        self.assertEqual(self.chip.counters[0].fired, 4)

    def test_the_reload_of_0_is_65536_the_tick_of_18_2_hz(self):
        self.assertEqual(self.chip.events(0, self.lines), 65536 // 4)

    def test_a_counter_of_mode_0_counts_once(self):
        program(self.chip, 0, 0, 100)
        self.clock.ticks = 100
        self.assertIsNone(self.chip.events(25, self.lines))
        self.assertEqual(self.lines.raised, [0])
        self.clock.ticks = 500
        self.assertIsNone(self.chip.events(125, self.lines))
        self.assertEqual(self.lines.raised, [0])

    def test_setting_the_counter_again_starts_the_count_and_says_so(self):
        called = []
        self.chip.on_change = lambda: called.append(1)
        program(self.chip, 0, 2, 1000)
        self.assertEqual(len(called), 2)  # the mode, and the count
        self.clock.ticks = 900
        program(self.chip, 0, 2, 1000)
        self.assertEqual(self.chip.events(225, self.lines), 475)  # a count from here, not from the old start

    def test_the_machine_raises_the_timer_line_to_a_program_that_took_the_interrupt(self):
        from dosloth import pic
        from dosloth.flags import IF
        cpu = CPU()
        pics = pic.install(cpu)
        chip = timer.install(cpu)
        chip.on_change = cpu.wake
        cpu.event_sources.append(lambda now: chip.events(now, pics))
        cpu.wake()
        cpu.mem[0x08 * 4:0x08 * 4 + 4] = (0x2000 << 16).to_bytes(4, "little")  # a handler at 2000:0000
        cpu.mem[0x20000:0x20002] = b"\x90\xCF"
        cpu.mem[0x10000:0x10000 + 0x6000] = b"\x90" * 0x6000
        cpu.s[CPU.CS] = 0x1000
        cpu.s[CPU.SS] = 0x3000
        cpu.r[CPU.SP] = 0x1000
        cpu.ip = 0
        cpu.flags |= IF
        chip.write(0x43, 0x34)  # counter 0, both bytes, mode 2
        chip.write(0x40, 0xE8)
        chip.write(0x40, 0x03)  # 1000 ticks: 41,900 instructions
        entered = None
        for _ in range(50000):
            cpu.step()
            if cpu.s[CPU.CS] == 0x2000:
                entered = cpu.instructions
                break
        self.assertIsNotNone(entered)
        due = -(-1000 // timer.TICKS_PER_INSTRUCTION)  # the instruction at which 1000 ticks have gone
        self.assertEqual(entered, due + 1)  # taken when that many are done, before the next


if __name__ == "__main__":
    unittest.main()
