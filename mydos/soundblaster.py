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
off (D1h, D3h), the time constant (40h), the playing from memory by DMA (14h: it is over at once, nothing is
played: the DMA controller says the channel has reached its end and the card raises its interrupt), and the interrupt that it raises when it is told to (F2h; it is on line 7, and a read of 0Eh, which
says "I have heard it", takes it back). Any other command stops the emulator, so that nothing goes unnoticed. The card says it is the
SB16 of version 4.05. The mixer is a row of bytes that keep what they are given.
"""
BASE = 0x220
FIRST_PROBED = 0x210  # the places where a setup program looks for the card, and where nothing is, but at BASE
LAST_PROBED = 0x26F
READY = 0xAA
VERSION = (4, 5)
MIXER_SIZE = 256

MIXER_INDEX, MIXER_DATA, RESET, READ_DATA, WRITE, READ_STATUS = 0x04, 0x05, 0x06, 0x0A, 0x0C, 0x0E
ECHO, VERSION_COMMAND, SPEAKER_ON, SPEAKER_OFF = 0xE0, 0xE1, 0xD1, 0xD3
TIME_CONSTANT = 0x40  # one byte follows: how fast the data is played, as 256 less a number of microseconds
DMA_OUTPUT = 0x14  # two bytes follow: the number of the bytes to play, less one, low and high; the DMA gives them
DMA_CHANNEL = 1  # the channel of the DMA controller that the card is on
ARGUMENTS = {ECHO: 1, TIME_CONSTANT: 1, DMA_OUTPUT: 2}  # how many bytes follow each command that takes any
RAISE_IRQ = 0xF2  # the DSP raises its interrupt at once: how a program finds the line that the card is on
IRQ = 7  # the line of the interrupt controllers that the card is on


class SoundBlaster:
    """The state of the card. All of it is numbers and lists, so that a saved state can hold it."""

    def __init__(self, pics=None, dma=None):
        self.dma = dma  # the DMA controller (dma.Dma), told when a transfer ends (not saved)
        self.pics = pics  # the interrupt controllers, for the card to raise its line on (not saved)
        self.mixer_index = 0
        self.mixer = [0] * MIXER_SIZE
        self.resetting = False  # a 1 was written to the reset port, and the 0 that ends the reset is awaited
        self.answers = []  # the bytes that the DSP has to say, in order
        self.waiting_for = 0  # the command that waits for its arguments (the echo), or 0
        self.arguments = []  # the bytes of the arguments that came
        self.time_constant = 0
        self.speaker = False

    def state(self):
        """What a saved state holds of the card: all but the controllers."""
        return {name: value for name, value in vars(self).items() if name not in ("pics", "dma")}

    def read(self, port):
        offset = port - BASE
        if offset == MIXER_DATA:
            return self.mixer[self.mixer_index]
        if offset == READ_DATA:
            return self.answers.pop(0) if self.answers else 0xFF
        if offset == WRITE:
            return 0x00  # a command can be written at once: bit 7 is clear
        if offset == READ_STATUS:
            if self.pics:
                self.pics.lower_line(IRQ)  # an interrupt that has not been taken is forgotten; one in service goes on
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
                self.waiting_for, self.arguments = 0, []
        elif offset == WRITE:
            self.command(value)

    def command(self, value):
        """What the DSP does with a byte that is written to it: an argument of the command before, or a command."""
        if self.waiting_for:
            self.arguments.append(value)
            if len(self.arguments) == ARGUMENTS[self.waiting_for]:
                command, arguments, self.waiting_for, self.arguments = self.waiting_for, self.arguments, 0, []
                self.finish(command, arguments)
        elif value in ARGUMENTS:
            self.waiting_for = value
        elif value == VERSION_COMMAND:
            self.answers += list(VERSION)
        elif value == RAISE_IRQ:
            self.interrupt()
        elif value in (SPEAKER_ON, SPEAKER_OFF):
            self.speaker = value == SPEAKER_ON
        else:
            raise NotImplementedError(f"the Sound Blaster has no command {value:02X}")

    def finish(self, command, arguments):
        """A command that had arguments has them all."""
        if command == ECHO:
            self.answers.append(~arguments[0] & 0xFF)  # the echo gives the byte back, with every bit turned
        elif command == TIME_CONSTANT:
            self.time_constant = arguments[0]
        else:  # the DMA output: the sound is not played, and it is over at once
            if self.dma:
                self.dma.finish(DMA_CHANNEL)
            self.interrupt()

    def interrupt(self):
        if self.pics:
            self.pics.raise_line(IRQ)


def install(cpu, pics=None, dma=None):
    """Put the card on the CPU: it at BASE, and nothing at the other places that are looked at. Returns the card."""
    card = SoundBlaster(pics, dma)
    for port in range(FIRST_PROBED, LAST_PROBED + 1):
        cpu.port_readers[port] = lambda port: 0xFF
        cpu.port_writers[port] = lambda port, value: None
    for port in range(BASE, BASE + 16):
        cpu.port_readers[port] = card.read
        cpu.port_writers[port] = card.write
    return card
