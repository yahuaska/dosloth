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
    the palette             256 colors of 6 bits for each of red, green and blue, the DAC (ports 3C6h-3C9h: the
                            mask, the read and write addresses and the data, three bytes a color, with the address
                            going up by itself); and the 16 palette registers of the attribute controller (ports
                            3C0h and 3C1h, one port for the index and the data in turn, which reading 3DAh sets
                            back to the index), which say which of the 256 each of the 16 colors of a pixel is.
                            They start as the BIOS leaves them, which is the 16 colors of the EGA.
    every other port        the last byte written to it, or 0 when nothing was written.

Port 3DAh, the status of the card, is different: reading it gives 00h and 09h turn about. Bit 3 is "the beam is
going back to the top" and bit 0 is "nothing is being drawn right now". Programs wait in a loop for these bits to
change, and a status that never changed would keep such a program there for good, without a word.

What is not done (a port outside 3C0h-3DFh, such as the monochrome ones at 3B4h, stops the emulator, and so does
a memory access in a mode it does not know): odd/even addressing and 256-color modes (graphics controller register
5, bits 4-6), the window being moved or made smaller by register 6, the text modes. The palette entries the BIOS
fills in past the first 64 are not there: they are black. The attribute controller's overscan, plane enable and
panning registers are kept but do nothing.
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

ATTRIBUTE_PORT = 0x3C0
ATTRIBUTE_READ_PORT = 0x3C1
PEL_MASK_PORT = 0x3C6
DAC_READ_ADDRESS_PORT = 0x3C7  # written: where reading starts; read: whether the DAC is reading or writing
DAC_WRITE_ADDRESS_PORT = 0x3C8
DAC_DATA_PORT = 0x3C9
MODE_CONTROL = 0x10  # the attribute controller register: bit 7 says where bits 4 and 5 of a color come from
OVERSCAN = 0x11
COLOR_SELECT = 0x14

# the 16 colors of the EGA, in 6 bits for each of red, green and blue
CGA_COLORS = [(0, 0, 0), (0, 0, 42), (0, 42, 0), (0, 42, 42), (42, 0, 0), (42, 0, 42), (42, 21, 0), (42, 42, 42),
              (21, 21, 21), (21, 21, 63), (21, 63, 21), (21, 63, 63), (63, 21, 21), (63, 21, 63), (63, 63, 21),
              (63, 63, 63)]
# which of the 256 colors of the DAC each of the 16 colors of a pixel is, when the BIOS has set mode 12h: the first
# eight are themselves but for brown, the next eight are the bright ones, which the EGA kept at 38h-3Fh
DEFAULT_PALETTE = [0, 1, 2, 3, 4, 5, 0x14, 7, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E, 0x3F]
# the other attribute controller registers: mode control (graphics), overscan, plane enable, panning, color select
DEFAULT_ATTRIBUTES = DEFAULT_PALETTE + [0x01, 0x00, 0x0F, 0x00, 0x00]


def default_dac():
    """The 256 colors the BIOS leaves in the DAC, as 768 bytes: red, green, blue of each. Only the ones the 16
    colors of the EGA use are filled in: 0-15, 14h and 38h-3Fh. The rest are black."""
    dac = bytearray(768)
    for number, color in enumerate(CGA_COLORS):
        dac[number * 3:number * 3 + 3] = bytes(color)
    dac[0x14 * 3:0x14 * 3 + 3] = bytes(CGA_COLORS[6])
    for number in range(8):
        dac[(0x38 + number) * 3:(0x38 + number) * 3 + 3] = bytes(CGA_COLORS[8 + number])
    return dac


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
        self.changed = False  # the picture may be another since whoever shows the screen last looked
        self.reset_palette()

    def reset_palette(self):
        """The colors as the BIOS leaves them."""
        self.dac = default_dac()
        self.pel_mask = 0xFF
        self.attributes = list(DEFAULT_ATTRIBUTES)  # the registers of the attribute controller
        self.attribute_index = 0
        self.attribute_data_next = False  # the next byte to port 3C0h is data, not an index
        self.dac_read_index = self.dac_write_index = 0
        self.dac_read_part = 0  # which of red (0), green (1) and blue (2) is read next
        self.dac_written = []  # the parts of the color being written so far
        self.dac_reading = False

    def write(self, port, value):
        """A byte written to one of the ports."""
        index_port = INDEX_PORT_OF.get(port)
        if index_port is not None:
            self.registers[index_port, self.last_written.get(index_port, 0)] = value
        elif port == ATTRIBUTE_PORT:
            if self.attribute_data_next:
                if self.attribute_index < len(self.attributes):
                    self.attributes[self.attribute_index] = value
                    self.changed = True
            else:
                self.attribute_index = value & 0x1F  # bit 5 says whether the palette is the screen's: ignored
            self.attribute_data_next = not self.attribute_data_next
        elif port == PEL_MASK_PORT:
            self.pel_mask = value
            self.changed = True
        elif port == DAC_READ_ADDRESS_PORT:
            self.dac_read_index, self.dac_read_part, self.dac_reading = value, 0, True
        elif port == DAC_WRITE_ADDRESS_PORT:
            self.dac_write_index, self.dac_written, self.dac_reading = value, [], False
        elif port == DAC_DATA_PORT:
            self.dac_written.append(value & 0x3F)
            if len(self.dac_written) == 3:
                self.set_dac(self.dac_write_index, *self.dac_written)
                self.dac_written = []
                self.dac_write_index = (self.dac_write_index + 1) & 0xFF
        else:
            self.last_written[port] = value

    def read(self, port):
        """A byte read from one of the ports."""
        if port == STATUS_PORT:
            self.status ^= RETRACE
            self.attribute_data_next = False  # reading the status sets the attribute controller back to an index
            return self.status
        index_port = INDEX_PORT_OF.get(port)
        if index_port is not None:
            return self.registers.get((index_port, self.last_written.get(index_port, 0)), 0)
        if port == ATTRIBUTE_PORT:
            return self.attribute_index
        if port == ATTRIBUTE_READ_PORT:
            return self.attributes[self.attribute_index] if self.attribute_index < len(self.attributes) else 0
        if port == PEL_MASK_PORT:
            return self.pel_mask
        if port == DAC_READ_ADDRESS_PORT:
            return 3 if self.dac_reading else 0
        if port == DAC_WRITE_ADDRESS_PORT:
            return self.dac_write_index
        if port == DAC_DATA_PORT:
            value = self.dac[self.dac_read_index * 3 + self.dac_read_part]
            self.dac_read_part += 1
            if self.dac_read_part == 3:
                self.dac_read_part = 0
                self.dac_read_index = (self.dac_read_index + 1) & 0xFF
            return value
        return self.last_written.get(port, 0)

    def set_dac(self, index, red, green, blue):
        """Make color `index` of the 256 this one, in 6 bits for each part."""
        self.dac[index * 3:index * 3 + 3] = bytes((red & 0x3F, green & 0x3F, blue & 0x3F))
        self.changed = True

    def get_dac(self, index):
        return tuple(self.dac[index * 3:index * 3 + 3])

    def set_palette_register(self, number, value):
        """Say which of the 256 colors of the DAC the color `number` (0-15) of a pixel is (the attribute
        controller's registers 0-15), or set another register of it, such as the overscan color (11h)."""
        self.attributes[number] = value
        self.changed = True

    def palette_register(self, number):
        return self.attributes[number]

    def colors(self):
        """The 16 colors of a pixel as the screen shows them: a list of (red, green, blue) in 8 bits each."""
        select = self.attributes[COLOR_SELECT]
        result = []
        for value in self.attributes[:16]:
            if self.attributes[MODE_CONTROL] & 0x80:  # bits 4 and 5 come from the color select register
                index = (select & 0x0F) << 4 | (value & 0x0F)
            else:  # bits 6 and 7 do
                index = (select & 0x0C) << 4 | (value & 0x3F)
            red, green, blue = self.get_dac(index & self.pel_mask)
            result.append(tuple(part << 2 | part >> 4 for part in (red, green, blue)))  # 6 bits -> 8 bits
        return result

    def clear(self, keep_planes=False):
        """What a change of video mode does to the card: the registers and the colors are as the BIOS leaves them, the
        latches are empty, and the planes are empty too, unless the mode set is to keep the picture."""
        self.registers = dict(POWER_ON)
        self.latches = [0, 0, 0, 0]
        self.reset_palette()
        self.changed = True
        if not keep_planes:
            for plane in self.planes:
                plane[:] = bytes(PLANE_SIZE)

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
        self.changed = True
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
