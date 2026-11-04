"""Stopping a program at an address: where it is asked for (`-b`), and what stops it.

The address is written as the logs of the emulator write it: `000F:0010E060` (the selector or the segment, a colon, and
the offset, in hex), or the offset alone, which is any selector: a 32-bit program has but one for its code, and it is
tedious to type.
"""

SEGMENT_MAX, OFFSET_MAX = 0xFFFF, 0xFFFFFFFF


class BreakpointHit(Exception):
    """Raised by CPU.run when the program is about to execute the instruction at a breakpoint. The machine is then
    between two instructions: the one at the breakpoint has not been begun, and the program can go on from it."""


def parse(text):
    """An address as written on the command line, as (the segment or None, the offset). Raises a ValueError that
    says what is wrong, for the text of the answer to a person."""
    wrong = ValueError(f"{text!r} is not an address: it is written as SEGMENT:OFFSET or as OFFSET, in hex")
    segment_text, colon, offset_text = text.rpartition(":")
    try:
        segment = int(segment_text, 16) if colon else None
        offset = int(offset_text, 16)
    except ValueError:
        raise wrong from None
    if offset < 0 or (segment is not None and segment < 0):
        raise wrong
    if segment is not None and segment > SEGMENT_MAX:
        raise ValueError(f"the segment of {text!r} is more than {SEGMENT_MAX:X}")
    if offset > OFFSET_MAX:
        raise ValueError(f"the offset of {text!r} is more than {OFFSET_MAX:X}")
    return segment, offset
