import struct
import unittest

from mydos import dos, flags, psp
from mydos.cpu import CPU
from mydos.handles import OpenFile
from tests import test_dos

PARENT = 0x1000  # the PSP of the program that starts the others
PARENT_SIZE = 0x3100  # what it keeps for itself: its segments 2000h and 3000h are in it
FIRST_FREE = PARENT + PARENT_SIZE


def com(code, name=None, name_at=0x20):
    """A .COM image: `code`, and, `name_at` bytes into it, a zero-ended name."""
    image = bytearray(code).ljust(name_at, b"\x90")
    if name is not None:
        image += name.encode() + b"\0"
    return bytes(image)


def exe(code, image_size=32, relocations=((0x10, 0),), ss=1, sp=0x100, minalloc=0x10, maxalloc=0x20, header=2):
    """A .EXE: `code` at the start of an image of `image_size` bytes, a header of `header` paragraphs."""
    image = bytearray(code).ljust(image_size, b"\0")
    total = header * 16 + image_size
    table = b"".join(struct.pack("<2H", *entry) for entry in relocations)
    head = struct.pack("<2s13H", b"MZ", total % 512, (total + 511) // 512, len(relocations), header, minalloc,
                       maxalloc, ss, sp, 0, 0, 0, 0x1C, 0)
    return (head + table).ljust(header * 16, b"\0") + bytes(image)


EXIT = lambda code: bytes([0xB8, code, 0x4C, 0xCD, 0x21])  # mov ax, 4C<code>h ; int 21h


class ProcessTest(test_dos.FileFunctionsTest):
    """A parent program at 0800:0000 that has an INT 21h there and a PSP of its own at 1000h."""

    def setUp(self):
        super().setUp()
        cpu = self.cpu
        cpu.s[CPU.CS] = 0x0800
        cpu.mem[0x8000:0x8002] = bytes([0xCD, 0x21])
        psp.build(cpu, PARENT, PARENT - 0x10, dos.MEMORY_TOP_SEG, "C:\\PARENT.COM")
        cpu.s[CPU.ES] = PARENT
        self.call(0x4A00, bx=PARENT_SIZE)  # the parent keeps what it needs and gives the rest back
        self.assertIsNone(self.failed())
        cpu.s[CPU.ES] = 0x3000

    def program(self, name, data):
        (self.base / name).write_bytes(data)

    def parameters(self, env=0, tail=0, fcb1=0, fcb2=0, at=0x0400):
        """Write a parameter block at ES:BX = 3000:at; the pointers are given as (segment, offset)."""
        cpu = self.cpu
        words = [env, tail & 0xFFFF, tail >> 16, fcb1 & 0xFFFF, fcb1 >> 16, fcb2 & 0xFFFF, fcb2 >> 16]
        for i, word in enumerate(words):
            cpu.ww(0x3000, at + i * 2, word)
        return at

    def start(self, name, data=None, **keywords):
        """Run INT 21h AH=4Bh for the program `name` on C:. Gives what the parent had in BX."""
        if data is not None:
            self.program(name, data)
        block = self.parameters(**keywords)
        self.call(0x4B00, "C:\\" + name, bx=block, dx=0x0100)

    def run_child(self, steps=50):
        """Step the CPU until the programs that were started have ended (the parent is running again)."""
        depth = len(self.state.parents)
        for _ in range(steps):
            if len(self.state.parents) < depth:
                return
            self.cpu.step()
        self.fail("the program did not end")

    def at_psp(self, offset, size=2, segment=None):
        segment = self.state.psp_seg if segment is None else segment
        return int.from_bytes(bytes(self.cpu.rb(segment, offset + i) for i in range(size)), "little")


class TestStartingAProgram(ProcessTest):
    def test_a_com_program_starts_at_100_with_the_segment_registers_on_its_psp(self):
        self.start("CHILD.COM", com(EXIT(7)))
        self.assertIsNone(self.failed())
        cpu = self.cpu
        child = self.state.psp_seg
        self.assertGreater(child, FIRST_FREE - 1)
        self.assertEqual([cpu.s[i] for i in (CPU.CS, CPU.DS, CPU.ES, CPU.SS)], [child] * 4)
        self.assertEqual((cpu.ip, cpu.r[CPU.SP], cpu.r[CPU.AX]), (0x100, 0xFFFE, 0))
        self.assertEqual(cpu.flags, flags.ALWAYS_ONE | flags.IF)
        self.assertEqual(bytes(cpu.mem[child * 16 + 0x100:child * 16 + 0x105]), EXIT(7))

    def test_the_child_gets_the_memory_that_is_free_and_the_psp_says_where_it_ends(self):
        self.start("CHILD.COM", com(EXIT(7)))
        child = self.state.psp_seg
        self.assertEqual(self.at_psp(0x02), dos.MEMORY_TOP_SEG)
        self.assertEqual(self.state.memory.blocks[child], dos.MEMORY_TOP_SEG - child)
        self.assertEqual(self.state.memory.largest(), 0)

    def test_the_psp_names_the_parent_and_the_environment_and_the_dta_is_the_childs(self):
        self.start("CHILD.COM", com(EXIT(7)))
        child = self.state.psp_seg
        self.assertEqual(self.at_psp(0x16), PARENT)
        env = self.at_psp(0x2C)
        self.assertEqual(env + self.state.memory.blocks[env], child)  # the environment is the block before the program
        self.assertEqual(self.state.dta, (child, 0x80))
        self.assertEqual(self.at_psp(0x50, 3), 0xCB21CD)

    def test_a_child_that_ends_gives_the_machine_back_to_its_parent(self):
        cpu = self.cpu
        cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.r[CPU.BP] = 0x1357, 0x2468, 0x3579
        self.program("CHILD.COM", com(EXIT(7)))
        block = self.parameters()
        self.call(0x4B00, "C:\\CHILD.COM", bx=block, cx=0x2222, dx=0x0100)
        cpu.r[CPU.SI] = cpu.r[CPU.DI] = cpu.r[CPU.BP] = 0xBEEF  # the child spoils what it likes
        self.run_child()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x0800, 2))  # the instruction after the INT 21h
        self.assertEqual((cpu.s[CPU.DS], cpu.s[CPU.ES], cpu.s[CPU.SS]), (0x2000, 0x3000, cpu.s[CPU.SS]))
        self.assertEqual((cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX]), (block, 0x2222, 0x0100))
        self.assertEqual((cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.r[CPU.BP]), (0x1357, 0x2468, 0x3579))
        self.assertEqual(cpu.r[CPU.AX], 0)
        self.assertFalse(cpu.get_flag(flags.CF))

    def test_the_flags_of_the_parent_come_back_but_for_the_carry_which_says_it_went_well(self):
        self.cpu.flags |= flags.ZF | flags.DF | flags.CF
        self.start("CHILD.COM", com(EXIT(7)))
        self.cpu.flags |= flags.CF  # the child ends with the carry set
        self.run_child()
        self.assertTrue(self.cpu.get_flag(flags.ZF) and self.cpu.get_flag(flags.DF))
        self.assertFalse(self.cpu.get_flag(flags.CF))

    def test_the_parent_is_the_running_program_again_with_its_own_psp_and_dta(self):
        self.call(0x1A00, dx=0x0300)  # a DTA of the parent's own
        self.assertEqual(self.state.dta, (0x2000, 0x0300))
        self.start("CHILD.COM", com(EXIT(7)))
        self.run_child()
        self.assertEqual(self.state.psp_seg, PARENT)
        self.assertEqual(self.state.dta, (0x2000, 0x0300))
        self.assertEqual(self.state.parents, [])

    def test_ah_4dh_gives_the_exit_code_of_the_child_once(self):
        self.start("CHILD.COM", com(EXIT(7)))
        self.run_child()
        self.call(0x4D00)
        self.assertEqual(self.cpu.r[CPU.AX], 7)
        self.call(0x4D00)
        self.assertEqual(self.cpu.r[CPU.AX], 0)

    def test_a_child_may_end_with_int_20h_and_that_is_exit_code_0(self):
        self.start("CHILD.COM", com(bytes([0xCD, 0x20])))
        self.run_child()
        self.call(0x4D00)
        self.assertEqual(self.cpu.r[CPU.AX], 0)

    def test_the_child_ends_through_the_int_20h_of_its_psp_too(self):
        self.start("CHILD.COM", com(bytes([0x1E, 0x31, 0xC0, 0x50, 0xCB])))  # push ds ; xor ax,ax ; push ax ; retf
        self.run_child()
        self.assertEqual(self.state.parents, [])
        self.call(0x4D00)
        self.assertEqual(self.cpu.r[CPU.AX], 0)

    def test_the_memory_of_the_child_and_its_environment_comes_back(self):
        blocks = dict(self.state.memory.blocks)
        owners = dict(self.state.memory.owners)
        self.start("CHILD.COM", com(EXIT(7)))
        self.assertEqual(len(self.state.memory.blocks), len(blocks) + 2)
        self.run_child()
        self.assertEqual(self.state.memory.blocks, blocks)
        self.assertEqual(self.state.memory.owners, owners)

    def test_what_the_child_allocated_comes_back_too(self):
        blocks = dict(self.state.memory.blocks)
        code = bytes([0xB4, 0x4A, 0xBB, 0x00, 0x10, 0xCD, 0x21,  # mov ah,4A ; mov bx,1000h ; int 21h (ES is the PSP)
                      0xB4, 0x48, 0xBB, 0x10, 0x00, 0xCD, 0x21]) + EXIT(0)  # mov ah,48 ; mov bx,10h ; int 21h ; exit
        self.start("CHILD.COM", com(code))
        for _ in range(6):
            self.cpu.step()
        self.assertEqual(len(self.state.memory.blocks), len(blocks) + 3)
        self.assertIsNone(self.failed())
        self.run_child()
        self.assertEqual(self.state.memory.blocks, blocks)

    def test_ah_62h_is_the_psp_of_the_program_that_runs(self):
        self.call(0x6200)
        self.assertEqual(self.cpu.r[CPU.BX], PARENT)
        self.start("CHILD.COM", com(EXIT(7)))
        child = self.state.psp_seg
        self.cpu.mem[child * 16 + 0x200:child * 16 + 0x204] = bytes([0xB4, 0x51, 0xCD, 0x21])
        self.cpu.ip = 0x200
        self.cpu.step()
        self.cpu.step()
        self.assertEqual(self.cpu.r[CPU.BX], child)

    def test_a_child_can_start_a_child_of_its_own_and_it_all_unwinds(self):
        blocks = dict(self.state.memory.blocks)
        self.program("GRAND.COM", com(EXIT(5)))
        code = bytes([0xB4, 0x4A, 0xBB, 0x00, 0x10, 0xCD, 0x21,  # keep 64 KB: the rest is free for the grandchild
                      0xB8, 0x00, 0x4B, 0xBA, 0x40, 0x01, 0xBB, 0x60, 0x01, 0xCD, 0x21,  # exec: name 140h, block 160h
                      0xB4, 0x4D, 0xCD, 0x21,  # ah = 4Dh: what did it end with?
                      0xB4, 0x4C, 0xCD, 0x21])  # and exit with that code (AL)
        self.start("CHILD.COM", com(code, "C:\\GRAND.COM", name_at=0x40).ljust(0x60) + bytes(14))
        depth = []
        for _ in range(40):
            depth.append(len(self.state.parents))
            if not self.state.parents:
                break
            self.cpu.step()
        self.assertIn(2, depth)
        self.assertEqual(self.state.parents, [])
        self.assertEqual(self.state.memory.blocks, blocks)
        self.call(0x4D00)
        self.assertEqual(self.cpu.r[CPU.AX], 5)


class TestWhatTheChildIsGiven(ProcessTest):
    def test_the_parents_environment_is_copied_and_the_name_of_the_child_is_added(self):
        self.start("CHILD.COM", com(EXIT(7)))
        env = self.at_psp(0x2C)
        text = bytes(self.cpu.rb(env, i) for i in range(80))
        self.assertTrue(text.startswith(b"COMSPEC=C:\\COMMAND.COM\0PATH=C:\\\0\0\x01\0C:\\CHILD.COM\0"), text)

    def test_the_block_of_the_environment_holds_all_of_it_in_whole_paragraphs(self):
        for strings in (b"\0", b"A=1\0\0", b"A=1234567890\0\0", b"A=" + b"x" * 40 + b"\0\0"):
            self.cpu.mem[0x20800:0x20800 + len(strings)] = strings
            self.start("CHILD.COM", com(EXIT(7)), env=0x2080)
            env = self.at_psp(0x2C)
            needed = len(psp.environment("C:\\CHILD.COM", [s for s in strings.split(b"\0") if s]))
            self.assertEqual(self.state.memory.blocks[env], (needed + 15) // 16, strings)
            self.run_child()

    def test_the_environment_the_parameter_block_names_is_the_one_the_child_gets(self):
        for i, byte in enumerate(b"A=1\0BB=22\0\0"):
            self.cpu.wb(0x2000, 0x0800 + i, byte)
        self.start("CHILD.COM", com(EXIT(7)), env=0x2080)
        env = self.at_psp(0x2C)
        text = bytes(self.cpu.rb(env, i) for i in range(40))
        self.assertTrue(text.startswith(b"A=1\0BB=22\0\0\x01\0C:\\CHILD.COM\0"), text)

    def test_the_environment_of_the_child_is_its_own_block_and_not_the_one_of_the_parent(self):
        for i, byte in enumerate(b"A=1\0\0"):
            self.cpu.wb(0x2000, 0x0800 + i, byte)
        self.start("CHILD.COM", com(EXIT(7)), env=0x2080)
        self.assertNotEqual(self.at_psp(0x2C), 0x2080)
        self.assertEqual(self.cpu.rb(0x2080, 0), ord("A"))  # and the original is as it was

    def test_a_long_command_tail_comes_whole(self):
        tail = bytes([120]) + bytes(range(65, 65 + 120)) + b"\r"
        for i, byte in enumerate(tail):
            self.cpu.wb(0x2000, 0x0600 + i, byte)
        self.start("CHILD.COM", com(EXIT(7)), tail=0x2000 << 16 | 0x0600)
        child = self.state.psp_seg
        self.assertEqual(bytes(self.cpu.mem[child * 16 + 0x80:child * 16 + 0x80 + len(tail)]), tail)

    def test_the_fcbs_are_copied_in_all_their_16_bytes(self):
        for i in range(16):
            self.cpu.wb(0x2000, 0x0700 + i, 0x41 + i)
            self.cpu.wb(0x2000, 0x0710 + i, 0x61 + i)
        self.start("CHILD.COM", com(EXIT(7)), fcb1=0x2000 << 16 | 0x0700, fcb2=0x2000 << 16 | 0x0710)
        child = self.state.psp_seg
        self.assertEqual(bytes(self.cpu.mem[child * 16 + 0x5C:child * 16 + 0x6C]), bytes(range(0x41, 0x51)))
        self.assertEqual(bytes(self.cpu.mem[child * 16 + 0x6C:child * 16 + 0x7C]), bytes(range(0x61, 0x71)))

    def test_the_command_tail_and_the_two_fcbs_are_copied_into_the_psp(self):
        for i, byte in enumerate(b"\x05 /abc\r"):
            self.cpu.wb(0x2000, 0x0600 + i, byte)
        for i, byte in enumerate(b"\x03FIRST      "):
            self.cpu.wb(0x2000, 0x0700 + i, byte)
        for i, byte in enumerate(b"\x04SECOND     "):
            self.cpu.wb(0x2000, 0x0710 + i, byte)
        self.start("CHILD.COM", com(EXIT(7)), tail=0x2000 << 16 | 0x0600, fcb1=0x2000 << 16 | 0x0700,
                   fcb2=0x2000 << 16 | 0x0710)
        child = self.state.psp_seg
        self.assertEqual(bytes(self.cpu.mem[child * 16 + 0x80:child * 16 + 0x87]), b"\x05 /abc\r")
        self.assertEqual(bytes(self.cpu.mem[child * 16 + 0x5C:child * 16 + 0x68]), b"\x03FIRST      ")
        self.assertEqual(bytes(self.cpu.mem[child * 16 + 0x6C:child * 16 + 0x78]), b"\x04SECOND     ")

    def test_the_child_shares_the_open_files_of_the_parent_and_closes_only_its_own(self):
        self.call(0x3D00, "NOTES.TXT")
        inherited = self.state.handles.get(self.cpu.r[CPU.AX])
        code = bytes([0xB8, 0x00, 0x3D, 0xBA, 0x20, 0x01, 0xCD, 0x21]) + EXIT(0)  # open NOTES.TXT, then exit
        self.start("CHILD.COM", com(code, "NOTES.TXT"))
        parents_table = self.state.parents[-1].handles
        self.assertIsNot(self.state.handles, parents_table)
        self.assertIs(self.state.handles.get(5), inherited)
        for _ in range(3):
            self.cpu.step()
        own = self.state.handles.get(6)
        self.assertIsInstance(own, OpenFile)
        self.assertIsNone(parents_table.get(6))
        self.run_child()
        self.assertTrue(own.file.closed)
        self.assertFalse(inherited.file.closed)
        self.assertIs(self.state.handles, parents_table)
        self.assertIs(self.state.handles.get(5), inherited)

    def test_a_handle_the_child_closes_is_still_open_for_the_parent(self):
        self.call(0x3D00, "NOTES.TXT")
        inherited = self.state.handles.get(5)
        code = bytes([0xB4, 0x3E, 0xBB, 0x05, 0x00, 0xCD, 0x21]) + EXIT(0)  # close handle 5, exit
        self.start("CHILD.COM", com(code))
        for _ in range(3):
            self.cpu.step()
        self.assertIsNone(self.state.handles.get(5))
        self.assertFalse(inherited.file.closed)
        self.run_child()
        self.assertFalse(inherited.file.closed)
        self.assertIs(self.state.handles.get(5), inherited)


class TestExeProgram(ProcessTest):
    def test_the_exe_is_placed_after_its_psp_relocated_and_the_registers_are_from_its_header(self):
        self.start("CHILD.EXE", exe(EXIT(9)))
        self.assertIsNone(self.failed())
        cpu = self.cpu
        child = self.state.psp_seg
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (child + 0x10, 0))
        self.assertEqual((cpu.s[CPU.SS], cpu.r[CPU.SP]), (child + 0x11, 0x100))
        self.assertEqual((cpu.s[CPU.DS], cpu.s[CPU.ES]), (child, child))
        self.assertEqual(cpu.rw(child + 0x10, 0x10), child + 0x10)  # the word that the relocation names
        self.assertEqual(bytes(cpu.mem[(child + 0x10) * 16:(child + 0x10) * 16 + 5]), EXIT(9))

    def test_the_exe_gets_what_its_header_asks_for_and_no_more(self):
        self.start("CHILD.EXE", exe(EXIT(9), minalloc=0x10, maxalloc=0x20))
        child = self.state.psp_seg
        self.assertEqual(self.state.memory.blocks[child], 0x10 + 2 + 0x20)
        self.assertEqual(self.at_psp(0x02), child + 0x32)
        self.assertGreater(self.state.memory.largest(), 0)

    def test_an_exe_that_wants_everything_gets_everything(self):
        self.start("CHILD.EXE", exe(EXIT(9), maxalloc=0xFFFF))
        child = self.state.psp_seg
        self.assertEqual(child + self.state.memory.blocks[child], dos.MEMORY_TOP_SEG)

    def test_a_maxalloc_below_minalloc_is_taken_as_the_minimum(self):
        self.start("CHILD.EXE", exe(EXIT(9), minalloc=0x30, maxalloc=0x10))
        self.assertEqual(self.state.memory.blocks[self.state.psp_seg], 0x12 + 0x30)

    def test_the_exe_ends_and_its_code_can_be_asked_for(self):
        self.start("CHILD.EXE", exe(EXIT(9)))
        self.run_child()
        self.call(0x4D00)
        self.assertEqual(self.cpu.r[CPU.AX], 9)

    def test_a_file_is_an_exe_by_its_first_bytes_not_by_its_name(self):
        self.start("CHILD.OVL", exe(EXIT(9)))
        self.assertEqual(self.cpu.ip, 0)
        self.assertEqual(self.cpu.s[CPU.CS], self.state.psp_seg + 0x10)

    def test_a_com_with_an_exe_name_is_a_com(self):
        self.start("CHILD.EXE", com(EXIT(7)))
        self.assertEqual(self.cpu.ip, 0x100)


class TestWhenItCannotBeStarted(ProcessTest):
    def unchanged(self, memory, registers):
        self.assertEqual(self.state.memory.blocks, memory)
        self.assertEqual(self.state.parents, [])
        self.assertEqual(self.state.psp_seg, PARENT)
        self.assertEqual(self.state.dta, (PARENT, 0x80))
        self.assertEqual((self.cpu.s[CPU.CS], self.cpu.ip), (0x0800, 2))
        self.assertEqual(self.cpu.r[CPU.BX], registers)

    def test_a_program_that_is_not_there_is_error_2(self):
        memory = dict(self.state.memory.blocks)
        self.start("NOTHING.COM")
        self.assertEqual(self.failed(), 2)
        self.unchanged(memory, 0x0400)

    def test_a_directory_cannot_be_started(self):
        memory = dict(self.state.memory.blocks)
        self.start("GAMES")
        self.assertIsNotNone(self.failed())
        self.unchanged(memory, 0x0400)

    def test_a_drive_that_is_not_there_is_error_15(self):
        self.call(0x4B00, "Q:\\X.COM", bx=self.parameters())
        self.assertEqual(self.failed(), 15)

    def test_an_exe_that_needs_more_than_the_free_memory_is_error_8_and_nothing_is_left_taken(self):
        memory = dict(self.state.memory.blocks)
        self.start("BIG.EXE", exe(EXIT(9), minalloc=0xF000, maxalloc=0xF000))
        self.assertEqual(self.failed(), 8)
        self.unchanged(memory, 0x0400)

    def test_an_exe_that_needs_exactly_what_is_free_does_start(self):
        env = (len(psp.environment("C:\\EXACT.EXE")) + 15) // 16
        free = dos.MEMORY_TOP_SEG - FIRST_FREE - env
        self.start("EXACT.EXE", exe(EXIT(9), minalloc=free - 0x12, maxalloc=free - 0x12))
        self.assertIsNone(self.failed())
        self.run_child()
        self.start("MORE.EXE", exe(EXIT(9), minalloc=free - 0x11, maxalloc=free - 0x11))
        self.assertEqual(self.failed(), 8)

    def test_a_com_needs_64_kb_and_does_not_start_in_less(self):
        self.cpu.s[CPU.ES] = PARENT
        self.call(0x4A00, bx=0x8001)  # what is left, 0FFFh paragraphs, is a paragraph short of 64 KB
        self.cpu.s[CPU.ES] = 0x3000
        memory = dict(self.state.memory.blocks)
        self.start("CHILD.COM", com(EXIT(7)))
        self.assertEqual(self.failed(), 8)
        self.assertEqual(self.state.memory.blocks, memory)

    def test_an_exe_with_half_a_header_is_error_11(self):
        memory = dict(self.state.memory.blocks)
        self.start("CUT.EXE", b"MZ\x10\x00")
        self.assertEqual(self.failed(), 11)
        self.unchanged(memory, 0x0400)

    def test_the_other_ways_to_exec_stop_the_emulator(self):
        for al in (1, 3, 5):
            with self.assertRaisesRegex(NotImplementedError, f"AH=4B AL={al:02X}"):
                self.call(0x4B00 | al, "C:\\X.COM", bx=self.parameters())


class TestWithoutAParent(unittest.TestCase):
    def test_the_first_program_ends_the_emulator_with_its_code(self):
        from mydos.cpu import Exit
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x0800
        cpu.mem[0x8000:0x8002] = bytes([0xCD, 0x21])
        cpu.r[CPU.AX] = 0x4C03
        with self.assertRaises(Exit) as caught:
            cpu.step()
        self.assertEqual(caught.exception.code, 3)

    def test_int_20h_ends_it_with_code_0(self):
        from mydos.cpu import Exit
        cpu = CPU()
        dos.install(cpu)
        cpu.s[CPU.CS] = 0x0800
        cpu.mem[0x8000:0x8002] = bytes([0xCD, 0x20])
        with self.assertRaises(Exit) as caught:
            cpu.step()
        self.assertEqual(caught.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
