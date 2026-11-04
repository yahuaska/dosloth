"""Driving the program by hand: a step, a step over a call, going on up to a breakpoint.

This is what a window with a debugger (and anything else that wants to look at the machine between two instructions)
stands on; it draws nothing itself. Every command runs the program some way and answers with a Stop: why it stopped.
"""
from dataclasses import dataclass

from .breakpoints import BreakpointHit
from .cpu import CPU, Exit
from .disasm import disasm

STEP, BREAKPOINT, PAUSE, EXIT = "step", "breakpoint", "pause", "exit"


class Pause(Exception):
    """What the `tick` of Debugger.go raises (or any function that it calls) to stop the program where it is: for a
    person who pressed "stop" while the program runs."""


@dataclass
class Stop:
    """Why the program stopped: `reason` is STEP (it did what was asked for), BREAKPOINT (it is before the instruction
    at one), PAUSE (a Pause was raised) or EXIT (it ended, with `code`)."""
    reason: str
    code: int = None


class Debugger:
    """The commands of a person who watches a program run. The machine is between two instructions when a command
    returns, and the next command goes on from there; after an EXIT nothing runs any more and every command answers
    with the same EXIT."""

    def __init__(self, cpu):
        """Take the machine `cpu`; the breakpoints it already has stay."""
        self.cpu = cpu
        self.exit = None  # the Stop of the end of the program, once it ended

    def add_breakpoint(self, segment, offset):
        """Stop before the instruction at segment:offset (any segment if it is None) when going on."""
        self.cpu.add_breakpoint(segment, offset)

    def remove_breakpoint(self, segment, offset):
        """Take such a breakpoint away; returns whether it was there."""
        return self.cpu.remove_breakpoint(segment, offset)

    def breakpoints(self):
        """All the breakpoints, as (segment or None, offset), in the order of their offsets."""
        found = []
        for offset, where in self.cpu.breakpoints.items():
            found.extend((None if where is True else segment, offset) for segment in ([None] if where is True
                                                                                       else sorted(where)))
        return sorted(found, key=lambda one: (one[1], -1 if one[0] is None else one[0]))

    def step(self):
        """Run exactly one instruction, whatever is in the way (a call goes into the procedure)."""
        if self.exit:
            return self.exit
        try:
            self.cpu.step()
        except Exit as e:
            return self.ended(e)
        return Stop(STEP)

    def step_over(self, every=None, tick=None):
        """Run one instruction; but a CALL or an INT runs whole, up to the instruction after it (with the same stack:
        a procedure that calls itself does not end it sooner). It stops at a breakpoint met on the way, and `tick` and
        `every` are as for go()."""
        if self.exit:
            return self.exit
        cpu = self.cpu
        length, text = disasm(cpu, cpu.s[CPU.CS], cpu.ip)
        if text.split(" ")[0] not in ("call", "int"):
            return self.step()
        after, stack = cpu.ip + length, cpu.wide(CPU.SP)
        return self.run(lambda: cpu.ip == after and cpu.wide(CPU.SP) >= stack, every, tick)

    def go(self, every=None, tick=None):
        """Run up to a breakpoint or the end of the program. With `tick` and `every` it calls `tick` after every
        `every` instructions, and if that raises a Pause, the program stops where it is."""
        if self.exit:
            return self.exit
        try:
            return self.ended(Exit(self.cpu.run(every, tick)))  # run() takes the Exit and returns its code
        except Pause:
            return Stop(PAUSE)
        except BreakpointHit:
            return Stop(BREAKPOINT)

    def run(self, done, every=None, tick=None):
        """Run until `done()` is true before an instruction (not before the first), or a breakpoint, or the end."""
        if self.exit:
            return self.exit
        cpu, steps = self.cpu, 0
        try:
            while True:
                cpu.step()
                if done():
                    return Stop(STEP)
                if cpu.breakpoints and cpu.at_breakpoint():
                    return Stop(BREAKPOINT)
                if tick is not None:
                    steps += 1
                    if steps == every:
                        tick()
                        steps = 0
        except Exit as e:
            return self.ended(e)
        except Pause:
            return Stop(PAUSE)

    def ended(self, exit):
        """The program ended with the Exit `exit`: remember it."""
        self.exit = Stop(EXIT, exit.code)
        return self.exit
