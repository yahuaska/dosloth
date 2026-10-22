# mydos

Python project: a from-scratch x86 real-mode emulator plus a minimal DOS, so we can run DOS programs.
Imagine DOS, DOSBox and similar do not exist. Stdlib only, no emulation libraries.

## Principles
- Naive is better than complex. Solve problems when they appear; don't plan far ahead.
- No speculative abstractions. Add a feature only when a program we want to run needs it.
- Unimplemented opcode or interrupt: raise loudly with CS:IP and the bytes. Never guess.
- Small steps: each step should make one more real program run.
- Add tests only when a bug bites.

## Layout
- `mydos/cpu.py`  - memory, registers, flags, instruction loop
- `mydos/dos.py`  - INT 21h and friends (Python-side handlers)
- `mydos/bios.py` - BIOS interrupts (INT 1Ah time of day, INT 16h keyboard, INT 10h video: modes 03h/12h, the
  colors AH=10h, the VGA check so far), also Python-side handlers
- `mydos/config.py` - the configuration (TOML): drives, what is mounted in them, where a program starts;
  `config.example.toml` shows the format. `python -m mydos -c FILE program` uses it; without `-c` the machine is
  built in: floppies A: B:, a CD-ROM D: with no disc, the current directory as the hard disk C:. A drive is a
  "floppy", "hdd" or "cdrom" (`type`; `removable` is the old way). What is mounted in a CD-ROM is read-only.
  INT 2Fh answers MSCDEX questions from the CD-ROM drives
- `mydos/filesystem.py` - the file systems of the drives: HostDir (a host directory, names matched without
  regard to case, only 8.3 names visible) and MemFs (in memory), with one interface and DOS error numbers
- `mydos/volumes.py` - the drives as a program uses them: a file system per drive, the current directory of each, and
  resolve(): a DOS path (`D:\X`, `..\Y`, `a/b`) -> file system + names. The program on the command line is named this way too
- `mydos/handles.py` - the table of 20 open handles (the JFT): 0-4 are the console, stderr, AUX and PRN, files
  take the lowest free slot
- `mydos/dump.py` - the crash dump (`<program>.crash.json` and `.crash.mem`): beside the program, or in the current
  directory when that cannot be written to (a program on a CD-ROM); a failing dump never hides the real error
- `mydos/timer.py` - the 8253/8254 timer on ports 40h-43h; its time is the count of executed instructions
- `mydos/vga.py` - a VGA card for the 16-color planar modes: the ports 3C0h-3DFh, and the window A0000h-AFFFFh as
  four planes with the write modes 0-3, read modes 0-1 and the latches; the colors: the DAC (3C6h-3C9h) and the
  palette registers of the attribute controller (3C0h). The CPU hands that window to the card (`cpu.video`), so
  the crash dump's `.mem` file has nothing in it for the screen
- `mydos/screen.py` - the planes -> a `Frame` of RGB pixels (mode 12h layout, 640x480, the colors of the card), and
  the `Monitor` that looks after all the displays: polls their windows, shows the picture when the card was drawn on
- `mydos/display.py` - the `Display` interface (`present(frame)`, `poll()`, `close()`) that every window or file that
  shows the picture goes through; `PngFile` is the first. `-s PNGFILE` uses it
- `mydos/window.py` - `TkWindow`, a Tk window behind that interface: shows the picture, passes the keys typed in it
  on to the BIOS keyboard. Tk is loaded only when `-w` asks for a window
- `mydos/mouse.py` - the mouse and its INT 33h driver (reset, show/hide, position and buttons, press and release
  counts, ranges, the program's subroutine remembered but not called); the window gives it the events
- `mydos/keys.py` - a key of Tk (`Return`, `a`, `F1`) -> the BIOS scan code and ASCII code, for a US keyboard
- `mydos/disasm.py` - bytes -> text like `mov ax, bx`, only used for the trace. Teach it every opcode the CPU learns
  (`tests/test_disasm.py` fails if it is out of step)
- `mydos/__main__.py` - loads a .COM/.EXE and runs it: `python -m mydos tests/hello.com`;
  `-d LOGFILE` writes every executed instruction to LOGFILE, e.g. `1773:0880  8B C3  mov ax, bx`;
  `-s PNGFILE` keeps the screen in a PNG file, written again when the picture changes and at the end;
  `-w` shows the screen in a window and takes the keys typed in it (closing the window stops the program)
- `tests/`        - tiny test programs (`make_hello.py` builds `hello.com`)

## Memory model
1 MB flat bytearray, address = seg*16 + off (no A20 wrap yet).
.COM loads at PSP:0100, CS=DS=ES=SS=PSP segment, SP=FFFE.
The PSP is at segment 1000 (psp.py fills in the fields programs read: top of memory at PSP:0002, the
environment segment at PSP:002C, the handle table, an empty command line); the environment is at 0FF0.
