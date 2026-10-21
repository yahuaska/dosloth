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
- `mydos/bios.py` - BIOS interrupts (INT 1Ah time of day so far), also Python-side handlers
- `mydos/config.py` - the configuration (TOML): drives, what is mounted in them, where a program starts;
  `config.example.toml` shows the format. `python -m mydos -c FILE program` uses it; without `-c` the machine is
  built in: A: B: D: empty removable drives, the current directory as the hard disk C:. So far the drives are
  used for drive numbers and types; the mounted media will matter when the file functions arrive
- `mydos/filesystem.py` - the file systems of the drives: HostDir (a host directory, names matched without
  regard to case, only 8.3 names visible) and MemFs (in memory), with one interface and DOS error numbers
- `mydos/volumes.py` - the drives as a program uses them: a file system per drive, the current directory of each, and
  resolve(): a DOS path (`D:\X`, `..\Y`, `a/b`) -> file system + names. The program on the command line is named this way too
- `mydos/handles.py` - the table of 20 open handles (the JFT): 0-4 are the console, stderr, AUX and PRN, files
  take the lowest free slot
- `mydos/timer.py` - the 8253/8254 timer on ports 40h-43h; its time is the count of executed instructions
- `mydos/vga.py` - a VGA card that does nothing: the ports 3C0h-3DFh answer and the registers read back,
  nothing is drawn; the video memory is plain memory
- `mydos/disasm.py` - bytes -> text like `mov ax, bx`, only used for the trace. Teach it every opcode the CPU learns
  (`tests/test_disasm.py` fails if it is out of step)
- `mydos/__main__.py` - loads a .COM/.EXE and runs it: `python -m mydos tests/hello.com`;
  `-d LOGFILE` writes every executed instruction to LOGFILE, e.g. `1773:0880  8B C3  mov ax, bx`
- `tests/`        - tiny test programs (`make_hello.py` builds `hello.com`)

## Memory model
1 MB flat bytearray, address = seg*16 + off (no A20 wrap yet).
.COM loads at PSP:0100, CS=DS=ES=SS=PSP segment, SP=FFFE.
The PSP is at segment 1000 (psp.py fills in the fields programs read: top of memory at PSP:0002, the
environment segment at PSP:002C, the handle table, an empty command line); the environment is at 0FF0.
