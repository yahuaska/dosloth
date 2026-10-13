"""The Program Segment Prefix: the 256 bytes DOS puts in front of every program it loads.

The program's own code finds out about its surroundings here, before it calls DOS for anything: how
much memory it may use, where its environment is, what was typed after its name. A C library's startup
reads these fields to size its heap and stack, so an empty PSP, with every field 0, makes it conclude
that there is no memory at all.

Only the fields programs are known to read are filled in; the rest stay 0.
"""
import os

JFT_OFFSET = 0x18  # the table of file handles that starts at PSP:0018
JFT_SIZE = 20  # how many handles a program may have open


def environment(program_path):
    """The bytes of an environment block: NAME=value strings, an empty string to end them, the program's name.

    After the strings come a word with the number of strings that follow (always 1), and the full name of
    the program, which is how a program learns where it was started from.
    """
    strings = [b"COMSPEC=C:\\COMMAND.COM", b"PATH=C:\\"]
    name = b"C:\\" + os.path.basename(program_path).upper().encode("ascii", "replace")
    return b"\0".join(strings) + b"\0\0" + (1).to_bytes(2, "little") + name + b"\0"


def build(cpu, psp_seg, env_seg, top_seg, program_path):
    """Write the PSP at psp_seg:0000 and the environment block at env_seg:0000.

    `top_seg` is the segment just above the memory the program may use; it goes to PSP:0002, and the
    program works out from it how much room it has.
    """
    psp = bytearray(256)
    psp[0x00:0x02] = b"\xCD\x20"  # INT 20h: the old way to end a program
    psp[0x02:0x04] = top_seg.to_bytes(2, "little")
    # the file handles: standard input, output and error all go to the console, auxiliary and printer to
    # their devices; 0FFh marks a handle that is not open
    psp[JFT_OFFSET:JFT_OFFSET + JFT_SIZE] = bytes([1, 1, 1, 0, 2]) + b"\xFF" * (JFT_SIZE - 5)
    psp[0x2C:0x2E] = env_seg.to_bytes(2, "little")
    psp[0x32:0x34] = JFT_SIZE.to_bytes(2, "little")  # how many handles fit in the table
    psp[0x34:0x38] = JFT_OFFSET.to_bytes(2, "little") + psp_seg.to_bytes(2, "little")  # where the table is
    psp[0x50:0x53] = b"\xCD\x21\xCB"  # INT 21h, RETF: a program can call this to reach DOS
    for fcb in (0x5C, 0x6C):  # the two file control blocks made from the first two words of the command line
        psp[fcb] = 0  # the current drive
        psp[fcb + 1:fcb + 12] = b" " * 11  # a blank file name
    psp[0x80:0x82] = b"\x00\x0D"  # the command line: no characters, and the carriage return that ends it
    cpu.mem[psp_seg * 16:psp_seg * 16 + 256] = psp
    env = environment(program_path)
    cpu.mem[env_seg * 16:env_seg * 16 + len(env)] = env
