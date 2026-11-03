"""The patches: how a piece of code is found and replaced, and that the Python that stands in for the code of the
Smacker player does what that code does (the real code is run beside it, on the same input, when the game is there)."""
import hashlib
import random
import struct
import unittest
from pathlib import Path

from mydos import loader, patches, psp
from mydos.cpu import CPU, Exit
from mydos.patches import PATCH_BYTES, Patch
from mydos.xmem import ExtendedMemory

SMKP = Path(__file__).resolve().parent.parent / "XCOMA" / "UFOEXE" / "SMKP.EXE"


def patch_of(handler, code=b"abcd", address=0x2000):
    return Patch("test", address, len(code), hashlib.sha256(code).hexdigest(), handler)


class TestMechanism(unittest.TestCase):
    def test_the_code_that_is_the_one_is_patched_at_its_first_two_bytes(self):
        cpu = CPU()
        cpu.mem[0x2000:0x2004] = b"abcd"
        self.assertEqual(patches.apply(cpu, [patch_of(None)]), ["test"])
        self.assertEqual(bytes(cpu.mem[0x2000:0x2004]), PATCH_BYTES + b"cd")

    def test_other_code_is_left_alone(self):
        cpu = CPU()
        cpu.mem[0x2000:0x2004] = b"abce"
        self.assertEqual(patches.apply(cpu, [patch_of(None)]), [])
        self.assertEqual(bytes(cpu.mem[0x2000:0x2004]), b"abce")

    def test_the_interrupt_runs_the_handler_that_decides_where_the_program_goes_on(self):
        cpu = CPU()
        cpu.mem[0x2000:0x2004] = b"abcd"
        seen = []

        def handler(cpu):
            seen.append(cpu.ip)
            cpu.ip = 0x3000
        patches.install(cpu, [patch_of(handler)])
        patches.apply(cpu, [patch_of(handler)])
        cpu.s[CPU.CS] = 0
        cpu.ip = 0x2000
        cpu.step()
        self.assertEqual((seen, cpu.ip), ([0x2002], 0x3000))  # it saw the address after the INT

    def test_the_interrupt_where_there_is_no_patch_is_an_error(self):
        cpu = CPU()
        patches.install(cpu)
        cpu.mem[0x2000:0x2002] = PATCH_BYTES
        cpu.s[CPU.CS] = 0
        cpu.ip = 0x2000
        with self.assertRaisesRegex(NotImplementedError, "no patch there"):
            cpu.step()

    def test_the_patches_that_there_are_name_their_code_by_a_hash(self):
        for patch in patches.PATCHES:
            self.assertEqual(len(patch.digest), 64)
            self.assertGreater(patch.length, len(PATCH_BYTES))


# --- the tree of the Smacker player, against the code it stands in for


def build_tree(rng, values, prefix=()):
    """A tree for the walk of the player: the dwords (a leaf: 80000000h and the value; a node: the offset to its
    right side, the left side being next), and the bits that lead to each value."""
    if len(values) == 1:
        return [0x80000000 | values[0]], {values[0]: list(prefix)}
    cut = rng.randrange(1, len(values))
    left, left_codes = build_tree(rng, values[:cut], prefix + (0,))
    right, right_codes = build_tree(rng, values[cut:], prefix + (1,))
    return [4 + 4 * len(left)] + left + right, {**left_codes, **right_codes}


def shape(rng, leaves):
    """The kinds of the nodes in the order they come: True for a leaf, with a node before the two sides of it."""
    if leaves == 1:
        return [True]
    cut = rng.randrange(1, leaves)
    return [False] + shape(rng, cut) + shape(rng, leaves - cut)


LOW, HIGH, STRUCT, OUT = 0x1200000, 0x1210000, 0x1148C0, 0x1300000  # the trees, 16 bytes, the output
STREAM, STACK, EXIT_AT = 0x1400000, 0x1500000, 0x1510000  # the bits, the stack, a HLT where the code returns to


def scenario(seed):
    """What the code finds when it is called, as (the bytes to put in memory as {address: bytes}, the registers)."""
    rng = random.Random(seed)
    low_values = rng.sample(range(0x200), rng.choice([1, 2, 7, 40]))
    high_values = rng.sample(range(0x200), rng.choice([1, 3, 12, 60]))
    low_dwords, low_codes = build_tree(rng, low_values)
    high_dwords, high_codes = build_tree(rng, high_values)
    leaves = [(rng.choice(low_values), rng.choice(high_values)) for _ in range(rng.choice([1, 2, 5, 60, 150]))]
    bits = []
    pairs = [(a & 0xFF) | (b & 0xFF) << 8 for a, b in leaves]
    for escape in (rng.choice(pairs), rng.choice(pairs), rng.randrange(0x10000)):  # the third usually is not met
        bits += [escape >> i & 1 for i in range(16)]
    kinds, number = shape(rng, len(leaves)), 0
    for is_leaf in kinds:
        if is_leaf:
            a, b = leaves[number]
            number += 1
            bits += [0] + low_codes[a] + high_codes[b]
        else:
            bits.append(1)
    bits.append(0)  # and a zero ends it
    left = rng.choice([1, 5, 9, 17, 32])  # how much is in the buffer: one more than the bits that are
    buffer = sum(bit << i for i, bit in enumerate(bits[:left - 1]))
    rest = bits[left - 1:]
    rest += [0] * (-len(rest) % 32)
    stream = b"".join(struct.pack("<I", sum(bit << i for i, bit in enumerate(rest[at:at + 32])))
                      for at in range(0, len(rest), 32))
    memory = {LOW: struct.pack(f"<{len(low_dwords)}I", *low_dwords), HIGH: struct.pack(f"<{len(high_dwords)}I",
                                                                                        *high_dwords),
              0x114928: struct.pack("<II", LOW, HIGH), 0x114960: bytes([left]), STREAM: stream,
              STRUCT: bytes(range(1, 17))}  # not zero: the code has to clear them
    return memory, {"esi": STREAM, "ebx": buffer, "edx": STRUCT, "edi": OUT, "eax": 0x12345678, "ecx": 0x9ABCDEF0}


def game_machine(patch):
    cpu = CPU()
    psp.build(cpu, 0x1000, 0x0FF0, 0x9000, "C:\\SMKP.EXE")
    loader.load_le(cpu, SMKP.read_bytes(), 0x1000, ExtendedMemory(), patch=patch)
    patches.install(cpu)
    return cpu


def run_scenario(cpu, memory, registers, entry=0x10DD90, flags=None):
    for address, data in memory.items():
        cpu.mem[address:address + len(data)] = data
    for number, name in enumerate(("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi")):
        if name in registers:
            cpu.set32(number, registers[name])
    cpu.set32(CPU.SP, STACK - 4)
    cpu.mem[STACK - 4:STACK] = struct.pack("<I", EXIT_AT)  # the code returns to a HLT
    cpu.mem[EXIT_AT] = 0xF4
    cpu.ip = entry
    if flags is not None:
        cpu.flags = flags
    try:
        while True:
            cpu.step()
    except Exit:
        pass


class Beside(unittest.TestCase):
    """Run the code of the game and the patch that stands in for it on the same input, and compare."""

    def beside(self, memory, registers, entry, regions, flags=None):
        original, patched = game_machine(patch=False), game_machine(patch=True)
        run_scenario(original, memory, registers, entry, flags)
        run_scenario(patched, memory, registers, entry, flags)
        self.assertEqual((original.r, original.rh), (patched.r, patched.rh))
        self.assertEqual((original.flags, original.ip), (patched.flags, patched.ip))
        for start, end in regions:  # (the pieces of the stack below the place of ESP are not what it is about)
            self.assertEqual(bytes(original.mem[start:end]), bytes(patched.mem[start:end]), hex(start))
        self.assertGreater(original.instructions, patched.instructions)
        return original, patched


@unittest.skipUnless(SMKP.exists(), "the game is not here")
class TestTheTreeOfSmacker(Beside):
    def test_the_game_code_at_that_place_is_the_one_the_patch_is_for(self):
        cpu = game_machine(patch=True)
        for patch in patches.PATCHES:
            self.assertEqual(bytes(cpu.mem[patch.address:patch.address + 2]), PATCH_BYTES, patch.name)

    def test_the_result_is_the_one_of_the_code_it_replaces(self):
        for seed in range(60):
            memory, registers = scenario(seed)
            with self.subTest(seed=seed):
                original, patched = self.beside(memory, registers, 0x10DD90,
                                                ((0x112000, 0x122000), (OUT, OUT + 0x10000), (LOW, HIGH + 0x10000)))
                self.assertGreater(original.instructions, 4 * patched.instructions)


# --- the tables made from the tree


TREE, TABLE, LIST, BITMAP = 0x1600000, 0x1700000, 0x1800000, 0x1900000
FLAG_CHOICES = (0x0002, 0x0002 | 0x0001, 0x0002 | 0x08D5, 0x0002 | 0x0400 | 0x0200)  # some of the flags set, some clear


def nodes(rng, leaves):
    """A tree as big_tree writes it: a leaf is its value in the upper 16 bits and 1 below, a node is the offset of its
    right side (the left one follows it) above the 13th bit."""
    if leaves == 1:
        return [rng.randrange(0x10000) << 16 | 1]
    cut = rng.randrange(1, leaves)
    left, right = nodes(rng, cut), nodes(rng, leaves - cut)
    return [(4 + 4 * len(left)) << 13] + left + right


def junk(rng):
    return {name: rng.randrange(1 << 32) for name in ("eax", "ecx", "ebp")}


@unittest.skipUnless(SMKP.exists(), "the game is not here")
class TestTheTables(Beside):
    def test_the_table_that_is_looked_up_by_bits(self):
        for seed in range(60):
            rng = random.Random(seed)
            tree = nodes(rng, rng.choice([1, 2, 3, 20, 100]))
            depth = rng.choice([1, 3, 6, 8, 11])
            memory = {TREE: struct.pack(f"<{len(tree)}I", *tree), 0x114940: struct.pack("<I", TREE - 0x20),
                      0x114950: struct.pack("<II", depth, rng.choice([1 << depth, 1 << depth, 5]))}
            registers = {**junk(rng), "edx": TREE, "edi": TABLE}
            with self.subTest(seed=seed):
                self.beside(memory, registers, 0x10E090, ((0x112000, 0x122000), (TABLE, TABLE + 0x4000)),
                            rng.choice(FLAG_CHOICES))

    def test_the_values_that_are_in_the_tree_are_marked_in_a_bitmap(self):
        for seed in range(60):
            rng = random.Random(seed)
            count = rng.choice([1, 2, 10, 300])
            entries = [rng.randrange(1 << 32) for _ in range(count)]
            entries = [entry | 1 if rng.random() < 0.5 else entry & ~1 for entry in entries]
            memory = {LIST: struct.pack(f"<{count}I", *entries), BITMAP: bytes(rng.randrange(256) for _ in range(64))}
            registers = {**junk(rng), "esi": LIST - 0x7468, "eax": 4 * count + rng.randrange(4), "edi": BITMAP}
            registers["edx"] = rng.randrange(1 << 32)
            with self.subTest(seed=seed):
                self.beside(memory, registers, 0x10E1A0, ((BITMAP, BITMAP + 0x2000), (LIST, LIST + 0x2000),
                                                          (0x112000, 0x122000)), rng.choice(FLAG_CHOICES))

    def test_the_values_of_the_tree_are_put_in_the_low_halves(self):
        for seed in range(60):
            rng = random.Random(seed)
            count = rng.choice([1, 2, 10, 300])
            entries = [rng.randrange(1 << 32) for _ in range(count)]
            entries = [entry | 1 if rng.random() < 0.5 else entry & ~1 for entry in entries]
            memory = {LIST: struct.pack(f"<{count}I", *entries), 0x114970: struct.pack("<H", rng.randrange(0x10000))}
            registers = {**junk(rng), "esi": LIST - 0x7468, "eax": 4 * count + rng.randrange(4)}
            registers["edx"] = rng.randrange(1 << 32)
            with self.subTest(seed=seed):
                self.beside(memory, registers, 0x10E1D0, ((LIST, LIST + 0x2000), (0x112000, 0x122000)),
                            rng.choice(FLAG_CHOICES))


if __name__ == "__main__":
    unittest.main()
