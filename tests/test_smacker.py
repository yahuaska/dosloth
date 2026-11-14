"""The Smacker videos decoded in Python (dosloth/smacker.py) and played in a window (dosloth/smkplay.py).

Small videos made here show the parts of the format; the videos of the game are checked against the pictures that the
emulator makes when it runs the player of the game (SMKP.EXE) on them, when the game and its disc are here."""
import hashlib
import struct
import unittest
from pathlib import Path

from dosloth import smkplay
from dosloth.smacker import Bits, SmackerError, Video, new_palette

ROOT = Path(__file__).resolve().parent.parent
SMKP = ROOT / "XCOMA" / "UFOEXE" / "SMKP.EXE"
CONFIG = ROOT / "config_xcomapoc.toml"


class BitWriter:
    """Bits put the way a Smacker stream has them: the lowest bit of each byte first."""

    def __init__(self):
        self.bits = []

    def put(self, value, count=1):
        self.bits.extend(value >> n & 1 for n in range(count))
        return self

    def data(self):
        padded = self.bits + [0] * (-len(self.bits) % 32)
        return bytes(sum(bit << n for n, bit in enumerate(padded[at:at + 8])) for at in range(0, len(padded), 8))


def one_value_tree(writer, value):
    """A big tree with one leaf, `value`: the byte trees of its low and high bytes have one leaf each."""
    writer.put(1)  # the tree is there
    for byte in (value & 0xFF, value >> 8):
        writer.put(1).put(0).put(byte, 8).put(0)  # a byte tree: there, a leaf and its 8 bits, the end
    for escape in (0x1111, 0x2222, 0x3333):  # values that the leaf is not
        writer.put(escape, 16)
    writer.put(0).put(0)  # the big tree: a leaf (the bytes come from the byte trees, which need no bits), the end


def make_video(width, height, type_value, frames, palette=None, sound=b""):
    """A video of `frames` frames of `width` x `height` whose tree of the types has the one value `type_value` (the
    other trees are not there), the first frame with `palette` (the bytes of a palette chunk after its length) and
    every frame with the sound track `sound`."""
    trees = BitWriter()
    trees.put(0).put(0).put(0)  # no trees of the masks, the colors and the full blocks
    one_value_tree(trees, type_value)
    tree_data = trees.data()
    bodies, types = [], []
    for number in range(frames):
        body, kind = b"", 0
        if number == 0 and palette is not None:
            chunk = bytes([0]) + palette
            chunk += bytes(-len(chunk) % 4)
            body += bytes([len(chunk) // 4]) + chunk[1:]
            kind |= 1
        if sound:
            body += struct.pack("<I", 4 + len(sound)) + sound
            kind |= 2
        body += bytes(4)  # the picture: no bits are read from a tree of one value
        body += bytes(-len(body) % 4)
        bodies.append(body)
        types.append(kind)
    header = struct.pack("<4s5i", b"SMK2", width, height, frames, -8333, 0) + bytes(7 * 4)
    header += struct.pack("<5I", len(tree_data), 0, 0, 0, 0) + bytes(7 * 4) + bytes(4)
    assert len(header) == 0x68
    return (header + b"".join(struct.pack("<I", len(body)) for body in bodies) + bytes(types) + tree_data
            + b"".join(bodies))


class TestTheBits(unittest.TestCase):
    def test_the_lowest_bit_of_a_byte_comes_first(self):
        bits = Bits(bytes((0b10110001, 0xFF)))
        self.assertEqual([bits.bit() for _ in range(4)], [1, 0, 0, 0])
        self.assertEqual(bits.get(6), 0b111011)


class TestThePalette(unittest.TestCase):
    def test_a_color_that_stays_one_from_the_palette_before_and_a_new_one(self):
        palette = bytearray(range(256)) * 3
        before = bytes(palette)
        # 3 colors stay (82h); 2 colors are the colors 10 and 11 of before (41h 0Ah); a new color; the rest stay (FFh, F9h)
        ops = bytes((0x82, 0x41, 0x0A, 0x3F, 0x20, 0x01, 0xFF, 0xF9))
        chunk = bytes([3]) + ops + bytes(3)  # 12 bytes with the byte of the length: 3 dwords
        self.assertEqual(new_palette(chunk, 0, palette), 12)
        self.assertEqual(palette[:9], before[:9])
        self.assertEqual(palette[9:15], before[30:36])
        self.assertEqual(palette[15:18], bytes((0x3F, 0x20, 0x01)))
        self.assertEqual(palette[18:], before[18:])

    def test_a_palette_that_ends_too_soon(self):
        with self.assertRaises(SmackerError):
            new_palette(bytes((1, 0x80, 0, 0)), 0, bytearray(768))


class TestAVideoMadeHere(unittest.TestCase):
    def test_a_solid_run_over_all_the_blocks_in_the_color_of_the_palette(self):
        solid_run_of_6 = 0x2A << 8 | 5 << 2 | 3  # the color 2Ah, the run of the 6th length (6 blocks), solid
        data = make_video(12, 8, solid_run_of_6, 2, palette=bytes((0x3F, 0x00, 0x15, 0xFF, 0xFE)), sound=b"noise!")
        video = Video(data)
        self.assertEqual((video.width, video.height, video.count, video.frame_ms), (12, 8, 2, 83.33))
        frames = [(bytes(pixels), bytes(palette)) for pixels, palette in video.frames()]
        self.assertEqual(frames[0][0], bytes([0x2A]) * 96)
        self.assertEqual(frames[0][1][:3], bytes((0x3F, 0x00, 0x15)))  # a new color 0, and 128 and 127 that stay
        self.assertEqual(frames[1], frames[0])

    def test_a_void_run_leaves_the_frame_as_it_was(self):
        void_run_of_6 = 5 << 2 | 2
        frames = [bytes(pixels) for pixels, _ in Video(make_video(12, 8, void_run_of_6, 1)).frames()]
        self.assertEqual(frames, [bytes(96)])

    def test_what_is_not_a_smacker_video_of_this_kind(self):
        good = make_video(12, 8, 2, 1)
        for data, why in ((b"SMK4" + good[4:], "SMK4"), (good[:0x14] + struct.pack("<i", 1) + good[0x18:], "flags"),
                          (good + b"more", "length"), (good[:0x68 - 1], "short")):
            with self.subTest(why), self.assertRaises(SmackerError):
                Video(data)


class FakeWindow:
    """What the player needs of a window: it keeps the pictures, and a key or the closing can be made to come."""

    def __init__(self, key_after=None):
        self.shown, self.closed, self.key_after = [], False, key_after

    def present(self, frame):
        self.shown.append(frame)

    def poll(self):
        if self.key_after is not None and len(self.shown) >= self.key_after:
            self.press(1, 27)


class FakeClock:
    def __init__(self, step=0.0):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class TestPlaying(unittest.TestCase):
    VIDEO = make_video(8, 4, 0x07 << 8 | 1 << 2 | 3, 5, palette=bytes((0x80 | 6, 0x3F, 0x3F, 0x3F, 0xFF, 0xF7)))

    def test_every_frame_is_shown_at_its_time_in_the_colors_of_the_card(self):
        window, clock = FakeWindow(), FakeClock()
        self.assertEqual(smkplay.play(Video(self.VIDEO), window, clock, clock.sleep), (5, 5))
        self.assertEqual(len(window.shown), 5)
        frame = window.shown[0]
        self.assertEqual((frame.width, frame.height), (8, 4))
        self.assertEqual(frame.rgb[:3], bytes((0xFF, 0xFF, 0xFF)))  # 3Fh of 6 bits is FFh of 8, as the card makes it
        self.assertGreaterEqual(clock.now, 4 * 0.08333)

    def test_a_picture_that_is_late_by_a_frame_is_left_out_but_the_frames_go_on(self):
        window, clock = FakeWindow(), FakeClock(step=0.1)  # every look at the clock is a frame and more later
        decoded, shown = smkplay.play(Video(self.VIDEO), window, clock, clock.sleep)
        self.assertEqual(decoded, 5)
        self.assertLess(shown, 5)

    def test_a_key_stops_it(self):
        window, clock = FakeWindow(key_after=2), FakeClock()
        self.assertEqual(smkplay.play(Video(self.VIDEO), window, clock, clock.sleep), (2, 2))


class TestTheVideosOfTheGame(unittest.TestCase):
    """The first frames of a video of the game are those that the emulator shows when it runs the player of the game on
    it: the same pixels, and the same colors in the DAC of the card. (So are all the frames of the three videos, but for
    the frame 1 of the video 1, whose picture the player leaves out on the machine, with the patches or without them,
    and the three after it, which show that.)"""
    FRAMES = 24  # (from the 12th on, the frames have every kind of block)

    def test_the_first_frames_of_the_video_2_are_those_of_the_player(self):
        from dosloth import config, dos, loader, machine, psp
        from dosloth.cpu import Exit
        from dosloth.volumes import Volumes
        if not SMKP.exists() or not CONFIG.exists():
            self.skipTest("the game and the machine for it are not here")
        try:
            volumes = Volumes(config.load(CONFIG))
            where = volumes.resolve("d:\\xcom3\\smk\\wingame2.smk")
            with where.filesystem.open(where.parts) as file:
                video = Video(file.read())
        except Exception as error:
            self.skipTest(f"the disc of the game is not here: {error}")
        player = volumes.resolve("ufoexe/smkp.exe")
        computer = machine.build(volumes)
        cpu, card = computer.cpu, computer.card
        psp.build(cpu, psp.PSP_SEG, psp.ENV_SEG, dos.MEMORY_TOP_SEG, player.dos_path(), tail=b" 2 GOFORIT SOUND")
        loader.load(cpu, SMKP.read_bytes(), psp.PSP_SEG, computer.state.extended)
        seen = []

        def trace(cpu):
            if cpu.ip == 0x103BC0 and cpu.s[cpu.CS] == 0x0F:  # SmackNextFrame: the frame before it is on the screen
                pixels = bytearray(64000)
                for number, plane in enumerate(card.planes):
                    pixels[number::4] = bytes(plane[:16000])
                seen.append((hashlib.sha1(pixels).hexdigest(), hashlib.sha1(bytes(card.dac)).hexdigest()))
                if len(seen) == self.FRAMES:
                    raise Exit(0)
        cpu.trace = trace
        cpu.run(20000, lambda: None)
        mine = []
        for pixels, palette in video.frames():
            mine.append((hashlib.sha1(pixels).hexdigest(), hashlib.sha1(palette).hexdigest()))
            if len(mine) == self.FRAMES:
                break
        self.assertEqual(mine, seen)
