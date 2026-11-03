"""The DMA controller of the PC (an 8237, the channels 0-3) and the page registers: the registers, and no transfers.

A device that moves bytes to or from memory by itself (a sound card) gets a channel, and a program tells the controller
where the data is and how much: the address and the count of the channel (a byte at a time, the low byte first, and
the flip-flop says which comes: it is cleared by a write to port 0Ch), the page of the address (the high bits, in a
register of its own), the mode (the direction, one transfer or many, the channel), and the mask (a channel works only
when it is not masked). The ports are:
    00h-07h   the address (even ports) and the count (odd ports) of the channels 0 to 3
    08h       read: the status, the bits 0-3 say that the channel has reached the end of its count (read clears them);
              write: the command (kept)
    0Ah       the mask of one channel: the bits 0-1 the channel, the bit 2 set means "mask it"
    0Bh       the mode: the bits 0-1 the channel, the bits 2-3 the direction, the bit 4 the repeat, the bits 6-7 the way
    0Ch       clear the flip-flop;  0Dh the reset of the controller (it masks everything);  0Eh unmask all;  0Fh write
              the mask of all, the bits 0-3
    81h, 82h, 83h, 87h   the pages of the channels 2, 3, 1, 0
Every channel is masked at the start.

Not done: the transfers. A device says that a channel has reached its end with `finish`, and the status shows it. The
memory that the channel points at is `address(channel)`, the count `count(channel)`, for a device that will move data.
The second controller, of the 16-bit channels (C0h-DFh), is not here.
"""
CHANNELS = 4
ADDRESS_PORTS = (0x00, 0x02, 0x04, 0x06)
COUNT_PORTS = (0x01, 0x03, 0x05, 0x07)
STATUS_COMMAND, MASK_ONE, MODE, CLEAR_FLIP_FLOP, RESET, UNMASK_ALL, MASK_ALL = 0x08, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F
PAGE_PORTS = {0x87: 0, 0x83: 1, 0x81: 2, 0x82: 3}
MASK_BIT = 0x04


class Dma:
    """The registers of the controller. All of it is numbers and lists, so that a saved state can hold it."""

    def __init__(self):
        self.addresses = [0] * CHANNELS  # 16 bits: the address in the page
        self.counts = [0] * CHANNELS  # the number of bytes to move, less one
        self.pages = [0] * CHANNELS
        self.modes = [0] * CHANNELS
        self.masked = [True] * CHANNELS
        self.ended = 0  # the bits of the channels that have reached the end of their count, as the status shows them
        self.command = 0
        self.high_next = False  # the flip-flop: the next byte of an address or a count is the high one

    def address(self, channel):
        """The address in memory that the channel points at: the page, and the address in it."""
        return self.pages[channel] << 16 | self.addresses[channel]

    def count(self, channel):
        """How many bytes the channel moves."""
        return self.counts[channel] + 1

    def finish(self, channel):
        """A device says that the channel has moved all of its count."""
        self.ended |= 1 << channel

    def write_register(self, registers, channel, value):
        """A byte of a 16-bit register: the low one, or the high one, as the flip-flop says."""
        if self.high_next:
            registers[channel] = registers[channel] & 0x00FF | value << 8
        else:
            registers[channel] = registers[channel] & 0xFF00 | value
        self.high_next = not self.high_next

    def read_register(self, registers, channel):
        value = registers[channel] >> 8 if self.high_next else registers[channel] & 0xFF
        self.high_next = not self.high_next
        return value

    def read(self, port):
        if port in ADDRESS_PORTS:
            return self.read_register(self.addresses, ADDRESS_PORTS.index(port))
        if port in COUNT_PORTS:
            return self.read_register(self.counts, COUNT_PORTS.index(port))
        if port in PAGE_PORTS:
            return self.pages[PAGE_PORTS[port]]
        if port == STATUS_COMMAND:
            status, self.ended = self.ended, 0
            return status
        return 0xFF

    def write(self, port, value):
        if port in ADDRESS_PORTS:
            self.write_register(self.addresses, ADDRESS_PORTS.index(port), value)
        elif port in COUNT_PORTS:
            self.write_register(self.counts, COUNT_PORTS.index(port), value)
        elif port in PAGE_PORTS:
            self.pages[PAGE_PORTS[port]] = value
        elif port == STATUS_COMMAND:
            self.command = value
        elif port == MASK_ONE:
            self.masked[value & 3] = bool(value & MASK_BIT)
        elif port == MODE:
            self.modes[value & 3] = value
        elif port == CLEAR_FLIP_FLOP:
            self.high_next = False
        elif port == RESET:
            self.__init__()
        elif port == UNMASK_ALL:
            self.masked = [False] * CHANNELS
        elif port == MASK_ALL:
            self.masked = [bool(value >> channel & 1) for channel in range(CHANNELS)]


def install(cpu):
    """Put the controller on the CPU. Returns it."""
    controller = Dma()
    for port in (*ADDRESS_PORTS, *COUNT_PORTS, STATUS_COMMAND, MASK_ONE, MODE, CLEAR_FLIP_FLOP, RESET, UNMASK_ALL,
                 MASK_ALL, *PAGE_PORTS):
        cpu.port_readers[port] = controller.read
        cpu.port_writers[port] = controller.write
    return controller
