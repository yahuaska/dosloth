"""A VGA card that does nothing: its ports answer, and nothing is drawn.

A program that finds a VGA card talks to it through the I/O ports 3C0h-3DFh. This stand-in lets every one of those
ports be written and read, so such a program can go on running. It draws nothing, and the video memory at A000:0000
is plain memory, not the four planes of the real card.

What it remembers:
    the indexed registers   the sequencer (index port 3C4h, data port 3C5h), the graphics controller (3CEh and
                            3CFh) and the CRT controller (3D4h and 3D5h). A byte written to the data port goes
                            to the register the index port names, and reading the data port gives it back.
    every other port        the last byte written to it, or 0 when nothing was written. The palette (3C8h and
                            3C9h) is therefore only a memory of its last byte, not 256 colors.

Port 3DAh, the status of the card, is different: reading it gives 00h and 09h turn about. Bit 3 is "the beam is
going back to the top" and bit 0 is "nothing is being drawn right now". Programs wait in a loop for these bits to
change, and a status that never changed would keep such a program there for good, without a word.

What is not done (a port outside 3C0h-3DFh, such as the monochrome ones at 3B4h, stops the emulator): the planes,
the latches, the write and read modes, the palette, the attribute controller's index/data switch, the video modes
and a picture on the screen.
"""

FIRST_PORT = 0x3C0
LAST_PORT = 0x3DF
STATUS_PORT = 0x3DA
INDEX_PORT_OF = {0x3C5: 0x3C4, 0x3CF: 0x3CE, 0x3D5: 0x3D4}  # data port -> its index port
RETRACE = 0x09  # the two bits of the status port that programs wait for


class Vga:
    """The card: what has been written to its ports."""

    def __init__(self):
        self.last_written = {}  # port -> the last byte written to it
        self.registers = {}  # (index port, index) -> the byte written to that register
        self.status = 0

    def write(self, port, value):
        """A byte written to one of the ports."""
        index_port = INDEX_PORT_OF.get(port)
        if index_port is None:
            self.last_written[port] = value
        else:
            self.registers[index_port, self.last_written.get(index_port, 0)] = value

    def read(self, port):
        """A byte read from one of the ports."""
        if port == STATUS_PORT:
            self.status ^= RETRACE
            return self.status
        index_port = INDEX_PORT_OF.get(port)
        if index_port is None:
            return self.last_written.get(port, 0)
        return self.registers.get((index_port, self.last_written.get(index_port, 0)), 0)


def install(cpu):
    """Put the card on ports 3C0h-3DFh of the CPU."""
    vga = Vga()
    for port in range(FIRST_PORT, LAST_PORT + 1):
        cpu.port_readers[port] = vga.read
        cpu.port_writers[port] = vga.write
    return vga
