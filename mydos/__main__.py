import argparse
import sys
from pathlib import Path

from . import calllog, config, disasm, dos, dump, loader, machine, psp, screen, snapshot
from .cpu import CPU
from .display import PngFile
from .window import TkWindow, WindowError
from .filesystem import FsError
from .psp import ENV_SEG, PSP_SEG
from .volumes import Volumes

TICK_EVERY = 20_000  # the displays are looked after this often, in instructions: some 20 times a second


def load(cpu, data, dos_path, extended):
    """Put the program on the command line in memory at the PSP segment, with its PSP and environment.

    `data` is the program file and `dos_path` its full DOS name (`C:\\SETUP\\ESETUP.EXE`), which goes to the program
    in its environment.
    """
    psp.build(cpu, PSP_SEG, ENV_SEG, dos.MEMORY_TOP_SEG, dos_path)
    loader.load(cpu, data, PSP_SEG, extended)


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
    parser.add_argument("-t", metavar="LOGFILE", help="write every call of the program to DOS (INT 21h, 2Fh, 25h, 20h) "
                        "to LOGFILE: what it asked, the names, and what DOS answered; to see what a program does "
                        "when it fails")
    parser.add_argument("-s", metavar="PNGFILE", help="show the screen in this PNG file: written again whenever the "
                        "picture has changed, and at the end, also after a crash")
    parser.add_argument("-w", action="store_true", help="show the screen in a window; the keys typed in it go to "
                        "the program. Closing the window stops the program. F12 saves the state of the machine "
                        "beside the program, as PROGRAM.state")
    parser.add_argument("-r", metavar="STATE", help="go on from a saved state (PROGRAM.state from F12, or "
                        "PROGRAM.crash.state that a crash leaves) instead of starting a program; use the same "
                        "configuration as when it was saved")
    parser.add_argument("program", nargs="?", help="the .COM or .EXE file to run, named as DOS names it: "
                        "`ESETUP.EXE`, `C:\\SETUP\\ESETUP.EXE`, `setup/esetup.exe`; relative to the current directory "
                        "of the starting drive (the `cwd` of the configuration)")
    args = parser.parse_args()
    if bool(args.program) == bool(args.r):
        parser.error("name a program to run, or a state to go on from (-r), and not both")
    try:
        settings = config.load(args.c) if args.c else config.default(Path.cwd())
    except config.ConfigError as error:
        parser.error(str(error))  # prints the message with the usage and exits with status 2, before anything runs

    volumes = Volumes(settings)
    data = where = None
    if args.program:
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
    if where is not None and hasattr(where.filesystem, "host_path"):
        host_path = str(where.filesystem.host_path(where.parts))

    computer = machine.build(volumes)
    cpu = computer.cpu
    if args.r:
        try:
            snapshot.load(args.r, computer)
        except snapshot.SnapshotError as error:
            parser.error(str(error))
        host_path = computer.program or args.r
    else:
        computer.program = host_path
    quick_save = snapshot.QuickSave(computer)
    displays = []
    if args.s:
        displays.append(PngFile(args.s))
    if args.w:
        try:
            displays.append(TkWindow(computer.keyboard.press, computer.mouse.update, save=quick_save.ask))
        except WindowError as error:
            parser.error(f"cannot open a window: {error}")
    monitor = screen.Monitor(computer.card, displays) if displays else None
    if monitor:
        monitor.hook = quick_save
    if args.w:  # only a window has keys to give, so only with one can a program wait for a key
        computer.keyboard.waiter = lambda: monitor.wait_for_key(computer.keyboard)
    if not args.r:
        load(cpu, data, where.dos_path(), computer.state.extended)
    log = open(args.d, "w") if args.d else None
    if log:
        cpu.trace = make_trace(log)
    dos_log = call_log = None
    if args.t:
        try:
            dos_log = open(args.t, "w", buffering=1)  # a line at a time: a crash leaves the log whole
        except OSError as error:
            parser.error(f"cannot write {args.t}: {error.strerror or error}")
    if dos_log:
        call_log = calllog.install(cpu, dos_log)
    try:
        code = cpu.run(TICK_EVERY, monitor.tick) if monitor else cpu.run()
    except Exception as e:
        dump.write(cpu, host_path, e)  # leave something to debug with, then fail as usual
        snapshot.save_near(computer, host_path, ".crash.state", rewind=True)  # and a state to go on from, once fixed
        raise
    finally:
        if monitor:
            monitor.show()  # the last picture, whether the program ended or crashed
            monitor.close()
        if log:
            log.close()
        if dos_log:
            call_log.finish()
            dos_log.close()
    sys.exit(code)


main()
