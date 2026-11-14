"""The look at the 16 events of the timer of the sound system (HMI SOS) in SMKP.EXE (the loop at 1022E9)."""

from ..cpu import CPU
from ._base import Patch
from ._common import DWORD, MASK, WORD


# On every tick of the timer (the procedure at 1022CB) the sound system looks at its 16 events, one after the other
# (the dword SLOT is the number of the one it is at), and calls those that have a handler: a far pointer of 6 bytes
# in HANDLERS. Most of them have none (13 of 16 in the player), and an empty one is 10 instructions. The patch is at the
# head of the loop: it goes over the empty events at once, and lets the code do the one that has a handler (from
# HANDLER_CODE: the count of time of the event and the call), which comes back to the head.
SLOT = 0x11489C
HANDLERS = 0x114383
EVENTS = 16
HANDLER_CODE = 0x102319  # the code of an event that has a handler
LOOP_END = 0x102388  # the code after the loop


def next_event_with_handler(cpu):
    """What the loop does up to an event that has a handler, or to its end: SLOT goes on by the empty ones, EAX is
    6 times the number of the last event that was looked at (the `imul`), and the flags are those of the last `cmp`."""
    mem, base = cpu.mem, cpu.bases[cpu.s[CPU.DS]]
    slot = DWORD.unpack_from(mem, base + SLOT)[0]
    executed = 0
    while True:
        executed += 2  # cmp, jb
        if slot >= EVENTS:
            cpu.sub_with_flags(slot, EVENTS, 32)
            cpu.ip = LOOP_END
            executed += 1  # jmp
            break
        eax = slot * 6 & MASK
        cpu.set32(CPU.AX, eax)
        offset = DWORD.unpack_from(mem, base + (HANDLERS + eax & MASK))[0]
        executed += 3  # imul, cmp, jne
        if offset:
            cpu.sub_with_flags(offset, 0, 32)
            cpu.ip = HANDLER_CODE
            break
        selector = WORD.unpack_from(mem, base + (HANDLERS + 4 + eax & MASK))[0]
        executed += 2  # cmp, je
        if selector:
            cpu.sub_with_flags(selector, 0, 16)
            cpu.ip = HANDLER_CODE
            break
        slot = (slot + 1) & MASK
        DWORD.pack_into(mem, base + SLOT, slot)
        executed += 3  # jmp, inc, jmp
    cpu.instructions += executed - 1  # (the INT stands for the first one)


PATCHES = [Patch("the events of the timer of the sound system", 0x1022E9, 0x9F,
                 "ad2a69ea54f8b8cd14f4e54ff88f73f2a53e10ac2a91e786e357d6f0ad00d44e", next_event_with_handler)]
