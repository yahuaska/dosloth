"""A VGA card for the 16-color planar modes: its ports answer, and its video memory is the four planes.

A program talks to the card through the I/O ports 3C0h-3DFh and draws through the window A000:0000-A000:FFFF.
The window is not plain memory. Behind every address lie four planes, one bit of each pixel's color in each, and
what a write does to them depends on registers the program has set up first. Reading an address fills the four
latches, one byte from each plane, and a later write can bring those bytes back, which is how a block of the
screen is copied four planes at a time.

What is done:
    the indexed registers   the sequencer (index port 3C4h, data port 3C5h), the graphics controller (3CEh and
                            3CFh) and the CRT controller (3D4h and 3D5h). A byte written to the data port goes
                            to the register the index port names, and reading the data port gives it back.
                            The registers start as the BIOS leaves them in a graphics mode: all planes can be
                            written (map mask 0Fh), every bit of a byte can be changed (bit mask FFh) and all
                            four planes take part in a color compare (color don't care 0Fh); the others are 0.
    the video memory        write modes 0, 1, 2 and 3 with set/reset, rotate, the four logical functions and
                            the bit mask; read modes 0 (one plane) and 1 (color compare); the latches.
    every other port        the last byte written to it, or 0 when nothing was written. The palette (3C8h and
                            3C9h) is therefore only a memory of its last byte, not 256 colors.

Port 3DAh, the status of the card, is different: reading it gives 00h and 09h turn about. Bit 3 is "the beam is
going back to the top" and bit 0 is "nothing is being drawn right now". Programs wait in a loop for these bits to
change, and a status that never changed would keep such a program there for good, without a word.

What is not done (a port outside 3C0h-3DFh, such as the monochrome ones at 3B4h, stops the emulator, and so does
a memory access in a mode it does not know): odd/even addressing and 256-color modes (graphics controller register
5, bits 4-6), the window being moved or made smaller by register 6, the text modes, the palette, the attribute
controller's index/data switch, the video modes of INT 10h.
"""

FIRST_PORT = 0x3C0
LAST_PORT = 0x3DF
STATUS_PORT = 0x3DA
INDEX_PORT_OF = {0x3C5: 0x3C4, 0x3CF: 0x3CE, 0x3D5: 0x3D4}  # data port -> its index port
RETRACE = 0x09  # the two bits of the status port that programs wait for

SEQUENCER = 0x3C4
GRAPHICS = 0x3CE
WINDOW_START = 0xA0000  # where the planes show in the address space,
WINDOW_END = 0xB0000  # 64 KB of it
PLANE_SIZE = 0x10000
UNSUPPORTED_MODE_BITS = 0x70  # in graphics controller register 5: odd/even, interleaved shift, 256 colors

# what the BIOS leaves in the registers that are not 0 after it has set a graphics mode
POWER_ON = {(SEQUENCER, 2): 0x0F, (GRAPHICS, 7): 0x0F, (GRAPHICS, 8): 0xFF}


class Vga:
    """The card: what has been written to its ports, the planes and the latches."""

    def __init__(self):
        self.last_written = {}  # port -> the last byte written to it
        self.registers = dict(POWER_ON)  # (index port, index) -> the byte written to that register
        self.status = 0
        self.planes = [bytearray(PLANE_SIZE) for _ in range(4)]
        self.latches = [0, 0, 0, 0]

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

    def sequencer(self, index):
        return self.registers.get((SEQUENCER, index), 0)

    def graphics(self, index):
        return self.registers.get((GRAPHICS, index), 0)

    def check_mode(self):
        if self.graphics(5) & UNSUPPORTED_MODE_BITS:
            raise NotImplementedError("VGA memory in odd/even, interleaved or 256-color mode (graphics register 5 "
                                      f"is {self.graphics(5):02X})")

    def read_memory(self, offset):
        """A byte read from the window. All four planes are copied into the latches, and the byte that comes back
        is one plane's (read mode 0) or says which of its bits have the color the program is looking for (mode 1)."""
        self.check_mode()
        planes = self.planes
        self.latches = [plane[offset] for plane in planes]
        if not self.graphics(5) & 0x08:
            return planes[self.graphics(4) & 3][offset]
        compare, care = self.graphics(2), self.graphics(7)
        result = 0xFF
        for number in range(4):
            if care >> number & 1:  # a plane that counts: keep the bits where it has the wanted bit
                result &= planes[number][offset] if compare >> number & 1 else ~planes[number][offset] & 0xFF
        return result

    def write_memory(self, offset, value):
        """A byte written to the window. For each plane the map mask allows, the byte that goes in is worked out
        from the byte of the CPU, the set/reset, the latch and the bit mask; which way depends on the write mode."""
        self.check_mode()
        mode = self.graphics(5) & 3
        map_mask = self.sequencer(2)
        latches = self.latches
        if mode == 1:  # the latches go back as they are
            for number in range(4):
                if map_mask >> number & 1:
                    self.planes[number][offset] = latches[number]
            return
        rotate = self.graphics(3) & 7
        function = self.graphics(3) >> 3 & 3  # how the data meets the latch: 0 replaces it, 1 AND, 2 OR, 3 XOR
        set_reset, enable_set_reset = self.graphics(0), self.graphics(1)
        bit_mask = self.graphics(8)
        rotated = (value >> rotate | value << (8 - rotate)) & 0xFF
        if mode == 3:
            bit_mask &= rotated
        for number in range(4):
            if not map_mask >> number & 1:
                continue
            if mode == 0:
                use_set_reset = enable_set_reset >> number & 1
                data = (0xFF if set_reset >> number & 1 else 0) if use_set_reset else rotated
            elif mode == 2:
                data = 0xFF if value >> number & 1 else 0
            else:
                data = 0xFF if set_reset >> number & 1 else 0
            latch = latches[number]
            if function == 1:
                data &= latch
            elif function == 2:
                data |= latch
            elif function == 3:
                data ^= latch
            self.planes[number][offset] = data & bit_mask | latch & ~bit_mask & 0xFF


def install(cpu):
    """Put the card on ports 3C0h-3DFh of the CPU, and its planes in the window A000:0000-A000:FFFF."""
    vga = Vga()
    for port in range(FIRST_PORT, LAST_PORT + 1):
        cpu.port_readers[port] = vga.read
        cpu.port_writers[port] = vga.write
    cpu.video, cpu.video_start, cpu.video_end = vga, WINDOW_START, WINDOW_END
    return vga
