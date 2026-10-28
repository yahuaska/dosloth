import unittest

from mydos.cpu import MEMORY_SIZE
from mydos.xmem import PAGE, START, ExtendedMemory


class TestExtendedMemory(unittest.TestCase):
    def test_it_begins_above_the_first_megabyte_and_ends_with_the_memory_of_the_machine(self):
        extended = ExtendedMemory()
        self.assertEqual((extended.start, extended.top, extended.end), (0x100000, 0x100000, MEMORY_SIZE))
        self.assertEqual(START, 0x100000)

    def test_blocks_are_given_out_one_after_another(self):
        extended = ExtendedMemory()
        self.assertEqual(extended.allocate(PAGE), START)
        self.assertEqual(extended.allocate(3 * PAGE), START + PAGE)
        self.assertEqual(extended.top, START + 4 * PAGE)

    def test_a_block_is_whole_pages(self):
        extended = ExtendedMemory()
        extended.allocate(1)
        self.assertEqual(extended.top, START + PAGE)
        extended.allocate(PAGE + 1)
        self.assertEqual(extended.top, START + 3 * PAGE)
        extended.allocate(PAGE)
        self.assertEqual(extended.top, START + 4 * PAGE)

    def test_a_block_of_nothing_is_nothing(self):
        extended = ExtendedMemory()
        self.assertEqual(extended.allocate(0), START)
        self.assertEqual(extended.top, START)

    def test_a_block_that_fits_exactly_is_given_and_one_more_byte_is_not(self):
        extended = ExtendedMemory(START, START + 2 * PAGE)
        self.assertEqual(extended.allocate(2 * PAGE), START)
        self.assertIsNone(extended.allocate(1))
        extended = ExtendedMemory(START, START + 2 * PAGE)
        self.assertIsNone(extended.allocate(2 * PAGE + 1))
        self.assertEqual(extended.top, START)  # nothing is taken by a block that is refused

    def test_a_block_and_those_after_it_are_taken_back_together(self):
        extended = ExtendedMemory()
        extended.allocate(PAGE)
        second = extended.allocate(PAGE)
        extended.allocate(PAGE)
        extended.release_from(second)
        self.assertEqual(extended.top, second)
        self.assertEqual(extended.allocate(PAGE), second)

    def test_the_first_block_can_be_taken_back_and_the_top_too(self):
        extended = ExtendedMemory()
        extended.allocate(PAGE)
        extended.release_from(extended.top)  # nothing after it: nothing changes
        self.assertEqual(extended.top, START + PAGE)
        extended.release_from(START)
        self.assertEqual(extended.top, START)

    def test_an_address_that_is_not_in_what_is_given_out_is_an_error(self):
        extended = ExtendedMemory()
        extended.allocate(PAGE)
        for address in (START - PAGE, START + 2 * PAGE, 0):
            with self.assertRaises(ValueError):
                extended.release_from(address)
        self.assertEqual(extended.top, START + PAGE)


if __name__ == "__main__":
    unittest.main()
