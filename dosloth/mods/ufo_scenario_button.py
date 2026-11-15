"""UFO2P.EXE (X-COM: Apocalypse): a fourth button in the main menu, "Scenario Generator", under Quit.

The game has the generator of scenarios (a tactical battle of one's own making) and its button in the menu: the
handler 1A9FE0 (fade_out, scenario_generator, [0x2292EC] = 3) and the text 3 of the group 29h. But the code of the
menu (000F:1AA010) never adds that button. This mod does, where the menu has added its other buttons and has not yet
shown the screen (at 1AA19A: `mov eax, 00260998`, before `call set_palette`, `call show_screen`):

1. at 1AA19A: the registers and the flags go on the stack of the game; the grey bar of Quit (188..447 x 249..276,
   a part of the picture b-setup.pcx) is copied 38 lines lower, where the buttons would go on, from the copy of the
   screen (which has no texts on it, and the buttons take their background from) to it and to the screen; then the
   word [1C4D14] is 320 (the menu sets it before each text: draw_text_centred centres by it and leaves it otherwise)
   and draw_text_centred(text, 320, 292) is called, as the menu calls it for the other buttons, with the return to
   the ROM, F000:0040
2. at F000:0040: add_button((188, 287, 447, 314), key 0 (none: the mouse only), 1A9FE0), with the return to F000:0042
3. at F000:0042: the registers and the flags back from the stack, EAX as the instruction that the INT stands for
   leaves it, and the code goes on with the call of set_palette.

The registers of the game are the same after the mod as before it but for what the two calls change in memory.
"""
import hashlib
import struct

from ..cpu import CPU
from ..patches._base import PATCH_BYTES, Patch
from ..rom import PATCH_RETURNS_AT

SITE = 0x1AA19A  # mov eax, 00260998 (5 bytes), then call set_palette, call show_screen
AFTER_SITE = 0x1AA19F
PALETTE = 0x00260998  # what the instruction at SITE puts in EAX
DRAW_TEXT_CENTRED = 0x117020  # (EAX text, EDX 1, EBX x, ECX y, ESI x; one dword on the stack, 0); ret 4
ADD_BUTTON = 0x11E600  # (EAX x0, EDX y0, EBX x1, ECX y1; stack: 0, 0, key, handler, 0 from the top); ret 14h
SCENARIO_GENERATOR = 0x1A9FE0  # the handler of the button
SCREEN, SCREEN_COPY = 0x272DEC, 0x272CB0  # the pointers to the 640x480 picture and to its copy
CENTRE = 0x1C4D14  # a word: where draw_text_centred centres a text
TEXTS = 0x1E3794  # the pointers to the groups of texts: a group is its strings one after another
MENU_TEXTS, SCENARIO_TEXT = 0x29, 3  # "Scenario Generator"
WIDTH = 640
LEFT, RIGHT = 188, 447
QUIT_TOP, QUIT_BOTTOM = 249, 276
STEP = 38  # from one button to the next
TOP, BOTTOM = QUIT_TOP + STEP, QUIT_BOTTOM + STEP
TEXT_X, TEXT_Y = 320, 254 + STEP  # the text of Quit is at y 254
ROM_DRAWN, ROM_ADDED = (segment * 16 + offset for segment, offset in PATCH_RETURNS_AT)


def text(cpu, group, number):
    """The address of string `number` of the group, as get_text (000F:153800) finds it."""
    mem = cpu.mem
    at = struct.unpack_from("<I", mem, TEXTS + 4 * group)[0]
    for _ in range(number):
        end = mem.find(b"\0", at)
        if end < 0:
            raise NotImplementedError(f"the texts of the group {group:X}h have no end")
        at = end + 1
    return at


def copy_bar(cpu):
    """The bar of Quit as the copy of the screen has it, with no text, under it in the copy and in the screen."""
    mem = cpu.mem
    copy, screen = (struct.unpack_from("<I", mem, pointer)[0] for pointer in (SCREEN_COPY, SCREEN))
    for y in range(QUIT_TOP, QUIT_BOTTOM + 1):
        row = mem[copy + y * WIDTH + LEFT:copy + y * WIDTH + RIGHT + 1]
        for base in (copy, screen):
            target = base + (y + STEP) * WIDTH + LEFT
            mem[target:target + len(row)] = row


def call(cpu, procedure, back, *stack):
    """Push the arguments on the stack (the first of `stack` first), and the return address, and go to `procedure`."""
    for value in stack:
        cpu.push32(value)
    cpu.push32(back)
    cpu.ip = procedure


def at_the_menu(cpu):
    for register in (CPU.AX, CPU.CX, CPU.DX, CPU.BX, CPU.SP, CPU.BP, CPU.SI, CPU.DI):
        cpu.push32(cpu.wide(register))
    cpu.push32(cpu.flags | cpu.eflags_high << 16)
    copy_bar(cpu)
    struct.pack_into("<H", cpu.mem, CENTRE, TEXT_X)
    for register, value in ((CPU.AX, text(cpu, MENU_TEXTS, SCENARIO_TEXT)), (CPU.DX, 1), (CPU.BX, TEXT_X),
                            (CPU.CX, TEXT_Y), (CPU.SI, TEXT_X)):
        cpu.set32(register, value)
    call(cpu, DRAW_TEXT_CENTRED, ROM_DRAWN, 0)


def text_drawn(cpu):
    for register, value in ((CPU.AX, LEFT), (CPU.DX, TOP), (CPU.BX, RIGHT), (CPU.CX, BOTTOM)):
        cpu.set32(register, value)
    call(cpu, ADD_BUTTON, ROM_ADDED, 0, SCENARIO_GENERATOR, 0, 0, 0)


def button_added(cpu):
    saved_flags = cpu.pop32()
    cpu.flags, cpu.eflags_high = saved_flags & 0xFFFF, saved_flags >> 16
    values = [cpu.pop32() for _ in range(8)]
    for register, value in zip((CPU.DI, CPU.SI, CPU.BP, CPU.SP, CPU.BX, CPU.DX, CPU.CX, CPU.AX), values):
        if register != CPU.SP:  # as POPAD: ESP is where the pops leave it
            cpu.set32(register, value)
    cpu.set32(CPU.AX, PALETTE)  # mov eax, 00260998
    cpu.ip = AFTER_SITE


ROM_STUB = hashlib.sha256(PATCH_BYTES).hexdigest()  # what is at the places of the ROM: the INT itself


MODS = {"scenario_button": [
    Patch("UFO2P.EXE: the button of the scenario generator in the main menu", SITE, 0x0F,
          "4c7bdd72c587249fec49b316960df69c014554388a53652e4a35fffe90bf3a4d", at_the_menu,
          also=((0x1AA010, 0x18A, "63bd9df691fcbd7de58b0248f0dfa3ed23a8cab85e05ed33258c02c82e79c761"),  # the menu
                (ADD_BUTTON, 0x37, "8ccb44d842eb513bc49c82382892481eb177b3fb825d01f97d828db017d04709"),
                (DRAW_TEXT_CENTRED, 0x8D8, "7c229cef05700bb9c87db5f561badf301c7e307d6db6a4208b8acf1f7fcf1d84"),
                (SCENARIO_GENERATOR, 0x1D, "bd7aa71a0a37707366b90b1d73357d55a95792708349cf966c82c03ffa629bd5"))),
    Patch("the scenario button: the text is drawn", ROM_DRAWN, len(PATCH_BYTES), ROM_STUB, text_drawn),
    Patch("the scenario button: the button is added", ROM_ADDED, len(PATCH_BYTES), ROM_STUB, button_added),
]}
