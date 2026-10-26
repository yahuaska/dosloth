"""Programs that start programs: INT 21h AH=4Bh (EXEC), and the end of a program, which gives the machine back.

DOS runs one program at a time, and a program that starts another one waits until it ends. We do the same without a
second CPU: EXEC puts the registers of the parent aside, loads the child and lets the CPU go on in it. When the
child ends (AH=4Ch, INT 20h), the registers of the parent come back and it goes on after its own INT 21h.
"""
from dataclasses import dataclass

from . import command, flags, loader, psp
from .cpu import CPU, Exit
from .filesystem import FILE_NOT_FOUND, PATH_NOT_FOUND, FsError
from .handles import HandleTable

INSUFFICIENT_MEMORY = 8
BAD_FORMAT = 11  # a .EXE with a header that is not all there
TAIL_SIZE = 128  # the command tail: a length, up to 126 characters and the carriage return that ends them
MAX_ENVIRONMENT = 32768  # the most an environment can be; a block with no end is cut here


@dataclass
class Parent:
    """What a program that started another one leaves behind, to be taken back when the child ends."""
    psp_seg: int
    registers: list
    segments: list
    ip: int
    flags: int
    dta: tuple
    handles: HandleTable


def read_environment(cpu, segment):
    """The strings (bytes, without their zero) of the environment block at `segment`: they end at an empty string."""
    strings, current = [], bytearray()
    for i in range(MAX_ENVIRONMENT):
        byte = cpu.rb(segment, i)
        if byte:
            current.append(byte)
        elif current:
            strings.append(bytes(current))
            current = bytearray()
        else:
            break
    return strings


def read_pointer(cpu, segment, offset):
    """The far pointer (offset, then segment) at segment:offset, as (segment, offset)."""
    return cpu.rw(segment, (offset + 2) & 0xFFFF), cpu.rw(segment, offset)


def read_bytes(cpu, segment, offset, count):
    """`count` bytes from segment:offset, going round at the end of the segment as an offset does."""
    return bytes(cpu.rb(segment, (offset + i) & 0xFFFF) for i in range(count))


def read_tail(cpu, block):
    """The command line that the parameter block at `block` (segment, offset) gives to the program: the far pointer
    at its 2nd byte leads to a length byte and that many characters, as text."""
    segment, offset = block
    tail_offset = cpu.rw(segment, (offset + 2) & 0xFFFF)
    tail_segment = cpu.rw(segment, (offset + 4) & 0xFFFF)
    length = cpu.rb(tail_segment, tail_offset)
    return read_bytes(cpu, tail_segment, tail_offset + 1, length).decode("cp437")


def start(cpu, state, where, block):
    """AH=4Bh, AL=00h for the file at `where` (a volumes.Location). The file is read and started; but a COMMAND.COM
    that is not there (nor its directory: a program may look for it wherever its COMSPEC says) is the pocket one, see
    command.py: it does the command of the tail itself, and the program that asked goes on, with no carry and AX = 0,
    as after any child that ended well."""
    try:
        with where.filesystem.open(where.parts) as program:
            data = program.read()
    except FsError as error:
        if error.code not in (FILE_NOT_FOUND, PATH_NOT_FOUND) or not command.is_command_com(where.parts):
            raise
        state.exit_status = command.Shell(state.volumes).run(read_tail(cpu, block))
        cpu.r[CPU.AX] = 0
        cpu.set_flag(flags.CF, False)
        return
    execute(cpu, state, data, where.dos_path(), block)


def execute(cpu, state, data, dos_path, block):
    """AH=4Bh, AL=00h: load the program in `data` (the file; `dos_path` is its name) and start it.

    `block` is the segment and offset of the parameter block: the segment of the environment (0: the parent's),
    and the far pointers to the command tail and to the two FCBs. Raises an FsError for what DOS answers with an
    error: 8 if there is not memory enough, 11 for a .EXE with no header.
    """
    try:
        minimum, maximum = loader.memory_needed(data)
    except ValueError as error:
        raise FsError(BAD_FORMAT, str(error)) from None
    segment, offset = block
    parent_psp = state.psp_seg
    env_source = cpu.rw(segment, offset) or cpu.rw(parent_psp, 0x2C)
    strings = read_environment(cpu, env_source)
    env_size = (len(psp.environment(dos_path, strings)) + 15) // 16
    env_seg = state.memory.allocate(env_size, parent_psp)
    if env_seg is None:
        raise FsError(INSUFFICIENT_MEMORY, "no room for the environment of the program")
    if state.memory.largest() < minimum:
        state.memory.free(env_seg)
        raise FsError(INSUFFICIENT_MEMORY, f"the program needs {minimum:X}h paragraphs, {state.memory.largest():X}h "
                      "are free")
    size = min(maximum, state.memory.largest())
    child = state.memory.allocate(size, parent_psp)
    state.memory.own(child, child)
    state.memory.own(env_seg, child)

    psp.build(cpu, child, env_seg, child + size, dos_path, strings, parent_psp)
    tail_at, fcb1_at, fcb2_at = (read_pointer(cpu, segment, offset + at) for at in (2, 6, 10))
    cpu.mem[child * 16 + 0x80:child * 16 + 0x80 + TAIL_SIZE] = read_bytes(cpu, *tail_at, TAIL_SIZE)
    for target, at in ((0x5C, fcb1_at), (0x6C, fcb2_at)):
        cpu.mem[child * 16 + target:child * 16 + target + 16] = read_bytes(cpu, *at, 16)

    state.parents.append(Parent(parent_psp, list(cpu.r), list(cpu.s), cpu.ip, cpu.flags, state.dta, state.handles))
    state.psp_seg = child
    state.dta = (child, 0x80)
    state.handles = state.handles.copy()
    loader.load(cpu, data, child)
    cpu.r[CPU.AX] = 0
    cpu.flags = flags.ALWAYS_ONE | flags.IF


def terminate(cpu, state, code):
    """AH=4Ch and INT 20h: the running program ends with `code`.

    The first program ends the emulator. A child gives the machine back to its parent: its memory is freed, the files
    it opened are closed (those it shares with the parent stay open), the registers of the parent are restored, and
    the parent finds AX = 0 and no carry, which is what a good EXEC leaves. The code waits for AH=4Dh.
    """
    if not state.parents:
        state.handles.close_all()  # a file left open is closed, and what was written to it is kept
        raise Exit(code)
    parent = state.parents.pop()
    state.handles.close_all()  # what the child shares with its parent stays open for the parent
    state.memory.free_owned(state.psp_seg)
    state.handles, state.dta, state.psp_seg = parent.handles, parent.dta, parent.psp_seg
    state.exit_status = code
    cpu.r[:], cpu.s[:], cpu.ip, cpu.flags = parent.registers, parent.segments, parent.ip, parent.flags
    cpu.r[CPU.AX] = 0
    cpu.set_flag(flags.CF, False)
