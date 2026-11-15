"""UFO2P.EXE (X-COM: Apocalypse): the developers' mode, the byte [0x1D1324] at 1.

`main` (000F:100010) looks at the byte once, and nothing in the game writes it, so it is 0:

    1001BA  cmp byte [001D1324], 00
    1001C1  je 1001DE                  ; 0: the game
    1001C3  call load_city_map         ; 1: the mapper of the city of the developers,
    1001C8  call city_mapper           ;    and when it is left
    1001CD  call 103890
    1001D2  call 10879C
    1001D7  xor eax, eax
    1001D9  call exit                  ;    the program ends with the code 0 (for XCOMAPOC.EXE: the game is over)

The mod is where the byte is looked at: it puts 1 in it and sets the flags as `cmp byte [001D1324], 00` sets them
with 1 there, and the code goes on with the `je`.
"""
from ..patches._base import Patch

SITE = 0x1001BA  # cmp byte [001D1324], 00 (7 bytes)
AFTER_SITE = 0x1001C1
FLAG = 0x1D1324


def developer_mode(cpu):
    cpu.mem[FLAG] = 1
    cpu.sub_with_flags(cpu.mem[FLAG], 0, 8)  # the flags of the cmp
    cpu.ip = AFTER_SITE


MODS = {"developer_mapper": [
    Patch("UFO2P.EXE: the developers' mode (the mapper of the city)", SITE, 0x24,  # up to the game's way at 1001DE
          "4c05c6d3a20977ffedb708d6274969e40c1fe187f3f820276d3ab94c7336f0ba", developer_mode),
]}
