"""The patches: how a piece of code is found and replaced, and that the Python that stands in for the code of the
Smacker player does what that code does (the real code is run beside it, on the same input, when the game is there)."""
import hashlib
import random
import struct
import tempfile
import unittest
from pathlib import Path

from mydos import dos, loader, patches, psp
from mydos.cpu import CPU, Exit, NEVER
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


# --- the loops that go down the tree to a leaf

DESCENT_TREE, DESCENT_STREAM = 0x1A00000, 0x1B00000
DESCENTS = ((0x11105B, 0x111083), (0x1110D9, 0x111101))  # where a loop starts, and where the code goes on after it


def tree_to_go_down(rng, leaves):
    """The nodes of a tree as the code goes down it: a leaf has the top bit set and a byte, a node has the offset of its
    right side (the left one follows it)."""
    if leaves == 1:
        return [0x80000000 | rng.randrange(256)]
    cut = rng.randrange(1, leaves)
    left, right = tree_to_go_down(rng, cut), tree_to_go_down(rng, leaves - cut)
    return [4 + 4 * len(left)] + left + right


@unittest.skipUnless(SMKP.exists(), "the game is not here")
class TestTheDescentOfTheTree(unittest.TestCase):
    def run_loop(self, patch, seed, entry, leaf_at):
        rng = random.Random(seed)
        nodes = tree_to_go_down(rng, rng.randrange(2, 200))
        cpu = game_machine(patch=patch)
        cpu.mem[leaf_at] = 0xF4  # a HLT where the code goes on, to stop there
        memory = {DESCENT_TREE: struct.pack(f"<{len(nodes)}I", *nodes), DESCENT_STREAM: rng.randbytes(4096),
                  0x114960: bytes([rng.choice([1, 2, 5, 17, 32])])}
        registers = {"eax": nodes[0], "ecx": rng.randrange(1 << 32), "edx": DESCENT_TREE,
                     "ebx": rng.randrange(1 << 32), "esi": DESCENT_STREAM}
        run_scenario(cpu, memory, registers, entry, rng.choice(FLAG_CHOICES))
        return cpu

    def test_the_result_is_the_one_of_the_code_it_replaces(self):
        for entry, leaf_at in DESCENTS:
            for seed in range(60):
                with self.subTest(entry=hex(entry), seed=seed):
                    original = self.run_loop(False, seed, entry, leaf_at)
                    patched = self.run_loop(True, seed, entry, leaf_at)
                    self.assertEqual((original.r, original.rh), (patched.r, patched.rh))
                    self.assertEqual((original.flags, original.ip), (patched.flags, patched.ip))
                    self.assertEqual(original.mem[0x114960], patched.mem[0x114960])
                    self.assertEqual(original.instructions, patched.instructions)  # the time is the same


# --- the loop of the sound of Smacker

SOUND_FRAME, SOUND_RING = 0x1C00000, 0x1C10000
SOUND_TREES = (0x114928, 0x11492C, 0x114930, 0x114934)  # where the roots of the four trees are


@unittest.skipUnless(SMKP.exists(), "the game is not here")
class TestTheLoopOfTheSound(unittest.TestCase):
    def run_loop(self, patch, seed):
        rng = random.Random(seed)
        cpu = game_machine(patch=patch)
        cpu.mem[0x111166] = 0xF4  # a HLT where the loop ends
        memory = {DESCENT_STREAM: rng.randbytes(8192), 0x114960: bytes([rng.choice([1, 2, 5, 17, 32])])}
        for number, root_at in enumerate(SOUND_TREES):
            nodes = tree_to_go_down(rng, rng.choice([1, 1, 2, 5, 40, 150]))
            memory[DESCENT_TREE + 0x1000 * number] = struct.pack(f"<{len(nodes)}I", *nodes)
            memory[root_at] = struct.pack("<I", DESCENT_TREE + 0x1000 * number)
        slots = rng.choice([2, 5, 16])
        start = SOUND_RING + 4 * rng.randrange(slots)
        memory[SOUND_FRAME + 0x14] = struct.pack("<II", SOUND_RING, SOUND_RING + 4 * slots)  # the ring: from, to
        memory[0x114A9C] = struct.pack("<I", SOUND_RING + 4 * rng.randrange(1, slots))  # (the start is never met: it is what it wraps to)
        registers = {"ebx": rng.randrange(1 << 32), "ecx": rng.randrange(1 << 32), "esi": DESCENT_STREAM,
                     "edi": start, "ebp": SOUND_FRAME, "eax": rng.randrange(1 << 32), "edx": rng.randrange(1 << 32)}
        run_scenario(cpu, memory, registers, 0x11104C, rng.choice(FLAG_CHOICES))
        return cpu

    def test_the_result_is_the_one_of_the_code_it_replaces(self):
        for seed in range(80):
            with self.subTest(seed=seed):
                original, patched = self.run_loop(False, seed), self.run_loop(True, seed)
                self.assertEqual((original.r, original.rh), (patched.r, patched.rh))
                self.assertEqual((original.flags, original.ip), (patched.flags, patched.ip))
                self.assertEqual(bytes(original.mem[SOUND_RING:SOUND_RING + 0x100]),
                                 bytes(patched.mem[SOUND_RING:SOUND_RING + 0x100]))
                self.assertEqual(original.mem[0x114960], patched.mem[0x114960])
                self.assertEqual(original.instructions, patched.instructions)


# --- the blocks of a frame of Smacker

BLOCKS_AT = 0x10F7B5  # where the INT of the patch is: the second instruction of the loop, `cmp al, 8`
FRAME_CALL, FRAME_RETURN = 0x10F680, 0x103F2D  # the procedure that draws a frame, and where it returns to
CONFIG = Path(__file__).resolve().parent.parent / "config_xcomapoc.toml"  # the machine with the disc of the game


@unittest.skipUnless(SMKP.exists(), "the game is not here")
class TestTheRowsOfAMonoBlock(unittest.TestCase):
    def test_the_sixteen_ways_to_put_the_two_colors_are_those_of_the_code(self):
        rng = random.Random(5)
        for way in range(16):
            for _ in range(20):
                colors = rng.randrange(1 << 32)
                cpu = game_machine(patch=False)
                cpu.mem[0x10FB80] = 0xF4  # a HLT where the ways end
                run_scenario(cpu, {}, {"edx": colors, "eax": rng.randrange(1 << 32)}, 0x10FA80 + 16 * way)
                with self.subTest(way=way, colors=hex(colors)):
                    self.assertEqual(cpu.wide(CPU.AX), patches.row_of_colors(way, colors))
                    self.assertEqual(cpu.instructions, patches.ROW_COUNTS[way] + 1)  # (and the HLT)


def disc_machine():
    """The volumes of the machine with the disc of the game, or a SkipTest if they are not here."""
    from mydos import config
    from mydos.volumes import Volumes
    if not SMKP.exists() or not CONFIG.exists():
        raise unittest.SkipTest("the game and the machine for it are not here")
    try:
        volumes = Volumes(config.load(CONFIG))
        volumes.resolve("d:\\xcom3\\smk\\wingame2.smk")
    except Exception as error:  # no disc, no video
        raise unittest.SkipTest(f"the disc of the game is not here: {error}")
    return volumes


def save_machine_at(volumes, path, segment, ip, number):
    """Play the video 2 up to the `number`th time that the code is at segment:ip, and save the machine in `path`. The
    patches of the pieces that come later are not made: the machine is one that nothing has changed."""
    from mydos import machine, snapshot
    saved = list(patches.LATE_PATCHES)
    patches.LATE_PATCHES.clear()
    try:
        where = volumes.resolve("ufoexe/smkp.exe")
        computer = machine.build(volumes)
        cpu = computer.cpu
        psp.build(cpu, psp.PSP_SEG, psp.ENV_SEG, dos.MEMORY_TOP_SEG, where.dos_path(), tail=b" 2 GOFORIT SOUND")
        loader.load(cpu, SMKP.read_bytes(), psp.PSP_SEG, computer.state.extended)
        entries = [0]

        def trace(cpu):
            if cpu.ip == ip and cpu.s[CPU.CS] == segment:
                entries[0] += 1
                if entries[0] == number:
                    snapshot.save(str(path), computer)
                    raise Exit(0)
        cpu.trace = trace
        cpu.run(20000, lambda: None)
    finally:
        patches.LATE_PATCHES[:] = saved


def run_saved_machine(volumes, path, patched, segment, back, inside=None):
    """Put the saved machine back, and run it until it returns (to `back`, with the stack as it was less the address): once
    as the code is, once with the patch. `inside(cpu, ip)` is called at an address on the way, if it is given, to change
    something (the same for both). Returns what is left: the registers, the flags, the count of instructions, the hash of
    the memory and that of the picture."""
    from mydos import machine, snapshot
    saved = list(patches.LATE_PATCHES)
    if not patched:
        patches.LATE_PATCHES.clear()
    try:
        computer = machine.build(volumes)
        cpu = computer.cpu
        snapshot.load(str(path), computer)
        patches.install(cpu)
        if patched:
            patches.apply_late(cpu)
    finally:
        patches.LATE_PATCHES[:] = saved
    if not patched:  # the code as it is: what the patch stood in the place of goes back
        cpu.mem[BLOCKS_AT:BLOCKS_AT + 2] = bytes((0x3C, 0x08))  # cmp al, 8
    cpu.event_sources, cpu.next_event = [], NEVER  # nothing of the devices comes in the middle
    stack = cpu.wide(CPU.SP)
    start = cpu.instructions

    def trace(cpu):
        if inside is not None:
            inside(cpu)
        if cpu.s[CPU.CS] == segment and cpu.ip == back and cpu.wide(CPU.SP) == stack + 4:
            raise Exit(0)
    cpu.trace = trace
    cpu.run(1 << 30, lambda: None)
    for place in (BLOCKS_AT, MIXER_AT + 0x46, MIXER_AT + 0xBB):  # (where the INTs stand)
        cpu.mem[place:place + 2] = bytes(2)
    picture = hashlib.sha1(b"".join(bytes(plane) for plane in computer.card.planes)).hexdigest()
    return (cpu.r, cpu.rh, cpu.s[:], cpu.flags, cpu.ip, cpu.instructions - start, hashlib.sha1(cpu.mem).hexdigest(), picture)


class TestTheBlocksOfAFrame(unittest.TestCase):
    """The frame of a video (SMKP.EXE plays the video 2 of the disc) is drawn by the code and by the patch, from the same
    state of the machine (the entry of the procedure for the third frame, put in a file and taken out again): the
    registers, the flags, the count of instructions, the memory and the picture have to be the same. It plays the
    first frames of a video, so it takes some time."""

    @classmethod
    def setUpClass(cls):
        cls.volumes = disc_machine()
        cls.directory = tempfile.TemporaryDirectory()
        cls.state = Path(cls.directory.name) / "frame.state"
        save_machine_at(cls.volumes, cls.state, 0x0F, FRAME_CALL, 3)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_the_frame_is_the_one_the_code_draws(self):
        original = run_saved_machine(self.volumes, self.state, False, 0x0F, FRAME_RETURN)
        patched = run_saved_machine(self.volumes, self.state, True, 0x0F, FRAME_RETURN)
        self.assertGreater(original[5], 100_000)  # it is a frame with blocks in it
        self.assertEqual(original, patched)


# --- the mixer of the sound, that comes with the driver, later than the program

MIXER_AT = 0x12E144  # where the procedure is, in the run of the video 2 (the driver is put there)
MIXER_CALL, MIXER_LOOP = 0x2144, 0x217A  # the offsets in its segment: the procedure, and just before its first loop


class TestTheMixerOfTheSound(unittest.TestCase):
    """The procedure that mixes the sound into the ring of the buffer is run by the code and by the patch from the
    same saved machine, with the source, the ring and the volume made random (so that the sums go over the end of a byte
    too): the same registers, memory and count of instructions."""

    @classmethod
    def setUpClass(cls):
        cls.volumes = disc_machine()
        cls.directory = tempfile.TemporaryDirectory()
        cls.state = Path(cls.directory.name) / "mixer.state"
        save_machine_at(cls.volumes, cls.state, 0x47, MIXER_CALL, 3)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def randomized(self, seed):
        rng = random.Random(seed)
        done = []

        def inside(cpu):
            if cpu.s[CPU.CS] == 0x47 and cpu.ip == MIXER_LOOP and not done:
                done.append(True)
                source = cpu.bases[cpu.s[CPU.DS]] + cpu.wide(CPU.SI)
                ring = cpu.bases[cpu.s[CPU.ES]]
                cpu.mem[source:source + 0x4000] = rng.randbytes(0x4000)
                cpu.mem[ring:ring + 0x2000] = rng.randbytes(0x2000)
                cpu.r[CPU.BX] = cpu.r[CPU.BX] & 0xFF | rng.randrange(256) << 8
        return inside

    def test_the_mixing_is_the_one_of_the_code(self):
        for seed in range(6):
            with self.subTest(seed=seed):
                back = self.return_address()
                original = run_saved_machine(self.volumes, self.state, False, 0x47, back, self.randomized(seed))
                patched = run_saved_machine(self.volumes, self.state, True, 0x47, back, self.randomized(seed))
                self.assertGreater(original[5], 1000)
                self.assertEqual(original, patched)

    def return_address(self):
        from mydos import machine, snapshot
        computer = machine.build(self.volumes)
        snapshot.load(str(self.state), computer)
        cpu = computer.cpu
        return cpu.rd(cpu.s[CPU.SS], cpu.wide(CPU.SP))
