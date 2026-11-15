import unittest

from dosloth import flags, kbc, pic
from dosloth.cpu import CPU

HANDLER = 0x2000  # the segment of the program's handler of INT 9
STORE = 0x3000  # the segment where it puts the bytes it read


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.cpu = cpu = CPU()
        self.pics = pic.install(cpu)
        self.controller = kbc.install(cpu, self.pics)
        cpu.s[CPU.CS], cpu.ip = 0x1000, 0
        cpu.mem[0x10000:0x10002] = bytes([0xEB, 0xFE])  # the program: jmp $
        cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x4000, 0x0100
        cpu.flags = 0x0002 | flags.IF

    def hook(self):
        """A handler of INT 9 as a program has it: in al, 60h; stosb; mov al, 20h; out 20h, al; iret."""
        cpu = self.cpu
        cpu.mem[HANDLER * 16:HANDLER * 16 + 7] = bytes([0xE4, 0x60, 0xAA, 0xB0, 0x20, 0xE6, 0x20])
        cpu.mem[HANDLER * 16 + 7] = 0xCF
        cpu.mem[9 * 4:9 * 4 + 4] = (HANDLER << 16).to_bytes(4, "little")
        cpu.s[CPU.ES], cpu.r[CPU.DI] = STORE, 0

    def go(self, steps=5 * kbc.LOOK_EVERY):
        self.cpu.wake()
        for _ in range(steps):
            self.cpu.step()

    def stored(self, count):
        return bytes(self.cpu.mem[STORE * 16:STORE * 16 + count])


class TestTheBytes(ControllerTest):
    def test_a_key_down_and_up_is_its_make_code_and_its_break_code(self):
        self.controller.key(0x1E, True)
        self.controller.key(0x1E, False)
        self.assertEqual(list(self.controller.waiting), [0x1E, 0x9E])

    def test_a_key_of_the_grey_block_sends_e0_before_each_code(self):
        self.controller.key(0x48, True, extended=True)
        self.controller.key(0x48, False, extended=True)
        self.assertEqual(list(self.controller.waiting), [0xE0, 0x48, 0xE0, 0xC8])


class TestTheInterrupt(ControllerTest):
    def test_the_handler_of_int_9_gets_every_byte_in_turn_from_port_60h(self):
        self.hook()
        self.controller.key(0x1E, True)  # A
        self.controller.key(0x1E, False)
        self.controller.key(0x48, True, extended=True)  # Up
        self.go()
        self.assertEqual(self.stored(5), bytes([0x1E, 0x9E, 0xE0, 0x48, 0]))
        self.assertEqual((self.cpu.s[CPU.CS], self.cpu.ip), (0x1000, 0))  # the program goes on
        self.assertEqual(self.pics.master.in_service, 0)

    def test_the_next_byte_waits_while_the_line_is_in_service(self):
        self.hook()
        self.cpu.mem[HANDLER * 16 + 3:HANDLER * 16 + 5] = bytes([0xEB, 0xFE])  # the handler stops before its EOI
        self.controller.key(0x1E, True)
        self.controller.key(0x1E, False)
        self.go()
        self.assertEqual(self.stored(2), bytes([0x1E, 0]))
        self.assertEqual((self.controller.read(0x60), list(self.controller.waiting)), (0x1E, [0x9E]))

    def test_the_port_gives_the_last_byte_as_often_as_it_is_read(self):
        self.hook()
        self.controller.key(0x39, True)
        self.go()
        self.assertEqual([self.cpu.port_in(0x60) for _ in range(3)], [0x39] * 3)

    def test_with_no_handler_the_bytes_go_and_nothing_waits(self):
        self.controller.key(0x1E, True)
        self.controller.key(0x1E, False)
        self.go()
        self.assertEqual((list(self.controller.waiting), self.pics.master.in_service), ([], 0))

    def test_nothing_comes_while_the_program_does_not_allow_interrupts(self):
        self.hook()
        self.cpu.flags &= ~flags.IF
        self.controller.key(0x1E, True)
        self.go()
        self.assertEqual(self.stored(1), b"\0")
