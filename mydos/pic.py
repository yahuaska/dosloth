"""The two interrupt controllers of the PC (8259): the lines that devices raise, and the vectors that the CPU gets.

The master is at ports 20h and 21h, the slave at A0h and A1h, and the slave's request comes in on line 2 of the master.
So the lines 0-7 are the master's, the lines 8-15 the slave's. A device (the timer, a sound card) raises a line with
`Pics.raise_line`. The CPU asks `Pics.pending` whether a line is allowed to interrupt now, and if so takes it with
`Pics.acknowledge`, which gives the number of the line, and `Pics.vector` the vector of that line (the base of its
controller and the line: 8 + line for the master, 70h + line - 8 for the slave, as the BIOS sets them).

What a controller does with the lines:
    request    a line that was raised and has not been taken yet (IRR); a raised line stays asked for until it is taken
    mask       a set bit forbids that line (the port 21h or A1h, read and written: a program lets a line through for
               a while); a masked line stays asked for and comes through when it is let
    in service a line that was taken and that its handler has not said is over (ISR); while it is, no line of its
               priority or lower comes (the line 0 is the first, the line 7 is the last)
    EOI        a write of 20h to the command port says that the handler of the highest line in service is over; 60h
               plus the line says it of that line. The handler of a line of the slave says it to both controllers.
A write of an initialization (ICW1: bit 4 set) takes the bytes that follow on the other port, the base vector (ICW2),
the wiring (ICW3) and the mode (ICW4, if ICW1 says it comes), and does not mistake them for a mask; it clears the mask.
A read of the command port gives the lines that are in service or asked for, as OCW3 chose (it says "asked for" at
the start).

Not done: the rotation of priorities, the special masks, the level mode (a line is as an edge), the automatic EOI.
The masks begin as DOS leaves them: the timer, the keyboard, the slave and the floppy.
"""
MASTER, SLAVE = 0x20, 0xA0
DOS_MASTER_MASK = 0xB8  # lines 0, 1, 2 and 6 are open
MASTER_BASE, SLAVE_BASE = 0x08, 0x70
CASCADE_LINE = 2  # the line of the master that the slave comes in on
ICW1 = 0x10  # the bit that makes a write to the command port the start of an initialization
ICW4_NEEDED = 0x01  # in ICW1: a fourth byte follows
OCW3 = 0x08  # the bit that makes a write to the command port a command of reading (OCW3), when ICW1 is not set
READ_REGISTER = 0x02  # in OCW3: the next read of the command port is of a register chosen by the bit 0
READ_IN_SERVICE = 0x01
EOI = 0x20  # in OCW2: the end of an interrupt; the bit 6 makes it of the line in the low three bits
SPECIFIC = 0x40
LINES = 8


class Controller:
    """One 8259. All it holds is numbers, so that a saved state can hold it."""

    def __init__(self, mask, base, on_change=None):
        self.mask = mask
        self.base = base
        self.requests = 0  # the lines that are asked for
        self.in_service = 0  # the lines that are being served
        self.expects = 0  # the bytes of an initialization still to come
        self.first_is_vector = False  # the next byte of the initialization is the base vector
        self.read_in_service = False  # a read of the command port gives the lines in service and not those asked for
        self.on_change = on_change  # called whenever something has changed that may let a line through (not saved)

    def state(self):
        """What a saved state holds of the controller: all but the function that it calls."""
        return {name: value for name, value in vars(self).items() if name != "on_change"}

    def changed(self):
        if self.on_change:
            self.on_change()

    def first_allowed(self, extra=0):
        """The first line that may interrupt now (`extra` are the requests of other places, in a bit mask), or None."""
        asked = (self.requests | extra) & ~self.mask
        for line in range(LINES):
            if self.in_service >> line & 1:
                return None  # a line of this priority or higher is being served: what comes next waits
            if asked >> line & 1:
                return line
        return None

    def take(self, line):
        """The line is served: it is no more asked for, and it is in service."""
        self.requests &= ~(1 << line)
        self.in_service |= 1 << line

    def end(self, line=None):
        """The end of the service of the line, or of the first one that is in service."""
        if line is None:
            for line in range(LINES):
                if self.in_service >> line & 1:
                    break
            else:
                return
        self.in_service &= ~(1 << line)
        self.changed()

    def read_command(self, port):
        return self.in_service if self.read_in_service else self.requests

    def read_mask(self, port):
        return self.mask

    def write_command(self, port, value):
        if value & ICW1:
            self.expects = 3 if value & ICW4_NEEDED else 2  # ICW2 and ICW3, and ICW4 if it is said
            self.first_is_vector = True
            self.mask = 0  # an initialization clears the mask
            self.requests = self.in_service = 0
            self.read_in_service = False
        elif value & OCW3:
            if value & READ_REGISTER:
                self.read_in_service = bool(value & READ_IN_SERVICE)
        elif value & EOI:
            self.end(value & 7 if value & SPECIFIC else None)
        self.changed()

    def write_mask(self, port, value):
        if self.expects:
            if self.first_is_vector:
                self.base = value & 0xF8
                self.first_is_vector = False
            self.expects -= 1
        else:
            self.mask = value
        self.changed()


class Pics:
    """The pair of controllers, and the lines that come in on them."""

    def __init__(self, on_change=None):
        self.master = Controller(DOS_MASTER_MASK, MASTER_BASE, on_change)
        self.slave = Controller(0xFF, SLAVE_BASE, on_change)
        self.on_change = on_change

    def __iter__(self):
        return iter((self.master, self.slave))

    def __getitem__(self, index):
        return (self.master, self.slave)[index]

    def raise_line(self, line):
        """A device asks for an interrupt on the line, 0-15."""
        chip = self.slave if line >= LINES else self.master
        chip.requests |= 1 << (line % LINES)
        if self.on_change:
            self.on_change()

    def lower_line(self, line):
        """A device takes its request back (the program has done what the device waited for) before it was taken."""
        chip = self.slave if line >= LINES else self.master
        chip.requests &= ~(1 << (line % LINES))

    def pending(self):
        """The line that may interrupt now, or None."""
        from_slave = self.slave.first_allowed()
        line = self.master.first_allowed(1 << CASCADE_LINE if from_slave is not None else 0)
        if line == CASCADE_LINE:
            return None if from_slave is None else LINES + from_slave
        return line

    def acknowledge(self):
        """The CPU takes the line that `pending` gave: it goes in service. Returns the line, or None if none is."""
        line = self.pending()
        if line is None:
            return None
        if line >= LINES:
            self.slave.take(line - LINES)
            self.master.in_service |= 1 << CASCADE_LINE
        else:
            self.master.take(line)
        return line

    def vector(self, line):
        chip = self.slave if line >= LINES else self.master
        return chip.base + line % LINES

    def end(self, line):
        """What the handler of the line says when it ends, to the controllers it is to be said to."""
        if line >= LINES:
            self.slave.end(line - LINES)
            self.master.end(CASCADE_LINE)
        else:
            self.master.end(line)


def install(cpu):
    """Put both controllers on the CPU, and let the CPU look at them. Returns the Pics."""
    pics = Pics(cpu.wake)
    cpu.pics = pics
    for base, chip in ((MASTER, pics.master), (SLAVE, pics.slave)):
        cpu.port_readers[base] = chip.read_command
        cpu.port_readers[base + 1] = chip.read_mask
        cpu.port_writers[base] = chip.write_command
        cpu.port_writers[base + 1] = chip.write_mask
    return pics
