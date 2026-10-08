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
