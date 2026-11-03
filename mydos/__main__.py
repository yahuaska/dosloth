import argparse
import sys
from pathlib import Path

from . import breakpoints, calllog, config, disasm, dos, dump, loader, machine, patches, psp, screen, snapshot
from .cpu import CPU
from .debugger import Debugger
from .debugwindow import DebugWindow
from .display import PngFile
from .profile import Profile
from .window import TkWindow, WindowError
from .filesystem import FsError
from .psp import ENV_SEG, PSP_SEG
from .volumes import Volumes

TICK_EVERY = 20_000  # the displays are looked after this often, in instructions: some 20 times a second
BREAKPOINT_EXIT = 3  # the code that the emulator ends with when a program is stopped at a breakpoint (2 is for usage)


def load(cpu, data, dos_path, extended, tail=b""):
    """Put the program on the command line in memory at the PSP segment, with its PSP and environment.

    `data` is the program file and `dos_path` its full DOS name (`C:\\SETUP\\ESETUP.EXE`), which goes to the program
    in its environment; `tail` is its command line (see psp.tail_of).
    """
    psp.build(cpu, PSP_SEG, ENV_SEG, dos.MEMORY_TOP_SEG, dos_path, tail=tail)
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
    parser = argparse.ArgumentParser(
        prog="python -m mydos", description="Run a DOS .COM or .EXE program.",
        epilog="The options and the name of the program come first, in any order. After `--` is the command line of "
               "the program, as it would be typed after its name in DOS, whatever the words are: "
               "python -m mydos -w smkp.exe -d trace.log -- 0 GOFORIT -w")
    parser.add_argument("-c", metavar="FILE", help="the configuration (TOML): drives, mounts, starting directory; "
                        "without it the current directory is the hard disk C:")
    parser.add_argument("-d", metavar="LOGFILE", help="write every executed instruction to LOGFILE")
    parser.add_argument("-b", metavar="ADDRESS", action="append", default=[], help="stop before the instruction at "
                        "ADDRESS, as `000F:0010E060` (a selector or segment, and an offset, in hex; the addresses of "
                        "the logs) or just the offset (at any selector), and write a dump as a crash does, with a "
                        "state to go on from (-r); more than one -b are all breakpoints")
    parser.add_argument("--debug", action="store_true", help="open the window of the debugger: the program waits there "
                        "before its first instruction, and goes on by a step (F7), over a call (F8) or up to a "
                        "breakpoint (F9); the breakpoints of -b are in it")
    parser.add_argument("-p", metavar="REPORT", help="count how many times every instruction is executed, and write "
                        "the hottest places of the program to REPORT when it ends (about a third slower)")
    parser.add_argument("--no-patches", action="store_true", help="run the code of the program as it is, with no "
                        "Python in its place (see patches.py): to trace it, or to compare with a patched run")
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
    argv = sys.argv[1:]
    cut = argv.index("--") if "--" in argv else len(argv)  # the options end at the first `--`
    args = parser.parse_args(argv[:cut])
    if bool(args.program) == bool(args.r):
        parser.error("name a program to run, or a state to go on from (-r), and not both")
    if args.r and cut < len(argv) - 1:
        parser.error("a state that is gone on from (-r) has its program started already: no arguments for it")
    try:
        stops = {text: breakpoints.parse(text) for text in args.b}
    except ValueError as error:
        parser.error(str(error))
    try:
        tail = psp.tail_of(argv[cut + 1:])  # what follows it is the command line of the program, as it is
    except ValueError as error:
        parser.error(str(error))
    try:
        settings = config.load(args.c) if args.c else config.default(Path.cwd())
    except config.ConfigError as error:
        parser.error(str(error))  # prints the message with the usage and exits with status 2, before anything runs

    if args.no_patches:
        patches.PATCHES.clear()
        patches.LATE_PATCHES.clear()
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
        load(cpu, data, where.dos_path(), computer.state.extended, tail)
    for text in args.b:
        cpu.add_breakpoint(*stops[text])
    debug_window = None
    if args.debug:
        try:
            debug_window = DebugWindow(Debugger(cpu))
        except WindowError as error:
            parser.error(f"cannot open a window: {error}")
        monitor = monitor or screen.Monitor(computer.card, [])
        monitor.displays.append(debug_window)
        debug_window.tick_others = monitor.tick
    log = open(args.d, "w") if args.d else None
    if log:
        cpu.trace = make_trace(log)
    profile = None
    if args.p:
        try:
            report = open(args.p, "w")
        except OSError as error:
            parser.error(f"cannot write {args.p}: {error.strerror or error}")
        profile = Profile()
        cpu.trace = profile.watching(cpu.trace)  # after the log of -d: the count goes first, the line of the log next
    dos_log = call_log = None
    if args.t:
        try:
            dos_log = open(args.t, "w", buffering=1)  # a line at a time: a crash leaves the log whole
        except OSError as error:
            parser.error(f"cannot write {args.t}: {error.strerror or error}")
    if dos_log:
        call_log = calllog.install(cpu, dos_log)
    try:
        if debug_window:
            code = debug_window.run()
        else:
            code = cpu.run(TICK_EVERY, monitor.tick) if monitor else cpu.run()
    except breakpoints.BreakpointHit as stop:
        dump.write(cpu, host_path, stop)  # as for a crash: the dump, and a state to go on from, where it stands
        snapshot.save_near(computer, host_path, ".crash.state")
        print(stop, file=sys.stderr)
        code = BREAKPOINT_EXIT
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
        if profile:
            report.write(profile.report(cpu))
            report.close()
        if dos_log:
            call_log.finish()
            dos_log.close()
    sys.exit(code)


main()
