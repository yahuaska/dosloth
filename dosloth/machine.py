"""The machine as a whole: a CPU with the BIOS, DOS, the timer, the video card, the mouse and a silent Sound Blaster on it.

A run builds it the same way whether the program is started or the state of an earlier run is put back (see
snapshot.py), so the code that does it is here, once.
"""
from dataclasses import dataclass

from . import bios, country, dma, dos, mouse, patches, pic, soundblaster, sysport, timer, vesa, vga
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
    pics: object = None  # (master, slave): the interrupt controllers
    dma: object = None  # the DMA controller
    soundblaster: object = None  # the silent card
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
    controllers = pic.install(cpu)
    channels = dma.install(cpu)
    cpu.event_sources.append(lambda now: clock.events(now, controllers))
    clock.on_change = cpu.wake
    blaster = soundblaster.install(cpu, controllers, channels)
    cpu.wake()
    patches.install(cpu)
    return Machine(cpu, keyboard, state, clock, card, pointer, port, volumes, controllers, channels, blaster)


def install_rom(cpu):
    """Write what the ROM of a real machine holds: the data of the BIOS that does not change while a program runs."""
    vesa.install(cpu)
    country.install(cpu)
    dos.install_iret(cpu)
