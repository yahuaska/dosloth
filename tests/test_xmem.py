import unittest

from mydos.cpu import MEMORY_SIZE
from mydos.xmem import PAGE, START, ExtendedMemory


class TestExtendedMemory(unittest.TestCase):
    def test_it_begins_above_the_first_megabyte_and_ends_with_the_memory_of_the_machine(self):
        extended = ExtendedMemory()
        self.assertEqual((extended.start, extended.end, extended.blocks), (0x100000, MEMORY_SIZE, {}))
        self.assertEqual(START, 0x100000)

    def test_blocks_are_given_out_one_after_another(self):
        extended = ExtendedMemory()
        self.assertEqual(extended.allocate(PAGE), START)
        self.assertEqual(extended.allocate(3 * PAGE), START + PAGE)
        self.assertEqual(extended.blocks, {START: PAGE, START + PAGE: 3 * PAGE})

    def test_a_block_is_whole_pages(self):
        extended = ExtendedMemory()
        extended.allocate(1)
        extended.allocate(PAGE + 1)
        extended.allocate(PAGE)
        self.assertEqual(sorted(extended.blocks.values()), [PAGE, PAGE, 2 * PAGE])
        self.assertEqual(sorted(extended.blocks), [START, START + PAGE, START + 3 * PAGE])

    def test_a_block_of_nothing_is_given_a_page_so_that_it_can_be_told_from_the_next(self):
        extended = ExtendedMemory()
        self.assertEqual((extended.allocate(0), extended.allocate(0)), (START, START + PAGE))

    def test_each_block_has_its_owner(self):
        extended = ExtendedMemory()
        extended.allocate(PAGE, 0x1000)
        extended.allocate(PAGE, 0x2000)
        extended.allocate(PAGE)
        self.assertEqual(extended.owners, {START: 0x1000, START + PAGE: 0x2000, START + 2 * PAGE: 0})

    def test_a_block_that_fits_exactly_is_given_and_one_more_byte_is_not(self):
        extended = ExtendedMemory(START, START + 2 * PAGE)
        self.assertEqual(extended.allocate(2 * PAGE), START)
        self.assertIsNone(extended.allocate(1))
        extended = ExtendedMemory(START, START + 2 * PAGE)
        self.assertIsNone(extended.allocate(2 * PAGE + 1))
        self.assertEqual(extended.blocks, {})  # nothing is taken by a block that is refused

    def test_a_block_that_is_given_back_is_free_and_its_room_is_the_first_to_be_used_again(self):
        extended = ExtendedMemory()
        extended.allocate(PAGE)
        second = extended.allocate(2 * PAGE)
        extended.allocate(PAGE)
        self.assertTrue(extended.free(second))
        self.assertEqual(extended.allocate(PAGE), second)  # the first gap that fits
        self.assertEqual(extended.allocate(2 * PAGE), START + 4 * PAGE)  # what is left of it is too small
        self.assertEqual(extended.allocate(PAGE), second + PAGE)

    def test_a_block_that_is_not_there_is_not_freed(self):
        extended = ExtendedMemory()
        extended.allocate(PAGE)
        self.assertFalse(extended.free(START + PAGE))
        self.assertFalse(extended.free(START + 1))
        self.assertEqual(len(extended.blocks), 1)

    def test_everything_of_a_program_is_taken_back_and_nothing_of_the_others(self):
        extended = ExtendedMemory()
        extended.allocate(PAGE, 0x1000)
        extended.allocate(PAGE, 0x2000)
        extended.allocate(PAGE, 0x2000)
        extended.allocate(PAGE, 0x3000)
        extended.free_owned(0x2000)
        self.assertEqual(extended.owners, {START: 0x1000, START + 3 * PAGE: 0x3000})
        self.assertEqual(extended.blocks, {START: PAGE, START + 3 * PAGE: PAGE})
        extended.free_owned(0x4000)  # a program that has nothing
        self.assertEqual(len(extended.blocks), 2)

    def test_the_gaps_the_largest_one_and_what_is_free(self):
        extended = ExtendedMemory(START, START + 10 * PAGE)
        self.assertEqual((extended.gaps(), extended.largest(), extended.free_bytes()),
                         ([(START, 10 * PAGE)], 10 * PAGE, 10 * PAGE))
        extended.allocate(2 * PAGE)
        second = extended.allocate(PAGE)
        extended.allocate(4 * PAGE)
        extended.free(START)
        self.assertEqual(extended.gaps(), [(START, 2 * PAGE), (START + 7 * PAGE, 3 * PAGE)])
        self.assertEqual((extended.largest(), extended.free_bytes()), (3 * PAGE, 5 * PAGE))
        extended.allocate(3 * PAGE)
        extended.free(second)
        self.assertEqual(extended.gaps(), [(START, 3 * PAGE)])
        extended.allocate(3 * PAGE)
        self.assertEqual((extended.gaps(), extended.largest(), extended.free_bytes()), ([], 0, 0))


if __name__ == "__main__":
    unittest.main()
