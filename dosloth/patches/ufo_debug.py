"""The debug printf of UFO2P.EXE (the game), which the build of the game left empty, says what it is given.

The game calls the procedure at 1026E0 with a format and its arguments on the stack, as printf is called (" loadfile2
%s", "FATAL ERROR: %i", "in day loop.", the use of the memory...), and it is a bare RET: the text was taken out of the
build. The patch writes the text to the standard error of the host, with a new line after it when the text has none
(most of the texts of the game have none: they would all run into one line), and returns as the RET does: nothing else
changes, and the count of the instructions is that of the RET.

The same RET stands for other procedures that were emptied too, which are called with their arguments in registers (as
`mov eax, 7Fh; call 1026E0`). A call of printf is told by what the caller does after it: it takes the arguments off
the stack (`add esp, N`, and N / 4 is how many there are, the format with them); a call that is not followed by it
is not a printf, and nothing is written."""
import re
import struct
import sys

from ..cpu import CPU
from ._base import Patch
from ._common import DWORD, MASK, give_back

DEBUG_PRINTF_AT = 0x1026E0
ADD_ESP = b"\x83\xC4"  # add esp, imm8
SPEC = re.compile(rb"%([-+ 0#]*)(\*|\d+)?(?:\.(\d+))?([hl]?)([diouxXcs%])|%")
TEXT_LIMIT = 4096  # the longest string that a %s (or the format) is taken to be


def string_at(cpu, address):
    mem, base = cpu.mem, cpu.bases[cpu.s[CPU.DS]]
    start = base + address
    end = mem.find(b"\0", start, start + TEXT_LIMIT)
    if end < 0:
        raise NotImplementedError(f"the debug printf: no end of the string at {address:X}")
    return bytes(mem[start:end])


def format_text(cpu, form, arguments):
    """The text that printf makes of `form` (bytes) and the dwords `arguments`, in the code page of the machine."""
    arguments = list(arguments)

    def take():
        if not arguments:
            raise NotImplementedError(f"the debug printf: {form!r} asks for more arguments than it is given")
        return arguments.pop(0)

    def one(match):
        flags, width, precision, size, kind = match.groups()
        if kind is None:
            raise NotImplementedError(f"the debug printf: a conversion that is not known in {form!r}")
        if kind == b"%":
            return b"%"
        if width == b"*":  # the width is an argument (a negative one is a width to the left)
            given = take()
            given = given - (1 << 32) if given & 0x80000000 else given
            flags, width = (flags + b"-" if given < 0 else flags), str(abs(given)).encode()
        spec = "%" + flags.decode() + (width or b"").decode() + ("." + precision.decode() if precision else "")
        value = take()
        if size == b"h":
            value &= 0xFFFF
        if kind in b"di":
            bits = 16 if size == b"h" else 32
            value = value - (1 << bits) if value >> (bits - 1) & 1 else value
            return (spec + "d").encode() % value
        if kind in b"ouxX":
            return (spec + kind.decode().replace("u", "d")).encode() % value
        if kind == b"c":
            return (spec + "c").encode() % (value & 0xFF)
        return (spec + "s").encode() % string_at(cpu, value)
    return SPEC.sub(one, form).decode("cp437")


def debug_printf(cpu):
    mem, base = cpu.mem, cpu.bases[cpu.s[CPU.SS]]
    sp = cpu.wide(CPU.SP)
    back = DWORD.unpack_from(mem, base + sp)[0]
    code = cpu.bases[cpu.s[CPU.CS]] + back
    if bytes(mem[code:code + 2]) == ADD_ESP and mem[code + 2] >= 4 and not mem[code + 2] & 3:
        count = mem[code + 2] // 4
        words = struct.unpack_from(f"<{count}I", mem, base + (sp + 4 & MASK))
        text = format_text(cpu, string_at(cpu, words[0]), words[1:])
        sys.stderr.write(text if text.endswith("\n") else text + "\n")  # (one line at least for a call)
        sys.stderr.flush()
    give_back(cpu)  # (the RET: the one instruction, which the INT stands for)


PATCHES = [Patch("the debug printf of UFO2P.EXE", DEBUG_PRINTF_AT, 0x10,  # (the RET and the zeros after it)
                 "e5024788649a7a232bc663563b4f26b4c7f7447931f50d5eb449dd355ca5a326", debug_printf)]
