# DOSloth

**The world's slowest incomplete DOS emulator.**

*The antipode of DOSEMU: the emu runs; the sloth hangs on its branch, and gets there eventually.*

<p align="center">
  <img src="docs/keep-calm-and-step.png" alt="Keep calm and step()" width="220">
</p>

A from-scratch x86 emulator and a minimal DOS, in pure Python, standard library only. No DOSBox, no QEMU, no
emulation libraries: just a CPU loop that does a few million instructions a second on a good day, and a lot of
stubbornness.

It exists to run real DOS programs from the end of the DOS era, one failure at a time. The first target is
**X-COM: Apocalypse** (the 1997 game, 32-bit, DOS/4G-style protected mode, Smacker video, Sound Blaster).

> **Status: work in progress.** Programs run until they ask for something that is not there yet, and then the
> emulator stops loudly and tells you what it was. See [What works](#what-works) and [What does not](#what-does-not).

## Why

Because it is fun to find out how little it takes. The rules, in short:

- **Naive is better than complex.** Solve a problem when a program hits it. No speculative abstractions.
- **Never guess.** An opcode, an interrupt or a port that is not implemented raises an error with `CS:IP` and the
  bytes. A wrong guess that silently "works" is worse than a stop.
- **Small steps.** Each step makes one more real program run.
- **Slow is fine.** Correct first. Where a loop is too hot, Python does its work in the code's place (see
  [Patches](#patches)), and the program does not notice.

## What works

- **CPU:** the 8086 up to the 386 instruction set that the programs use: real mode, and the flat 32-bit mode of a
  DOS/4G-style program (selectors with bases, 32-bit `EIP`/`ESP`, prefixes `66h`/`67h`, SIB addresses, the `0Fh`
  opcodes of the games), plus a subset of the x87 coprocessor in Python floats.
- **Loaders:** `.COM`, `.EXE` (MZ), and `LE` executables of the DOS/4G kind (objects, fixups, the start code), with
  a small DPMI (`INT 31h`) and memory above 1 MB.
- **DOS:** the `INT 21h` functions that the programs call (files, directories, memory blocks, handles, FCBs,
  environment, process), `INT 2Fh`, and a PSP like the real one. Drives are described in a TOML file: a host
  directory, a floppy in memory, or an ISO 9660 image (with its sectors, for programs that read them).
- **BIOS:** keyboard, time of day, video modes `03h`/`12h`/`13h`, a few VESA calls, equipment and memory size.
- **Hardware:** VGA (planes, latches, DAC, the window at `A000:0000`), the timer (8253), two interrupt controllers
  (8259), the DMA controller (8237), a Sound Blaster (the card answers and plays on time, but nothing comes out of
  the speakers: it is silent), a mouse, and the keyboard.
- **Tools:** a window (`-w`) or a PNG (`-s`) for the screen, an instruction log (`-d`), a log of DOS calls (`-t`),
  a profiler (`-p`), breakpoints (`-b`), a debugger window (`--debug`), crash dumps and snapshots of the whole
  machine to go on from (`-r`, and `F12` in the window).
- **Games, so far:** the setup programs of X-COM: Apocalypse run to the end (a Sound Blaster is found, the
  configuration is written), and its Smacker video player (`SMKP.EXE`) plays the video 2 of the disc (83 seconds) in about real time, in a
  window.

## What does not

- Most of the hardware: no audio output, no real EGA/CGA/Hercules corners, no network, no serial or parallel ports.
- Protected mode in general. Only what a flat 32-bit DOS/4G-style program needs is there; there is no GDT/IDT, no
  paging, no tasks.
- The rest of the game. `UFO2P.EXE`, `TACP.EXE` and the launcher have not been checked yet, and some `INT 21h`
  functions, ports and opcodes are still missing: the emulator stops on the first one it needs.
- Speed on a plain run. See below.

## Speed

The emulator counts time in executed instructions: **50 million instructions are one second** of the machine (the
32-bit programs of a 486 or a Pentium need the time to be that fast). Python does a few million of them a second, so
a plain run is a lot slower than the machine it pretends to be.

What makes a game playable anyway:

- **Patches.** A few hot loops of a program (the decoder of a video frame, the mixer of the sound, and the like)
  are done in Python in the place of the code, with the same registers, flags, memory and count of instructions,
  so the time of the program is the same. They are checked by a hash of the code, so another version of a program
  is left as it is.
- **Idle time.** A program that polls the keyboard in a tight loop lets the time go on to the next event of the
  devices instead of burning instructions.
- **Block moves.** `REP MOVS` and `REP STOS` are one copy when that gives the same result as the loop.

The video 2 of X-COM: Apocalypse (83 seconds) plays in about 75–83 seconds of work in a window.

## Requirements

- Python 3.12 or newer (3.14 is what it is developed on). Only the standard library is used (`tkinter` for the
  window, if you ask for one).
- Linux is what it is developed on.
- **The programs are not here.** Bring your own DOS programs, and, for the game, your own copy of it and its disc.

## Quick start

The project is DOSloth; the Python package is still called `mydos`.

```sh
# a program in the current directory, no configuration: the current directory is the hard disk C:
python -m mydos hello.com

# a configuration: drives and what is in them (see config.example.toml), the screen in a window
python -m mydos -c config.example.toml -w samples/some.exe

# arguments of the program go after --
python -m mydos -c config.toml -w ufoexe/smkp.exe -- 2 GOFORIT

# the screen in a PNG file that is written again whenever the picture changes (no display needed)
python -m mydos -s screen.png hello.com
```

`config.example.toml` explains the drives (`floppy`, `hdd`, `cdrom`) and what can be mounted in them (`dir`,
`tmpfs`, `iso`).

### Options

| Option | What it does |
|---|---|
| `-c FILE` | the configuration: drives, mounts, the starting directory |
| `-w` | show the screen in a window; the keys go to the program, `F12` saves the state of the machine |
| `-s PNG` | write the screen to a PNG file |
| `-d LOG` | write every executed instruction to a log |
| `-t LOG` | write every call of the program to DOS (`INT 21h`, `2Fh`, ...) with the answers |
| `-p REPORT` | count the instructions of every place and write the hottest ones at the end |
| `-b ADDRESS` | stop before the instruction at `ADDRESS` (`000F:0010E060`), and write a dump |
| `--debug` | a debugger window: step, step over, run to a breakpoint |
| `-r STATE` | go on from a saved state |
| `--no-patches` | run the code as it is, with no Python in its place |

When a program fails, the emulator writes `PROGRAM.crash.json` (registers, flags, the code and the stack at the
failure) and `PROGRAM.crash.mem` (the memory, sparse), and a state to go on from.

## Patches

A patch is a module of the package [`mydos/patches/`](mydos/patches): it replaces the first two bytes of a known
piece of code with `INT F0h`, and the handler of that interrupt does what the code would have done, in Python.
The code is found by a hash of its bytes and its exact address, so a patch never touches what it was not made for.
Pieces of code that arrive later (a driver that a program reads from a file) are found by their bytes.

The package knows none of its patches by name: when it is imported, every module in it whose name does not begin
with an underscore is imported, and its `PATCHES` and `LATE_PATCHES` are collected. To make a patch is to put a
module there. Every patch is tested against the code it replaces, on random data and on a saved machine.

## Tests

```sh
python -m unittest discover tests
```

The tests that need the game (`XCOMA/`) or its disc skip themselves when they are not there. Some tests run 32-bit
programs that were assembled with `nasm` and built by `gcc -m32` (the bytes are in the tests, so the tools are not
needed to run them) and check the result against what Python says it must be. The suite takes a minute or two, and under a memory limit it is happy with 1 GB (the memory of the machine is a
256 MB anonymous `mmap` that costs nothing until it is written).

## Layout

| Path | What is in it |
|---|---|
| `mydos/cpu.py`, `ops32.py`, `flat.py`, `modrm.py`, `fpu.py` | the CPU: real mode, the 386 part, the flat memory, the coprocessor |
| `mydos/dos.py`, `dos32.py`, `dpmi.py`, `loader.py`, `le.py` | DOS, its flat-mode form, DPMI, the loaders |
| `mydos/bios.py`, `vga.py`, `vesa.py`, `timer.py`, `pic.py`, `dma.py`, `soundblaster.py` | the BIOS and the hardware |
| `mydos/filesystem.py`, `iso.py`, `volumes.py`, `config.py` | drives, host directories, ISO 9660, the configuration |
| `mydos/patches/` | Python in the place of hot code |
| `mydos/debugger.py`, `debugwindow.py`, `profile.py`, `calllog.py`, `snapshot.py`, `dump.py` | the tools |
| `tests/` | the tests |
| `AGENTS.md` | the detailed map of the code and the rules of the project |

## License

Not chosen yet.
