"""Where a program spends its instructions: how many times each address was executed, and the places where it is most.

The count is made by a trace hook (see `Profile.watching`), so it costs a call for every instruction, and the report
is written when the program is over. What is hot in an emulated program is hot for the emulator too, and this says
what is worth to be done by Python instead of by the emulation of the instructions.
"""
from collections import defaultdict

from .disasm import disasm

GAP = 32  # addresses that are closer than this, in bytes, are one place
PLACES = 12  # how many of the hottest places are listed
LISTED = 60  # how many instructions of a place are shown, the hottest first


class Profile:
    """The count of the instructions executed, by address (the segment and the offset together)."""

    def __init__(self):
        self.counts = defaultdict(int)

    def watching(self, other=None):
        """A trace hook for CPU.trace that counts, and calls `other` (a hook that was there) after it."""
        counts, CS = self.counts, 1  # CPU.CS

        def hook(cpu):
            counts[cpu.s[CS] << 32 | cpu.ip] += 1
            if other is not None:
                other(cpu)
        return hook

    def places(self):
        """The addresses that were executed, gathered into places: lists of (key, count) in the order of addresses,
        where the next address is less than GAP bytes after the one before. A key is the segment and the offset
        as segment << 32 | offset."""
        found, current, last = [], [], None
        for key in sorted(self.counts):
            if last is not None and key - last > GAP:
                found.append(current)
                current = []
            current.append((key, self.counts[key]))
            last = key
        if current:
            found.append(current)
        return found

    def report(self, cpu):
        """The text of the report: the total, the hottest places with what they cost, and the instructions of each."""
        digits = 8 if cpu.ip_mask > 0xFFFF else 4
        total = sum(self.counts.values())

        def address(key):
            return f"{key >> 32:04X}:{key & 0xFFFFFFFF:0{digits}X}"
        ranked = sorted(self.places(), key=lambda place: -sum(count for _, count in place))[:PLACES]
        lines = [f"instructions executed: {total:,} at {len(self.counts):,} different addresses",
                 f"the hottest places (addresses less than {GAP} bytes apart are one place):"]
        for number, place in enumerate(ranked, 1):
            cost = sum(count for _, count in place)
            lines.append(f"  {number:2}. {address(place[0][0])} - {address(place[-1][0])}  {cost:>12,}  "
                         f"{100 * cost / total:5.1f}%  {len(place)} addresses")
        for number, place in enumerate(ranked, 1):
            lines.append("")
            lines.append(f"place {number}: how many times each instruction was executed, the hottest first")
            for key, count in sorted(place, key=lambda one: -one[1])[:LISTED]:
                _, text = disasm(cpu, key >> 32, key & 0xFFFFFFFF)
                lines.append(f"  {count:>12,}  {address(key)}  {text}")
        return "\n".join(lines) + "\n"
