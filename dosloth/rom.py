"""What the BIOS ROM of a real machine has that a program can find: handlers of interrupts that do no more than they must.

A program that takes over an interrupt asks for the old vector (INT 21h AH=35h) and calls it, or jumps to it, when it is done
(it hands the interrupt on). Where nothing is set, a real machine has a handler of the BIOS in its ROM; here it is one of
these, in the segment F000h: an IRET; and for the vectors of the interrupt controllers one that says the end of the
interrupt first and then does an IRET.
"""
# What INT 21h AH=35h gives for a vector that has no handler of ours: one in the ROM that does no more than a BIOS does,
# an IRET; for the vectors of the interrupt controllers it says the end of the interrupt first, and then does an IRET
IRET_AT = (0xF000, 0x0001)
MASTER_EOI_AT = (0xF000, 0x0010)
SLAVE_EOI_AT = (0xF000, 0x0020)
ROM_HANDLERS = {
    IRET_AT: b"\xCF",
    MASTER_EOI_AT: b"\x50\xB0\x20\xE6\x20\x58\xCF",  # push ax ; mov al, 20h ; out 20h, al ; pop ax ; iret
    SLAVE_EOI_AT: b"\x50\xB0\x20\xE6\xA0\xE6\x20\x58\xCF",  # the same to both controllers
}


def install_iret(cpu):
    """Put the handlers of ROM_HANDLERS in memory, where the BIOS ROM would be."""
    for (segment, offset), code in ROM_HANDLERS.items():
        cpu.mem[segment * 16 + offset:segment * 16 + offset + len(code)] = code


def default_handler(n):
    """The address (segment, offset) of the handler that the ROM has for interrupt `n`."""
    return MASTER_EOI_AT if 0x08 <= n <= 0x0F else SLAVE_EOI_AT if 0x70 <= n <= 0x77 else IRET_AT
