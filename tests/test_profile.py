"""The count of the instructions by address, the places it gathers, and what the report says."""
import unittest

from mydos.cpu import CPU
from mydos.profile import GAP, Profile
from tests.test_breakpoints import machine

# 0000 mov cx,5 / 0003 nop / 0004 loop 0003 / 0006 mov ax,4C07h / 0009 int 21h : the nop and the loop run five times
LOOP = bytes.fromhex("b9 05 00 90 e2 fd b8 07 4c cd 21")


def profiled(code=LOOP, other=None):
    cpu = machine(code)
    profile = Profile()
    cpu.trace = profile.watching(other)
    cpu.run()
    return cpu, profile


class TestCount(unittest.TestCase):
    def test_every_address_is_counted_as_often_as_it_ran(self):
        cpu, profile = profiled()
        counts = {key & 0xFFFFFFFF: count for key, count in profile.counts.items()}
        self.assertEqual(counts, {0: 1, 3: 5, 4: 5, 6: 1, 9: 1})
        self.assertEqual(sum(profile.counts.values()), cpu.instructions)
        self.assertEqual({key >> 32 for key in profile.counts}, {0x1000})

    def test_the_hook_that_was_there_still_runs_after_the_count(self):
        seen = []
        cpu, profile = profiled(other=lambda cpu: seen.append(cpu.ip))
        self.assertEqual(len(seen), cpu.instructions)
        self.assertEqual(seen[:3], [0, 3, 4])

    def test_the_same_offset_in_another_segment_is_another_address(self):
        profile = Profile()
        cpu = machine(bytes.fromhex("90 90"))
        cpu.trace = profile.watching()
        cpu.step()
        cpu.s[CPU.CS] = 0x2000
        cpu.ip = 0
        cpu.step()
        self.assertEqual(sorted(profile.counts), [0x1000 << 32, 0x2000 << 32])


class TestPlaces(unittest.TestCase):
    def test_addresses_that_are_close_are_one_place_and_far_ones_are_not(self):
        profile = Profile()
        for offset in (0x100, 0x104, 0x120, 0x100 + GAP + 0x20 + 1, 0x400):
            profile.counts[0x0F << 32 | offset] += 1
        places = [[key & 0xFFFFFFFF for key, _ in place] for place in profile.places()]
        self.assertEqual(places, [[0x100, 0x104, 0x120], [0x141], [0x400]])

    def test_the_gap_itself_is_still_one_place(self):
        profile = Profile()
        profile.counts[0x0F << 32 | 0x100] += 1
        profile.counts[0x0F << 32 | 0x100 + GAP] += 1
        self.assertEqual(len(profile.places()), 1)
        profile.counts[0x0F << 32 | 0x100 + 2 * GAP + 1] += 1
        self.assertEqual(len(profile.places()), 2)

    def test_another_segment_is_another_place_though_the_offset_is_near(self):
        profile = Profile()
        profile.counts[0x1000 << 32 | 5] += 1
        profile.counts[0x1001 << 32 | 5] += 1
        self.assertEqual(len(profile.places()), 2)


class TestReport(unittest.TestCase):
    def test_the_total_and_the_hottest_place_come_first(self):
        cpu, profile = profiled()
        lines = profile.report(cpu).split("\n")
        self.assertEqual(lines[0], "instructions executed: 13 at 5 different addresses")
        self.assertIn("1000:0000 - 1000:0009", lines[2])
        self.assertIn(" 13 ", lines[2])
        self.assertIn("100.0%", lines[2])

    def test_the_instructions_of_a_place_are_listed_hottest_first_with_their_text(self):
        cpu, profile = profiled()
        text = profile.report(cpu)
        listing = text[text.index("place 1:"):].split("\n")[1:6]
        self.assertEqual([line.split()[0] for line in listing], ["5", "5", "1", "1", "1"])
        self.assertTrue(listing[0].endswith("1000:0003  nop"))  # of two that ran as often, the first address first
        self.assertIn("loop", listing[1])

    def test_the_hottest_place_is_the_first_whatever_the_order_of_addresses(self):
        profile = Profile()
        cpu = machine()
        profile.counts[0x1000 << 32 | 0x10] = 3
        profile.counts[0x1000 << 32 | 0x400] = 50
        lines = profile.report(cpu).split("\n")
        self.assertIn("1000:0400 - 1000:0400", lines[2])
        self.assertIn("1000:0010 - 1000:0010", lines[3])

    def test_offsets_are_8_digits_in_the_flat_mode(self):
        cpu = machine()
        cpu.ip_mask = 0xFFFFFFFF
        profile = Profile()
        profile.counts[0x0F << 32 | 0x10E04B] = 2
        self.assertIn("000F:0010E04B", profile.report(cpu))


if __name__ == "__main__":
    unittest.main()
