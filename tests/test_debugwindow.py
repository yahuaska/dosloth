"""What the debugger window says (the plain functions), and that its buttons drive the program (a hidden window)."""
import unittest

from dosloth.cpu import CPU
from dosloth.debugger import Debugger
from dosloth.debugwindow import (DebugWindow, breakpoint_text, changed_registers, hexdump, listing, register_lines,
                               register_values, set_register, write_memory)
from dosloth.window import WindowError
from tests.test_breakpoints import machine

try:
    import tkinter
except ImportError:  # a Python without Tk
    tkinter = None


class TestViews(unittest.TestCase):
    def test_the_registers_of_real_mode(self):
        cpu = machine()
        cpu.r[CPU.AX], cpu.r[CPU.DI] = 0x1234, 0xBEEF
        cpu.flags |= 0x0041  # carry and zero
        lines = register_lines(cpu)
        self.assertEqual(lines[0], "AX=1234 CX=0000 DX=0000 BX=0000")
        self.assertEqual(lines[1], "SP=FFFE BP=0000 SI=0000 DI=BEEF")
        self.assertEqual(lines[2], "ES=0000 CS=1000 SS=1000 DS=1000")
        self.assertEqual(lines[3], "IP=0000")
        self.assertTrue(lines[4].startswith("FLAGS="))
        self.assertEqual(lines[4], "FLAGS=0043 .....Z..C")

    def test_the_flags_are_letters_when_set(self):
        cpu = machine()
        cpu.flags = 0x0002 | 0x0001 | 0x0040 | 0x0200
        self.assertEqual(register_lines(cpu)[4][-9:], "..I..Z..C")

    def test_the_listing_goes_on_from_the_next_instruction(self):
        found = listing(machine(), 3)
        self.assertEqual([(seg, off) for seg, off, _ in found], [(0x1000, 0), (0x1000, 1), (0x1000, 2)])
        self.assertEqual(found[0][2], "1000:0000  90                    nop")

    def test_the_listing_takes_instructions_of_any_length(self):
        found = listing(machine(), 5)
        self.assertEqual([off for _, off, _ in found], [0, 1, 2, 3, 6])  # mov ax, 4C07h is three bytes
        self.assertIn("mov ax", found[3][2])

    def test_a_hex_dump(self):
        cpu = machine()
        lines = hexdump(cpu, 0x1000, 0, rows=2)
        self.assertEqual(lines[0], "1000:00000000  90 90 90 B8 07 4C CD 21 00 00 00 00 00 00 00 00  .....L.!........")
        self.assertTrue(lines[1].startswith("1000:00000010  00 00"))

    def test_how_a_breakpoint_is_written(self):
        self.assertEqual(breakpoint_text(None, 0x10E04B), "10E04B")
        self.assertEqual(breakpoint_text(0x0F, 0x10E04B), "000F:10E04B")


class TestEditing(unittest.TestCase):
    def test_a_register_is_set_in_hex(self):
        cpu = machine()
        set_register(cpu, "ax=1234")
        set_register(cpu, " DI = ffff ")
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.DI]), (0x1234, 0xFFFF))

    def test_a_32_bit_name_sets_the_high_half_as_well(self):
        cpu = machine()
        set_register(cpu, "EBX=DEADBEEF")
        self.assertEqual((cpu.r[CPU.BX], cpu.rh[CPU.BX]), (0xBEEF, 0xDEAD))

    def test_segments_ip_and_flags(self):
        cpu = machine()
        set_register(cpu, "ES=2000")
        set_register(cpu, "IP=0005")
        set_register(cpu, "FLAGS=FFFF")
        self.assertEqual((cpu.s[CPU.ES], cpu.ip), (0x2000, 5))
        self.assertEqual(cpu.flags & 0x0002, 0x0002)  # the bit that is always there
        self.assertEqual(cpu.flags & 0x8000, 0)  # and not the one that real mode never has
        set_register(cpu, "EIP=7")
        self.assertEqual(cpu.ip, 7)
        set_register(cpu, "IP=10005")  # an offset wraps where the machine's offsets do
        self.assertEqual(cpu.ip, 5)

    def test_what_cannot_be_set_is_said_and_nothing_changes(self):
        cpu = machine()
        for text in ("", "AX", "AX=", "AX=zz", "AX=10000", "EAX=100000000", "XX=1", "ES=10000", "FLAGS=10000", "AX=-1"):
            with self.assertRaisesRegex(ValueError, "cannot set|not a register to set"):
                set_register(cpu, text)
        self.assertEqual(cpu.r[CPU.AX], 0)

    def test_what_changed_is_found_by_name(self):
        cpu = machine()
        before = register_values(cpu)
        cpu.r[CPU.CX] = 1
        cpu.ip = 3
        cpu.flags |= 1
        self.assertEqual(changed_registers(before, register_values(cpu)), {"CX", "IP", "FLAGS"})
        self.assertEqual(changed_registers(before, before), set())

    def test_bytes_are_written(self):
        cpu = machine()
        write_memory(cpu, "1000:20 90 cc 1")
        self.assertEqual(bytes(cpu.mem[0x10020:0x10024]), bytes((0x90, 0xCC, 0x01, 0)))
        cpu.s[CPU.DS] = 0x2000
        write_memory(cpu, "30 FF")  # the offset alone is in the segment DS
        self.assertEqual((cpu.mem[0x20030], cpu.mem[0x10030]), (0xFF, 0))

    def test_what_cannot_be_written_is_said_before_anything_is(self):
        cpu = machine()
        for text in ("", "1000:20", "1000:20 zz", "1000:20 90 100", "1000:20 -1", "zz 90", "10000:0 90"):
            with self.assertRaises(ValueError):
                write_memory(cpu, text)
        self.assertEqual(cpu.mem[0x10020], 0)

    def test_the_listing_can_start_anywhere(self):
        found = listing(machine(), 2, where=(0x1000, 3))
        self.assertEqual([off for _, off, _ in found], [3, 6])


@unittest.skipIf(tkinter is None, "this Python has no tkinter")
class TestWindow(unittest.TestCase):
    def setUp(self):
        self.cpu = machine()
        self.debugger = Debugger(self.cpu)
        try:
            self.window = DebugWindow(self.debugger, hidden=True)
        except WindowError as error:
            self.skipTest(f"no window: {error}")
        self.addCleanup(self.window.close)

    def text(self, widget):
        return widget.get("1.0", "end-1c")

    def test_it_shows_the_code_the_registers_and_the_memory(self):
        self.assertTrue(self.text(self.window.code).startswith(" > 1000:0000"))
        self.assertIn("AX=0000", self.text(self.window.registers))
        self.assertIn("1000:00000000  90 90 90", self.text(self.window.memory))

    def test_step_moves_the_arrow(self):
        self.window.on_step()
        self.assertEqual(self.cpu.ip, 1)
        self.assertTrue(self.text(self.window.code).startswith(" > 1000:0001"))

    def test_a_breakpoint_from_a_line_is_marked_in_the_list_and_stops_go(self):
        self.window.toggle_breakpoint(2)
        self.assertEqual(self.debugger.breakpoints(), [(0x1000, 2)])
        self.assertEqual(self.window.breakpoint_list.get(0), "1000:2")
        self.assertEqual(self.text(self.window.code).split("\n")[2][:3], "*  ")
        self.window.on_go()
        self.assertEqual(self.cpu.ip, 2)
        self.assertEqual(self.window.status.cget("text"), "stopped at a breakpoint")
        self.window.toggle_breakpoint(0)  # the line now is the one at the breakpoint: it goes away
        self.assertEqual(self.debugger.breakpoints(), [])

    def test_a_breakpoint_typed_in(self):
        self.window.breakpoint_entry.insert(0, "3")
        self.window.on_add_breakpoint()
        self.assertEqual(self.debugger.breakpoints(), [(None, 3)])
        self.window.breakpoint_list.selection_set(0)
        self.window.on_remove_breakpoint()
        self.assertEqual(self.debugger.breakpoints(), [])

    def test_a_breakpoint_that_is_not_an_address_is_said(self):
        self.window.breakpoint_entry.insert(0, "zz")
        self.window.on_add_breakpoint()
        self.assertIn("is not an address", self.window.status.cget("text"))
        self.assertEqual(self.debugger.breakpoints(), [])

    def test_go_to_the_end_and_then_nothing_runs(self):
        self.window.on_go()
        self.assertEqual(self.window.status.cget("text"), "the program ended with code 7")
        self.assertEqual(str(self.window.buttons["step"].cget("state")), "disabled")
        count = self.cpu.instructions
        self.window.on_step()
        self.assertEqual(self.cpu.instructions, count)
        self.window.close()
        self.assertEqual(self.window.run(), 7)

    def test_stop_pauses_a_running_program(self):
        self.window.tick_others = self.window.on_stop  # as if the button was pressed during the run
        self.cpu.mem[0x10000:0x10004] = bytes.fromhex("eb fe 90 90")  # jmp $: it never ends
        self.window.on_go()
        self.assertEqual(self.window.status.cget("text"), "stopped")
        self.assertFalse(self.window.running)

    def test_a_program_that_fails_leaves_the_window_open_and_the_error_for_the_end(self):
        self.cpu.mem[0x10000] = 0x0F  # an opcode that is not there
        self.cpu.mem[0x10001] = 0xFF
        self.window.on_step()
        self.assertIn("Error", self.window.status.cget("text"))
        self.window.close()
        with self.assertRaises(Exception):
            self.window.run()

    def test_the_registers_a_step_changes_are_marked(self):
        self.assertEqual(self.window.registers.tag_ranges("changed"), ())
        self.window.on_step()
        self.assertEqual(self.marked(), ["IP=0001"])
        self.window.on_step()
        self.window.on_step()
        self.window.on_step()  # mov ax, 4C07h
        self.assertEqual(self.marked(), ["AX=4C07", "IP=0006"])

    def marked(self):
        """The pieces of the register panel that are marked as changed."""
        ranges = self.window.registers.tag_ranges("changed")
        return [self.window.registers.get(ranges[i], ranges[i + 1]) for i in range(0, len(ranges), 2)]

    def test_a_register_is_set_from_its_entry(self):
        self.window.register_entry.insert(0, "CX=00FF")
        self.window.on_set_register()
        self.assertEqual(self.cpu.r[CPU.CX], 0xFF)
        self.assertEqual(self.marked(), ["CX=00FF"])
        self.window.register_entry.delete(0, "end")
        self.window.register_entry.insert(0, "CX=")
        self.window.on_set_register()
        self.assertIn("not a register to set", self.window.status.cget("text"))

    def test_nothing_is_edited_in_a_program_that_has_ended(self):
        self.window.on_go()
        self.window.register_entry.insert(0, "CX=5")
        self.window.on_set_register()
        self.assertEqual(self.cpu.r[CPU.CX], 0)

    def test_memory_is_written_from_its_entry(self):
        self.window.write_entry.insert(0, "1000:0 40")
        self.window.on_write_memory()
        self.assertEqual(self.cpu.mem[0x10000], 0x40)
        self.assertIn("1000:00000000  40 90 90", self.text(self.window.memory))
        self.assertIn("inc ax", self.text(self.window.code))

    def test_the_code_from_an_address_and_back_to_the_next_instruction(self):
        self.window.code_entry.insert(0, "1000:3")
        self.window.on_code_at()
        self.assertTrue(self.text(self.window.code).startswith("   1000:0003"))
        self.assertNotIn(">", self.text(self.window.code)[:3])
        self.window.on_follow()
        self.assertTrue(self.text(self.window.code).startswith(" > 1000:0000"))
        self.window.code_entry.delete(0, "end")
        self.window.code_entry.insert(0, "5")  # the offset alone: the segment of the code
        self.window.on_code_at()
        self.assertTrue(self.text(self.window.code).startswith("   1000:0005"))

    def test_a_step_brings_the_listing_back_to_the_program(self):
        self.window.code_entry.insert(0, "3")
        self.window.on_code_at()
        self.window.on_step()
        self.assertTrue(self.text(self.window.code).startswith(" > 1000:0001"))

    def test_the_memory_follows_the_address_typed(self):
        self.window.memory_entry.delete(0, "end")
        self.window.memory_entry.insert(0, "1000:3")
        self.window.refresh()
        self.assertIn("1000:00000003  B8 07 4C", self.text(self.window.memory))
        self.window.memory_entry.delete(0, "end")
        self.window.memory_entry.insert(0, "x")
        self.window.refresh()
        self.assertIn("is not an address", self.text(self.window.memory))


if __name__ == "__main__":
    unittest.main()
