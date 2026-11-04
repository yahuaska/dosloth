"""IN and OUT with the operand size of 32 bits (step 3c2): the byte forms are the same, a doubleword goes to four
neighbouring ports, the low byte first, and the prefix 66h makes it a word again."""
from dosloth.disasm import disasm
from tests.test_cpu386 import Machine

PORT = 0x0170


class TestPorts(Machine):
    def ports(self, cpu, answers=()):
        """Put devices on the ports: the writes are collected, and the reads give the bytes of `answers` in turn."""
        self.written, self.read_from, replies = [], [], list(answers)
        cpu.port_writers = _Everywhere(lambda port, value: self.written.append((port, value)))
        cpu.port_readers = _Everywhere(lambda port: self.read_from.append(port) or replies.pop(0))

    def run32(self, code, answers=(), **registers):
        cpu = self.start(code, **registers)
        cpu.code32 = True  # 32-bit code: the operands are of 32 bits
        cpu.osize32 = True
        self.ports(cpu, answers)
        cpu.step()
        return cpu

    def test_out_eax_to_dx_goes_to_four_ports_the_low_byte_first(self):
        self.run32(bytes([0xEF]), eax=0x11223344, edx=PORT)
        self.assertEqual(self.written, [(PORT, 0x44), (PORT + 1, 0x33), (PORT + 2, 0x22), (PORT + 3, 0x11)])

    def test_out_eax_with_the_port_in_the_next_byte(self):
        self.run32(bytes([0xE7, 0x70]), eax=0x01020304)
        self.assertEqual(self.written, [(0x70, 4), (0x71, 3), (0x72, 2), (0x73, 1)])

    def test_in_eax_from_dx_reads_four_ports_into_all_of_eax(self):
        cpu = self.run32(bytes([0xED]), answers=[0xDD, 0xCC, 0xBB, 0xAA], edx=PORT, eax=0x12345678)
        self.assertEqual(cpu.get32(0), 0xAABBCCDD)

    def test_in_eax_with_the_port_in_the_next_byte(self):
        cpu = self.run32(bytes([0xE5, 0x60]), answers=[1, 2, 3, 4])
        self.assertEqual(cpu.get32(0), 0x04030201)

    def test_the_ports_go_round_at_ffff(self):
        self.run32(bytes([0xEF]), eax=0xA1B2C3D4, edx=0xFFFE)
        self.assertEqual([port for port, _ in self.written], [0xFFFE, 0xFFFF, 0x0000, 0x0001])

    def test_the_ports_that_are_read_go_round_at_ffff_too(self):
        cpu = self.run32(bytes([0xED]), answers=[1, 2, 3, 4], edx=0xFFFF)
        self.assertEqual((self.read_from, cpu.get32(0)), ([0xFFFF, 0, 1, 2], 0x04030201))

    def test_the_byte_forms_are_the_same_in_32_bit_code_and_leave_the_rest_of_eax_alone(self):
        self.run32(bytes([0xE6, 0x43]), eax=0xAABBCC36)
        self.assertEqual(self.written, [(0x43, 0x36)])
        cpu = self.run32(bytes([0xEC]), answers=[0x9A], edx=PORT, eax=0x12345678)
        self.assertEqual(cpu.get32(0), 0x1234569A)
        cpu = self.run32(bytes([0xE4, 0x40]), answers=[0x11], eax=0x12345678)
        self.assertEqual(cpu.get32(0), 0x12345611)
        self.run32(bytes([0xEE]), eax=0x1234FF77, edx=PORT)
        self.assertEqual(self.written, [(PORT, 0x77)])

    def test_the_prefix_66_makes_a_word_of_it_in_32_bit_code(self):
        cpu = self.start(bytes([0x66, 0xEF]), eax=0x11223344, edx=PORT)
        cpu.code32 = True
        self.ports(cpu)
        cpu.step()
        self.assertEqual(self.written, [(PORT, 0x44), (PORT + 1, 0x33)])
        cpu = self.start(bytes([0x66, 0xED]), edx=PORT, eax=0xFFFF0000)
        cpu.code32 = True
        self.ports(cpu, [0x34, 0x12])
        cpu.step()
        self.assertEqual(cpu.get32(0), 0xFFFF1234)

    def test_the_prefix_66_makes_a_doubleword_of_it_in_16_bit_code(self):
        cpu = self.start(bytes([0x66, 0xEF]), eax=0x11223344, edx=PORT)
        self.ports(cpu)
        cpu.step()
        self.assertEqual(self.written, [(PORT, 0x44), (PORT + 1, 0x33), (PORT + 2, 0x22), (PORT + 3, 0x11)])

    def test_the_disassembler_names_eax_with_the_size_of_32_bits(self):
        for code, text in (((0xEF,), "out dx, eax"), ((0xED,), "in eax, dx"), ((0xE7, 0x70), "out 70, eax"),
                           ((0xE5, 0x60), "in eax, 60"), ((0xEE,), "out dx, al"), ((0xE4, 0x40), "in al, 40")):
            cpu = self.start(bytes(code))
            cpu.code32 = True
            self.assertEqual(disasm(cpu, 0x1000, 0), (len(code), text))
        cpu = self.start(bytes([0x66, 0xEF]))
        cpu.code32 = True
        self.assertEqual(disasm(cpu, 0x1000, 0), (2, "out dx, ax"))
        cpu = self.start(bytes([0xEF]))
        self.assertEqual(disasm(cpu, 0x1000, 0), (1, "out dx, ax"))


class _Everywhere(dict):
    """A table of devices that has the same one on every port."""

    def __init__(self, device):
        super().__init__()
        self.device = device

    def get(self, port, default=None):
        return self.device if default is None else default
