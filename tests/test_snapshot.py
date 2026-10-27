import contextlib
import io
import json
import mmap
import os
import tempfile
import tomllib
import unittest
import zipfile
from collections import deque
from pathlib import Path

from mydos import config, dos, machine, psp, snapshot
from mydos.cpu import CPU
from mydos.handles import OpenFile
from mydos.volumes import Volumes

# mov si,300h ; loop: in al,40h ; mov [si],al ; inc si ; jmp loop      writes what the timer says, byte after byte
TIMER_LOOP = bytes([0xBE, 0x00, 0x03, 0xE4, 0x40, 0x88, 0x04, 0x46, 0xEB, 0xF9])
# mov si,300h ; loop: mov ah,0 ; int 16h ; mov [si],ax ; add si,2 ; jmp loop     keeps the keys it is given
KEY_LOOP = bytes([0xBE, 0x00, 0x03, 0xB4, 0x00, 0xCD, 0x16, 0x89, 0x04, 0x83, 0xC6, 0x02, 0xEB, 0xF5])

TEXT = """
    [disks.C]
    type = 'hdd'
    [disks.D]
    type = 'hdd'
    [mounts]
    C = { type = "dir", path = "." }
    D = { type = "tmpfs" }
"""


class TestValues(unittest.TestCase):
    def round_trip(self, value):
        blobs = []
        packed = snapshot.pack(value, blobs)
        return snapshot.unpack(json.loads(json.dumps(packed)), blobs)

    def test_what_json_holds_goes_as_it_is(self):
        for value in (None, True, False, 0, -7, 2 ** 40, 1.5, "text", [1, [2, "a"]]):
            self.assertEqual(self.round_trip(value), value)
            self.assertIs(type(self.round_trip(value)), type(value))

    def test_bytes_and_bytearrays_go_to_the_list_of_blobs_and_keep_their_kind(self):
        blobs = []
        packed = snapshot.pack([b"ab", bytearray(b"cd")], blobs)
        self.assertEqual(blobs, [b"ab", b"cd"])
        back = snapshot.unpack(packed, blobs)
        self.assertEqual((back[0], type(back[0])), (b"ab", bytes))
        self.assertEqual((back[1], type(back[1])), (bytearray(b"cd"), bytearray))

    def test_a_bytearray_that_comes_back_is_a_copy_that_can_be_changed(self):
        blobs = []
        packed = snapshot.pack(bytearray(b"cd"), blobs)
        first, second = snapshot.unpack(packed, blobs), snapshot.unpack(packed, blobs)
        first[0] = 0
        self.assertEqual(second, bytearray(b"cd"))
        self.assertEqual(blobs, [b"cd"])

    def test_tuples_and_deques_and_dicts_with_any_keys_come_back_as_they_were(self):
        value = {(1, 2): [(3, 4), deque([5, 6])], 7: {"a": None}, "b": (), 0x3C4: b"\0"}
        back = self.round_trip(value)
        self.assertEqual(back, value)
        self.assertIsInstance(back[(1, 2)][0], tuple)
        self.assertIsInstance(back[(1, 2)][1], deque)
        self.assertEqual(list(back), list(value))  # in the same order

    def test_what_cannot_be_saved_is_said(self):
        for value in (object(), {1, 2}, lambda: 0, range(3)):
            with self.assertRaisesRegex(TypeError, "cannot save a"):
                snapshot.pack([value], [])

    def test_a_kind_of_value_from_a_later_version_is_said(self):
        with self.assertRaisesRegex(snapshot.SnapshotError, r"\$new"):
            snapshot.unpack({"$new": 1}, [])


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.base = root / "drive"
        self.out = root / "out"
        self.base.mkdir()
        self.out.mkdir()
        (self.base / "NOTES.TXT").write_bytes(b"some notes")
        for name in ("A.TXT", "B.TXT", "C.TXT"):
            (self.base / name).write_bytes(name.encode())
        (self.base / "CHILD.COM").write_bytes(TIMER_LOOP)
        self.machines = []

    def make(self):
        """A new machine on the drives of this test."""
        volumes = Volumes(config.parse(tomllib.loads(TEXT), self.base))
        computer = machine.build(volumes)
        self.machines.append(computer)
        self.addCleanup(self.close_files, computer)
        return computer

    @staticmethod
    def close_files(computer):
        """Close what the machine has open, in the table it has now and in those of its parents."""
        computer.state.handles.close_all()
        for parent in computer.state.parents:
            parent.handles.close_all()

    def call(self, computer, ax, name=None, bx=0, cx=0, dx=0x0100, es=0x3000):
        """INT 21h with these registers, from a stub of a program at 0800:0000. A name goes to DS:DX."""
        cpu = computer.cpu
        cpu.s[CPU.CS], cpu.s[CPU.DS], cpu.s[CPU.ES] = 0x0800, 0x2000, es
        cpu.mem[0x8000:0x8002] = bytes([0xCD, 0x21])
        if name is not None:
            for i, byte in enumerate(name.encode() + b"\0"):
                cpu.wb(0x2000, dx + i, byte)
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = ax, bx, cx, dx
        cpu.ip = 0
        cpu.step()
        self.assertFalse(cpu.get_flag(1), f"INT 21h AX={ax:04X} failed with {cpu.r[CPU.AX]}")

    def busy(self, computer):
        """A machine with a parent that has files open and a search in progress, and a child that is running."""
        cpu = computer.cpu
        psp.build(cpu, 0x1000, 0x0FF0, dos.MEMORY_TOP_SEG, "C:\\PARENT.COM")
        self.call(computer, 0x4A00, bx=0x3100, es=0x1000)  # the parent keeps what it needs
        self.call(computer, 0x3D00, "NOTES.TXT")  # handle 5
        self.call(computer, 0x3F00, bx=5, cx=3, dx=0x0300)  # reads "som"
        self.call(computer, 0x4200, bx=5, cx=0, dx=5)  # and goes on to 5
        self.call(computer, 0x3C00, "D:\\OUT.DAT")  # handle 6, in the memory disk
        for i, byte in enumerate(b"hello!"):
            cpu.wb(0x2000, 0x0400 + i, byte)
        self.call(computer, 0x4000, bx=6, cx=6, dx=0x0400)
        self.call(computer, 0x1A00, dx=0x0500)
        self.call(computer, 0x4E00, "C:\\*.TXT", cx=0)  # a search that has more to give
        self.call(computer, 0x4B00, "C:\\CHILD.COM", bx=0x0600)  # the child, which shares what the parent has open

    def go(self, computer, steps):
        for _ in range(steps):
            computer.cpu.step()

    def state_of(self, computer):
        """The machine as plain data, but for the flag that says the picture wants drawing, which a snapshot sets."""
        data = snapshot.unpack(*snapshot.capture(computer))
        del data["vga"]["changed"]
        return data

    def same(self, a, b):
        first, second = self.state_of(a), self.state_of(b)
        self.assertEqual(list(first), list(second))
        for key in first:
            self.assertTrue(first[key] == second[key], f"{key} differs")


class TestAMachineThatGoesOn(SnapshotTest):
    def test_a_run_from_a_snapshot_is_the_run_it_was_taken_from(self):
        a = self.make()
        self.busy(a)
        self.go(a, 20)
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        self.go(a, 100)
        b = self.make()
        snapshot.load(path, b)
        self.go(b, 100)
        self.same(a, b)
        child = a.state.psp_seg
        self.assertTrue(any(a.cpu.mem[child * 16 + 0x300:child * 16 + 0x300 + 100]))  # the loop wrote what it read
        self.assertEqual(a.cpu.instructions, b.cpu.instructions)

    def test_what_the_snapshot_holds_is_the_machine_as_it_was_before_the_run_on(self):
        a = self.make()
        self.busy(a)
        self.go(a, 20)
        before = self.state_of(a)
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        self.go(a, 100)
        b = self.make()
        snapshot.load(path, b)
        after = self.state_of(b)
        for key in before:
            self.assertTrue(before[key] == after[key], f"{key} differs")

    def test_the_files_are_open_again_at_the_places_they_were(self):
        a = self.make()
        self.busy(a)
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        state = b.state
        self.assertEqual(state.handles.get(5).file.tell(), 5)
        self.assertEqual(state.handles.get(5).file.mode, "rb")  # opened for reading only, as it was
        self.assertEqual((state.handles.get(5).readable, state.handles.get(5).writable), (True, False))
        self.assertEqual(state.handles.get(5).file.read(), b"notes")
        self.assertEqual(state.handles.get(6).file.tell(), 6)
        self.assertEqual(state.handles.get(6).dos_path, "D:\\OUT.DAT")
        with b.volumes.filesystems[3].open(["OUT.DAT"]) as file:
            self.assertEqual(file.read(), b"hello!")

    def test_the_child_and_the_parent_share_their_files_after_it_as_before(self):
        a = self.make()
        self.busy(a)
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        state = b.state
        self.assertEqual(len(state.parents), 1)
        parents = state.parents[0].handles
        for handle in (5, 6):
            self.assertIs(state.handles.get(handle), parents.get(handle))
            self.assertIsInstance(state.handles.get(handle), OpenFile)
            self.assertEqual(state.handles.users[id(state.handles.get(handle))], 2)
        self.assertIs(state.handles.users, parents.users)
        self.assertEqual(state.psp_seg, a.state.psp_seg)

    def test_the_child_closing_its_file_does_not_close_the_parents_after_it_either(self):
        a = self.make()
        self.busy(a)
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        shared = b.state.handles.get(5)
        b.state.handles.close(5)
        self.assertFalse(shared.file.closed)
        b.state.parents[0].handles.close(5)
        self.assertTrue(shared.file.closed)

    def test_a_search_goes_on_where_it_was(self):
        a = self.make()
        self.busy(a)
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual(b.state.searches.last, a.state.searches.last)
        self.assertEqual(b.state.searches.rest, a.state.searches.rest)
        self.assertEqual(len(b.state.searches.rest[b.state.searches.last]), 3)
        self.assertEqual(b.state.parents[0].dta, (0x2000, 0x0500))  # the child has a DTA of its own

    def test_what_the_program_asked_for_when_it_opened_a_file_comes_back(self):
        a = self.make()
        self.busy(a)
        self.call(a, 0x3C00, "D:\\TWO.DAT")  # 7: made, so both
        self.call(a, 0x3E00, bx=7)
        self.call(a, 0x3D01, "D:\\TWO.DAT")  # 7 again: for writing only
        self.call(a, 0x3D00, "D:\\TWO.DAT")  # 8: for reading only
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        got = [(b.state.handles.get(n).readable, b.state.handles.get(n).writable) for n in (5, 6, 7, 8)]
        self.assertEqual(got, [(True, False), (True, True), (False, True), (True, False)])

    def test_the_standard_handles_come_back_as_they_were(self):
        a = self.make()
        self.busy(a)
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual(b.state.handles.slots[:5], a.state.handles.slots[:5])
        self.assertEqual([type(slot).__name__ for slot in b.state.handles.slots[:5]],
                         ["Console", "Console", "Console", "NullDevice", "NullDevice"])
        self.assertEqual(b.state.handles.slots[2].stream, "stderr")
        self.assertEqual(b.state.handles.slots[3].name, "AUX")
        self.assertEqual(b.state.parents[0].handles.slots[:5], a.state.parents[0].handles.slots[:5])

    def test_the_directories_and_the_memory_disk_come_back(self):
        a = self.make()
        self.busy(a)
        a.volumes.directories[2] = ["GAMES"]
        a.volumes.current_drive = 3
        with a.volumes.filesystems[3].create(["X.DAT"]) as file:
            file.write(b"abc")
        a.volumes.filesystems[3].mkdir(["SUB"])
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual((b.volumes.current_drive, b.volumes.directories[2]), (3, ["GAMES"]))
        self.assertEqual([entry.name for entry in b.volumes.filesystems[3].listdir([])], ["OUT.DAT", "SUB", "X.DAT"])
        with b.volumes.filesystems[3].open(["X.DAT"]) as file:
            self.assertEqual(file.read(), b"abc")

    def test_the_video_card_the_mouse_the_timer_and_the_keyboard_come_back(self):
        a = self.make()
        self.busy(a)
        a.card.planes[2][1234] = 0x5A
        a.card.latches[1] = 7
        a.card.dac[3:6] = bytes([1, 2, 3])
        a.card.registers[(0x3CE, 5)] = 0x42
        a.card.changed = False
        a.mouse.x, a.mouse.y, a.mouse.presses[1] = 11, 22, 3
        a.mouse.handler = (0x1F, 0x1234, 0x5678)
        a.timer.counters[0].reload = 1234
        a.timer.counters[2].latched = [1, 2]
        a.keyboard.press(0x1E, 0x61)
        a.keyboard.press(0x30, 0x62)
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual(b.card.planes[2][1234], 0x5A)
        self.assertEqual(b.card.latches[1], 7)
        self.assertEqual(bytes(b.card.dac[3:6]), bytes([1, 2, 3]))
        self.assertEqual(b.card.registers[(0x3CE, 5)], 0x42)
        self.assertTrue(b.card.changed)  # the picture is drawn at once
        self.assertEqual((b.mouse.x, b.mouse.y, b.mouse.presses[1]), (11, 22, 3))
        self.assertEqual(b.mouse.handler, (0x1F, 0x1234, 0x5678))
        self.assertEqual((b.timer.counters[0].reload, b.timer.counters[2].latched), (1234, [1, 2]))
        self.assertEqual(list(b.keyboard.buffer), [0x1E61, 0x3062])

    def test_the_high_halves_of_the_registers_and_the_alignment_check_flag_come_back(self):
        a = self.make()
        a.cpu.rh[:] = [0x1111, 0x2222, 0x3333, 0x4444, 0x5555, 0x6666, 0x7777, 0x8888]
        a.cpu.eflags_high = 0x4
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual((b.cpu.rh, b.cpu.eflags_high), (a.cpu.rh, 0x4))

    def test_the_high_halves_of_the_parent_come_back_too(self):
        a = self.make()
        self.busy(a)
        a.state.parents[0].high = [0x1111, 0x2222, 0x3333, 0x4444, 0x5555, 0x6666, 0x7777, 0x8888]
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual(b.state.parents[0].high, a.state.parents[0].high)

    def test_what_the_program_wrote_to_port_61h_comes_back(self):
        a = self.make()
        a.sysport.value = 0x0B
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual(b.sysport.value, 0x0B)

    def test_a_snapshot_of_a_machine_without_port_61h_leaves_it_as_built(self):
        a = self.make()
        packed, blobs = self.tampered_without(a, "sysport")
        b = self.make()
        b.sysport.value = 0x05
        snapshot.restore(b, packed, blobs)
        self.assertEqual(b.sysport.value, 0x05)

    def tampered_without(self, computer, part):
        data = snapshot.unpack(*snapshot.capture(computer))
        del data[part]
        blobs = []
        return snapshot.pack(data, blobs), blobs

    def test_the_planes_are_the_same_lists_as_before_so_that_the_card_still_uses_them(self):
        b = self.make()
        planes = b.card.planes
        first = planes[0]
        a = self.make()
        a.card.planes[0][5] = 9
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        snapshot.load(path, b)
        self.assertIs(b.card.planes, planes)
        self.assertIs(b.card.planes[0], first)
        self.assertEqual(first[5], 9)

    def test_a_snapshot_made_while_a_key_is_waited_for_goes_on_by_asking_for_it_again(self):
        taken = []

        def waiter(computer, capture):
            def wait():
                if capture and not taken:
                    taken.append(snapshot.capture(computer, rewind=True))  # inside INT 16h: ahead of the INT
                computer.keyboard.press(0x1E, ord("a"))
            return wait

        a = self.make()
        a.cpu.s[CPU.CS] = a.cpu.s[CPU.DS] = 0x1000
        a.cpu.mem[0x10100:0x10100 + len(KEY_LOOP)] = KEY_LOOP
        a.cpu.ip = 0x0100
        a.keyboard.waiter = waiter(a, True)
        self.go(a, 3)  # mov si ; mov ah,0 ; int 16h, which waits
        self.assertEqual(len(taken), 1)
        self.go(a, 40)
        b = self.make()
        snapshot.restore(b, *taken[0])
        self.assertEqual(b.cpu.instructions, 2)  # the INT is not counted: it is done again
        self.assertEqual((b.cpu.s[CPU.CS], b.cpu.ip), (0x1000, 0x0105))  # at the INT 16h
        self.assertEqual(b.cpu.mem[0x10300], 0)
        b.keyboard.waiter = waiter(b, False)
        self.go(b, 41)
        self.same(a, b)
        self.assertEqual(a.cpu.rw(0x1000, 0x300), 0x1E61)

    def test_without_a_rewind_the_machine_goes_on_from_where_it_is(self):
        a = self.make()
        a.cpu.s[CPU.CS] = 0x1000
        a.cpu.mem[0x10100:0x10100 + len(TIMER_LOOP)] = TIMER_LOOP
        a.cpu.ip = 0x0100
        self.go(a, 5)
        packed, blobs = snapshot.capture(a)
        b = self.make()
        snapshot.restore(b, packed, blobs)
        self.assertEqual((b.cpu.ip, b.cpu.instructions), (a.cpu.ip, 5))
        packed, blobs = snapshot.capture(a, rewind=True)
        snapshot.restore(b, packed, blobs)
        self.assertEqual((b.cpu.ip, b.cpu.instructions), (a.cpu.start_ip, 4))


class TestAFileOnADiscImage(SnapshotTest):
    def test_a_file_open_on_the_disc_is_open_again_at_its_place(self):
        from tests.iso_builder import build
        (self.base / "disc.iso").write_bytes(build({"DATA.BIN": bytes(range(200))}))
        text = TEXT.replace("[mounts]", "[disks.E]\n    type = 'cdrom'\n    [mounts]").replace(
            'D = { type = "tmpfs" }', 'D = { type = "tmpfs" }\n    E = { type = "iso", path = "disc.iso" }')
        volumes = Volumes(config.parse(tomllib.loads(text), self.base))
        a = machine.build(volumes)
        self.call(a, 0x3D00, "E:\\DATA.BIN")
        self.call(a, 0x3F00, bx=5, cx=10, dx=0x0300)
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        a.state.handles.close_all()
        b = machine.build(Volumes(config.parse(tomllib.loads(text), self.base)))
        snapshot.load(path, b)
        self.assertEqual(b.state.handles.get(5).file.tell(), 10)
        self.assertEqual(b.state.handles.get(5).file.read(4), bytes([10, 11, 12, 13]))
        self.assertEqual(b.state.handles.get(5).dos_path, "E:\\DATA.BIN")
        b.state.handles.close_all()


class TestWhatIsPutBack(SnapshotTest):
    def saved(self, name="x.state"):
        a = self.make()
        self.busy(a)
        path = str(self.out / name)
        snapshot.save(path, a)
        return a, path

    def test_the_rom_is_the_one_of_the_code_and_not_of_the_snapshot(self):
        a = self.make()
        a.cpu.mem[0xF0000] = 0x00  # as in a snapshot of a time before there was a routine there
        a.cpu.mem[0xC0100:0xC0109] = bytes(9)
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual(b.cpu.mem[0xF0000], 0xCB)
        self.assertEqual(bytes(b.cpu.mem[0xC0100:0xC0109]), b"mydos VGA")

    def test_a_snapshot_from_before_the_386_leaves_the_high_halves_as_built(self):
        a = self.make()
        packed, blobs = self.tampered(a, lambda data: [data["cpu"].pop(key) for key in ("rh", "eflags_high")])
        b = self.make()
        b.cpu.rh[:] = [9] * 8
        b.cpu.eflags_high = 0x4
        snapshot.restore(b, packed, blobs)
        self.assertEqual((b.cpu.rh, b.cpu.eflags_high), ([9] * 8, 0x4))

    def test_the_ram_is_the_one_of_the_snapshot(self):
        a = self.make()
        a.cpu.mem[0x400] = 0x77  # in the data area of the BIOS
        a.cpu.mem[0x12345] = 0x99
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual((b.cpu.mem[0x400], b.cpu.mem[0x12345]), (0x77, 0x99))

    def test_the_registers_the_flags_and_the_place_come_back(self):
        a = self.make()
        a.cpu.r[:] = [1, 2, 3, 4, 5, 6, 7, 8]
        a.cpu.s[:] = [9, 10, 11, 12]
        a.cpu.ip, a.cpu.flags, a.cpu.instructions = 0x1234, 0x0A47, 999
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual((b.cpu.r, b.cpu.s, b.cpu.ip, b.cpu.flags, b.cpu.instructions),
                         ([1, 2, 3, 4, 5, 6, 7, 8], [9, 10, 11, 12], 0x1234, 0x0A47, 999))
        self.assertEqual(b.cpu.start_ip, 0x1234)

    def test_the_name_of_the_program_comes_back_for_naming_a_dump(self):
        a = self.make()
        a.program = "/some/where/GAME.EXE"
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual(b.program, "/some/where/GAME.EXE")

    def test_other_drives_are_refused_in_plain_words(self):
        a, path = self.saved()
        other = tomllib.loads("[disks.C]\ntype = 'hdd'\n[mounts]\nC = { type = 'tmpfs' }\n")
        b = machine.build(Volumes(config.parse(other, self.base)))
        with self.assertRaisesRegex(snapshot.SnapshotError, "other drives"):
            snapshot.load(path, b)

    def test_the_same_drives_with_other_directories_are_fine(self):
        a, path = self.saved()
        elsewhere = self.out / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "NOTES.TXT").write_bytes(b"other notes")
        text = TEXT.replace('path = "."', f'path = "{elsewhere}"')
        b = machine.build(Volumes(config.parse(tomllib.loads(text), self.base)))
        self.addCleanup(self.close_files, b)
        snapshot.load(path, b)
        self.assertEqual(b.state.handles.get(5).file.read(), b" notes")  # position 5 of the other file: "other notes"

    def test_a_file_that_is_gone_is_said_by_name(self):
        a, path = self.saved()
        (self.base / "NOTES.TXT").unlink()
        b = self.make()
        with self.assertRaisesRegex(snapshot.SnapshotError, r"cannot open C:\\NOTES.TXT again"):
            snapshot.load(path, b)

    def test_a_snapshot_of_a_later_version_is_refused(self):
        a = self.make()
        packed, blobs = self.tampered(a, lambda data: data.update(version=snapshot.VERSION + 1))
        with self.assertRaisesRegex(snapshot.SnapshotError, "version"):
            snapshot.restore(self.make(), packed, blobs)

    def tampered(self, computer, edit):
        """The snapshot of the machine, changed by `edit(data)` before it is packed."""
        data = snapshot.unpack(*snapshot.capture(computer))
        edit(data)
        blobs = []
        return snapshot.pack(data, blobs), blobs

    def test_a_part_that_the_snapshot_lacks_stays_as_the_new_machine_made_it(self):
        a, path = self.saved()

        def edit(data):
            for part in ("vga", "timer", "keyboard", "mouse", "volumes", "files"):
                del data[part]
            for name in ("break_checking", "exit_status", "memory", "searches", "handles", "parents", "dta"):
                del data["dos"][name]
        packed, blobs = self.tampered(a, edit)
        b = self.make()
        b.mouse.x = 123
        b.state.exit_status = 4
        b.card.latches[0] = 9
        snapshot.restore(b, packed, blobs)
        self.assertEqual((b.mouse.x, b.state.exit_status, b.card.latches[0]), (123, 4, 9))
        self.assertEqual((b.state.psp_seg, b.cpu.ip, b.cpu.instructions),
                         (a.state.psp_seg, a.cpu.ip, a.cpu.instructions))
        self.assertEqual(b.state.parents, [])
        self.assertEqual(b.state.dta, (0x1000, 0x80))

    def test_a_new_field_in_the_snapshot_that_the_code_does_not_have_is_ignored(self):
        a = self.make()
        a.card.invented = 5
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertFalse(hasattr(b.card, "invented"))

    def test_the_memory_of_another_size_is_refused(self):
        a = self.make()
        packed, blobs = self.tampered(a, lambda data: data["cpu"]["mem"].update(size=1000))
        with self.assertRaisesRegex(snapshot.SnapshotError, "size"):
            snapshot.restore(self.make(), packed, blobs)

    def test_a_snapshot_of_version_1_has_the_first_megabyte_in_one_piece_and_it_is_put_at_the_start(self):
        a = self.make()
        old = bytearray(1024 * 1024)
        old[0x500], old[0x12345], old[-1] = 0x77, 0x99, 0x55
        packed, blobs = self.tampered(a, lambda data: data["cpu"].update(mem=old))
        b = self.make()
        b.cpu.mem[0x30000] = 5  # a piece of the first megabyte is replaced by what the snapshot has
        b.cpu.mem[100 << 20] = 7  # what the old snapshot knows nothing of stays
        snapshot.restore(b, packed, blobs)
        self.assertEqual((b.cpu.mem[0x500], b.cpu.mem[0x12345], b.cpu.mem[0x30000]), (0x77, 0x99, 0))
        self.assertEqual(b.cpu.mem[0xFFFFF], 0x55)  # to the last byte
        self.assertEqual(b.cpu.mem[100 << 20], 7)

    def test_a_memory_in_one_piece_that_is_bigger_than_the_memory_of_the_machine_is_refused(self):
        a = self.make()
        packed, blobs = self.tampered(a, lambda data: data["cpu"].update(mem=bytearray(2 * 1024 * 1024)))
        b = self.make()
        b.cpu.mem = mmap.mmap(-1, 1024 * 1024)
        with self.assertRaisesRegex(snapshot.SnapshotError, "size"):
            snapshot.restore(b, packed, blobs)
        exact, blobs = self.tampered(a, lambda data: data["cpu"].update(mem=bytearray(1024 * 1024)))
        snapshot.restore(b, exact, blobs)  # as big as the memory is not too big

    def test_the_memory_above_the_first_megabyte_comes_back_and_what_the_snapshot_lacks_is_zeroed(self):
        a = self.make()
        a.cpu.mem[100 << 20] = 9
        a.cpu.mem[len(a.cpu.mem) - 1] = 4
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        b = self.make()
        b.cpu.mem[0x30000] = 5
        b.cpu.mem[(50 << 20) + 17] = 7
        snapshot.load(path, b)
        self.assertEqual((b.cpu.mem[100 << 20], b.cpu.mem[len(b.cpu.mem) - 1]), (9, 4))
        self.assertEqual((b.cpu.mem[0x30000], b.cpu.mem[(50 << 20) + 17]), (0, 0))


class TestFiles(SnapshotTest):
    def test_the_file_is_a_zip_with_the_state_and_one_member_for_each_blob(self):
        a = self.make()
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            blobs = [archive.read(name) for name in names[1:]]
            state = snapshot.unpack(json.loads(archive.read("state.json")), blobs)
            self.assertEqual(state["version"], snapshot.VERSION)
            self.assertEqual(snapshot.VERSION, 2)  # what has the memory in runs
            self.assertEqual(names[0], "state.json")
            self.assertLess(len(archive.read("blob/0")), 1024 * 1024)  # the first run of the memory, not all of it
            self.assertEqual(sorted(names[1:]), sorted(f"blob/{i}" for i in range(len(names) - 1)))

    def test_the_file_appears_whole_and_nothing_is_left_beside_it(self):
        a = self.make()
        path = str(self.out / "x.state")
        snapshot.save(path, a)
        self.assertEqual(os.listdir(self.out), ["x.state"])

    def test_a_snapshot_that_cannot_be_written_leaves_nothing_and_raises(self):
        a = self.make()
        with self.assertRaises(OSError):
            snapshot.save(str(self.out / "missing" / "x.state"), a)
        self.assertEqual(os.listdir(self.out), [])

    def test_a_snapshot_that_is_written_but_cannot_take_its_place_leaves_no_part_behind(self):
        a = self.make()
        (self.out / "x.state").mkdir()  # the name is taken by a directory
        with self.assertRaises(OSError):
            snapshot.save(str(self.out / "x.state"), a)
        self.assertEqual(os.listdir(self.out), ["x.state"])

    def test_a_file_that_is_not_a_snapshot_is_said_in_plain_words(self):
        (self.out / "junk.state").write_bytes(b"not a zip at all")
        with self.assertRaisesRegex(snapshot.SnapshotError, r"cannot read the snapshot .*junk.state"):
            snapshot.load(str(self.out / "junk.state"), self.make())
        with self.assertRaisesRegex(snapshot.SnapshotError, "cannot read the snapshot"):
            snapshot.load(str(self.out / "missing.state"), self.make())

    def test_a_zip_without_the_state_is_said_too(self):
        with zipfile.ZipFile(self.out / "empty.state", "w") as archive:
            archive.writestr("other", b"x")
        with self.assertRaisesRegex(snapshot.SnapshotError, "cannot read the snapshot"):
            snapshot.load(str(self.out / "empty.state"), self.make())

    def test_a_snapshot_made_again_takes_the_place_of_the_one_before(self):
        a = self.make()
        path = str(self.out / "x.state")
        a.cpu.ip = 0x111
        snapshot.save(path, a)
        a.cpu.ip = 0x222
        snapshot.save(path, a)
        b = self.make()
        snapshot.load(path, b)
        self.assertEqual(b.cpu.ip, 0x222)


class TestSavingNearTheProgram(SnapshotTest):
    def setUp(self):
        super().setUp()
        self.here = Path(self.tmp.name) / "here"
        self.here.mkdir()
        self.old = os.getcwd()
        os.chdir(self.here)
        self.addCleanup(os.chdir, self.old)

    def save(self, computer, program, suffix=".state", rewind=False):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            path = snapshot.save_near(computer, program, suffix, rewind)
        return path, stderr.getvalue()

    def test_it_goes_beside_the_program_and_says_so(self):
        a = self.make()
        path, message = self.save(a, str(self.base / "GAME.EXE"))
        self.assertEqual(path, str(self.base / "GAME.EXE.state"))
        self.assertTrue((self.base / "GAME.EXE.state").exists())
        self.assertIn(f"state saved: {path}", message)
        self.assertEqual(os.listdir(self.here), [])

    def test_the_suffix_is_what_is_asked_for(self):
        path, _ = self.save(self.make(), str(self.base / "GAME.EXE"), ".crash.state")
        self.assertEqual(path, str(self.base / "GAME.EXE.crash.state"))

    def test_where_the_program_is_cannot_be_written_to_it_goes_to_the_current_directory(self):
        a = self.make()
        path, message = self.save(a, str(self.out / "missing" / "GAME.EXE"))
        self.assertEqual(path, str(self.here / "GAME.EXE.state"))
        self.assertTrue((self.here / "GAME.EXE.state").exists())

    def test_a_dos_name_of_the_program_gives_its_file_name_for_the_current_directory(self):
        path, _ = self.save(self.make(), "D:\\GAMES\\GAME.EXE")
        self.assertEqual(path, str(self.here / "GAME.EXE.state"))

    def test_when_it_cannot_be_done_anywhere_it_says_so_and_does_not_raise(self):
        if os.geteuid() == 0:
            self.skipTest("root can write to a directory that is read-only")
        a = self.make()
        self.here.chmod(0o555)
        self.addCleanup(self.here.chmod, 0o755)
        path, message = self.save(a, str(self.out / "missing" / "GAME.EXE"))
        self.assertIsNone(path)
        self.assertIn("no state could be saved", message)

    def test_what_goes_wrong_in_the_making_does_not_raise_either(self):
        a = self.make()
        a.mouse.handler = object()  # a thing no snapshot can hold
        path, message = self.save(a, str(self.base / "GAME.EXE"))
        self.assertIsNone(path)
        self.assertIn("no state could be saved", message)
        self.assertIn("cannot save a object", message)
        self.assertEqual([name for name in os.listdir(self.base) if "state" in name], [])


class TestQuickSave(SnapshotTest):
    def test_nothing_is_saved_until_it_is_asked_for(self):
        a = self.make()
        a.program = str(self.out / "GAME.EXE")
        quick = snapshot.QuickSave(a)
        with contextlib.redirect_stderr(io.StringIO()):
            quick(False)
        self.assertEqual(os.listdir(self.out), [])

    def test_asking_saves_at_the_next_tick_and_once(self):
        a = self.make()
        a.program = str(self.out / "GAME.EXE")
        a.cpu.ip = 0x4321
        quick = snapshot.QuickSave(a)
        quick.ask()
        self.assertEqual(os.listdir(self.out), [])  # not at once: when the emulator can
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            quick(False)
        self.assertEqual(os.listdir(self.out), ["GAME.EXE.state"])
        self.assertIn("state saved", stderr.getvalue())
        (self.out / "GAME.EXE.state").unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            quick(False)
        self.assertEqual(os.listdir(self.out), [])

    def test_inside_a_call_the_snapshot_goes_on_from_the_start_of_the_instruction(self):
        a = self.make()
        a.program = str(self.out / "GAME.EXE")
        a.cpu.ip, a.cpu.start_ip, a.cpu.instructions = 0x0107, 0x0105, 10
        quick = snapshot.QuickSave(a)
        quick.ask()
        with contextlib.redirect_stderr(io.StringIO()):
            quick(True)
        b = self.make()
        snapshot.load(str(self.out / "GAME.EXE.state"), b)
        self.assertEqual((b.cpu.ip, b.cpu.instructions), (0x0105, 9))

    def test_between_instructions_it_goes_on_from_where_the_machine_is(self):
        a = self.make()
        a.program = str(self.out / "GAME.EXE")
        a.cpu.ip, a.cpu.start_ip, a.cpu.instructions = 0x0107, 0x0105, 10
        quick = snapshot.QuickSave(a)
        quick.ask()
        with contextlib.redirect_stderr(io.StringIO()):
            quick(False)
        b = self.make()
        snapshot.load(str(self.out / "GAME.EXE.state"), b)
        self.assertEqual((b.cpu.ip, b.cpu.instructions), (0x0107, 10))

    def test_the_suffix_can_be_another(self):
        a = self.make()
        a.program = str(self.out / "GAME.EXE")
        quick = snapshot.QuickSave(a, ".quick")
        quick.ask()
        with contextlib.redirect_stderr(io.StringIO()):
            quick(False)
        self.assertEqual(os.listdir(self.out), ["GAME.EXE.quick"])


class TestBuild(SnapshotTest):
    def test_a_machine_has_its_parts_and_the_cpu_works_with_them(self):
        a = self.make()
        self.assertIs(a.cpu.keyboard, a.keyboard)
        self.assertIs(a.cpu.video, a.card)
        self.assertIs(a.state.volumes, a.volumes)
        self.assertIs(a.config, a.volumes.config)
        self.assertEqual(len(a.timer.counters), 3)
        self.assertIsNone(a.program)
        self.assertIs(a.sysport.timer, a.timer)  # port 61h runs on the clock of the timer
        self.assertEqual(a.cpu.port_readers[0x61], a.sysport.read)

    def test_two_machines_share_nothing(self):
        a, b = self.make(), self.make()
        a.cpu.mem[0x500] = 5
        a.mouse.x = 1
        a.card.planes[0][0] = 1
        self.assertEqual((b.cpu.mem[0x500], b.card.planes[0][0]), (0, 0))
        self.assertNotEqual(b.mouse.x, 1)


if __name__ == "__main__":
    unittest.main()
