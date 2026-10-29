"""Loading an LE program and starting it in the flat mode (step 3b1): where the objects go, the fixups, the selectors,
the registers at the start, and what happens when the program cannot be loaded."""
import struct
import unittest
from pathlib import Path

from mydos import flags, le, loader, psp
from mydos.cpu import CPU, Exit
from mydos.flat import REAL_MODE, Bases
from mydos.xmem import PAGE, START, ExtendedMemory
from tests.le_builder import F, Obj, RW, RX, build_bound, build_program

SMKP = Path(__file__).resolve().parent.parent / "XCOMA" / "UFOEXE" / "SMKP.EXE"
PSP, ENV = 0x1000, 0x0FF0
DATA_AT = 0x12345678
FUNC = 0x27
# After 16 HLTs (a wrong entry shows), at offset 10h:
#   mov eax,[data] / mov ebx,esp / mov cx,es / mov dx,ds / call func / mov si,cs / int 21h / func: mov edi,5A5Ah / ret
CODE = (b"\xF4" * 16 + bytes.fromhex("a100000000") + bytes.fromhex("89e3") + bytes.fromhex("668cc1") +
        bytes.fromhex("668cda") + bytes.fromhex("e800000000") + bytes.fromhex("668cce") + bytes.fromhex("cd21") +
        bytes.fromhex("bf5a5a0000c3"))
DATA = struct.pack("<I", DATA_AT)
STACK = 0x1800


def make_program(**keywords):
    """Two objects: the code (0x1800 bytes, two pages) and the data (0x1800, two pages), with the fixups the code
    needs to run and the three that write selectors into the data."""
    data = DATA.ljust(0x100, b"\xCC")
    fixups = [F(7, 2, 0, obj=1, at=0x11), F(8, 1, FUNC, obj=1, at=0x1E),
              F(2, 1, obj=2, at=0x20), F(6, 1, 0x10, obj=2, at=0x30), F(3, 2, 0x34, obj=2, at=0x40)]
    objects = [Obj(CODE, size=0x1800, base=0x10000, flags=RX), Obj(data, size=0x1800, base=0x30000, flags=RW)]
    return build_program(objects, entry=(1, 0x10), stack=(2, STACK), fixups=fixups, **keywords)


def end_of_the_run(cpu):
    raise Exit(0)


class Machine(unittest.TestCase):
    def setUp(self):
        self.cpu = CPU()
        psp.build(self.cpu, PSP, ENV, 0x9000, "C:\\GAME.EXE")
        self.extended = ExtendedMemory()
        self.cpu.int_handlers[0x21] = end_of_the_run

    def load(self, data=None):
        loader.load_le(self.cpu, make_program() if data is None else data, PSP, self.extended)

    def run_to_the_end(self, limit=1000):
        for _ in range(limit):
            try:
                self.cpu.step()
            except Exit:
                return
        self.fail("the program did not end")

    def dword(self, address):
        return struct.unpack_from("<I", self.cpu.mem, address)[0]


class TestTheObjects(Machine):
    def test_they_go_one_after_another_from_the_first_megabyte_each_in_whole_pages(self):
        self.load()
        self.assertEqual(self.extended.top, START + 4 * PAGE)
        self.assertEqual((self.cpu.mem[START], self.cpu.mem[START + 0x10]), (0xF4, 0xA1))
        self.assertEqual(self.dword(START + 2 * PAGE), DATA_AT)
        self.assertEqual(bytes(self.cpu.mem[START + 2 * PAGE + 4:START + 2 * PAGE + 0x20]), b"\xCC" * 0x1C)

    def test_what_is_in_the_pages_beyond_an_object_is_zeros_and_what_was_there_before_is_gone(self):
        self.cpu.mem[START + 0x1800:START + 0x2000] = b"\xEE" * 0x800
        self.cpu.mem[START + 2 * PAGE + 0x1800:START + 4 * PAGE] = b"\xEE" * 0x800
        self.load()
        self.assertEqual(bytes(self.cpu.mem[START + 0x1800:START + 0x2000]), bytes(0x800))
        self.assertEqual(bytes(self.cpu.mem[START + 2 * PAGE + 0x1800:START + 4 * PAGE]), bytes(0x800))
        self.assertEqual(bytes(self.cpu.mem[START + 2 * PAGE + 0x100:START + 2 * PAGE + 0x1800]), bytes(0x1700))

    def test_nothing_beyond_the_last_page_of_the_last_object_is_touched(self):
        self.cpu.mem[START + 4 * PAGE] = 0x99
        self.load()
        self.assertEqual(self.cpu.mem[START + 4 * PAGE], 0x99)

    def test_a_program_bound_to_the_extender_is_loaded_the_same(self):
        self.load(build_bound(make_program()))
        self.assertEqual(self.dword(START + 2 * PAGE), DATA_AT)
        self.assertEqual(self.cpu.ip, START + 0x10)

    def test_the_next_program_goes_after_it_and_one_that_is_taken_back_leaves_its_room(self):
        self.load()
        mark = self.extended.top
        self.load()
        self.assertEqual(self.extended.top, mark + 4 * PAGE)
        self.assertEqual(self.cpu.ip, mark + 0x10)
        self.extended.release_from(mark)
        self.load()
        self.assertEqual(self.cpu.ip, mark + 0x10)
        self.assertEqual(self.extended.top, mark + 4 * PAGE)


class TestTheFixups(Machine):
    def test_they_are_made_for_where_the_objects_went(self):
        self.load()
        code, data = START, START + 2 * PAGE
        self.assertEqual(self.dword(code + 0x11), data)  # an offset in the data object
        self.assertEqual(self.dword(code + 0x1E), FUNC - (0x1E + 4))  # a distance inside one object
        self.assertEqual(struct.unpack_from("<H", self.cpu.mem, data + 0x20)[0], loader.CODE_SELECTOR)
        self.assertEqual(self.dword(data + 0x30), code + 0x10)
        self.assertEqual(struct.unpack_from("<H", self.cpu.mem, data + 0x34)[0], loader.CODE_SELECTOR)
        self.assertEqual(struct.unpack_from("<HH", self.cpu.mem, data + 0x40), ((data + 0x34) & 0xFFFF,
                                                                                loader.DATA_SELECTOR))


class TestTheStart(Machine):
    def test_the_machine_is_in_the_flat_mode_whatever_it_was_in_before(self):
        self.cpu.use_flat_memory(Bases({0x77: 0}))  # a parent that is an LE program itself
        self.cpu.ip_mask, self.cpu.stack32, self.cpu.code32 = 0xFFFFFFFF, True, True
        self.load()
        mode = self.cpu.get_mode()
        self.assertEqual((mode["flat"], mode["ip_mask"], mode["stack32"], mode["code32"]),
                         (True, 0xFFFFFFFF, True, True))
        self.assertEqual(mode["bases"], {loader.CODE_SELECTOR: 0, loader.DATA_SELECTOR: 0, PSP: PSP * 16,
                                         ENV: ENV * 16})

    def test_a_machine_in_real_mode_goes_to_the_flat_one(self):
        self.assertEqual(self.cpu.get_mode(), REAL_MODE)
        self.load()
        self.assertTrue(self.cpu.flat)

    def test_the_registers_the_selectors_the_entry_and_the_stack(self):
        cpu = self.cpu
        cpu.r[:], cpu.rh[:], cpu.s[:] = [7] * 8, [9] * 8, [0x1111] * 6  # what the one before left
        self.load()
        self.assertEqual([cpu.get32(n) for n in range(8) if n != CPU.SP], [0] * 7)
        self.assertEqual(cpu.get32(CPU.SP), START + 2 * PAGE + STACK)
        self.assertEqual(cpu.s[:], [PSP, loader.CODE_SELECTOR, loader.DATA_SELECTOR, loader.DATA_SELECTOR, 0, 0])
        self.assertEqual(cpu.ip, START + 0x10)

    def test_the_program_runs_from_its_entry_and_sees_its_data_its_stack_and_its_calls(self):
        self.load()
        self.run_to_the_end()
        cpu = self.cpu
        self.assertEqual(cpu.get32(CPU.AX), DATA_AT)
        self.assertEqual(cpu.get32(CPU.BX), START + 2 * PAGE + STACK)
        self.assertEqual((cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI]), (PSP, loader.DATA_SELECTOR,
                                                                         loader.CODE_SELECTOR))
        self.assertEqual(cpu.get32(CPU.DI), 0x5A5A)  # the call and the return worked in the flat mode

    def test_the_psp_and_the_environment_are_read_by_their_segments_as_selectors(self):
        self.load()
        cpu = self.cpu
        self.assertEqual(cpu.rw(PSP, 0), 0x20CD)  # INT 20h at the start of the PSP
        env = cpu.rw(PSP, 0x2C)
        self.assertEqual(env, ENV)
        self.assertEqual(bytes(cpu.rb(env, i) for i in range(8)), b"COMSPEC=")
        self.assertEqual(cpu.rb(PSP, 0x81), 0x0D)  # the command tail

    def test_the_registers_of_the_machine_that_loaded_it_are_not_kept_in_the_32_bit_halves(self):
        self.cpu.rh[:] = [0xFFFF] * 8
        self.load()
        self.assertEqual(self.cpu.rh[CPU.AX], 0)


class TestWhenItCannotBeLoaded(Machine):
    def assert_untouched(self, top):
        self.assertEqual(self.extended.top, top)
        self.assertEqual(bytes(self.cpu.mem[START:START + 4 * PAGE]), bytes(4 * PAGE))
        self.assertEqual(self.cpu.get_mode(), REAL_MODE)

    def test_not_enough_room_for_the_objects_says_so_and_gives_everything_back(self):
        self.extended = ExtendedMemory(START, START + 3 * PAGE)  # the code goes in, the data does not
        with self.assertRaisesRegex(MemoryError, "no room for the object 2"):
            self.load()
        self.assert_untouched(START)

    def test_a_fixup_that_is_not_done_leaves_the_memory_and_the_pointer_as_they_were(self):
        objects = [Obj(CODE, size=0x1800, flags=RX), Obj(DATA, size=0x1800, base=0x30000, flags=RW)]
        data = build_program(objects, entry=(1, 0x10), stack=(2, STACK), fixups=[F(1, 2, 0, obj=1, at=0x11)])
        self.extended.allocate(PAGE)  # something is already there
        with self.assertRaisesRegex(le.LeError, "kind 1"):
            self.load(data)
        self.assertEqual(self.extended.top, START + PAGE)
        self.assertEqual(bytes(self.cpu.mem[START + PAGE:START + 5 * PAGE]), bytes(4 * PAGE))

    def test_a_file_that_is_not_an_le_program_is_an_error_of_the_format(self):
        with self.assertRaises(le.LeError):
            self.load(b"MZ" + bytes(100))


@unittest.skipUnless(SMKP.exists(), "the game is not installed")
class TestTheGame(Machine):
    def test_the_objects_are_the_pages_of_the_file_with_the_fixups_made_and_the_start_is_the_start_code(self):
        data = SMKP.read_bytes()
        self.load(data)
        image = le.LeImage(data, le.locate(data))
        bases, top = {}, START
        for obj in image.objects:
            bases[obj.number] = top
            top += (obj.size + PAGE - 1) // PAGE * PAGE
        self.assertEqual(self.extended.top, top)
        owner = {}
        for obj in image.objects:
            for index in range(obj.page_count):
                owner[obj.first_page - 1 + index] = (obj, index * image.page_size)
        checked = 0
        for fixup in image.fixups():
            if fixup.source != le.SOURCE_OFFSET_32:
                continue
            obj, start = owner[fixup.page]
            expected = bases[fixup.target_object] + fixup.target_offset + fixup.additive
            self.assertEqual(self.dword(bases[obj.number] + start + fixup.offset), expected & 0xFFFFFFFF)
            checked += 1
        self.assertGreater(checked, 1000)
        self.assertEqual(self.cpu.ip, bases[image.entry_object] + image.entry_offset)
        self.assertEqual(self.cpu.get32(CPU.SP), bases[image.stack_object] + image.stack_offset)

    def test_the_start_code_is_answered_by_the_extender_that_the_machine_plays(self):
        from mydos import dos
        dos.install(self.cpu)
        answers = []

        def watch(number):
            handler = self.cpu.int_handlers[number]

            def watched(cpu):
                asked = cpu.get32(CPU.AX)
                handler(cpu)
                answers.append((number, asked, cpu.get32(CPU.AX), cpu.get_flag(flags.CF)))
            self.cpu.int_handlers[number] = watched
        watch(0x21)
        watch(0x31)
        self.load(SMKP.read_bytes())
        for _ in range(10000):
            if len(answers) == 3:
                break
            self.cpu.step()
        self.assertEqual([call[:2] for call in answers], [(0x21, 0x3000), (0x21, 0xFF00), (0x31, 0x0006)])
        self.assertNotEqual(answers[1][2] & 0xFF, 0)  # DOS/4G is here
        self.assertEqual([call[3] for call in answers], [False] * 3)
        gs = self.cpu.s[CPU.GS]
        self.assertEqual(self.cpu.bases[gs], 0)
        self.assertEqual(self.cpu.bases[loader.DATA_SELECTOR], 0)  # the base of the data, which the 0006 was asked of

    def test_it_runs_to_its_first_call_of_dos_asking_who_is_there(self):
        calls = []
        self.cpu.int_handlers[0x21] = lambda cpu: (calls.append((cpu.r[CPU.AX] >> 8, cpu.get32(CPU.BX))),
                                                   end_of_the_run(cpu))
        self.load(SMKP.read_bytes())
        self.run_to_the_end(100)
        self.assertEqual(calls, [(0x30, 0x50484152)])  # AH=30h with 'PHAR': is it Phar Lap?
        self.assertEqual(self.cpu.get32(CPU.SP) % 4, 0)


if __name__ == "__main__":
    unittest.main()
