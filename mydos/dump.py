"""Crash dump: registers as JSON plus the whole memory as a raw binary file."""
import json
import sys

from .cpu import CPU

REG16 = ["AX", "CX", "DX", "BX", "SP", "BP", "SI", "DI"]  # order of CPU.r
SEGS = ["ES", "CS", "SS", "DS"]                           # order of CPU.s


def write(cpu, prog_path, exc):
    """Write <prog>.crash.json and <prog>.crash.mem after the program died with exc.

    The .mem file is the flat 1 MB memory, so the file offset of a byte equals its
    physical address (seg*16 + off). The JSON has the registers and, for a quick look
    without opening the .mem file, the code at the failed instruction and the stack top.
    """
    cs, ss = cpu.s[CPU.CS], cpu.s[CPU.SS]
    sp = cpu.r[CPU.SP]
    info = {
        "program": prog_path,
        "error": f"{type(exc).__name__}: {exc}",
        "registers": {name: f"{cpu.r[i]:04X}" for i, name in enumerate(REG16)},
        "segments": {name: f"{cpu.s[i]:04X}" for i, name in enumerate(SEGS)},
        "ip": f"{cpu.ip:04X}",
        "start_ip": f"{cpu.start_ip:04X}",  # where the failed instruction began
        "flags": f"{cpu.flags:04X}",
        # 16 bytes of code at CS:start_ip, as hex
        "code": bytes(cpu.rb(cs, cpu.start_ip + i) for i in range(16)).hex(" "),
        # 8 words on top of the stack at SS:SP
        "stack": [f"{cpu.rw(ss, sp + 2 * i):04X}" for i in range(8)],
        "memory_file": prog_path + ".crash.mem",
    }
    with open(prog_path + ".crash.json", "w") as f:
        json.dump(info, f, indent=2)
    with open(prog_path + ".crash.mem", "wb") as f:
        f.write(cpu.mem)
    print(f"crash dump: {prog_path}.crash.json, {prog_path}.crash.mem", file=sys.stderr)
