"""The machine as a whole: a CPU with the BIOS, DOS, the timer, the video card and the mouse on it.

A run builds it the same way whether the program is started or the state of an earlier run is put back (see
snapshot.py), so the code that does it is here, once.
"""
from dataclasses import dataclass

from . import bios, country, dos, mouse, sysport, timer, vesa, vga
from .cpu import CPU


@dataclass
class Machine:
    """What a run is made of. The parts are the objects that hold state; the CPU holds the rest, in its memory."""
    cpu: object
    keyboard: object
    state: object  # the DosState
    timer: object
    card: object
    mouse: object
    sysport: object  # port 61h
    volumes: object
    program: str | None = None  # where the program is on the host, which is what a dump of a crash is named after

    @property
    def config(self):
        return self.volumes.config


def build(volumes):
    """A machine for the drives of `volumes`, with nothing loaded yet."""
    cpu = CPU()
    keyboard = bios.install(cpu, volumes.config)
    state = dos.install(cpu, volumes)
    clock = timer.install(cpu)
    card = vga.install(cpu)
    pointer = mouse.install(cpu)
    port = sysport.install(cpu, clock)
    return Machine(cpu, keyboard, state, clock, card, pointer, port, volumes)


def install_rom(cpu):
    """Write what the ROM of a real machine holds: the data of the BIOS that does not change while a program runs."""
    vesa.install(cpu)
    country.install(cpu)
