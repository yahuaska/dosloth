"""The two interrupt controllers of the PC (8259), as a stub: they keep the masks, and no interrupt comes from them.

The master is at ports 20h and 21h, the slave at A0h and A1h. A program reads and writes the mask of the interrupts
(the port 21h or A1h: a set bit forbids that line) to let a line through for a while, and writes 20h (A0h) to say that
an interrupt is over (EOI). Here the masks are kept as written, and what a program writes to the command port is
taken and forgotten, but for the start of an initialization (ICW1), which takes the bytes that follow on the other
port (the base vector, the wiring, the mode) and does not mistake them for a mask. A read of the command port says that
no line asks and nothing is served.

What is not done: nothing is delivered. The timer, the keyboard and the cards raise no interrupt, so a program that
waits for one does not get it. The masks begin as DOS leaves them: the timer, the keyboard, the slave and the floppy.
"""
MASTER, SLAVE = 0x20, 0xA0
DOS_MASTER_MASK = 0xB8  # lines 0, 1, 2 and 6 are open
ICW1 = 0x10  # the bit that makes a write to the command port the start of an initialization
ICW4_NEEDED = 0x01  # in ICW1: a fourth byte follows


class Controller:
    """One 8259. `mask` is the mask of the lines; `expects` counts the bytes of an initialization still to come."""

    def __init__(self, mask):
        self.mask = mask
        self.expects = 0

    def read_command(self, port):
        return 0

    def read_mask(self, port):
        return self.mask

    def write_command(self, port, value):
        if value & ICW1:
            self.expects = 3 if value & ICW4_NEEDED else 2  # ICW2 and ICW3, and ICW4 if it is said
            self.mask = 0  # an initialization clears the mask

    def write_mask(self, port, value):
        if self.expects:
            self.expects -= 1
        else:
            self.mask = value


def install(cpu):
    """Put both controllers on the CPU. Returns them: (master, slave)."""
    master, slave = Controller(DOS_MASTER_MASK), Controller(0xFF)
    for base, chip in ((MASTER, master), (SLAVE, slave)):
        cpu.port_readers[base] = chip.read_command
        cpu.port_readers[base + 1] = chip.read_mask
        cpu.port_writers[base] = chip.write_command
        cpu.port_writers[base + 1] = chip.write_mask
    return master, slave
