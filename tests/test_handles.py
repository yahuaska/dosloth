import unittest

from mydos.handles import TABLE_SIZE, Console, HandleTable, NullDevice, OpenFile


class SpyFile:
    """A file object that remembers whether it was closed."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def opened(name="C:\\F"):
    return OpenFile(SpyFile(), name)


class TestTheStartingTable(unittest.TestCase):
    def test_the_first_five_handles_are_open_and_the_rest_are_free(self):
        table = HandleTable()
        self.assertEqual([type(table.get(h)).__name__ for h in range(5)], ["Console"] * 3 + ["NullDevice"] * 2)
        self.assertEqual([table.get(h) for h in range(5, TABLE_SIZE)], [None] * (TABLE_SIZE - 5))
        self.assertEqual(TABLE_SIZE, 20)

    def test_what_the_first_five_are(self):
        table = HandleTable()
        streams = [table.get(handle).stream for handle in (0, 1, 2)]
        self.assertEqual(streams, ["stdout", "stdout", "stderr"])
        self.assertEqual((table.get(3).name, table.get(4).name), ("AUX", "PRN"))

    def test_a_handle_outside_the_table_is_not_open(self):
        table = HandleTable()
        for handle in (-1, 20, 21, 255, 0xFFFF, 10 ** 6):
            self.assertIsNone(table.get(handle), handle)
            self.assertFalse(table.close(handle), handle)


class TestHandingOutHandles(unittest.TestCase):
    def test_the_first_file_gets_5_and_the_next_ones_follow(self):
        table = HandleTable()
        self.assertEqual([table.allocate(opened()) for _ in range(3)], [5, 6, 7])

    def test_the_entry_is_what_is_found_under_the_number(self):
        table = HandleTable()
        entry = opened("C:\\GAMES\\A.DAT")
        self.assertIs(table.get(table.allocate(entry)), entry)

    def test_the_lowest_free_number_is_used_again(self):
        table = HandleTable()
        for _ in range(5):
            table.allocate(opened())  # 5 to 9
        table.close(7)
        table.close(6)
        self.assertEqual([table.allocate(opened()) for _ in range(3)], [6, 7, 10])

    def test_closing_a_standard_handle_frees_it_for_a_file(self):
        table = HandleTable()
        table.close(0)
        self.assertEqual(table.allocate(opened()), 0)  # the old way of redirecting the input
        table.close(3)
        self.assertEqual(table.allocate(opened()), 3)

    def test_twenty_handles_and_no_more(self):
        table = HandleTable()
        handles = [table.allocate(opened()) for _ in range(15)]
        self.assertEqual(handles, list(range(5, 20)))
        self.assertFalse(table.has_free())
        self.assertIsNone(table.allocate(opened()))

    def test_has_free(self):
        table = HandleTable()
        self.assertTrue(table.has_free())
        for _ in range(15):
            table.allocate(opened())
        table.close(11)
        self.assertTrue(table.has_free())


class TestClosing(unittest.TestCase):
    def test_closing_a_file_closes_its_file_object_and_frees_the_number(self):
        table = HandleTable()
        entry = opened()
        handle = table.allocate(entry)
        self.assertTrue(table.close(handle))
        self.assertTrue(entry.file.closed)
        self.assertIsNone(table.get(handle))

    def test_closing_twice_is_a_failure_the_second_time(self):
        table = HandleTable()
        handle = table.allocate(opened())
        self.assertTrue(table.close(handle))
        self.assertFalse(table.close(handle))

    def test_closing_a_device_just_frees_it(self):
        table = HandleTable()
        self.assertTrue(table.close(1))
        self.assertIsNone(table.get(1))
        self.assertIsInstance(table.get(0), Console)  # the others are not touched

    def test_closing_one_leaves_the_other_files_open(self):
        table = HandleTable()
        first, second = opened(), opened()
        a, b = table.allocate(first), table.allocate(second)
        table.close(a)
        self.assertEqual((first.file.closed, second.file.closed), (True, False))
        self.assertIs(table.get(b), second)

    def test_close_all_closes_every_file_and_frees_every_slot(self):
        table = HandleTable()
        files = [opened() for _ in range(4)]
        for entry in files:
            table.allocate(entry)
        table.close_all()
        self.assertTrue(all(entry.file.closed for entry in files))
        self.assertEqual(table.slots, [None] * TABLE_SIZE)

    def test_two_tables_do_not_share_anything(self):
        one, other = HandleTable(), HandleTable()
        one.allocate(opened())
        self.assertIsNone(other.get(5))


class TestSharingWithAChild(unittest.TestCase):
    def test_a_copy_holds_the_same_things_in_the_same_slots(self):
        table = HandleTable()
        entry = OpenFile(SpyFile(), "C:\\A")
        table.allocate(entry)
        child = table.copy()
        self.assertIsNot(child, table)
        self.assertIs(child.get(5), entry)
        self.assertEqual(child.slots[:5], table.slots[:5])

    def test_what_the_copy_closes_or_opens_is_its_own_business(self):
        table = HandleTable()
        child = table.copy()
        child.allocate(OpenFile(SpyFile(), "C:\\B"))
        self.assertIsNone(table.get(5))
        entry = OpenFile(SpyFile(), "C:\\A")
        table.allocate(entry)
        self.assertIsNone(child.get(6))

    def test_a_file_the_parent_and_the_child_hold_is_closed_when_the_last_lets_go(self):
        table = HandleTable()
        file = SpyFile()
        table.allocate(OpenFile(file, "C:\\A"))
        child = table.copy()
        self.assertTrue(child.close(5))
        self.assertFalse(file.closed)
        self.assertTrue(table.close(5))
        self.assertTrue(file.closed)

    def test_it_does_not_matter_who_lets_go_first(self):
        table = HandleTable()
        file = SpyFile()
        table.allocate(OpenFile(file, "C:\\A"))
        child = table.copy()
        table.close(5)
        self.assertFalse(file.closed)
        child.close_all()
        self.assertTrue(file.closed)

    def test_three_levels_need_three_closes(self):
        table = HandleTable()
        file = SpyFile()
        table.allocate(OpenFile(file, "C:\\A"))
        child = table.copy()
        grandchild = child.copy()
        for each in (grandchild, table):
            each.close(5)
            self.assertFalse(file.closed)
        child.close(5)
        self.assertTrue(file.closed)

    def test_a_file_put_straight_into_a_slot_is_closed_by_the_first_close(self):
        table = HandleTable()
        file = SpyFile()
        table.slots[7] = OpenFile(file, "C:\\A")
        table.close(7)
        self.assertTrue(file.closed)


if __name__ == "__main__":
    unittest.main()
