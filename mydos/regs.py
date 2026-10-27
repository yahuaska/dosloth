"""Register numbers, shared by cpu.py and modrm.py (which cpu.py imports)."""

# 16-bit general registers, in x86 encoding order
AX, CX, DX, BX, SP, BP, SI, DI = range(8)
# segment registers, in x86 encoding order (FS and GS are the 386's)
ES, CS, SS, DS, FS, GS = range(6)
