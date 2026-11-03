"""The system timer chip (an 8253/8254) on I/O ports 40h-43h.

It has three counters that count down at 1,193,182 ticks a second:
    counter 0 (port 40h)  the system clock: its wrap-around is the 18.2 times a second "timer tick"
    counter 1 (port 41h)  once drove memory refresh
    counter 2 (port 42h)  drives the speaker
and a control port (43h) that sets how each one works.

Nothing here runs in the background. A counter's value is worked out from the time whenever a program
reads it, and the time is the number of instructions executed so far, not the clock of the host. That
keeps every run the same as the one before, which matters when traces are compared. How many timer ticks
one instruction is worth is a guess, TICKS_PER_INSTRUCTION.

What is done: counting in binary in modes 0, 2 and 3 (the ones programs use), the control byte, the latch
command, and reading and loading counters as low byte, high byte, or both. What is not (it stops the
emulator, so nothing goes unnoticed): BCD counting, modes 1, 4 and 5, and the read-back command.

Counter 0 raises line 0 of the interrupt controllers (pic.py) at the end of every count, when the machine has them:
the machine makes `events` a source of the CPU (see CPU.events).
"""

PIT_FREQUENCY = 1193182  # ticks a second
# A real 8086 runs roughly 300,000 instructions a second, so one instruction is about 4 timer ticks.
TICKS_PER_INSTRUCTION = 4


class Counter:
    """What one of the three counters remembers."""

    def __init__(self, mode, reload):
        self.mode = mode
        self.access = 3  # 1: low byte only, 2: high byte only, 3: low byte then high byte
        self.reload = reload  # the value counting starts from; 0 means 65536
        self.start = 0  # the time, in ticks, when counting (re)started
        self.fired = 0  # how many times it has reached the end of its count since `start`, and the line was raised
        self.pending_low = None  # a low byte written, waiting for its high byte
        self.read_high_next = False  # the next read of an unlatched counter gives the high byte
        self.latched = None  # the bytes a latch command froze, still to be read, or None


class Timer:
    """The timer chip. `now()` gives the time in timer ticks."""

    def __init__(self, now, ticks_per_instruction=TICKS_PER_INSTRUCTION):
        self.now = now
        self.ticks_per_instruction = ticks_per_instruction
        self.on_change = None  # called when counter 0 is set again, so that the time of its next end of count is looked up
        # the state the BIOS leaves the chip in: counter 0 at the 18.2 Hz tick, counter 1 for refresh
        self.counters = [Counter(3, 0), Counter(2, 18), Counter(3, 0)]

    def value(self, counter):
        """The number the counter shows at this moment, 0-65535."""
        ticks = self.now() - counter.start
        reload = counter.reload or 65536
        if counter.mode == 0:  # counts down once, and goes on through zero
            value = reload - ticks
        elif counter.mode == 2:  # counts down to 1, then starts again from the reload value
            value = reload - ticks % reload
        else:  # mode 3, a square wave: counts down by two, so it makes the trip in half the time
            value = reload - 2 * (ticks % max(reload // 2, 1))
        return value & 0xFFFF  # 65536 shows as 0

    def events(self, instructions, pics):
        """Raise line 0 of the interrupt controllers when counter 0 has reached the end of its count, as the system
        clock does 18.2 times a second (or as often as a program has set it). `instructions` is the time. Returns the
        instruction count of the next end of count, or None if there will be none (a counter of mode 0 counts once).

        Several ends of count that went by since the last time make one request, as the controller keeps no more.
        """
        counter = self.counters[0]
        reload = counter.reload or 65536
        ends = (self.now() - counter.start) // reload
        if counter.mode == 0:
            ends = min(ends, 1)
        if ends > counter.fired:
            counter.fired = ends
            pics.raise_line(0)
        if counter.mode == 0 and counter.fired:
            return None
        next_end = counter.start + (counter.fired + 1) * reload  # in ticks
        return -(-next_end // self.ticks_per_instruction)

    def read(self, port):
        """A byte read from the data port of counter 0, 1 or 2."""
        counter = self.counters[port - 0x40]
        if counter.latched:
            return counter.latched.pop(0)  # the frozen value, byte by byte
        value = self.value(counter)  # not frozen: the live value, and it moves between the two reads
        if counter.access == 1:
            return value & 0xFF
        if counter.access == 2:
            return value >> 8
        if counter.read_high_next:  # access mode 3: the low byte first, then the high byte, and round again
            counter.read_high_next = False
            return value >> 8
        counter.read_high_next = True
        return value & 0xFF

    def write(self, port, byte):
        """A byte written to the control port (43h) or to the data port of one of the counters."""
        if port == 0x43:
            self.write_control(byte)
            return
        counter = self.counters[port - 0x40]
        if counter.access == 1:
            reload = byte
        elif counter.access == 2:
            reload = byte << 8
        else:  # low byte first, and the counter is loaded when the high byte arrives
            if counter.pending_low is None:
                counter.pending_low = byte
                return
            reload = counter.pending_low | byte << 8
            counter.pending_low = None
        counter.reload = reload
        counter.start = self.now()
        counter.fired = 0
        if self.on_change:
            self.on_change()

    def write_control(self, byte):
        """The control byte: bits 7-6 pick the counter, 5-4 how it is read and loaded, 3-1 the mode, 0 BCD."""
        number, access, mode, bcd = byte >> 6, (byte >> 4) & 3, (byte >> 1) & 7, byte & 1
        if number == 3:
            raise NotImplementedError("timer read-back command")
        counter = self.counters[number]
        if access == 0:  # the latch command: freeze the value now, so it can be read a byte at a time
            if counter.latched is None or not counter.latched:  # one that is still unread stays as it is
                value = self.value(counter)
                low, high = value & 0xFF, value >> 8
                counter.latched = {1: [low], 2: [high], 3: [low, high]}[counter.access]
            return
        if bcd:
            raise NotImplementedError("timer counting in BCD")
        mode = {6: 2, 7: 3}.get(mode, mode)  # the same modes, written another way
        if mode not in (0, 2, 3):
            raise NotImplementedError(f"timer mode {mode}")
        counter.mode, counter.access = mode, access
        if self.on_change:
            self.on_change()
        counter.pending_low, counter.read_high_next, counter.latched = None, False, None


def install(cpu, ticks_per_instruction=TICKS_PER_INSTRUCTION):
    """Put a timer on ports 40h-43h of the CPU. Its clock is the CPU's count of executed instructions. Returns it."""
    timer = Timer(lambda: cpu.instructions * ticks_per_instruction, ticks_per_instruction)
    for port in (0x40, 0x41, 0x42):
        cpu.port_readers[port] = timer.read
    for port in (0x40, 0x41, 0x42, 0x43):  # the control port can only be written
        cpu.port_writers[port] = timer.write
    return timer
