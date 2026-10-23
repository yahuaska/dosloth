import unittest

from mydos import dos, flags
from mydos.cpu import CPU
from mydos.memory import Blocks
from mydos.psp import PSP_SEG


def blocks():
    """The memory as a program finds it: its own block at 1000h up to the top at 2000h."""
    return Blocks(0x2000, 0x1000, 0x1000)


class TestBlocks(unittest.TestCase):
    def test_a_program_that_keeps_all_its_memory_leaves_nothing_to_allocate(self):
        memory = blocks()
        self.assertEqual(memory.largest(), 0)
        self.assertIsNone(memory.allocate(1))

    def test_after_the_program_shrinks_the_gap_is_what_it_gave_up(self):
        memory = blocks()
        self.assertTrue(memory.resize(0x1000, 0x200))
        self.assertEqual(memory.largest(), 0xE00)
        self.assertEqual(memory.allocate(0x100), 0x1200)
        self.assertEqual(memory.allocate(0x100), 0x1300)
        self.assertEqual(memory.largest(), 0xC00)

    def test_a_block_that_does_not_fit_is_not_given_and_nothing_changes(self):
        memory = blocks()
        memory.resize(0x1000, 0x200)
        self.assertIsNone(memory.allocate(0xE01))
        self.assertEqual(memory.allocate(0xE00), 0x1200)
        self.assertEqual(memory.largest(), 0)

    def test_a_freed_block_leaves_a_gap_that_the_first_fit_uses_again(self):
        memory = blocks()
        memory.resize(0x1000, 0x100)
        first, second, third = memory.allocate(0x100), memory.allocate(0x100), memory.allocate(0x100)
        self.assertEqual((first, second, third), (0x1100, 0x1200, 0x1300))
        self.assertTrue(memory.free(second))
        self.assertEqual(memory.largest(), 0xC00)  # the space after the last block is bigger than the hole
        self.assertEqual(memory.allocate(0x80), 0x1200)
        self.assertEqual(memory.allocate(0x80), 0x1280)
        self.assertEqual(memory.allocate(0x80), 0x1400)

    def test_the_first_gap_that_fits_is_taken_not_the_best(self):
        memory = blocks()
        memory.resize(0x1000, 0x100)
        a, b, c = memory.allocate(0x300), memory.allocate(0x10), memory.allocate(0x100)
        memory.free(a)
        memory.free(c)
        self.assertEqual(memory.allocate(0x10), 0x1100)

    def test_freeing_what_is_not_a_block_is_refused(self):
        memory = blocks()
        self.assertFalse(memory.free(0x1001))
        self.assertFalse(memory.free(0x1800))
        self.assertTrue(memory.free(0x1000))
        self.assertFalse(memory.free(0x1000))

    def test_a_block_can_grow_up_to_the_next_block(self):
        memory = blocks()
        memory.resize(0x1000, 0x100)
        first, second = memory.allocate(0x100), memory.allocate(0x100)
        self.assertEqual(memory.room_for(first), 0x100)
        self.assertFalse(memory.resize(first, 0x101))
        self.assertTrue(memory.resize(first, 0x100))
        self.assertTrue(memory.resize(first, 0x20))
        self.assertTrue(memory.resize(first, 0xE0))
        self.assertEqual(memory.room_for(second), 0xE00)
        self.assertTrue(memory.resize(second, 0xE00))
        self.assertEqual(memory.largest(), 0x20)  # what is left between the two blocks

    def test_resizing_what_is_not_a_block_says_so(self):
        self.assertIsNone(blocks().resize(0x1800, 1))

    def test_a_block_of_no_paragraphs_still_is_a_block_of_its_own(self):
        memory = blocks()
        memory.resize(0x1000, 0x100)
        first, second = memory.allocate(0), memory.allocate(0)
        self.assertEqual((first, second), (0x1100, 0x1101))
        self.assertTrue(memory.free(first))
        self.assertTrue(memory.free(second))

    def test_the_top_is_a_limit_for_growing_too(self):
        memory = blocks()
        self.assertFalse(memory.resize(0x1000, 0x1001))
        self.assertEqual(memory.room_for(0x1000), 0x1000)


def call(cpu, ah, bx=0x1111, es=0x3333):
    cpu.s[CPU.CS] = 0x0800
    cpu.mem[0x8000:0x8002] = bytes([0xCD, 0x21])
    cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ah << 8 | 0x77, bx, 0x2222, 0x3333
    cpu.s[CPU.ES] = es
    cpu.ip = 0
    cpu.step()
    return cpu


def failed(cpu):
    return bool(cpu.get_flag(flags.CF))


class TestMemoryFunctions(unittest.TestCase):
    def setUp(self):
        self.cpu = CPU()
        self.state = dos.install(self.cpu)

    def test_the_program_starts_with_all_the_memory_to_the_top(self):
        call(self.cpu, 0x48, 0xFFFF)
        self.assertTrue(failed(self.cpu))
        self.assertEqual(self.cpu.r[CPU.AX], 8)
        self.assertEqual(self.cpu.r[CPU.BX], 0)

    def test_shrinking_makes_room_and_asking_with_ffff_tells_how_much(self):
        call(self.cpu, 0x4A, 0x200, es=PSP_SEG)
        self.assertFalse(failed(self.cpu))
        call(self.cpu, 0x48, 0xFFFF)
        self.assertTrue(failed(self.cpu))
        self.assertEqual(self.cpu.r[CPU.AX], 8)
        self.assertEqual(self.cpu.r[CPU.BX], dos.MEMORY_TOP_SEG - PSP_SEG - 0x200)

    def test_allocating_gives_the_segment_in_ax_and_touches_nothing_else(self):
        call(self.cpu, 0x4A, 0x200, es=PSP_SEG)
        call(self.cpu, 0x48, 0x400)
        self.assertFalse(failed(self.cpu))
        self.assertEqual(self.cpu.r[CPU.AX], PSP_SEG + 0x200)
        self.assertEqual((self.cpu.r[CPU.BX], self.cpu.r[CPU.CX], self.cpu.r[CPU.DX]), (0x400, 0x2222, 0x3333))
        self.assertEqual(self.cpu.s[CPU.ES], 0x3333)

    def test_freeing_gives_the_block_back(self):
        call(self.cpu, 0x4A, 0x200, es=PSP_SEG)
        call(self.cpu, 0x48, 0x400)
        segment = self.cpu.r[CPU.AX]
        call(self.cpu, 0x49, es=segment)
        self.assertFalse(failed(self.cpu))
        call(self.cpu, 0x48, 0x400)
        self.assertEqual(self.cpu.r[CPU.AX], segment)

    def test_freeing_a_segment_that_is_no_block_is_error_9(self):
        call(self.cpu, 0x49, es=0x5000)
        self.assertTrue(failed(self.cpu))
        self.assertEqual(self.cpu.r[CPU.AX], 9)

    def test_resizing_a_segment_that_is_no_block_is_error_9(self):
        call(self.cpu, 0x4A, 0x10, es=0x5000)
        self.assertTrue(failed(self.cpu))
        self.assertEqual(self.cpu.r[CPU.AX], 9)

    def test_growing_into_the_next_block_is_error_8_with_the_room_there_is(self):
        call(self.cpu, 0x4A, 0x200, es=PSP_SEG)
        call(self.cpu, 0x48, 0x100)
        segment = self.cpu.r[CPU.AX]
        call(self.cpu, 0x48, 0x100)
        call(self.cpu, 0x4A, 0x180, es=segment)
        self.assertTrue(failed(self.cpu))
        self.assertEqual(self.cpu.r[CPU.AX], 8)
        self.assertEqual(self.cpu.r[CPU.BX], 0x100)

    def test_resizing_a_block_to_its_own_size_or_less_works(self):
        call(self.cpu, 0x4A, 0x200, es=PSP_SEG)
        call(self.cpu, 0x4A, 0x100, es=PSP_SEG)
        self.assertFalse(failed(self.cpu))
        call(self.cpu, 0x48, 0xFFFF)
        self.assertEqual(self.cpu.r[CPU.BX], dos.MEMORY_TOP_SEG - PSP_SEG - 0x100)

    def test_the_program_block_cannot_grow_past_the_top(self):
        call(self.cpu, 0x4A, dos.MEMORY_TOP_SEG - PSP_SEG + 1, es=PSP_SEG)
        self.assertTrue(failed(self.cpu))
        self.assertEqual(self.cpu.r[CPU.BX], dos.MEMORY_TOP_SEG - PSP_SEG)


class TestPspSegment(unittest.TestCase):
    def test_both_functions_give_the_segment_of_the_psp_in_bx_and_touch_nothing_else(self):
        for ah in (0x51, 0x62):
            cpu = CPU()
            dos.install(cpu)
            before = call(cpu, ah, bx=0x1111, es=0x3333).get_flag(flags.CF)
            self.assertEqual(cpu.r[CPU.BX], PSP_SEG, hex(ah))
            self.assertEqual(cpu.r[CPU.AX], ah << 8 | 0x77)
            self.assertEqual((cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.s[CPU.ES]), (0x2222, 0x3333, 0x3333))
            self.assertEqual(before, 0)

    def test_the_carry_flag_is_left_as_it_was(self):
        for carry in (False, True):
            cpu = CPU()
            dos.install(cpu)
            cpu.set_flag(flags.CF, carry)
            call(cpu, 0x62)
            self.assertEqual(bool(cpu.get_flag(flags.CF)), carry)


if __name__ == "__main__":
    unittest.main()
