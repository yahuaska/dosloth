"""Crash dump: registers as JSON plus the whole memory as a raw binary file."""
import json
import os
import re
import sys

from .cpu import CPU

REG16 = ["AX", "CX", "DX", "BX", "SP", "BP", "SI", "DI"]  # order of CPU.r
SEGS = ["ES", "CS", "SS", "DS"]                           # order of CPU.s


def describe(cpu, prog_path, exc):
    """What a dump says in JSON: the error, the registers, the code at the failed instruction and the stack top."""
    cs, ss = cpu.s[CPU.CS], cpu.s[CPU.SS]
    sp = cpu.r[CPU.SP]
    return {
        "program": prog_path,
        "error": f"{type(exc).__name__}: {exc}",
        "registers": {name: f"{cpu.r[i]:04X}" for i, name in enumerate(REG16)},
        # the high halves of the 32-bit registers, said only when a program has put something in them
        **({"high_halves": {f"E{name}": f"{cpu.rh[i]:04X}" for i, name in enumerate(REG16)}} if any(cpu.rh) else {}),
        "segments": {name: f"{cpu.s[i]:04X}" for i, name in enumerate(SEGS)},
        "ip": f"{cpu.ip:04X}",
        "start_ip": f"{cpu.start_ip:04X}",  # where the failed instruction began
        "flags": f"{cpu.flags:04X}",
        # 16 bytes of code at CS:start_ip, as hex
        "code": bytes(cpu.rb(cs, cpu.start_ip + i) for i in range(16)).hex(" "),
        # 8 words on top of the stack at SS:SP
        "stack": [f"{cpu.rw(ss, sp + 2 * i):04X}" for i in range(8)],
    }


def remove(base):
    """Take away what a dump that was not finished left behind."""
    for suffix in (".crash.json", ".crash.mem"):
        try:
            os.remove(base + suffix)
        except OSError:
            pass


def write(cpu, prog_path, exc):
    """Write <prog>.crash.json and <prog>.crash.mem after the program died with exc. Returns the path they share.

    The .mem file is the flat 1 MB memory, so the file offset of a byte equals its physical address (seg*16 + off).
    The JSON has the registers and, for a quick look without opening the .mem file, the code at the failed
    instruction and the stack top.

    They go beside the program. If that cannot be written to (a program on a CD-ROM, say), they go to the current
    directory of the emulator, named after the program, and the message says so. If that cannot be written to
    either, there is no dump, and a message says why: a failing dump must never hide the error that came before it.
    """
    info = describe(cpu, prog_path, exc)
    name = re.split(r"[\\/:]", prog_path)[-1]  # the file name of a path of the host or of DOS
    elsewhere = os.path.join(os.getcwd(), name)
    problems = []
    for base in dict.fromkeys((prog_path, elsewhere)):  # beside the program, then here: each once
        try:
            info["memory_file"] = base + ".crash.mem"
            with open(base + ".crash.json", "w") as f:
                json.dump(info, f, indent=2)
            with open(base + ".crash.mem", "wb") as f:
                f.write(cpu.mem)
        except OSError as error:
            remove(base)
            problems.append(f"{base}: {error.strerror or error}")
            continue
        note = " (the directory of the program cannot be written to)" if problems else ""
        print(f"crash dump: {base}.crash.json, {base}.crash.mem{note}", file=sys.stderr)
        return base
    print("no crash dump could be written: " + "; ".join(problems), file=sys.stderr)
    return None
