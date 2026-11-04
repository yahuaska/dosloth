import tomllib
import unittest
from pathlib import Path

from dosloth import config, dos, psp, sysvars
from dosloth.cpu import CPU

# The routine of install.exe that adds up the entries of the system file table (from 19B7:01E5):
#   push es ; push bx ; mov bx, FFFFh ; mov ah, 52h ; int 21h ; cmp bx, -1 ; je failed
#   les bx, es:[bx+4] ; xor ax, ax
#   again: cmp bx, -1 ; je done ; add ax, es:[bx+4] ; les bx, es:[bx] ; jmp again
#   done: pop bx ; pop es ; clc ; retf        failed: mov ax, FFFFh ; retf
INSTALLER_ROUTINE = bytes.fromhex("06 53 bb ff ff b4 52 cd 21 83 fb ff 74 18 26 c4 5f 04 33 c0 83 fb ff 74 09 "
                                  "26 03 47 04 26 c4 1f eb f2 5b 07 f8 cb")


def machine(text=None):
    cpu = CPU()
    volumes = None
    if text is not None:
        from dosloth.volumes import Volumes
        volumes = Volumes(config.parse(tomllib.loads(text), Path(".")))
    state = dos.install(cpu, volumes)
    return cpu, state


def word(cpu, offset):
    return cpu.rw(sysvars.SEGMENT, offset)


def long(cpu, offset):
    return word(cpu, offset) | word(cpu, offset + 2) << 16


class TestWhereTheTablesAre(unittest.TestCase):
    def test_they_lie_in_memory_that_nothing_else_uses(self):
        end_of_sft = sysvars.SFT_OFFSET + 6 + sysvars.FILES * sysvars.SFT_ENTRY_SIZE
        start, end = sysvars.SEGMENT * 16, sysvars.SEGMENT * 16 + end_of_sft
        self.assertGreaterEqual(start, 0x500)  # past the vectors and the data area of the BIOS (0 to 4FFh)
        self.assertLessEqual(end, sysvars.FIRST_MCB * 16)  # and short of the control block
        self.assertEqual(sysvars.FIRST_MCB * 16 + 16, psp.ENV_SEG * 16)  # the paragraph before the environment
        self.assertLessEqual(psp.ENV_SEG * 16 + 256, psp.PSP_SEG * 16)

    def test_the_list_and_the_table_do_not_overlap(self):
        self.assertLessEqual(sysvars.LIST_OFFSET + 0x35, sysvars.SFT_OFFSET)

    def test_the_constants(self):
        self.assertEqual((sysvars.SEGMENT, sysvars.LIST_OFFSET, sysvars.SFT_OFFSET, sysvars.FILES,
                          sysvars.SFT_ENTRY_SIZE), (0x0070, 2, 0x0100, 60, 0x3B))
        self.assertEqual(sysvars.FIRST_MCB, 0x0FEF)


class TestGetListOfLists(unittest.TestCase):
    def call(self, cpu):
        cpu.s[CPU.CS] = 0x1000
        cpu.mem[0x10000:0x10002] = bytes([0xCD, 0x21])
        cpu.r[CPU.AX], cpu.r[CPU.BX], cpu.r[CPU.CX], cpu.r[CPU.DX] = 0x5204, 0xFFFF, 0x2222, 0x3333
        cpu.r[CPU.SI], cpu.r[CPU.DI] = 0x4444, 0x5555
        cpu.s[CPU.DS], cpu.s[CPU.ES] = 0x2000, 0x3000
        cpu.ip = 0
        cpu.step()

    def test_es_bx_point_at_the_list(self):
        cpu, _ = machine()
        self.call(cpu)
        self.assertEqual((cpu.s[CPU.ES], cpu.r[CPU.BX]), (0x0070, 0x0002))

    def test_nothing_else_changes_and_the_flags_stay(self):
        cpu, _ = machine()
        cpu.flags = 0x0FD7
        self.call(cpu)
        self.assertEqual((cpu.r[CPU.AX], cpu.r[CPU.CX], cpu.r[CPU.DX], cpu.r[CPU.SI], cpu.r[CPU.DI], cpu.s[CPU.DS]),
                         (0x5204, 0x2222, 0x3333, 0x4444, 0x5555, 0x2000))
        self.assertEqual((cpu.flags, cpu.s[CPU.CS], cpu.ip), (0x0FD7, 0x1000, 2))

    def test_bx_is_no_longer_ffff_which_is_how_a_program_tells_that_the_function_is_there(self):
        cpu, _ = machine()
        self.call(cpu)
        self.assertNotEqual(cpu.r[CPU.BX], 0xFFFF)

    def test_the_routine_of_the_installer_counts_the_files_there_can_be(self):
        cpu, _ = machine()
        cpu.s[CPU.CS], cpu.s[CPU.SS], cpu.r[CPU.SP] = 0x1000, 0x4000, 0x0200
        cpu.mem[0x10100:0x10100 + len(INSTALLER_ROUTINE)] = INSTALLER_ROUTINE
        cpu.mem[0x10200] = 0xF4  # hlt: where the routine returns to
        cpu.ww(0x4000, 0x0200, 0x0200)  # the far return address: 1000:0200
        cpu.ww(0x4000, 0x0202, 0x1000)
        cpu.r[CPU.SP] = 0x0200
        cpu.ip = 0x0100
        cpu.r[CPU.BX], cpu.s[CPU.ES] = 0x1234, 0x5678
        for _ in range(200):
            if (cpu.s[CPU.CS], cpu.ip) == (0x1000, 0x0200):
                break
            cpu.step()
        self.assertEqual((cpu.s[CPU.CS], cpu.ip), (0x1000, 0x0200))
        self.assertEqual(cpu.r[CPU.AX], 60)
        self.assertFalse(cpu.get_flag(0x0001))
        self.assertEqual((cpu.r[CPU.BX], cpu.s[CPU.ES]), (0x1234, 0x5678))  # the routine put back what it pushed


class TestTheFields(unittest.TestCase):
    def setUp(self):
        self.cpu, _ = machine()
        self.base = sysvars.LIST_OFFSET

    def test_the_first_memory_control_block_is_the_word_before_the_list(self):
        self.assertEqual(word(self.cpu, self.base - 2), 0x0FEF)

    def test_the_block_is_the_last_one_owned_by_the_program_and_goes_to_the_top_of_memory(self):
        mcb = bytes(self.cpu.mem[0x0FEF * 16:0x0FEF * 16 + 16])
        self.assertEqual(mcb[0:1], b"Z")
        self.assertEqual(int.from_bytes(mcb[1:3], "little"), 0x1000)
        self.assertEqual(int.from_bytes(mcb[3:5], "little"), 0xA000 - 0x0FF0)
        self.assertEqual(mcb[5:], bytes(11))

    def test_the_pointers_to_what_we_have_not_are_ffff_ffff(self):
        for offset in (0x00, 0x08, 0x0C, 0x12, 0x16, 0x1A):
            self.assertEqual(long(self.cpu, self.base + offset), 0xFFFFFFFF, hex(offset))

    def test_the_system_file_table_is_where_the_list_says(self):
        self.assertEqual(long(self.cpu, self.base + 4), 0x0070 << 16 | sysvars.SFT_OFFSET)

    def test_the_sector_size_and_the_protected_fcbs(self):
        self.assertEqual((word(self.cpu, self.base + 0x10), word(self.cpu, self.base + 0x1E)), (512, 0))

    def test_the_join_count_is_0(self):
        self.assertEqual(self.cpu.mem[0x700 + self.base + 0x34], 0)

    def test_the_nul_device_heads_the_chain_of_devices(self):
        header = bytes(self.cpu.mem[0x700 + self.base + 0x22:0x700 + self.base + 0x22 + 18])
        self.assertEqual(header[0:4], b"\xFF\xFF\xFF\xFF")  # no device after it
        self.assertEqual(header[4:6], b"\x04\x80")  # a character device, and the NUL one
        self.assertEqual(header[6:10], bytes(4))
        self.assertEqual(header[10:], b"NUL     ")


class TestTheSystemFileTable(unittest.TestCase):
    def test_one_table_that_ends_the_chain_and_holds_60_free_entries(self):
        cpu, _ = machine()
        self.assertEqual(long(cpu, sysvars.SFT_OFFSET), 0xFFFFFFFF)
        self.assertEqual(word(cpu, sysvars.SFT_OFFSET + 4), 60)
        start = 0x700 + sysvars.SFT_OFFSET + 6
        self.assertEqual(bytes(cpu.mem[start:start + 60 * 0x3B]), bytes(60 * 0x3B))

    def test_nothing_is_written_after_the_last_entry(self):
        cpu = CPU()
        end = 0x700 + sysvars.SFT_OFFSET + 6 + 60 * 0x3B
        cpu.mem[end:end + 16] = b"\xA5" * 16
        dos.install(cpu)
        self.assertEqual(bytes(cpu.mem[end:end + 16]), b"\xA5" * 16)

    def test_what_is_there_before_is_overwritten_with_zeros(self):
        cpu = CPU()
        start = 0x700 + sysvars.SFT_OFFSET + 6
        cpu.mem[start:start + 60 * 0x3B] = b"\xFF" * (60 * 0x3B)
        dos.install(cpu)
        self.assertEqual(bytes(cpu.mem[start:start + 60 * 0x3B]), bytes(60 * 0x3B))


class TestWhatDependsOnTheMachine(unittest.TestCase):
    def machine_with(self, drives, types=None):
        text = "".join(f"[disks.{letter}]\ntype = '{types.get(letter, 'floppy') if types else 'floppy'}'\n"
                       for letter in drives)
        text += "[mounts]\n" + "".join(f"{letter} = {{ type = 'tmpfs' }}\n" for letter in drives
                                        if (types or {}).get(letter, "floppy") != "cdrom")
        return machine(text)[0]

    def test_lastdrive_is_5_for_a_small_machine_and_more_for_a_big_one(self):
        self.assertEqual(machine()[0].mem[0x700 + 2 + 0x21], 5)  # A: to D: and the least that DOS has
        self.assertEqual(self.machine_with("A").mem[0x700 + 0x23], 5)
        self.assertEqual(self.machine_with("ABCDEFGH").mem[0x700 + 0x23], 8)
        self.assertEqual(self.machine_with("ABCDE").mem[0x700 + 0x23], 5)
        self.assertEqual(self.machine_with("ABCDEF").mem[0x700 + 0x23], 6)
        self.assertEqual(self.machine_with("AZ").mem[0x700 + 0x23], 26)

    def test_the_block_devices_are_the_floppy_and_hard_disk_drives(self):
        self.assertEqual(machine()[0].mem[0x700 + 2 + 0x20], 3)  # A: B: C:, and D: is a CD-ROM
        cpu = self.machine_with("ABCDEF", {"C": "hdd", "D": "cdrom", "E": "cdrom", "F": "hdd"})
        self.assertEqual(cpu.mem[0x700 + 2 + 0x20], 4)
        cpu = self.machine_with("AD", {"D": "cdrom"})
        self.assertEqual(cpu.mem[0x700 + 2 + 0x20], 1)

    def test_each_machine_has_its_tables_in_its_own_memory(self):
        one, two = machine()[0], machine()[0]
        one.mem[0x700 + sysvars.SFT_OFFSET + 4] = 7
        self.assertEqual(word(two, sysvars.SFT_OFFSET + 4), 60)


if __name__ == "__main__":
    unittest.main()
