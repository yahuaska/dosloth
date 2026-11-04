"""Bit masks of the FLAGS register (bit 1 is always 1 on x86)."""

CF = 0x0001  # carry: unsigned overflow / borrow
PF = 0x0004  # parity: low byte of the result has an even number of 1 bits
AF = 0x0010  # auxiliary carry: carry out of bit 3 (for BCD arithmetic)
ZF = 0x0040  # zero: the result is 0
SF = 0x0080  # sign: the top bit of the result
TF = 0x0100  # trap (single-step)
IF = 0x0200  # interrupt enable
DF = 0x0400  # direction for string instructions: 1 = go downwards
OF = 0x0800  # overflow: signed overflow
IOPL = 0x3000  # the privilege level of I/O, two bits: no use in real mode, but a 386 keeps what is written (a 286 not)
NT = 0x4000  # nested task: the same

ALWAYS_ONE = 0x0002  # bit 1 of the flags word always reads as 1
# the flags that a word taken off the stack into the flags register changes: the nine of the 8086, and IOPL and NT,
# which a 386 keeps in real mode as well (and the programs that tell a 386 from a 286 look for just that). Bit 15 is
# never set in real mode, and the bits of EFLAGS above the 16th are kept apart, see CPU.eflags_high.
DEFINED = CF | PF | AF | ZF | SF | TF | IF | DF | OF | IOPL | NT
LOW = CF | PF | AF | ZF | SF  # the five flags that LAHF and SAHF carry to and from AH
