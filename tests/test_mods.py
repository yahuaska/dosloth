import random
import struct
import unittest
from pathlib import Path

from dosloth import loader, mods, patches, psp, rom
from dosloth.cpu import CPU
from dosloth.mods import ufo_developer_mapper as mapper
from dosloth.mods import ufo_scenario_button as button
from dosloth.patches import PATCH_BYTES
from dosloth.xmem import ExtendedMemory

UFO2P = Path(__file__).resolve().parent.parent / "XCOMA" / "UFOEXE" / "UFO2P.EXE"
SCREEN_AT, COPY_AT = 0x1700000, 0x1800000  # where the test puts the picture and its copy
STACK = 0x1900000


def ufo_machine(asked=()):
    cpu = CPU()
    rom.install_iret(cpu)  # the ROM has the places the calls of the mod return to
    cpu.mods = frozenset(asked)
    psp.build(cpu, 0x1000, 0x0FF0, 0x9000, "C:\\UFO2P.EXE")
    loader.load_le(cpu, UFO2P.read_bytes(), 0x1000, ExtendedMemory())
    patches.install(cpu, patches.PATCHES + mods.every_patch())
    return cpu


class TestTheRegistry(unittest.TestCase):
    def test_the_mods_are_found_by_their_names(self):
        self.assertIn("scenario_button", mods.MODS)
        self.assertEqual(mods.MODS["scenario_button"], button.MODS["scenario_button"])

    def test_a_machine_without_mods_asks_for_none(self):
        self.assertEqual(mods.asked_for(CPU()), [])


@unittest.skipUnless(UFO2P.exists(), "the game is not here")
class TestTheScenarioButton(unittest.TestCase):
    def test_the_mod_is_made_only_when_it_is_asked_for(self):
        self.assertNotEqual(bytes(ufo_machine().mem[button.SITE:button.SITE + 2]), PATCH_BYTES)
        self.assertEqual(bytes(ufo_machine({"scenario_button"}).mem[button.SITE:button.SITE + 2]), PATCH_BYTES)

    def test_the_bar_the_text_and_the_button_and_the_registers_as_they_were(self):
        cpu = ufo_machine({"scenario_button"})
        cpu.mem[button.DRAW_TEXT_CENTRED:button.DRAW_TEXT_CENTRED + 3] = bytes([0xC2, 0x04, 0x00])  # ret 4
        cpu.mem[button.ADD_BUTTON:button.ADD_BUTTON + 3] = bytes([0xC2, 0x14, 0x00])  # ret 14h
        rng = random.Random(1)
        for at, pointer in ((SCREEN_AT, button.SCREEN), (COPY_AT, button.SCREEN_COPY)):
            cpu.mem[at:at + 640 * 480] = bytes(rng.randrange(256) for _ in range(640 * 480))
            struct.pack_into("<I", cpu.mem, pointer, at)
        before_copy = bytes(cpu.mem[COPY_AT:COPY_AT + 640 * 480])
        for register in range(8):
            cpu.set32(register, rng.randrange(1 << 32))
        cpu.set32(CPU.SP, STACK)
        cpu.flags = 0x0002 | 0x0001 | 0x0040 | 0x0200  # CF, ZF, IF
        struct.pack_into("<H", cpu.mem, button.CENTRE, 0)  # as the text of Quit has left it
        cpu.ip = button.SITE
        before = (list(cpu.r), list(cpu.rh), cpu.flags)

        self.run_to(cpu, button.DRAW_TEXT_CENTRED)
        text_at = cpu.wide(CPU.AX)
        self.assertEqual(bytes(cpu.mem[text_at:text_at + 19]), b"Scenario Generator\0")
        self.assertEqual((cpu.wide(CPU.DX), cpu.wide(CPU.BX), cpu.wide(CPU.CX), cpu.wide(CPU.SI)), (1, 320, 292, 320))
        self.assertEqual(struct.unpack_from("<H", cpu.mem, button.CENTRE)[0], 320)
        self.assertEqual((cpu.rd(cpu.s[CPU.SS], cpu.wide(CPU.SP)), cpu.rd(cpu.s[CPU.SS], cpu.wide(CPU.SP) + 4)),
                         (button.ROM_DRAWN, 0))

        self.run_to(cpu, button.ADD_BUTTON)
        self.assertEqual((cpu.wide(CPU.AX), cpu.wide(CPU.DX), cpu.wide(CPU.BX), cpu.wide(CPU.CX)), (188, 287, 447, 314))
        stack = [cpu.rd(cpu.s[CPU.SS], cpu.wide(CPU.SP) + 4 * i) for i in range(6)]
        self.assertEqual(stack, [button.ROM_ADDED, 0, 0, 0, button.SCENARIO_GENERATOR, 0])  # as the menu calls it

        self.run_to(cpu, button.AFTER_SITE)
        r, rh, flags = before
        r[CPU.AX], rh[CPU.AX] = 0x0998, 0x0026  # mov eax, 00260998
        self.assertEqual((list(cpu.r), list(cpu.rh), cpu.flags), (r, rh, flags))
        for at, original in ((SCREEN_AT, None), (COPY_AT, before_copy)):
            for y in range(249, 277):  # the bar as the copy has it, without the text that the screen has on it
                bar = before_copy[y * 640 + 188:y * 640 + 448]
                self.assertEqual(bytes(cpu.mem[at + (y + 38) * 640 + 188:at + (y + 38) * 640 + 448]), bar)
            if original is not None:  # beside the bar the picture is as it was
                row = 300 * 640
                self.assertEqual(bytes(cpu.mem[at + row:at + row + 188]), original[row:row + 188])
                self.assertEqual(bytes(cpu.mem[at + row + 448:at + row + 640]), original[row + 448:row + 640])

    @staticmethod
    def run_to(cpu, address, limit=100):
        for _ in range(limit):
            cpu.step()
            if cpu.ip == address:
                return
        raise AssertionError(f"did not get to {address:X}")


@unittest.skipUnless(UFO2P.exists(), "the game is not here")
class TestTheDeveloperMapper(unittest.TestCase):
    def at_the_check(self, asked, flag=0):
        cpu = ufo_machine(asked)
        cpu.mem[mapper.FLAG] = flag
        cpu.set32(CPU.SP, STACK)
        cpu.flags = 0x0002 | 0x0040 | 0x0001  # ZF and CF, which the cmp has to change
        cpu.ip = mapper.SITE
        cpu.step()
        return cpu

    def test_the_mod_is_made_only_when_it_is_asked_for(self):
        self.assertNotEqual(bytes(ufo_machine().mem[mapper.SITE:mapper.SITE + 2]), PATCH_BYTES)
        self.assertEqual(bytes(ufo_machine({"developer_mapper"}).mem[mapper.SITE:mapper.SITE + 2]), PATCH_BYTES)

    def test_the_flag_is_1_and_the_code_goes_on_as_the_cmp_of_1_leaves_it(self):
        modded = self.at_the_check({"developer_mapper"})
        original = self.at_the_check((), flag=1)  # the game's own cmp, with the byte at 1
        self.assertEqual(modded.mem[mapper.FLAG], 1)
        self.assertEqual((modded.ip, hex(modded.flags)), (original.ip, hex(original.flags)))
        modded.step()  # je: not taken, to the mapper
        self.assertEqual(modded.ip, 0x1001C3)

    def test_without_the_mod_the_game_goes_its_way(self):
        cpu = self.at_the_check(())
        cpu.step()
        self.assertEqual(cpu.ip, 0x1001DE)
