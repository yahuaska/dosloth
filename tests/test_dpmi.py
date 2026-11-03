"""The extender that the machine plays for a 32-bit program (step 3c1): the descriptors of INT 31h, the question that
asks if DOS/4G is there, and the answers of INT 2Fh in the flat mode."""
import unittest

from mydos import dos, dpmi, flags
from mydos.xmem import PAGE, START, ExtendedMemory
from mydos.cpu import CPU, Exit
from mydos.loader import CODE_SELECTOR, DATA_SELECTOR

CODE_AT = 0x00200000


class FlatMachine(unittest.TestCase):
    """A CPU in the flat mode with DOS installed, which has an `int <n>` at CS:EIP, and the registers set before it."""

    def setUp(self):
        self.cpu = CPU()
        self.state = dos.install(self.cpu)
        self.cpu.enter_flat({CODE_SELECTOR: 0, DATA_SELECTOR: 0})
        self.bases = self.cpu.bases

    def call(self, number, ax, bx=0, cx=0, dx=0, si=None, di=None):
        cpu = self.cpu
        cpu.mem[CODE_AT:CODE_AT + 2] = bytes([0xCD, number])
        cpu.s[CPU.CS] = CODE_SELECTOR
        cpu.ip = CODE_AT
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        if si is not None:
            cpu.r[CPU.SI] = si
        if di is not None:
            cpu.r[CPU.DI] = di
        cpu.step()

    def failed(self):
        """None if the last call went well, else the error that it gave."""
        return self.cpu.r[CPU.AX] if self.cpu.get_flag(flags.CF) else None


class TestNewDescriptors(FlatMachine):
    def test_a_descriptor_is_a_selector_of_the_local_table_with_the_base_0_and_it_is_in_the_table(self):
        self.call(0x31, 0x0000, cx=1)
        selector = self.cpu.r[CPU.AX]
        self.assertIsNone(self.failed())
        self.assertEqual(selector, 3 << 3 | 7)  # the first after the code and the data of the program
        self.assertEqual(self.bases[selector], 0)

    def test_each_call_gives_a_new_one(self):
        selectors = []
        for _ in range(3):
            self.call(0x31, 0x0000, cx=1)
            selectors.append(self.cpu.r[CPU.AX])
        self.assertEqual(selectors, [0x1F, 0x27, 0x2F])

    def test_the_places_of_the_code_and_the_data_of_the_program_are_not_given_even_if_they_are_free(self):
        del self.bases[DATA_SELECTOR]  # the code selector stays: the program is running by it
        self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.cpu.r[CPU.AX], 3 << 3 | 7)

    def test_several_come_in_a_row_with_the_increment_of_8(self):
        self.call(0x31, 0x0000, cx=4)
        first = self.cpu.r[CPU.AX]
        self.assertEqual([first + 8 * i in self.bases for i in range(5)], [True] * 4 + [False])
        self.assertEqual(dpmi.INCREMENT, 8)

    def test_a_selector_that_is_in_the_table_is_not_given_again_whatever_it_is_for(self):
        self.bases[0x1F] = 0x1000  # the next one is taken (by a program that was given it before)
        self.bases[0x37] = 0x2000
        self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.cpu.r[CPU.AX], 0x27)
        self.call(0x31, 0x0000, cx=2)  # 0x2F and 0x37: the row has to skip the one that is taken
        self.assertEqual(self.cpu.r[CPU.AX], 0x3F)
        self.assertEqual((self.bases[0x1F], self.bases[0x37]), (0x1000, 0x2000))

    def test_the_segment_of_the_psp_is_not_a_selector_that_can_be_given(self):
        self.bases[0x1000] = 0x10000  # a selector with the number of a segment: the bits of its low part are 000
        for _ in range(3):
            self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.bases[0x1000], 0x10000)

    def test_none_is_an_error_8021(self):
        self.call(0x31, 0x0000, cx=0)
        self.assertEqual(self.failed(), 0x8021)
        self.assertEqual(sorted(self.bases), [CODE_SELECTOR, DATA_SELECTOR])

    def test_when_there_is_not_a_row_of_that_many_it_is_error_8011_and_nothing_is_taken(self):
        for index in range(3, 8192, 3):  # a descriptor in every third place: no row of three
            self.bases[index << 3 | 7] = 0
        taken = len(self.bases)
        self.call(0x31, 0x0000, cx=3)
        self.assertEqual(self.failed(), 0x8011)
        self.assertEqual(len(self.bases), taken)
        self.call(0x31, 0x0000, cx=2)  # a row of two is there
        self.assertIsNone(self.failed())

    def test_the_last_descriptor_of_the_table_can_be_had_and_there_is_no_one_after_it(self):
        for index in range(3, 8191):
            self.bases[index << 3 | 7] = 0
        self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.cpu.r[CPU.AX], 8191 << 3 | 7)
        self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.failed(), 0x8011)


class TestFreeing(FlatMachine):
    def test_a_descriptor_that_is_freed_is_not_in_the_table_and_can_be_given_again(self):
        self.call(0x31, 0x0000, cx=1)
        selector = self.cpu.r[CPU.AX]
        self.call(0x31, 0x0001, bx=selector)
        self.assertIsNone(self.failed())
        self.assertNotIn(selector, self.bases)
        self.call(0x31, 0x0000, cx=1)
        self.assertEqual(self.cpu.r[CPU.AX], selector)

    def test_one_that_is_not_there_is_error_8022(self):
        self.call(0x31, 0x0001, bx=0x1F)
        self.assertEqual(self.failed(), 0x8022)

    def test_a_selector_that_is_not_of_the_local_table_is_not_freed(self):
        self.bases[0x1000] = 0x10000  # that of the PSP
        self.call(0x31, 0x0001, bx=0x1000)
        self.assertEqual(self.failed(), 0x8022)
        self.assertIn(0x1000, self.bases)


class TestTheBase(FlatMachine):
    def make(self, base=0):
        self.call(0x31, 0x0000, cx=1)
        selector = self.cpu.r[CPU.AX]
        self.bases[selector] = base
        return selector

    def test_the_base_is_got_in_cx_and_dx_the_high_half_first(self):
        selector = self.make(0x12345678)
        self.call(0x31, 0x0006, bx=selector, cx=0xAAAA, dx=0xBBBB)
        self.assertEqual((self.failed(), self.cpu.r[CPU.CX], self.cpu.r[CPU.DX]), (None, 0x1234, 0x5678))

    def test_the_base_that_is_set_is_the_one_the_cpu_uses_at_once(self):
        selector = self.make()
        self.cpu.mem[0x300004] = 0x5A
        self.call(0x31, 0x0007, bx=selector, cx=0x0030, dx=0x0000)
        self.assertIsNone(self.failed())
        self.assertEqual(self.bases[selector], 0x300000)
        self.assertEqual(self.cpu.rb(selector, 4), 0x5A)

    def test_the_selectors_of_the_loader_can_be_asked_about_too(self):
        self.call(0x31, 0x0006, bx=CODE_SELECTOR)
        self.assertEqual((self.failed(), self.cpu.r[CPU.CX], self.cpu.r[CPU.DX]), (None, 0, 0))

    def test_a_selector_that_is_not_there_is_error_8022_for_all_four_calls(self):
        for ax in (0x0006, 0x0007, 0x0008, 0x0009):
            self.call(0x31, ax, bx=0x4F)
            self.assertEqual(self.failed(), 0x8022, hex(ax))

    def test_the_limit_and_the_rights_are_taken_and_change_nothing(self):
        selector = self.make(0x1000)
        for ax in (0x0008, 0x0009):
            self.call(0x31, ax, bx=selector, cx=0x000F, dx=0xFFFF)
            self.assertIsNone(self.failed())
        self.assertEqual(self.bases[selector], 0x1000)

    def test_a_base_of_more_than_32_bits_does_not_come_from_two_words(self):
        selector = self.make()
        self.call(0x31, 0x0007, bx=selector, cx=0xFFFF, dx=0xFFFF)
        self.assertEqual(self.bases[selector], 0xFFFFFFFF)


class TestExtendedMemory(FlatMachine):
    def allocate(self, size):
        """AX=0501h for `size` bytes: the address, the handle, and the error if there was one."""
        self.call(0x31, 0x0501, bx=size >> 16, cx=size & 0xFFFF)
        cpu = self.cpu
        return cpu.r[CPU.BX] << 16 | cpu.r[CPU.CX], cpu.r[CPU.SI] << 16 | cpu.r[CPU.DI], self.failed()

    def test_a_block_is_given_from_the_first_megabyte_and_the_handle_is_its_address(self):
        address, handle, error = self.allocate(0x2000)
        self.assertEqual((address, handle, error), (START, START, None))
        self.cpu.mem[address + 0x1FFF] = 0x5A  # all of it is there
        self.assertEqual(self.cpu.rb(DATA_SELECTOR, address + 0x1FFF), 0x5A)

    def test_the_next_block_is_after_it_and_a_size_is_cut_to_whole_pages(self):
        first = self.allocate(0x1001)[0]
        second = self.allocate(1)[0]
        self.assertEqual((first, second), (START, START + 2 * PAGE))

    def test_a_size_of_more_than_64_kb_is_in_bx_and_cx_the_high_half_first(self):
        address, _, error = self.allocate(0x00123456)
        self.assertIsNone(error)
        self.assertEqual(self.state.extended.blocks, {address: 0x124000})
        self.assertEqual(self.allocate(0x00010000)[0], address + 0x124000)

    def test_a_size_of_nothing_is_error_8021(self):
        self.assertEqual(self.allocate(0)[2], 0x8021)
        self.assertEqual(self.state.extended.blocks, {})

    def test_when_there_is_not_room_it_is_error_8012_and_nothing_is_taken(self):
        self.state.extended = ExtendedMemory(START, START + 4 * PAGE)
        self.assertEqual(self.allocate(4 * PAGE)[2], None)
        self.state.extended.free(START)
        self.assertEqual(self.allocate(4 * PAGE + 1)[2], 0x8012)
        self.assertEqual(self.state.extended.blocks, {})

    def test_the_block_is_the_property_of_the_running_program(self):
        self.state.psp_seg = 0x1234
        address = self.allocate(PAGE)[0]
        self.assertEqual(self.state.extended.owners, {address: 0x1234})

    def test_a_block_that_is_freed_is_free_and_is_given_again(self):
        address, handle, _ = self.allocate(2 * PAGE)
        self.allocate(PAGE)
        self.call(0x31, 0x0502, si=handle >> 16, di=handle & 0xFFFF)
        self.assertIsNone(self.failed())
        self.assertEqual(sorted(self.state.extended.blocks), [START + 2 * PAGE])
        self.assertEqual(self.allocate(PAGE)[0], address)

    def test_a_handle_that_is_not_one_is_error_8023(self):
        self.allocate(PAGE)
        for handle in (START + PAGE, START + 1, 0x00010000):
            self.call(0x31, 0x0502, si=handle >> 16, di=handle & 0xFFFF)
            self.assertEqual(self.failed(), 0x8023, hex(handle))
        self.assertEqual(len(self.state.extended.blocks), 1)

    def test_what_the_call_does_not_answer_is_left_as_it_was(self):
        self.cpu.set32(CPU.BP, 0x11223344)
        self.cpu.rh[CPU.CX] = 0xBEEF
        self.allocate(PAGE)
        self.assertEqual(self.cpu.get32(CPU.BP), 0x11223344)

    def test_the_facts_about_the_memory_are_48_bytes_at_es_edi_with_the_biggest_block_first(self):
        self.allocate(10 * PAGE)
        self.cpu.s[CPU.ES] = DATA_SELECTOR
        self.cpu.set32(CPU.DI, 0x300000)
        self.cpu.mem[0x300000 + 48:0x300000 + 52] = b"\xAA\xAA\xAA\xAA"
        self.call(0x31, 0x0500)
        self.assertIsNone(self.failed())
        extended = self.state.extended
        words = [self.cpu.rd(DATA_SELECTOR, 0x300000 + 4 * i) for i in range(13)]
        free = extended.free_bytes()
        total = (extended.end - extended.start) // PAGE
        self.assertEqual(words[:8], [free, free // PAGE, free // PAGE, total, free // PAGE, free // PAGE, total,
                                     free // PAGE])
        self.assertEqual(words[8:12], [0xFFFFFFFF] * 4)  # not known
        self.assertEqual(words[12], 0xAAAAAAAA)  # nothing beyond the 48 bytes

    def test_the_biggest_block_is_the_biggest_gap_and_not_all_that_is_free(self):
        self.allocate(PAGE)
        self.allocate(PAGE)
        self.allocate(PAGE)
        self.state.extended.free(START + PAGE)  # a gap of a page in front of the rest
        self.cpu.s[CPU.ES] = DATA_SELECTOR
        self.cpu.set32(CPU.DI, 0x300000)
        self.call(0x31, 0x0500)
        self.assertEqual(self.cpu.rd(DATA_SELECTOR, 0x300000), self.state.extended.largest())
        self.assertGreater(self.state.extended.largest(), PAGE)  # it is the one at the end

    def test_locking_and_unlocking_do_nothing_and_say_so(self):
        for ax in (0x0600, 0x0601):
            self.cpu.set_flag(flags.CF, True)
            self.call(0x31, ax, bx=0x0012, cx=0x3456, si=0, di=0x1000)
            self.assertEqual((self.failed(), self.cpu.r[CPU.BX], self.cpu.r[CPU.CX]), (None, 0x0012, 0x3456))

    def test_the_memory_of_a_device_is_at_the_linear_address_of_its_physical_one(self):
        self.call(0x31, 0x0800, bx=0xE000, cx=0x0000, si=0, di=0x1000)
        self.assertEqual((self.failed(), self.cpu.r[CPU.BX], self.cpu.r[CPU.CX]), (None, 0xE000, 0x0000))
        self.call(0x31, 0x0800, bx=0x000A, cx=0x0000, si=0, di=0x1000)
        self.assertEqual((self.cpu.r[CPU.BX], self.cpu.r[CPU.CX]), (0x000A, 0x0000))


class TestTheVectors(FlatMachine):
    def test_a_handler_is_set_to_cx_edx_and_given_back(self):
        self.call(0x31, 0x0205, bx=0x000F, cx=0x0027, dx=0x1234)
        self.assertIsNone(self.failed())
        self.assertEqual(self.cpu.pm_vectors[0x0F], (0x0027, 0x1234))
        self.cpu.r[CPU.CX] = self.cpu.r[CPU.DX] = 0
        self.call(0x31, 0x0204, bx=0x000F)
        self.assertEqual((self.failed(), self.cpu.r[CPU.CX], self.cpu.r[CPU.DX]), (None, 0x0027, 0x1234))

    def test_the_offset_is_all_of_edx_and_a_vector_that_is_not_set_is_the_handler_of_the_rom(self):
        self.cpu.set32(CPU.DX, 0x00F00123)
        self.call(0x31, 0x0205, bx=0x0008, cx=0x0027, dx=0x0123)
        self.cpu.set32(CPU.DX, 0x00F00123)
        self.cpu.pm_vectors[0x0A] = (0x0027, 0x00F00123)
        self.call(0x31, 0x0204, bx=0x000A)
        self.assertEqual(self.cpu.get32(CPU.DX), 0x00F00123)
        self.call(0x31, 0x0204, bx=0x0077)
        self.assertEqual((self.cpu.r[CPU.CX], self.cpu.get32(CPU.DX)), (CODE_SELECTOR, 0xF0020))


class TestTheVectorsOfTheRealMode(FlatMachine):
    def test_a_vector_is_set_in_the_table_at_address_0_and_given_back(self):
        self.call(0x31, 0x0201, bx=0x000F, cx=0x1404, dx=0x0010)
        self.assertIsNone(self.failed())
        self.assertEqual(self.cpu.mem[0x3C:0x40], bytes([0x10, 0x00, 0x04, 0x14]))
        self.cpu.r[CPU.CX] = self.cpu.r[CPU.DX] = 0
        self.call(0x31, 0x0200, bx=0x000F)
        self.assertEqual((self.failed(), self.cpu.r[CPU.CX], self.cpu.r[CPU.DX]), (None, 0x1404, 0x0010))

    def test_a_vector_that_is_not_set_is_the_handler_of_the_rom(self):
        self.call(0x31, 0x0200, bx=0x000F)
        self.assertEqual((self.cpu.r[CPU.CX], self.cpu.r[CPU.DX]), dos.MASTER_EOI_AT)
        self.call(0x31, 0x0200, bx=0x0060)
        self.assertEqual((self.cpu.r[CPU.CX], self.cpu.r[CPU.DX]), dos.IRET_AT)


class TestTheProgramHoldsAllTheMemory(FlatMachine):
    """DOS gave the memory below 1 MB to the program, as it does to every one, and the program has not given it back."""

    def test_the_program_gives_back_all_but_its_psp_when_it_asks_for_a_block(self):
        memory = self.state.memory
        self.assertEqual(memory.largest(), 0)  # nothing is free yet
        self.call(0x31, 0x0100, bx=0x304)
        self.assertIsNone(self.failed())
        self.assertEqual(memory.blocks[self.state.psp_seg], dpmi.PROGRAM_PARAGRAPHS)
        self.assertEqual(memory.blocks[self.cpu.r[CPU.AX]], 0x304)

    def test_a_block_that_is_more_than_all_of_it_is_still_error_8_and_the_program_has_given_back_what_it_could(self):
        self.call(0x31, 0x0100, bx=0xFFFF)
        self.assertEqual(self.failed(), 0x0008)
        self.assertEqual(self.cpu.r[CPU.BX], self.state.memory.largest())
        self.assertGreater(self.cpu.r[CPU.BX], 0x8000)

    def test_a_block_of_the_program_that_is_small_already_is_not_made_bigger(self):
        self.state.memory.resize(self.state.psp_seg, 0x20)
        self.state.memory.allocate(self.state.memory.largest(), 0)  # nothing is left
        self.call(0x31, 0x0100, bx=0x10)
        self.assertEqual(self.failed(), 0x0008)
        self.assertEqual(self.state.memory.blocks[self.state.psp_seg], 0x20)


class TestMemoryBelowTheMegabyte(FlatMachine):
    def setUp(self):
        super().setUp()
        self.state.memory.resize(self.state.psp_seg, 0x100)  # the program has given back what it did not need

    def test_a_block_of_paragraphs_has_a_segment_and_a_selector_with_its_base(self):
        self.call(0x31, 0x0100, bx=0x20)
        cpu = self.cpu
        segment, selector = cpu.r[CPU.AX], cpu.r[CPU.DX]
        self.assertIsNone(self.failed())
        self.assertEqual(self.state.memory.blocks[segment], 0x20)
        self.assertEqual(self.state.memory.owners[segment], self.state.psp_seg)
        self.assertEqual(self.bases[selector], segment * 16)
        self.assertEqual(selector & 7, 7)
        cpu.mem[segment * 16 + 5] = 0x77
        self.assertEqual(cpu.rb(selector, 5), 0x77)

    def test_when_there_is_not_room_it_is_error_8_with_the_biggest_block_in_bx(self):
        largest = self.state.memory.largest()
        before = dict(self.bases)
        self.call(0x31, 0x0100, bx=largest + 1)
        self.assertEqual((self.failed(), self.cpu.r[CPU.BX]), (0x0008, largest))
        self.assertEqual(self.bases, before)

    def test_without_a_descriptor_left_the_block_is_not_kept_either(self):
        for index in range(3, 8192):
            self.bases[index << 3 | 7] = 0
        blocks = dict(self.state.memory.blocks)
        self.call(0x31, 0x0100, bx=0x20)
        self.assertEqual(self.failed(), 0x8011)
        self.assertEqual(self.state.memory.blocks, blocks)

    def test_a_block_is_freed_by_its_selector_and_the_selector_goes_with_it(self):
        self.call(0x31, 0x0100, bx=0x20)
        segment, selector = self.cpu.r[CPU.AX], self.cpu.r[CPU.DX]
        self.call(0x31, 0x0101, dx=selector)
        self.assertIsNone(self.failed())
        self.assertNotIn(segment, self.state.memory.blocks)
        self.assertNotIn(selector, self.bases)

    def test_a_selector_that_is_not_of_a_block_is_error_8022_and_is_left_alone(self):
        for selector in (0x77, CODE_SELECTOR):
            self.call(0x31, 0x0101, dx=selector)
            self.assertEqual(self.failed(), 0x8022, hex(selector))
        self.assertIn(CODE_SELECTOR, self.bases)
        self.call(0x31, 0x0000, cx=1)  # a descriptor with the base 0: that is not the base of a block of DOS
        selector = self.cpu.r[CPU.AX]
        self.call(0x31, 0x0101, dx=selector)
        self.assertEqual(self.failed(), 0x8022)
        self.assertIn(selector, self.bases)
        self.assertIn(self.state.psp_seg, self.state.memory.blocks)  # the program's own block is not freed by it


class TestTheCall(FlatMachine):
    def test_the_version_is_dpmi_0_9_for_a_32_bit_program_on_a_486_with_the_pics_at_08_and_70(self):
        self.call(0x31, 0x0400)
        cpu = self.cpu
        self.assertIsNone(self.failed())
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (0x0009, 0x0001, 4, 0x0870))

    def test_a_call_that_is_not_done_stops_the_emulator_with_its_number(self):
        with self.assertRaisesRegex(NotImplementedError, "INT 31h AX=0300"):
            self.call(0x31, 0x0300)

    def test_a_successful_call_clears_the_carry_and_leaves_the_other_registers(self):
        cpu = self.cpu
        cpu.set_flag(flags.CF, True)
        cpu.set32(CPU.SI, 0x11223344)
        cpu.set32(CPU.BP, 0x55667788)
        self.call(0x31, 0x0006, bx=CODE_SELECTOR)
        self.assertEqual((cpu.get_flag(flags.CF), cpu.get32(CPU.SI), cpu.get32(CPU.BP)),
                         (False, 0x11223344, 0x55667788))

    def test_it_is_not_there_in_real_mode(self):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = b"\xCD\x31"
        with self.assertRaisesRegex(NotImplementedError, "INT 31h from real mode"):
            cpu.step()


class TestTheExtenderQuestion(FlatMachine):
    def test_al_says_it_is_there_and_gs_is_a_selector_with_the_base_0(self):
        self.cpu.set_flag(flags.CF, True)
        self.call(0x21, 0xFF00, dx=0x78)
        cpu = self.cpu
        self.assertNotEqual(cpu.r[CPU.AX] & 0xFF, 0)
        self.assertFalse(cpu.get_flag(flags.CF))
        self.assertIn(cpu.s[CPU.GS], self.bases)
        self.assertEqual(self.bases[cpu.s[CPU.GS]], 0)
        self.assertEqual(cpu.s[CPU.GS] & 7, 7)

    def test_gs_with_the_base_0_is_kept_and_another_one_is_replaced(self):
        self.call(0x21, 0xFF00, dx=0x78)
        gs = self.cpu.s[CPU.GS]
        self.call(0x21, 0xFF00, dx=0x78)
        self.assertEqual(self.cpu.s[CPU.GS], gs)
        self.assertEqual(len(self.bases), 3)
        self.bases[gs] = 0x5000  # the program made it something else
        self.call(0x21, 0xFF00, dx=0x78)
        self.assertNotEqual(self.cpu.s[CPU.GS], gs)
        self.assertEqual((self.bases[gs], self.bases[self.cpu.s[CPU.GS]]), (0x5000, 0))

    def test_a_gs_that_names_nothing_in_the_table_is_replaced(self):
        self.cpu.s[CPU.GS] = 0x77
        self.call(0x21, 0xFF00, dx=0x78)
        self.assertIn(self.cpu.s[CPU.GS], self.bases)
        self.assertNotEqual(self.cpu.s[CPU.GS], 0x77)

    def test_the_question_with_another_dx_is_not_the_one_that_is_done(self):
        with self.assertRaisesRegex(NotImplementedError, "INT 21h AX=FF00 in the flat mode"):
            self.call(0x21, 0xFF00, dx=0x79)

    def test_in_real_mode_it_is_still_a_function_that_is_not_done(self):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = b"\xCD\x21"
        cpu.r[CPU.AX], cpu.r[CPU.DX] = 0xFF00, 0x78
        with self.assertRaisesRegex(NotImplementedError, "INT 21h AH=FF"):
            cpu.step()


class TestDosInTheFlatMode(FlatMachine):
    def test_the_version_is_answered_as_in_real_mode(self):
        self.call(0x21, 0x3000)
        self.assertEqual((self.cpu.r[CPU.AX], self.failed()), (0x0107, None))

    def test_the_end_of_the_program_ends_the_emulator_with_the_code(self):
        with self.assertRaises(Exit) as stop:
            self.call(0x21, 0x4C07)
        self.assertEqual(stop.exception.code, 7)

    def test_a_function_that_is_not_done_for_the_flat_mode_stops_with_its_number_and_the_mode(self):
        for ax in (0x3C00, 0x4800, 0x0900):
            with self.assertRaisesRegex(NotImplementedError, f"INT 21h AX={ax:04X} in the flat mode"):
                self.call(0x21, ax)


class TestInt2f(FlatMachine):
    def test_the_question_if_the_program_is_in_protected_mode_is_answered_with_0(self):
        self.call(0x2F, 0x1686)
        self.assertEqual(self.cpu.r[CPU.AX], 0)

    def test_in_real_mode_the_ax_of_it_is_left_as_it_was_and_it_is_not_done(self):
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = b"\xCD\x2F"
        cpu.r[CPU.AX] = 0x1686
        with self.assertRaisesRegex(NotImplementedError, "INT 2Fh AX=1686"):
            cpu.step()

    def test_the_other_questions_are_answered_as_in_real_mode(self):
        self.call(0x2F, 0x1600)  # Windows: nothing answers, AX stays
        self.assertEqual(self.cpu.r[CPU.AX], 0x1600)
        with self.assertRaisesRegex(NotImplementedError, "INT 2Fh AX=FA00"):
            self.call(0x2F, 0xFA00)


if __name__ == "__main__":
    unittest.main()
