import mmap
import unittest

from dosloth import pages

MB = 1024 * 1024


def memory(megabytes=4):
    return mmap.mmap(-1, megabytes * MB, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)


class TestSpans(unittest.TestCase):
    def test_a_memory_of_zeros_has_no_spans(self):
        self.assertEqual(pages.spans(memory()), [])

    def test_a_byte_makes_its_page_a_span(self):
        mem = memory()
        mem[5] = 1
        mem[3 * pages.PAGE + 7] = 2
        self.assertEqual(pages.spans(mem), [(0, 4096), (3 * 4096, 4096)])

    def test_pages_that_follow_each_other_make_one_span(self):
        mem = memory()
        mem[4096 - 1] = 1
        mem[4096] = 1
        mem[2 * 4096] = 1
        self.assertEqual(pages.spans(mem), [(0, 3 * 4096)])

    def test_a_span_goes_on_over_the_edge_of_a_chunk_of_a_megabyte(self):
        mem = memory()
        mem[MB - 1] = 1
        mem[MB] = 1
        self.assertEqual(pages.spans(mem), [(MB - 4096, 2 * 4096)])

    def test_an_empty_chunk_ends_a_span_that_came_before_it(self):
        mem = memory()
        mem[MB - 1] = 1
        mem[3 * MB] = 1
        self.assertEqual(pages.spans(mem), [(MB - 4096, 4096), (3 * MB, 4096)])

    def test_a_span_at_the_very_end_is_closed_there(self):
        mem = memory()
        mem[len(mem) - 1] = 1
        self.assertEqual(pages.spans(mem), [(len(mem) - 4096, 4096)])
        mem = memory()
        mem[len(mem) - 4096:] = bytes([1]) * 4096
        mem[len(mem) - 2 * 4096] = 1
        self.assertEqual(pages.spans(mem), [(len(mem) - 2 * 4096, 2 * 4096)])

    def test_a_memory_that_is_full_is_one_span(self):
        mem = memory(2)
        mem[:] = bytes([1]) * len(mem)
        self.assertEqual(pages.spans(mem), [(0, 2 * MB)])

    def test_a_full_chunk_between_empty_ones_is_a_span_of_its_own(self):
        mem = memory()
        mem[MB:2 * MB] = bytes([1]) * MB
        self.assertEqual(pages.spans(mem), [(MB, MB)])


class TestRunsAndErase(unittest.TestCase):
    def test_a_run_has_what_is_in_its_pages(self):
        mem = memory()
        mem[4096 + 3:4096 + 6] = b"abc"
        (address, data), = pages.runs(mem)
        self.assertEqual((address, len(data), data[3:6]), (4096, 4096, b"abc"))
        self.assertIsInstance(data, bytes)

    def test_the_runs_put_back_on_a_memory_of_zeros_make_the_memory_that_they_came_from(self):
        mem = memory()
        mem[10] = 1
        mem[2 * MB + 5000] = 2
        other = memory()
        for address, data in pages.runs(mem):
            other[address:address + len(data)] = data
        self.assertEqual(pages.runs(other), pages.runs(mem))
        self.assertEqual((other[10], other[2 * MB + 5000]), (1, 2))

    def test_erase_puts_zeros_where_there_were_not_and_nowhere_else_changes(self):
        mem = memory()
        mem[10] = 1
        mem[4095] = 3  # the last byte of a page
        mem[3 * MB + 1] = 2
        mem[3 * MB + 4095] = 4
        pages.erase(mem)
        self.assertEqual(pages.spans(mem), [])
        self.assertEqual((mem[10], mem[4095], mem[3 * MB + 1], mem[3 * MB + 4095], len(mem)), (0, 0, 0, 0, 4 * MB))


if __name__ == "__main__":
    unittest.main()
