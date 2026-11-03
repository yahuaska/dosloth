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
The DSP knows what a program needs to find it and to play: the version (E1h), the echo (E0h), the speaker on and off
(D1h, D3h, and D8h to ask), the time constant (40h) and the rate (41h), the size of a block (48h), and the playing from memory by DMA:
single (14h, 91h) and repeated (1Ch, 90h), which can be stopped (D0h), let go on (D4h) and ended at the end of the block (DAh).
Nothing is heard: the card plays no sound, but it takes the time that the playing would take, as the machine counts
it (the instructions, see timer.py), and then the DMA controller says that the channel has reached its end and the
card raises its interrupt (line 7, taken back by a read of 0Eh, which says "I have heard it"; a program also asks for
it with F2h). A program that waits for the blocks to go by waits as long as on a real card. Any other command stops
the emulator, so that nothing goes unnoticed. The card says it is the SB16 of version 4.05. The mixer is a row of
bytes that keep what they are given.
"""
from . import timer

BASE = 0x220
FIRST_PROBED = 0x210  # the places where a setup program looks for the card, and where nothing is, but at BASE
LAST_PROBED = 0x26F
READY = 0xAA
VERSION = (4, 5)
MIXER_SIZE = 256

MIXER_INDEX, MIXER_DATA, RESET, READ_DATA, WRITE, READ_STATUS = 0x04, 0x05, 0x06, 0x0A, 0x0C, 0x0E
ECHO, VERSION_COMMAND, SPEAKER_ON, SPEAKER_OFF = 0xE0, 0xE1, 0xD1, 0xD3
TIME_CONSTANT = 0x40  # one byte follows: how fast the data is played, as 256 less a number of microseconds
SAMPLE_RATE = 0x41  # two bytes follow: the rate in hertz, the high byte first
BLOCK_SIZE = 0x48  # two bytes follow: the number of bytes of a block, less one, low and high
DMA_OUTPUT = 0x14  # two bytes follow: the number of the bytes to play, less one, low and high; the DMA gives them
AUTO_OUTPUT = 0x1C  # the same, again and again: blocks of the size that BLOCK_SIZE said
FAST_AUTO_OUTPUT, FAST_OUTPUT = 0x90, 0x91  # the same two, for the rates that the DSP cannot do in the other way
HALT, CONTINUE, END_AUTO, SPEAKER_STATUS = 0xD0, 0xD4, 0xDA, 0xD8
DMA_CHANNEL = 1  # the channel of the DMA controller that the card is on
ARGUMENTS = {ECHO: 1, TIME_CONSTANT: 1, SAMPLE_RATE: 2, BLOCK_SIZE: 2, DMA_OUTPUT: 2}  # the bytes after the command
RAISE_IRQ = 0xF2  # the DSP raises its interrupt at once: how a program finds the line that the card is on
IRQ = 7  # the line of the interrupt controllers that the card is on


class SoundBlaster:
    """The state of the card. All of it is numbers and lists, so that a saved state can hold it."""

    def __init__(self, pics=None, dma=None, clock=lambda: 0, wake=lambda: None):
        self.dma = dma  # the DMA controller (dma.Dma), told when a transfer ends (not saved)
        self.pics = pics  # the interrupt controllers, for the card to raise its line on (not saved)
        self.clock = clock  # the time: the number of instructions (not saved)
        self.wake = wake  # what to call when the time of the next event may have changed (not saved)
        self.mixer_index = 0
        self.mixer = [0] * MIXER_SIZE
        self.resetting = False  # a 1 was written to the reset port, and the 0 that ends the reset is awaited
        self.answers = []  # the bytes that the DSP has to say, in order
        self.waiting_for = 0  # the command that waits for its arguments (the echo), or 0
        self.arguments = []  # the bytes of the arguments that came
        self.time_constant = 0
        self.block_size = 0  # the bytes of a block of the repeated playing
        self.length = 0  # the bytes that the playing in hand is to play before the interrupt
        self.repeating = False  # the playing is the repeated one
        self.playing = False
        self.due = None  # the time when the playing in hand ends, or None
        self.speaker = False

    def state(self):
        """What a saved state holds of the card: all but the controllers."""
        return {name: value for name, value in vars(self).items() if name not in ("pics", "dma", "clock", "wake")}

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
                self.repeating = False
                self.stop()  # a reset stops the playing
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
        elif value in (AUTO_OUTPUT, FAST_AUTO_OUTPUT):  # the fast one is for a rate that the other cannot do: no more
            self.start(self.block_size, repeating=True)
        elif value == FAST_OUTPUT:
            self.start(self.block_size, repeating=False)
        elif value == SPEAKER_STATUS:
            self.answers.append(0xFF if self.speaker else 0x00)
        elif value == HALT:
            self.stop()
        elif value == CONTINUE:
            if self.length:
                self.start(self.length, self.repeating)
        elif value == END_AUTO:
            self.repeating = False  # the block in hand is played to its end, and that is all
        else:
            raise NotImplementedError(f"the Sound Blaster has no command {value:02X}")

    def finish(self, command, arguments):
        """A command that had arguments has them all."""
        if command == ECHO:
            self.answers.append(~arguments[0] & 0xFF)  # the echo gives the byte back, with every bit turned
        elif command == TIME_CONSTANT:
            self.time_constant = arguments[0]
        elif command == SAMPLE_RATE:
            rate = arguments[0] << 8 | arguments[1]
            self.time_constant = 256 - round(1_000_000 / rate) if rate else 0
        elif command == BLOCK_SIZE:
            self.block_size = (arguments[0] | arguments[1] << 8) + 1
        else:  # DMA_OUTPUT
            self.start((arguments[0] | arguments[1] << 8) + 1, repeating=False)

    def start(self, length, repeating):
        """Begin to play `length` bytes by DMA: the end of them is a time from now."""
        self.length, self.repeating, self.playing = length, repeating, True
        self.due = self.clock() + self.duration(length)
        if self.dma:
            self.dma.begin(DMA_CHANNEL, 1_000_000 / (256 - self.time_constant) * timer.TICKS_PER_INSTRUCTION
                           / timer.PIT_FREQUENCY)  # the bytes a instruction
        self.wake()

    def stop(self):
        self.playing, self.due = False, None
        if self.dma:
            self.dma.end(DMA_CHANNEL)

    def duration(self, length):
        """How many instructions the playing of `length` bytes takes: a byte takes (256 - the time constant)
        microseconds, and the machine counts the time of the timer chip in instructions."""
        microseconds = length * (256 - self.time_constant)
        return max(1, microseconds * timer.PIT_FREQUENCY // 1_000_000 // timer.TICKS_PER_INSTRUCTION)

    def events(self, now):
        """The end of a block, when its time comes: the channel is at its end, and the interrupt is raised. The
        repeated playing goes on with the next block. Returns the time of the next end, or None."""
        if self.due is None or now < self.due:
            return self.due
        if self.dma:
            self.dma.finish(DMA_CHANNEL)
        self.interrupt()
        if self.repeating:
            self.due += self.duration(self.length)
        else:
            self.stop()
        return self.due

    def interrupt(self):
        if self.pics:
            self.pics.raise_line(IRQ)


def install(cpu, pics=None, dma=None):
    """Put the card on the CPU: it at BASE, and nothing at the other places that are looked at. Its time is the count
    of instructions of the CPU, and it is one of the sources of its events. Returns the card."""
    card = SoundBlaster(pics, dma, lambda: cpu.instructions, cpu.wake)
    cpu.event_sources.append(card.events)
    for port in range(FIRST_PROBED, LAST_PROBED + 1):
        cpu.port_readers[port] = lambda port: 0xFF
        cpu.port_writers[port] = lambda port, value: None
    for port in range(BASE, BASE + 16):
        cpu.port_readers[port] = card.read
        cpu.port_writers[port] = card.write
    return card
