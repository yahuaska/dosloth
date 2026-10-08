class Exit(Exception):
    def __init__(self, code):
        self.code = code


class CPU:
    # 16-bit register indexes, in x86 encoding order
    AX, CX, DX, BX, SP, BP, SI, DI = range(8)
    ES, CS, SS, DS = range(4)

    def __init__(self):
        self.mem = bytearray(1024 * 1024)
        self.r = [0] * 8
        self.s = [0] * 4
        self.ip = 0
        self.flags = 0x0002
        self.int_handlers = {}  # int number -> fn(cpu)

    # --- memory ---
    def addr(self, seg, off):
        return (seg * 16 + off) & 0xFFFFF

    def rb(self, seg, off):
        return self.mem[self.addr(seg, off)]

    def rw(self, seg, off):
        return self.rb(seg, off) | self.rb(seg, off + 1) << 8

    # --- 8-bit register access: 0-3 = AL CL DL BL, 4-7 = AH CH DH BH ---
    def get8(self, n):
        v = self.r[n & 3]
        return (v >> 8) & 0xFF if n & 4 else v & 0xFF

    def set8(self, n, val):
        i = n & 3
        if n & 4:
            self.r[i] = (self.r[i] & 0x00FF) | (val & 0xFF) << 8
        else:
            self.r[i] = (self.r[i] & 0xFF00) | (val & 0xFF)

    # --- instruction fetch ---
    def fetch8(self):
        v = self.rb(self.s[self.CS], self.ip)
        self.ip = (self.ip + 1) & 0xFFFF
        return v

    def fetch16(self):
        lo = self.fetch8()
        return lo | self.fetch8() << 8

    def step(self):
        start_ip = self.ip
        op = self.fetch8()
        if op == 0x90:  # NOP
            pass
        elif 0xB0 <= op <= 0xB7:  # MOV r8, imm8
            self.set8(op - 0xB0, self.fetch8())
        elif 0xB8 <= op <= 0xBF:  # MOV r16, imm16
            self.r[op - 0xB8] = self.fetch16()
        elif op == 0xEB:  # JMP short
            d = self.fetch8()
            self.ip = (self.ip + d - (256 if d & 0x80 else 0)) & 0xFFFF
        elif op == 0xCD:  # INT imm8
            n = self.fetch8()
            h = self.int_handlers.get(n)
            if h is None:
                raise NotImplementedError(f"INT {n:02X} at {self.s[self.CS]:04X}:{start_ip:04X}")
            h(self)
        elif op == 0xF4:  # HLT
            raise Exit(0)
        else:
            raw = bytes(self.mem[self.addr(self.s[self.CS], start_ip):][:8]).hex(" ")
            raise NotImplementedError(
                f"opcode {op:02X} at {self.s[self.CS]:04X}:{start_ip:04X}: {raw}")

    def run(self):
        try:
            while True:
                self.step()
        except Exit as e:
            return e.code
