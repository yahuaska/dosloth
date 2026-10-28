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
- `mydos/ops32.py` - the 386 part of the CPU (a mixin): the prefix 66h, the high halves of the 32-bit registers
  (`cpu.rh`), the table `cpu.ops32` of the 32-bit forms (shifts, MUL/DIV, stack, near calls, string instructions...),
  the prefix 67h and the 32-bit addresses with SIB (in `modrm.py`); `cpu.code32` flips the sizes for a 32-bit code
  segment. The `0F` opcodes that the games use are in `cpu.ops0f` (MOVZX/MOVSX, SETcc, long Jcc, SHLD/SHRD, BT*...).
  FS and GS (`cpu.s` has six segments, prefixes 64h/65h). `cpu.ip_mask` and `cpu.stack32` say how wide IP and the
  stack pointer are (16 bits both, until the flat mode). Not done yet: the flat mode, x87
- `mydos/flat.py` - the flat memory of a 32-bit program: a segment register holds a selector, `cpu.bases` says its base.
  `cpu.enter_flat(bases)` makes the machine run a 32-bit program (the methods of this module in place of those of
  real mode, 32-bit EIP and ESP, 32-bit code), `leave_flat()` ends it; `use_flat_memory`/`use_real_memory` do the memory only; `get_mode`/`set_mode` keep the mode in snapshots and for a
  child. `tests/test_flat.py` runs the tests of the 386 instructions on it too (a selector is a paragraph there);
  `tests/test_flat32.py` runs 32-bit programs (nasm) and `tests/test_flat32_compiled.py` functions built by gcc -m32
  (four builds) above the first megabyte, against Python as the oracle
- `mydos/pages.py` - the memory is a 256 MB private anonymous `mmap` (costs nothing until written); this finds the
  parts that are not zero, for dumps and snapshots. Never copy or iterate all of `cpu.mem`: slice what you need
- `mydos/dos.py`  - INT 21h and friends (Python-side handlers)
- `mydos/bios.py` - BIOS interrupts (INT 1Ah time of day, INT 16h keyboard, INT 10h video: modes 03h/12h, the
  colors AH=10h, the VGA check; INT 11h equipment, INT 12h memory size), also Python-side handlers
- `mydos/config.py` - the configuration (TOML): drives, what is mounted in them, where a program starts;
  `config.example.toml` shows the format. `python -m mydos -c FILE program` uses it; without `-c` the machine is
  built in: floppies A: B:, a CD-ROM D: with no disc, the current directory as the hard disk C:. A drive is a
  "floppy", "hdd" or "cdrom" (`type`; `removable` is the old way). What is mounted in a CD-ROM is read-only.
  INT 2Fh answers MSCDEX questions from the CD-ROM drives
- `mydos/filesystem.py` - the file systems of the drives: HostDir (a host directory, names matched without
  regard to case, only 8.3 names visible) and MemFs (in memory), with one interface and DOS error numbers
- `mydos/iso.py` - a disc image (`.iso`, ISO 9660, 2048-byte sectors) as a CD-ROM: IsoFs gives its files like a host
  directory, IsoImage its raw sectors (MSCDEX INT 2Fh AX=1508h); config `D = { type = "iso", path = "cd.iso" }`
- `mydos/volumes.py` - the drives as a program uses them: a file system per drive, the current directory of each, and
  resolve(): a DOS path (`D:\X`, `..\Y`, `a/b`) -> file system + names. The program on the command line is named this way too
- `mydos/search.py` - the file search of INT 21h AH=4Eh/4Fh: DOS wildcard masks, the attributes a program asks for,
  the 43-byte record in the DTA, and the list of searches that are going on
- `mydos/memory.py` - the memory blocks of INT 21h AH=48h/49h/4Ah: a list of blocks and the first gap that fits
- `mydos/machine.py` - the CPU with BIOS, DOS, timer, video card and mouse on it (`build`), built the same way for a run
  and for a run that goes on from a state
- `mydos/snapshot.py` - the state of the machine saved to a zip file and put back: F12 in the window, a crash, and
  `-r STATE`; drives must match, ROM data is written again from the code
- `mydos/le.py` - programs in the LE format (the 32-bit programs of the DOS/4G games): the header, the objects, the
  pages, the fixups, an image of the objects for given bases; and where the LE program is in a file bound to the
  DOS/4G extender. The loader stops them with a message until the 32-bit mode is there
- `mydos/loader.py` - a .COM or .EXE put in memory at a PSP segment (the first program and the children of EXEC),
  and how much memory it asks for
- `mydos/process.py` - INT 21h AH=4Bh (EXEC): the parent put aside, the child loaded; the end of a program
  (AH=4Ch, INT 20h) gives the machine back to the parent
- `mydos/country.py` - the country of INT 21h AH=38h: the United States, with the table of formats
- `mydos/calllog.py` - `-t LOGFILE`: every call of the program to DOS (INT 21h, 2Fh, 25h, 20h) with its names and
  the answer; a run of the same call is folded into one; the last line of the log is the call that was being made
  when something went wrong
- `mydos/command.py` - a pocket COMMAND.COM: when a program starts one that is not on the disk, the command is done in
  Python: `/c md C:\X`, or a program (`/c SETUP.exe child>nul`: found like DOS does, started as a child, `>nul` kept);
  the rest stops loudly
- `mydos/sysport.py` - port 61h of the system board: the speaker bits a program sets (nothing is heard), the refresh bit
  that toggles every 18 timer ticks (programs wait on it), the output of timer counter 2
- `mydos/fcb.py` - a file name taken apart into the drive, name and extension of an FCB (INT 21h AH=29h)
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
  `-t LOGFILE` writes the calls of the program to DOS (see calllog.py);
  `-w` shows the screen in a window and takes the keys typed in it (closing the window stops the program; F12 saves
  the state as PROGRAM.state); `-r STATE` goes on from a saved state (a crash leaves PROGRAM.crash.state)
- `tests/`        - tiny test programs (`make_hello.py` builds `hello.com`)

## Memory model
1 MB flat bytearray, address = seg*16 + off (no A20 wrap yet).
.COM loads at PSP:0100, CS=DS=ES=SS=PSP segment, SP=FFFE.
The PSP is at segment 1000 (psp.py fills in the fields programs read: top of memory at PSP:0002, the
environment segment at PSP:002C, the handle table, an empty command line); the environment is at 0FF0.
