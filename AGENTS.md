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
- `mydos/__main__.py` - loads a .COM and runs it: `python -m mydos tests/hello.com`
- `tests/`        - tiny test programs (`make_hello.py` builds `hello.com`)

## Memory model
1 MB flat bytearray, address = seg*16 + off (no A20 wrap yet).
.COM loads at PSP:0100, CS=DS=ES=SS=PSP segment, SP=FFFE.
