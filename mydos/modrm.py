"""Decoder for the x86 ModRM byte (16-bit addressing).

Many opcodes (MOV, ADD, CMP, ...) are followed by a ModRM byte that says what their
operands are:

      7 6   5 4 3   2 1 0
     +-----+-------+-------+
     | mod |  reg  |  r/m  |
     +-----+-------+-------+

  reg  - a register operand (or, for some opcodes, an opcode extension).
         Whether it means AL/CL/... or AX/CX/... depends on the opcode, not on ModRM.
  mod, r/m - the other operand: either a register (mod = 11) or a memory address.

For a memory operand, mod says how many displacement bytes follow the ModRM byte,
and r/m picks the registers that are added up into the offset.
"""
from dataclasses import dataclass

from . import regs

# Base/index registers summed into the offset, by r/m value (for mod = 00, 01, 10).
# r/m = 6 is special: BP, except with mod = 00 where it is a bare 16-bit address.
_BASES = [
    (regs.BX, regs.SI),  # 0: [bx+si]
    (regs.BX, regs.DI),  # 1: [bx+di]
    (regs.BP, regs.SI),  # 2: [bp+si]
    (regs.BP, regs.DI),  # 3: [bp+di]
    (regs.SI,),          # 4: [si]
    (regs.DI,),          # 5: [di]
    (regs.BP,),          # 6: [bp]  (or [disp16] when mod = 00)
    (regs.BX,),          # 7: [bx]
]


@dataclass
class ModRM:
    """A decoded ModRM byte (plus any displacement bytes that followed it)."""
    reg: int        # the `reg` field: a register number, or an opcode extension
    is_reg: bool    # True: the r/m operand is a register; False: it is memory
    n: int = 0      # register number of the r/m operand (when is_reg)
    seg: int = 0    # segment *value* of the memory operand (when not is_reg)
    off: int = 0    # offset of the memory operand (when not is_reg)

    # Read/write the r/m operand, whichever kind it is. 8-bit register numbers
    # follow CPU.get8/set8 (0-3 = AL..BL, 4-7 = AH..BH); 16-bit ones index CPU.r.
    def read8(self, cpu):
        return cpu.get8(self.n) if self.is_reg else cpu.rb(self.seg, self.off)

    def read16(self, cpu):
        return cpu.r[self.n] if self.is_reg else cpu.rw(self.seg, self.off)

    def write8(self, cpu, val):
        if self.is_reg:
            cpu.set8(self.n, val)
        else:
            cpu.wb(self.seg, self.off, val)

    def write16(self, cpu, val):
        if self.is_reg:
            cpu.r[self.n] = val & 0xFFFF
        else:
            cpu.ww(self.seg, self.off, val)

    def rw_pair(self, cpu):
        """Read the two words that a far pointer in memory is made of: (offset, segment). Memory only."""
        return cpu.rw(self.seg, self.off), cpu.rw(self.seg, (self.off + 2) & 0xFFFF)

    # The same, with the width (8 or 16) as an argument, for code that handles both sizes alike.
    def read(self, cpu, bits):
        return self.read16(cpu) if bits == 16 else self.read8(cpu)

    def write(self, cpu, bits, val):
        if bits == 16:
            self.write16(cpu, val)
        else:
            self.write8(cpu, val)


def decode(cpu, seg_override=None):
    """Fetch a ModRM byte (and its displacement) at CS:IP and return a ModRM.

    seg_override is a segment register index (regs.ES/CS/SS/DS) from a prefix such as
    2E, or None to use the default segment.
    """
    b = cpu.fetch8()
    mod, reg, rm = b >> 6, (b >> 3) & 7, b & 7

    if mod == 3:  # r/m is a register, no memory involved
        return ModRM(reg=reg, is_reg=True, n=rm)

    if mod == 0 and rm == 6:  # special case: direct address [disp16]
        off = cpu.fetch16()
        default_seg = regs.DS
    else:
        off = sum(cpu.r[i] for i in _BASES[rm])
        if mod == 1:  # 8-bit displacement, sign-extended
            d = cpu.fetch8()
            off += d - 256 if d & 0x80 else d
        elif mod == 2:  # 16-bit displacement
            off += cpu.fetch16()
        # Addresses based on BP default to the stack segment, all others to DS.
        default_seg = regs.SS if regs.BP in _BASES[rm] else regs.DS

    seg = cpu.s[default_seg if seg_override is None else seg_override]
    return ModRM(reg=reg, is_reg=False, seg=seg, off=off & 0xFFFF)
