import argparse
import sys
from pathlib import Path

from . import bios, config, disasm, dos, dump, loader, mouse, psp, screen, timer, vga
from .cpu import CPU
from .display import PngFile
from .window import TkWindow, WindowError
from .filesystem import FsError
from .psp import ENV_SEG, PSP_SEG
from .volumes import Volumes

TICK_EVERY = 20_000  # the displays are looked after this often, in instructions: some 20 times a second


def load(cpu, data, dos_path):
    """Put the program on the command line in memory at the PSP segment, with its PSP and environment.

    `data` is the program file and `dos_path` its full DOS name (`C:\\SETUP\\ESETUP.EXE`), which goes to the program
    in its environment.
    """
    psp.build(cpu, PSP_SEG, ENV_SEG, dos.MEMORY_TOP_SEG, dos_path)
    loader.load(cpu, data, PSP_SEG)


def make_trace(log):
    """Return a CPU trace hook that writes one line per instruction to the open file `log`.

    A line looks like `1773:0880  8B C3        mov ax, bx`: address, the instruction bytes, the text.
    """
    def trace(cpu):
        seg, off = cpu.s[CPU.CS], cpu.ip
        length, text = disasm.disasm(cpu, seg, off)
        raw = bytes(cpu.rb(seg, off + i) for i in range(length)).hex(" ").upper()
        log.write(f"{seg:04X}:{off:04X}  {raw:<20}  {text}\n")
    return trace


def main():
    """Run the program named on the command line; exit with the program's exit code."""
    parser = argparse.ArgumentParser(prog="python -m mydos", description="Run a DOS .COM or .EXE program.")
    parser.add_argument("-c", metavar="FILE", help="the configuration (TOML): drives, mounts, starting directory; "
                        "without it the current directory is the hard disk C:")
    parser.add_argument("-d", metavar="LOGFILE", help="write every executed instruction to LOGFILE")
    parser.add_argument("-s", metavar="PNGFILE", help="show the screen in this PNG file: written again whenever the "
                        "picture has changed, and at the end, also after a crash")
    parser.add_argument("-w", action="store_true", help="show the screen in a window; the keys typed in it go to "
                        "the program. Closing the window stops the program")
    parser.add_argument("program", help="the .COM or .EXE file to run, named as DOS names it: `ESETUP.EXE`, "
                        "`C:\\SETUP\\ESETUP.EXE`, `setup/esetup.exe`; relative to the current directory of the "
                        "starting drive (the `cwd` of the configuration)")
    args = parser.parse_args()
    try:
        machine = config.load(args.c) if args.c else config.default(Path.cwd())
    except config.ConfigError as error:
        parser.error(str(error))  # prints the message with the usage and exits with status 2, before anything runs

    volumes = Volumes(machine)
    try:
        where = volumes.resolve(args.program)
        with where.filesystem.open(where.parts) as program:
            data = program.read()
    except FsError as error:
        # not found, not a file, or on a drive that is not there or has nothing in it
        parser.error(f"cannot run {args.program}: {error} (the program is named as DOS names a file, from the "
                     "starting directory; to run one that is elsewhere on the host, mount its directory with -c)")
    # the dump of a crash goes beside the program on the host, when the program is there
    host_path = args.program
    if hasattr(where.filesystem, "host_path"):
        host_path = str(where.filesystem.host_path(where.parts))

    cpu = CPU()
    keyboard = bios.install(cpu, machine)
    dos.install(cpu, volumes)
    timer.install(cpu)
    card = vga.install(cpu)
    pointer = mouse.install(cpu)
    displays = []
    if args.s:
        displays.append(PngFile(args.s))
    if args.w:
        try:
            displays.append(TkWindow(keyboard.press, pointer.update))
        except WindowError as error:
            parser.error(f"cannot open a window: {error}")
    monitor = screen.Monitor(card, displays) if displays else None
    if args.w:  # only a window has keys to give, so only with one can a program wait for a key
        keyboard.waiter = lambda: monitor.wait_for_key(keyboard)
    load(cpu, data, where.dos_path())
    log = open(args.d, "w") if args.d else None
    if log:
        cpu.trace = make_trace(log)
    try:
        code = cpu.run(TICK_EVERY, monitor.tick) if monitor else cpu.run()
    except Exception as e:
        dump.write(cpu, host_path, e)  # leave something to debug with, then fail as usual
        raise
    finally:
        if monitor:
            monitor.show()  # the last picture, whether the program ended or crashed
            monitor.close()
        if log:
            log.close()
    sys.exit(code)


main()
