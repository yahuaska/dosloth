"""Port B of the system board, I/O port 61h: the speaker, and a clock that programs wait on.

The bits that matter (what a program sees when it reads the port):
    bit 0  the gate of counter 2 of the timer: the counter counts only while it is set
    bit 1  the speaker is connected to the output of counter 2 (with bit 0, a tone; the counter says which)
    bit 4  the refresh of the memory, which toggles every 15 microseconds: a program that wants to wait for a time
           reads the port again and again until the bit has changed a number of times
    bit 5  the output of counter 2
The programs write bits 0 and 1 (`sound` sets them, `nosound` takes them off) and keep the others as they read
them. Nothing is heard here: the speaker is as good as off. But a program may go on as if it were not.

Time is the one of the timer chip (timer.py): the number of instructions executed, so every run is the same.
"""
PORT = 0x61
WRITABLE = 0x0F  # the bits that a write keeps: the gate, the speaker, and the two of the memory check
REFRESH_TICKS = 18  # the memory is refreshed every 15 microseconds, which is 18 ticks of the timer at 1.19 MHz
REFRESH_BIT = 0x10
COUNTER_2_OUT_BIT = 0x20
SPEAKER_COUNTER = 2


class SystemPort:
    """The port. `timer` is the timer chip (a timer.Timer): the clock, and counter 2 that the speaker is driven by."""

    def __init__(self, timer):
        self.timer = timer
        self.value = 0  # the bits that were written, as a program last left them

    def counter_out(self):
        """The output of counter 2 as bit 5 shows it: 1 or 0, from the mode the program put the counter in."""
        counter = self.timer.counters[SPEAKER_COUNTER]
        if counter.mode == 3:  # a square wave: high for the first half of the count, low for the second
            if not self.value & 1:  # with the gate closed the output stays high
                return 1
            return int(self.timer.value(counter) > (counter.reload or 65536) // 2)
        if counter.mode == 0:  # low until the count is done
            return int(self.timer.now() - counter.start >= (counter.reload or 65536))
        return 1  # mode 2 is high but for one tick

    def read(self, port):
        refresh = REFRESH_BIT if (self.timer.now() // REFRESH_TICKS) & 1 else 0
        return self.value | refresh | (COUNTER_2_OUT_BIT if self.counter_out() else 0)

    def write(self, port, byte):
        self.value = byte & WRITABLE


def install(cpu, timer):
    """Put port 61h on the CPU. Returns the port, for the machine to hold."""
    port = SystemPort(timer)
    cpu.port_readers[PORT] = port.read
    cpu.port_writers[PORT] = port.write
    return port
