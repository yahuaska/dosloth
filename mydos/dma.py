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

The data is not moved. But a device that plays or records through a channel says `begin(channel, rate)`: the bytes
go by at `rate` a instruction, and the address and the count that a program reads from the ports are the live ones,
as a real controller shows them (the address up and the count down, from the values that were programmed; the count
at 0FFFFh when it has gone to its end, and from the start again when the mode is the repeated one, bit 4). A program
that wants to know where the sound card is in its buffer reads them. `end(channel)` stops it, with the registers
where it was. A device says that a channel has reached its end of the count with `finish`, and the status shows it.
The memory that the channel points at is `address(channel)`, the count `count(channel)`.
The second controller, of the 16-bit channels (C0h-DFh), is not here.
"""
CHANNELS = 4
ADDRESS_PORTS = (0x00, 0x02, 0x04, 0x06)
COUNT_PORTS = (0x01, 0x03, 0x05, 0x07)
STATUS_COMMAND, MASK_ONE, MODE, CLEAR_FLIP_FLOP, RESET, UNMASK_ALL, MASK_ALL = 0x08, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F
REPEATED = 0x10  # in the mode: the channel begins again at its end
PAGE_PORTS = {0x87: 0, 0x83: 1, 0x81: 2, 0x82: 3}
MASK_BIT = 0x04


class Dma:
    """The registers of the controller. All of it is numbers and lists, so that a saved state can hold it."""

    def __init__(self, clock=lambda: 0):
        self.clock = clock  # the time, in instructions (not saved)
        self.started = [None] * CHANNELS  # the time when the channel began to move bytes, or None
        self.rates = [0.0] * CHANNELS  # the bytes a instruction
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

    def state(self):
        """What a saved state holds: all but the clock."""
        return {name: value for name, value in vars(self).items() if name != "clock"}

    def moved(self, channel):
        """How many bytes have gone through the channel since it began."""
        if self.started[channel] is None:
            return 0
        return int((self.clock() - self.started[channel]) * self.rates[channel])

    def live(self, channel):
        """The address and the count of the channel as they are now: (address in the page, count)."""
        size, moved = self.counts[channel] + 1, self.moved(channel)
        if self.modes[channel] & REPEATED:
            moved %= size
        elif moved > size:
            moved = size
        count = self.counts[channel] - moved
        return (self.addresses[channel] + moved) & 0xFFFF, count & 0xFFFF

    def begin(self, channel, rate):
        """A device begins to move bytes through the channel, `rate` of them a instruction."""
        self.end(channel)  # what went by before is in the registers
        self.started[channel], self.rates[channel] = self.clock(), rate

    def end(self, channel):
        """The device stops: the registers keep what they show."""
        if self.started[channel] is not None:
            address, count = self.live(channel)
            self.addresses[channel], self.counts[channel] = address, count
            self.started[channel] = None

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
        current = self.live(channel)[0 if registers is self.addresses else 1] if self.started[channel] is not None \
            else registers[channel]
        value = current >> 8 if self.high_next else current & 0xFF
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
            self.__init__(self.clock)
        elif port == UNMASK_ALL:
            self.masked = [False] * CHANNELS
        elif port == MASK_ALL:
            self.masked = [bool(value >> channel & 1) for channel in range(CHANNELS)]


def install(cpu):
    """Put the controller on the CPU. Its time is the count of instructions of the CPU. Returns it."""
    controller = Dma(lambda: cpu.instructions)
    for port in (*ADDRESS_PORTS, *COUNT_PORTS, STATUS_COMMAND, MASK_ONE, MODE, CLEAR_FLIP_FLOP, RESET, UNMASK_ALL,
                 MASK_ALL, *PAGE_PORTS):
        cpu.port_readers[port] = controller.read
        cpu.port_writers[port] = controller.write
    return controller
