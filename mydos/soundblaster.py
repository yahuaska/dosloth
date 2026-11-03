"""A Sound Blaster that makes no sound: a card that a program finds, and that it can talk to, but that plays nothing.

A program that wants to play sound looks for the card first. Setup programs try the base ports 210h, 220h ... 260h one
by one: reset the DSP (the chip of the card), wait for the byte 0AAh, and ask for the version. The card is at 220h
here. The other places have nothing, and a read there gives 0FFh, as a bus with nothing on it does; a write is lost.

What the card does at 220h (the offsets from the base):
    04h, 05h  the mixer: an index is written to 04h, and 05h reads and writes the register of that index
    06h       the reset: 1, and then 0, and the DSP answers 0AAh
    0Ah       the data of the DSP, read: what it answers (0FFh when it has nothing to say)
    0Ch       the commands of the DSP, written; read it for the status of writing: bit 7 clear says "ready"
    0Eh       the status of reading, bit 7 set when the DSP has something to say
The DSP knows what a program needs to find it, and no more: the version (E1h), the echo (E0h), the speaker on and
off (D1h, D3h). Any other command stops the emulator, so that nothing goes unnoticed. The card says it is the
SB16 of version 4.05. It has no interrupt and no DMA, and the mixer is a row of bytes that keep what they are given.
"""
BASE = 0x220
FIRST_PROBED = 0x210  # the places where a setup program looks for the card, and where nothing is, but at BASE
LAST_PROBED = 0x26F
READY = 0xAA
VERSION = (4, 5)
MIXER_SIZE = 256

MIXER_INDEX, MIXER_DATA, RESET, READ_DATA, WRITE, READ_STATUS = 0x04, 0x05, 0x06, 0x0A, 0x0C, 0x0E
ECHO, VERSION_COMMAND, SPEAKER_ON, SPEAKER_OFF = 0xE0, 0xE1, 0xD1, 0xD3


class SoundBlaster:
    """The state of the card. All of it is numbers and lists, so that a saved state can hold it."""

    def __init__(self):
        self.mixer_index = 0
        self.mixer = [0] * MIXER_SIZE
        self.resetting = False  # a 1 was written to the reset port, and the 0 that ends the reset is awaited
        self.answers = []  # the bytes that the DSP has to say, in order
        self.waiting_for = 0  # the command that waits for its argument (the echo), or 0
        self.speaker = False

    def read(self, port):
        offset = port - BASE
        if offset == MIXER_DATA:
            return self.mixer[self.mixer_index]
        if offset == READ_DATA:
            return self.answers.pop(0) if self.answers else 0xFF
        if offset == WRITE:
            return 0x00  # a command can be written at once: bit 7 is clear
        if offset == READ_STATUS:
            return 0x80 if self.answers else 0x00
        return 0xFF  # the rest of the ports, as the card of that time answers

    def write(self, port, value):
        offset = port - BASE
        if offset == MIXER_INDEX:
            self.mixer_index = value
        elif offset == MIXER_DATA:
            self.mixer[self.mixer_index] = value
        elif offset == RESET:
            if value == 1:
                self.resetting = True
            elif self.resetting:
                self.resetting = False
                self.answers = [READY]
                self.waiting_for = 0
        elif offset == WRITE:
            self.command(value)

    def command(self, value):
        """What the DSP does with a byte that is written to it: an argument of the command before, or a command."""
        if self.waiting_for == ECHO:
            self.answers.append(~value & 0xFF)  # the echo gives the byte back, with every bit turned
            self.waiting_for = 0
        elif value == ECHO:
            self.waiting_for = ECHO
        elif value == VERSION_COMMAND:
            self.answers += list(VERSION)
        elif value in (SPEAKER_ON, SPEAKER_OFF):
            self.speaker = value == SPEAKER_ON
        else:
            raise NotImplementedError(f"the Sound Blaster has no command {value:02X}")


def install(cpu):
    """Put the card on the CPU: it at BASE, and nothing at the other places that are looked at. Returns the card."""
    card = SoundBlaster()
    for port in range(FIRST_PROBED, LAST_PROBED + 1):
        cpu.port_readers[port] = lambda port: 0xFF
        cpu.port_writers[port] = lambda port, value: None
    for port in range(BASE, BASE + 16):
        cpu.port_readers[port] = card.read
        cpu.port_writers[port] = card.write
    return card
