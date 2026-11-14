"""The procedures of the strings of the C library in UFO2P.EXE (the game): strcmp, strupr (and toupper, which it
calls), strncpy, strnicmp and strcat.

They are not hot (some calls each in a run), but they are what the C library says they are, and Python says it shorter
than the trace of the code does. Each handler goes the way the code goes, to give the same registers, the same flags
(those of the last instruction that sets them, as it is on the way the code takes) and the same count of the
instructions. The arguments are those of Watcom: EAX, EDX, EBX; the result is in EAX. What the code pushes and pops is
not written below the stack pointer (nothing reads it there)."""

from .. import flags
from ..cpu import CPU
from ._base import Patch
from ._common import ARITHMETIC, DWORD, MASK, give_back

ZERO_FLAGS = flags.ZF | flags.PF  # what a comparison of a byte with itself, or a TEST of 0, leaves set


def data(cpu):
    return cpu.mem, cpu.bases[cpu.s[CPU.DS]]


def af_of_inc(value):
    """The auxiliary carry of `inc` of `value`: a carry out of the low four bits."""
    return flags.AF if value & 0xF == 0xF else 0


def af_of_dec(value):
    return flags.AF if value & 0xF == 0 else 0


def zero_test(cpu, af=None):
    """The flags of `test` (or `cmp ..., 0`) of a 0, with AF as `af` (None: as it was, for TEST leaves it alone)."""
    kept = cpu.flags & flags.AF if af is None else af
    cpu.flags = (cpu.flags & ~ARITHMETIC) | ZERO_FLAGS | kept


def end(cpu, executed):
    """The procedure returns: the instructions it ran (with the RET) are counted, the INT stands for the first."""
    cpu.instructions += executed - 1
    give_back(cpu)


# --- strcmp (1B0080): a dword at a time, while the dwords are the same and have no zero byte, 16 bytes in a round

STRCMP_AT = 0x1B0080


def has_zero_byte(word):
    return (word + 0xFEFEFEFF) & ~word & 0x80808080


def strcmp(cpu):
    """Compares the strings at EAX and EDX as unsigned bytes: EAX is 0, -1 or 1. EDX goes on by 16 for every round of
    four dwords that the loop goes through (it is not given back)."""
    mem, base = data(cpu)
    first, second = cpu.wide(CPU.AX), cpu.wide(CPU.DX)
    executed = 5  # push, push, mov, cmp, je
    if first == second:
        return equal_strings(cpu, executed)
    while True:
        for at in range(0, 16, 4):
            a = DWORD.unpack_from(mem, base + (first + at & MASK))[0]
            b = DWORD.unpack_from(mem, base + (second + at & MASK))[0]
            executed += 4  # mov, mov, cmp, jne
            if a != b:
                cpu.set32(CPU.DX, second)
                return different_dwords(cpu, executed, a, b)
            executed += 5 if at < 12 else 7  # not, add, and, and, j.. (and the two adds of the pointers)
            if at == 12:
                first, second = first + 16 & MASK, second + 16 & MASK
            if has_zero_byte(a):
                cpu.set32(CPU.DX, second)
                return equal_strings(cpu, executed)


def equal_strings(cpu, executed):
    cpu.sub_with_flags(0, 0, 32)  # sub eax, eax
    cpu.set32(CPU.AX, 0)
    end(cpu, executed + 4)  # sub, pop, pop, ret


def different_dwords(cpu, executed, a, b):
    """The bytes of the two dwords that differ, from the lowest: an equal zero byte before the difference is the end
    of both strings."""
    for number in range(4):
        x, y = a >> 8 * number & 0xFF, b >> 8 * number & 0xFF
        if number == 2:
            executed += 2  # shr, shr
        if number == 3:
            executed += 1  # cmp ah, ch (and they differ: the sbb is next)
            break
        executed += 2  # cmp, jne
        if x != y:
            break
        executed += 2  # cmp ..., 0; je
        if x == 0:
            return equal_strings(cpu, executed)
    borrow = 1 if x < y else 0
    result = cpu.sub_with_flags(0, 0, 32, borrow) | 1  # sbb eax, eax; or al, 1 (which leaves AF of the sbb)
    cpu.set_logic_flags(result & 0xFF, 8)
    cpu.set32(CPU.AX, result)
    end(cpu, executed + 5)  # sbb, or, pop, pop, ret


# --- toupper (120080) and strupr (11FFC0), which calls it for every byte

TOUPPER_AT, STRUPR_AT = 0x120080, 0x11FFC0
# what 120080 calls for 0 and above (a to z made A to Z)
ASCII_TOUPPER = (0x1B1549, 0x0E, "b4d460be978b80eba17160f338372e375c48b5d328c230be2f6ad16f320b5e40")
# The letters of code page 437 (81h to A4h) that the table of 120080 makes capitals: some are those of 437, the others
# the plain letter. The rest of 80h-FFh stay as they are.
CAPITALS = {0x81: 0x9A, 0x82: 0x45, 0x83: 0x41, 0x84: 0x8E, 0x85: 0x41, 0x86: 0x8F, 0x87: 0x80, 0x88: 0x45,
            0x89: 0x45, 0x8A: 0x45, 0x8B: 0x49, 0x8C: 0x49, 0x8D: 0x49, 0x93: 0x4F, 0x94: 0x99, 0x95: 0x4F,
            0x96: 0x55, 0x97: 0x55, 0x98: 0x59, 0xA0: 0x41, 0xA1: 0x49, 0xA2: 0x4F, 0xA3: 0x55, 0xA4: 0xA5}


def signed(value):
    return value - (1 << 32) if value & 0x80000000 else value


def upper(cpu, value):
    """What 120080 does to the int `value`, with the flags it leaves: returns the result and the instructions, from
    `push edx` to `ret`."""
    if not value & 0x80000000:  # push edx; test; jl; call 1B1549 ... pop edx; ret
        if signed(value) < 0x61:
            cpu.sub_with_flags(value, 0x61, 32)
            return value, 4 + 3 + 2
        if signed(value) > 0x7A:
            cpu.sub_with_flags(value, 0x7A, 32)
            return value, 4 + 5 + 2
        return cpu.sub_with_flags(value, 0x20, 32), 4 + 6 + 2
    index = cpu.sub_with_flags(value + 0x100 & MASK, 0x81, 32)  # lea edx, [eax+100h]; sub edx, 81h
    cpu.sub_with_flags(index, 0x23, 32)  # cmp edx, 23h; ja
    if index > 0x23:
        return value, 3 + 4 + 2  # push, test, jl; lea, sub, cmp, ja; pop edx, ret
    capital = CAPITALS.get(0x81 + index)
    if capital is None:
        return value, 3 + 4 + 1 + 2  # ...; jmp [table] to the `pop edx` of the end
    return capital, 3 + 4 + 1 + 3  # ...; jmp [table]; mov eax, the capital; pop edx; ret


def toupper(cpu):
    result, executed = upper(cpu, cpu.wide(CPU.AX))
    cpu.set32(CPU.AX, result)
    end(cpu, executed)


def strupr(cpu):
    """Makes the string at EAX capitals, byte by byte (as a signed char) by toupper, and gives back EAX."""
    mem, base = data(cpu)
    start = at = cpu.wide(CPU.AX)
    executed = 7  # push, push, push, mov, mov, cmp, je
    if mem[base + at] == 0:
        cpu.sub_with_flags(0, 0, 8)
        return end(cpu, executed + 5)  # mov, pop, pop, pop, ret
    while True:
        byte = mem[base + at]
        value, steps = upper(cpu, byte | 0xFFFFFF00 if byte & 0x80 else byte)  # movsx
        mem[base + at] = value & 0xFF
        executed += 2 + steps + 5  # movsx, call; mov [edx], al; mov cl, [edx+1]; inc edx; test; jne
        at = at + 1 & MASK
        if mem[base + at] == 0:
            break
    cpu.flags = cpu.flags & ~flags.AF | af_of_inc(at - 1 & MASK)  # inc edx
    zero_test(cpu)  # test cl, cl
    cpu.set32(CPU.AX, start)
    end(cpu, executed + 5)


# --- strncpy (1B0121): the bytes up to the zero, but not more than EBX; the rest of the EBX are zeros

STRNCPY_AT = 0x1B0121


def strncpy(cpu):
    """Copies the string at EDX to EAX, but not more than EBX bytes; when it ends before them, the rest of the EBX
    bytes are zeros. Gives back EAX; EDX is left after the bytes that were copied, EBX at 0."""
    mem, base = data(cpu)
    target, source, count = cpu.wide(CPU.AX), cpu.wide(CPU.DX), cpu.wide(CPU.BX)
    executed = 4  # push, push, mov, jmp
    af = None  # (as at the entry, when nothing changes it)
    while True:
        executed += 2  # test ebx, ebx; jne
        if not count:
            break
        byte = mem[base + source]
        executed += 3  # mov cl, [edx]; test cl, cl; je
        if not byte:
            break
        mem[base + target] = byte
        af = af_of_inc(target)  # inc edx; dec ebx; mov [eax], cl; inc eax (the last of them gives AF)
        source, count, target = source + 1 & MASK, count - 1, target + 1 & MASK
        executed += 4
    if count:  # the rest are zeros: test, je, dec, mov, inc, jmp for each
        if base + target + count > len(mem):
            raise NotImplementedError(f"strncpy of {count} zeros at {target:X}: past the end of the memory")
        mem[base + target:base + target + count] = bytes(count)
        executed += 6 * count
        af = af_of_dec(1)
    zero_test(cpu, af)  # test ebx, ebx that finds it 0
    cpu.set32(CPU.DX, source)
    cpu.set32(CPU.BX, 0)
    end(cpu, executed + 2 + 4)  # test, je; mov eax, esi; pop; pop; ret


# --- strnicmp (1BE169): not more than EBX bytes, A-Z as a-z

STRNICMP_AT = 0x1BE169


def lower_of(byte):
    """The byte as the code makes it small (only A to Z), and the instructions it takes: cmp, jl; cmp, jg; add."""
    if byte < 0x41:
        return byte, 2
    if byte > 0x5A:
        return byte, 4
    return byte + 0x20, 5


def strnicmp(cpu):
    """Compares not more than EBX bytes of the strings at EAX and EDX, the capitals A-Z as small letters: EAX is the
    difference of the first two bytes that differ (as small letters), or 0. EBX is left with the last byte of the
    second string that was looked at, EDX at it (or, when the strings are the same, after them)."""
    mem, base = data(cpu)
    first, second, count = cpu.wide(CPU.AX), cpu.wide(CPU.DX), cpu.wide(CPU.BX)
    executed = 4  # push, push, mov, mov
    af = None  # (as at the entry, when nothing changes it)
    while True:
        executed += 2  # test esi, esi; jne
        if not count:
            break
        a, b = mem[base + first], mem[base + second]
        cpu.set32(CPU.BX, b)
        x, steps_a = lower_of(a)
        y, steps_b = lower_of(b)
        executed += 4 + steps_a + 2 + steps_b + 2  # mov, xor, mov, mov; ...; xor, mov; ...; cmp al, ah; je
        if x != y:
            cpu.set32(CPU.AX, cpu.sub_with_flags(x, y, 32))  # xor, mov, mov, and, sub edx, eax; mov eax, edx
            cpu.set32(CPU.DX, cpu.wide(CPU.AX))
            return end(cpu, executed + 9)  # ... pop, pop, ret
        af = 0  # (the cmp of two equal bytes)
        executed += 2  # test ah, ah; je
        if not y:
            break
        first, second, count = first + 1 & MASK, second + 1 & MASK, count - 1
        af = af_of_dec(count + 1)  # inc ecx; inc edx; dec esi; jmp
        executed += 4
    zero_test(cpu, af)  # xor eax, eax
    cpu.set32(CPU.AX, 0)
    cpu.set32(CPU.DX, second)
    end(cpu, executed + 4)  # xor, pop, pop, ret


# --- strcat (1BD419): the end of the first string by `repne scasb`, then the second string after it, two bytes a round

STRCAT_AT = 0x1BD419


def strcat(cpu):
    """Puts the string at EDX after the one at EAX, and gives back EAX."""
    if cpu.flags & flags.DF:
        raise NotImplementedError("strcat with the direction flag set: the code would look for the end backwards")
    mem, base = data(cpu)
    target, source = cpu.wide(CPU.AX), cpu.wide(CPU.DX)
    end_at = mem.find(b"\0", base + target) - base  # (ES is DS: the code loads it so)
    if end_at < 0:
        raise NotImplementedError(f"strcat to {target:X}: no zero up to the end of the memory")
    executed = 14  # push ecx, push esi, push edi, mov, mov, push es, push ds, pop es, push edi, sub, dec, mov al,
    # repne scasb (one instruction, however many bytes), dec edi
    at = 0
    while True:
        byte = mem[base + source + at]
        mem[base + end_at + at] = byte
        executed += 4  # mov al, [esi]; mov [edi], al; cmp al, 0; je
        if not byte:
            break
        byte = mem[base + source + at + 1]
        mem[base + end_at + at + 1] = byte
        executed += 6  # mov al, [esi+1]; add esi, 2; mov [edi+1], al; add edi, 2; cmp al, 0; jne
        at += 2
        if not byte:
            break
    cpu.sub_with_flags(0, 0, 8)  # cmp al, 0 of the zero
    end(cpu, executed + 7)  # pop edi, pop es, mov eax, edi, pop edi, pop esi, pop ecx, ret


PATCHES = [
    Patch("strcmp of UFO2P.EXE", STRCMP_AT, 0xA1,
          "e2de07c2e8ce74e666f01edb1271e76c31a6a73b59b63c5fc7fbc0628e6a21d5", strcmp),
    Patch("strupr of UFO2P.EXE", STRUPR_AT, 0x139,  # (with the table of toupper and toupper, up to 1200F9)
          "3e0aecf6e7e8b4379af1b1fea9aa5d17ce279f45a1fb723dcbc66355a0b0b0b7", strupr, also=(ASCII_TOUPPER,)),
    Patch("toupper of UFO2P.EXE", TOUPPER_AT, 0x79,
          "5cf01f68563c68e9d27d737cb47d320a3c1968edbf50f5ae4e67e76f55605e26", toupper,
          also=((STRUPR_AT, 0x139, "3e0aecf6e7e8b4379af1b1fea9aa5d17ce279f45a1fb723dcbc66355a0b0b0b7"), ASCII_TOUPPER)),
    Patch("strncpy of UFO2P.EXE", STRNCPY_AT, 0x25,
          "591e290c35107ce4ac72f7618362d7ef94d2744102fc9b80516b62d88d974d96", strncpy),
    Patch("strnicmp of UFO2P.EXE", STRNICMP_AT, 0x53,
          "ed9dfbb7c417068c5cf4b54b55b6c0619e634ba690d12195f552a5207c2d47c4", strnicmp),
    Patch("strcat of UFO2P.EXE", STRCAT_AT, 0x33,
          "da48d3536673a26592954077f828ef18ed7f21adeb447797d5d67ae8a35672ca", strcat),
]
