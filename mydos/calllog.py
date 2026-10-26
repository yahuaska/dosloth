"""A log of the calls a program makes to DOS: what it asked, and what it was answered. Like strace, for DOS.

Every call of the interrupts of DOS (20h, 21h, 25h, 2Fh) is two lines, the question and the answer:

  2300000  1010:3336  INT 21h AX=3C00 BX=0000 CX=0000 DX=0100 SI=0000 DI=0000 DS=2000 ES=2000 "C:\\X\\Y.EXE"
                      -> CF=1 AX=0003 BX=0000 CX=0000 DX=0100

The number is how many instructions had been started (the same count as in the trace of the instructions, `-d`), then
the address of the INT instruction, the registers the call was made with, and, for the functions that take a name,
the name. A call that stops the emulator (one not done yet) ends with `NOT IMPLEMENTED`, and the one that ends the
program with `exit` and its code. The line is written before the call is carried out and finished after it, so
that the last line of the log is the call that was being made when something went wrong.

A run of calls that are the same in every way, the question and the answer, is written once, with a line under it
that says how many more there were (see CallLog): a program that waits for a key makes thousands.

For starting a program (AH=4Bh) the line also has `tail` and the command line the program is given, which is how a
program that has DOS run a command (`COMMAND.COM /C md C:\\X`) shows what it asked for. For AH=29h, the text.
"""
from . import flags
from .cpu import CPU, Exit

INTERRUPTS = (0x20, 0x21, 0x25, 0x2F)
MAX_TEXT = 100  # how much of a string is shown; more is cut and ends with "..."
# the functions of INT 21h that take a zero-ended name at DS:DX
NAMES = (0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x41, 0x43, 0x4B, 0x4E, 0x5B)
REGISTERS = (("AX", CPU.AX), ("BX", CPU.BX), ("CX", CPU.CX), ("DX", CPU.DX), ("SI", CPU.SI), ("DI", CPU.DI))
SEGMENTS = (("DS", CPU.DS), ("ES", CPU.ES))


def shown(data, cut):
    """The bytes as text in quotes: printable characters as they are (a backslash too: it is in every path), a quote
    and the others as \\xNN; ends with ... if cut."""
    text = "".join(chr(byte) if 0x20 <= byte < 0x7F and byte != 0x22 else f"\\x{byte:02X}" for byte in data)
    return f'"{text}{"..." if cut else ""}"'


def string_at(cpu, segment, offset, ends):
    """A string of memory, up to a byte of `ends` or MAX_TEXT bytes, as shown() writes it."""
    data = bytearray()
    for i in range(MAX_TEXT + 1):
        byte = cpu.rb(segment, (offset + i) & 0xFFFF)
        if byte in ends:
            return shown(data, False)
        if i == MAX_TEXT:
            return shown(data, True)
        data.append(byte)


def command_tail(cpu):
    """The command line the program gives to the program it starts (AH=4Bh): the parameter block at ES:BX holds a far
    pointer, at its 2nd byte, to a length byte and that many characters."""
    block_segment, block_offset = cpu.s[CPU.ES], cpu.r[CPU.BX]
    offset = cpu.rw(block_segment, (block_offset + 2) & 0xFFFF)
    segment = cpu.rw(block_segment, (block_offset + 4) & 0xFFFF)
    length = cpu.rb(segment, offset)
    data = bytes(cpu.rb(segment, (offset + 1 + i) & 0xFFFF) for i in range(min(length, MAX_TEXT)))
    return shown(data, length > MAX_TEXT)


def argument(cpu, number):
    """What the call has besides the registers: the name, the text to print, the bytes to write. Or ''."""
    if number != 0x21:
        return ""
    ah = cpu.get8(4)
    ds, dx = cpu.s[CPU.DS], cpu.r[CPU.DX]
    if ah in NAMES:
        text = " " + string_at(cpu, ds, dx, (0,))
        if ah == 0x4B and cpu.get8(0) in (0, 1):  # the program that is started, and what it is given to do
            text += " tail " + command_tail(cpu)
        return text
    if ah == 0x09:  # the text ends with a dollar sign
        return " " + string_at(cpu, ds, dx, (ord("$"),))
    if ah == 0x29:  # a name taken apart into an FCB: it ends where a name does, or with the end of the line
        return " " + string_at(cpu, ds, cpu.r[CPU.SI], (0, 0x0D))
    if ah == 0x40:  # the bytes to write: as many as CX says, but not all of them
        count = cpu.r[CPU.CX]
        data = bytes(cpu.rb(ds, (dx + i) & 0xFFFF) for i in range(min(count, MAX_TEXT)))
        return " " + shown(data, count > MAX_TEXT)
    return ""


def result(cpu, number, ah):
    """What the call answered: the carry flag and the four registers that answers come in; and for the current
    directory (AH=47h), which is given in a buffer, the directory. `ah` is the function that was asked for."""
    carry = bool(cpu.flags & flags.CF)
    text = f"CF={int(carry)}" + "".join(f" {name}={cpu.r[n]:04X}" for name, n in REGISTERS[:4])
    if number == 0x21 and ah == 0x47 and not carry:
        text += " " + string_at(cpu, cpu.s[CPU.DS], cpu.r[CPU.SI], (0,))
    return text


def request(cpu, number):
    """What was asked, as the text after the count: where, which call, with what."""
    registers = " ".join(f"{name}={cpu.r[n]:04X}" for name, n in REGISTERS)
    segments = " ".join(f"{name}={cpu.s[n]:04X}" for name, n in SEGMENTS)
    return f"{cpu.s[CPU.CS]:04X}:{cpu.start_ip:04X}  INT {number:02X}h {registers} {segments}{argument(cpu, number)}"


INDENT = " " * 22  # the second line of a call goes under the INT of the first


class CallLog:
    """The log: writes the calls to a file, and folds a run of the same call into one.

    A program that waits (asks for the time, over and over) makes thousands of calls that are the same in every
    way: the same question, the same answer. Only the first is written, and the run ends with a line that counts
    the others. Two calls that differ in the answer are not the same, whatever the question was. (A call that stops
    the program is the last one, and has an answer of its own, so it is never folded into one before it.)

    The first line of a call is written before the call is made, so that a call that never comes back is the last
    line of the file. When the call turns out to be the same as the one before, that line is taken back.
    """

    def __init__(self, file):
        self.file = file
        self.previous = None  # (the request without its count, the answer) of the last call that was written
        self.repeated = 0  # how many calls since then were the same, and are not written

    def begin(self, cpu, number):
        """Write the first line of a call. Returns what `end` needs."""
        position = self.file.tell()
        body = request(cpu, number)
        line = f"{cpu.instructions:>9}  {body}"
        self.file.write(line + "\n")
        return position, line, body

    def end(self, started, answer):
        """Finish the call: write its answer, or take it back if it is the same as the one before."""
        position, line, body = started
        if self.previous == (body, answer):
            self.file.seek(position)
            self.file.truncate()
            self.repeated += 1
            return
        if self.repeated:  # the run is over: say how long it was, before the line of this call
            self.file.seek(position)
            self.file.truncate()
            self.file.write(f"{INDENT}... the same call {self.repeated} more times\n{line}\n")
        self.file.write(f"{INDENT}-> {answer}\n")
        self.previous = (body, answer)
        self.repeated = 0

    def finish(self):
        """The log is over: say how long the last run of the same call was, if it was one."""
        if self.repeated:
            self.file.write(f"{INDENT}... the same call {self.repeated} more times\n")
            self.repeated = 0


def install(cpu, file, interrupts=INTERRUPTS):
    """Put the log in front of the handlers of DOS that the CPU has. `file` is a file open for writing. Returns the
    CallLog: call its finish() at the end."""
    log = CallLog(file)
    for number in interrupts:
        handler = cpu.int_handlers.get(number)
        if handler is not None:
            cpu.int_handlers[number] = _logged(number, handler, log)
    return log


def _logged(number, handler, log):
    def logged(cpu):
        ah = cpu.get8(4)
        started = log.begin(cpu, number)
        try:
            handler(cpu)
        except Exit as stop:
            log.end(started, f"exit {stop.code}")
            raise
        except NotImplementedError:
            log.end(started, "NOT IMPLEMENTED")
            raise
        log.end(started, result(cpu, number, ah))
    return logged
