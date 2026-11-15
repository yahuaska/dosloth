"""The drawing of a sprite in UFO2P.EXE (the game), the way of a sprite that is all inside the clip rectangle: the
loop at 16B37C of the procedure at 16B2E7, where the city and the mapper of the developers spend half of their time.

When the procedure has checked that the sprite (a picture of the kind 1, in the file of the pictures, as .PCK) is
all inside, EBX is the place of its corner in the screen, `screen + y * 640 + x`, and ESI is at its records:

    dword   where the record goes, from the corner (FFFFFFFFh: the end of the picture)
    3 bytes (not looked at), 1 byte N
    N dwords of pixels, all put (rep movsd)
    3 bytes of pixels, each put unless it is 0 (the colour that is seen through)
    1 byte (not looked at)

The code ends at 16BB13, which takes back all the registers (popad) but the flags: those of the `cmp edx, -1` of the
end of the picture are what is left of the loop, with the memory and the count of instructions. The patch does the
loop and goes to 16BB13 with them; the registers are not made as the loop leaves them, for the popad takes them back.
A record that would put pixels into the window of the video card, or out of the memory, or over the pixels it copies,
stops the emulator: a sprite that is all inside goes into the screen, which is memory of the program.
"""
from ..cpu import CPU
from ._base import Patch
from ._common import DWORD, MASK

LOOP_AT = 0x16B37C  # mov edx, [esi]: the top of the loop of the records
END_AT = 0x16BB13  # mov ebp, [001E5AA3]; cld; popad; pops; leave; ret
END = 0xFFFFFFFF
RECORD = 4 + 6 + 2 * 4 + 3 + 2  # mov, add, cmp, je; mov, add, add, movzx, inc, jcxz; 2 x (lodsb, cmp, je, inc edi);
# lodsb, cmp, je (the third pixel: no inc edi after it); inc esi, jmp
COPY = 1  # rep movsd, when N is not 0
PUT = 1  # mov [edi], al: a pixel of the three that is not 0
LAST = 4  # mov, add, cmp, je: the end of the picture


def draw_sprite(cpu):
    if cpu.bases[cpu.s[CPU.DS]] or cpu.bases[cpu.s[CPU.ES]]:
        raise NotImplementedError("the drawing of a sprite with a DS or ES that has a base")
    mem, size = cpu.mem, len(cpu.mem)
    video_start, video_end = cpu.video_start, cpu.video_end
    esi, corner = cpu.wide(CPU.SI), cpu.wide(CPU.BX)
    executed = LAST
    while True:
        offset = DWORD.unpack_from(mem, esi)[0]
        esi += 4
        if offset == END:
            break
        edi = corner + offset & MASK
        count = mem[esi + 3] * 4
        esi += 4
        end = edi + count + 3
        if end > size or edi < video_end and end > video_start:
            raise NotImplementedError(f"a sprite put at {edi:X}..{end:X}, which is not memory of the program")
        if count:
            if esi < edi < esi + count:  # rep movsd would copy what it has just put
                raise NotImplementedError(f"a sprite whose pixels at {esi:X} overlap where they go, {edi:X}")
            mem[edi:edi + count] = mem[esi:esi + count]
            esi += count
            edi += count
            executed += COPY
        for pixel in mem[esi:esi + 3]:
            if pixel:
                mem[edi] = pixel
                executed += PUT
            edi += 1
        esi += 4
        executed += RECORD
    cpu.sub_with_flags(END, END, 32)  # cmp edx, -1 with the end
    cpu.instructions += executed - 1  # (the INT stands for the first one)
    cpu.ip = END_AT


PATCHES = [Patch("the drawing of a sprite of UFO2P.EXE", LOOP_AT, 0x37,
                 "95601db5dff59769661a5dd94024d55a5abdf90c3d12faf22ac1b0ef84fcb4c0", draw_sprite)]
