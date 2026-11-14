"""Play a Smacker video in the window of the emulator, decoded in Python (dosloth/smacker.py): no machine is run.

    python -m dosloth.smkplay -c config_xcomapoc.toml D:\\XCOM3\\SMK\\INTRO1.SMK

The video is named as DOS names a file (as the program of `python -m dosloth` is), so a video on a disc image is found
with the configuration that mounts it. The frames come at the rate of the video; when the decoding falls behind, a
picture is left out (never a frame: each is drawn on the one before). A key, or closing the window, stops it. The
sound is not played: the emulator has no way to the host's sound yet.
"""
import argparse
import itertools
import sys
import time
from pathlib import Path

from . import config
from .filesystem import FsError
from .screen import Frame
from .smacker import SmackerError, Video
from .volumes import Volumes
from .window import TkWindow, WindowError


def color_tables(palette):
    """The three tables (red, green, blue) from a color number to its 8 bits, as the card makes them of 6 bits."""
    return [bytes(part << 2 | part >> 4 for part in palette[channel::3]) for channel in range(3)]


def picture(pixels, tables, width, height):
    rgb = bytearray(len(pixels) * 3)
    for channel, table in enumerate(tables):
        rgb[channel::3] = pixels.translate(table)
    return Frame(width, height, bytes(rgb))


def play(video, window, clock=time.monotonic, sleep=time.sleep):
    """Show the frames of `video` in `window` at their rate, until the end, a key or the window closed. Returns how many
    frames were decoded and how many of them were shown."""
    keys = []
    window.press = lambda scan, ascii: keys.append(scan)
    frames = video.frames()
    first = next(frames, None)  # (with the trees of the video, which take a while to make: the clock starts after them)
    if first is None:
        return 0, 0
    start = clock()
    shown = decoded = 0
    tables, last_palette = None, None
    for number, (pixels, palette) in enumerate(itertools.chain([first], frames)):
        decoded += 1
        due = start + number * video.frame_ms / 1000
        if palette != last_palette:
            tables, last_palette = color_tables(palette), bytes(palette)
        if clock() <= due + video.frame_ms / 1000:  # not a whole frame late: shown
            while clock() < due and not (keys or window.closed):
                window.poll()
                sleep(0.002)
            if keys or window.closed:
                break
            window.present(picture(pixels, tables, video.width, video.height))
            shown += 1
        window.poll()
        if keys or window.closed:
            break
    return decoded, shown


def main():
    parser = argparse.ArgumentParser(prog="python -m dosloth.smkplay",
                                     description="Play a Smacker video (.smk) in a window, decoded in Python.")
    parser.add_argument("-c", metavar="FILE", help="the configuration (TOML) whose drives the video is on; without it "
                        "the current directory is the hard disk C:")
    parser.add_argument("video", help="the .smk file, named as DOS names it: `D:\\XCOM3\\SMK\\INTRO1.SMK`")
    args = parser.parse_args()
    try:
        settings = config.load(args.c) if args.c else config.default(Path.cwd())
    except config.ConfigError as error:
        parser.error(str(error))
    volumes = Volumes(settings)
    try:
        where = volumes.resolve(args.video)
        with where.filesystem.open(where.parts) as file:
            data = file.read()
        video = Video(data)
    except (FsError, SmackerError) as error:
        parser.error(f"cannot play {args.video}: {error}")
    try:
        window = TkWindow(lambda scan, ascii: None, title=f"DOSloth - {args.video}")
    except WindowError as error:
        parser.error(f"cannot open a window: {error}")
    try:
        decoded, shown = play(video, window)
    finally:
        window.close()
    print(f"{decoded} of {video.count} frames, {shown} shown", file=sys.stderr)


if __name__ == "__main__":
    main()
