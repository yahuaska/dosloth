"""The flat memory of a 32-bit program: a segment register holds a selector, and a selector has a base.

In real mode the address of seg:off is seg * 16 + off, cut to 1 MB. In the protected mode of a 386 it is the base of
the segment that the selector names, plus the offset, in 32 bits: a 32-bit program of DOS/4G has selectors with base
0 for its code and its data, so the offset is the address. We have no descriptor tables in memory: a dictionary `bases`
says what the base of each selector is, and a selector that is not in it stops the emulator at once.

`CPU.use_flat_memory(bases)` puts the methods of this class in place of those of real mode (the reads and writes of
memory, the fetch of the program and step()), `use_real_memory()` takes them out again. `enter_flat(bases)` does that
and makes the pointers and the sizes of 32 bits, as a 32-bit program of DOS/4G has them; `leave_flat()` ends it. A
program is in one mode or the other from its start to its end, so this is done when it starts, and real mode pays
nothing for it.

Memory is the mmap of the machine, which is 256 MB: an address beyond it stops the emulator (an IndexError for a byte,
a struct.error for a word or a doubleword).
"""
import struct

from .opcodes import ADDRESS_SIZED, PREFIXES

U16 = struct.Struct("<H")
U32 = struct.Struct("<I")
# the methods that are put in place of those of real mode
FLAT_NAMES = ("rb", "rw", "rd", "wb", "ww", "wd", "addr", "fetch8", "fetch16", "fetch32", "step")


class Bases(dict):
    """Selector -> base address (linear). A selector that is not there is an error, said in plain words."""

    def __missing__(self, selector):
        raise NotImplementedError(f"the selector {selector:04X} is not in the table of selectors")


class RealModeBases:
    """The bases of real mode: a selector is a paragraph. With it the flat memory acts as the memory of real mode but
    for the wrap at 1 MB, and the tests of the instructions can be run on it as they are."""

    def __getitem__(self, selector):
        return selector * 16


# the mode of real mode, as get_mode() says it: what a new machine has, and what a program of real mode starts with
REAL_MODE = {"flat": False, "bases": {}, "ip_mask": 0xFFFF, "stack32": False, "code32": False}


class FlatMemory:
    """The part of the CPU that is the memory of the flat mode. The methods have the names of those of real mode, with
    `_flat` after them, and are put in their place by use_flat_memory()."""

    def use_flat_memory(self, bases):
        """From now on the segment registers hold selectors, and `bases` (selector -> base) tells where they point."""
        self.bases = bases
        self.flat = True
        for name in FLAT_NAMES:
            setattr(self, name, getattr(self, name + "_flat"))

    def enter_flat(self, bases):
        """Run a 32-bit program of the flat model from now on: selectors with bases, an instruction pointer and a stack
        pointer of 32 bits, and code whose sizes are of 32 bits (the prefixes 66h and 67h make them 16)."""
        self.use_flat_memory(bases)
        self.ip_mask, self.stack32, self.code32 = 0xFFFFFFFF, True, True

    def leave_flat(self):
        """Back to real mode, with 16 bits of everything."""
        self.set_mode(REAL_MODE)

    def use_real_memory(self):
        """Back to the memory of real mode, with its methods."""
        self.flat = False
        for name in FLAT_NAMES:
            self.__dict__.pop(name, None)

    def get_mode(self):
        """What makes the machine run in real mode or in the flat one, as plain data (to be put back by set_mode)."""
        return {"flat": self.flat, "bases": dict(self.bases) if self.flat else {}, "ip_mask": self.ip_mask,
                "stack32": self.stack32, "code32": self.code32}

    def set_mode(self, mode):
        """Put the machine in the mode that get_mode() described."""
        self.ip_mask, self.stack32, self.code32 = mode["ip_mask"], mode["stack32"], mode["code32"]
        if mode["flat"]:
            self.use_flat_memory(Bases(mode["bases"]))
        else:
            self.use_real_memory()

    # --- memory ---
    def addr_flat(self, seg, off):
        """The linear address of seg:off."""
        return (self.bases[seg] + off) & 0xFFFFFFFF

    def rb_flat(self, seg, off):
        """Read one byte of memory."""
        address = (self.bases[seg] + off) & 0xFFFFFFFF
        if self.video_start <= address < self.video_end:
            return self.video.read_memory(address - self.video_start)
        return self.mem[address]

    def wb_flat(self, seg, off, val):
        """Write one byte of memory."""
        address = (self.bases[seg] + off) & 0xFFFFFFFF
        if self.video_start <= address < self.video_end:
            self.video.write_memory(address - self.video_start, val & 0xFF)
        else:
            self.mem[address] = val & 0xFF

    def rw_flat(self, seg, off):
        """Read a word, low byte first."""
        address = (self.bases[seg] + off) & 0xFFFFFFFF
        if address < self.video_end and address + 2 > self.video_start:  # the window of the video card is in it
            return self.rb_flat(seg, off) | self.rb_flat(seg, off + 1) << 8
        return U16.unpack_from(self.mem, address)[0]

    def ww_flat(self, seg, off, val):
        """Write a word, low byte first."""
        address = (self.bases[seg] + off) & 0xFFFFFFFF
        if address < self.video_end and address + 2 > self.video_start:
            self.wb_flat(seg, off, val)
            self.wb_flat(seg, off + 1, val >> 8)
        else:
            U16.pack_into(self.mem, address, val & 0xFFFF)

    def rd_flat(self, seg, off):
        """Read a doubleword, low byte first."""
        address = (self.bases[seg] + off) & 0xFFFFFFFF
        if address < self.video_end and address + 4 > self.video_start:
            return self.rw_flat(seg, off) | self.rw_flat(seg, off + 2) << 16
        return U32.unpack_from(self.mem, address)[0]

    def wd_flat(self, seg, off, val):
        """Write a doubleword, low byte first."""
        address = (self.bases[seg] + off) & 0xFFFFFFFF
        if address < self.video_end and address + 4 > self.video_start:
            self.ww_flat(seg, off, val)
            self.ww_flat(seg, off + 2, val >> 16)
        else:
            U32.pack_into(self.mem, address, val & 0xFFFFFFFF)

    # --- the program ---
    def fetch8_flat(self):
        """Read the next byte of the program (at CS:EIP) and move IP past it."""
        ip = self.ip
        address = (self.bases[self.s[self.CS]] + ip) & 0xFFFFFFFF
        self.ip = (ip + 1) & self.ip_mask
        if self.video_start <= address < self.video_end:
            return self.video.read_memory(address - self.video_start)
        return self.mem[address]

    def fetch16_flat(self):
        """Read the next little-endian word of the program and move IP past it."""
        ip = self.ip
        address = (self.bases[self.s[self.CS]] + ip) & 0xFFFFFFFF
        if address < self.video_end and address + 2 > self.video_start:
            lo = self.fetch8()
            return lo | self.fetch8() << 8
        self.ip = (ip + 2) & self.ip_mask
        return U16.unpack_from(self.mem, address)[0]

    def fetch32_flat(self):
        """Read the next little-endian doubleword of the program and move IP past it."""
        ip = self.ip
        address = (self.bases[self.s[self.CS]] + ip) & 0xFFFFFFFF
        if address < self.video_end and address + 4 > self.video_start:
            lo = self.fetch16()
            return lo | self.fetch16() << 16
        self.ip = (ip + 4) & self.ip_mask
        return U32.unpack_from(self.mem, address)[0]

    def step_flat(self):
        """Run one instruction: step() of real mode, with the opcode read at the base of CS plus EIP."""
        self.instructions += 1
        if self.trace:
            self.trace(self)
        self.seg_override = None
        self.rep = None
        self.osize32 = self.asize32 = self.code32
        ip = self.start_ip = self.ip
        address = (self.bases[self.s[self.CS]] + ip) & 0xFFFFFFFF
        self.ip = (ip + 1) & self.ip_mask
        if self.video_start <= address < self.video_end:
            op = self.video.read_memory(address - self.video_start)
        else:
            op = self.mem[address]
        if op in PREFIXES:
            op = self.take_prefixes(op)
        handler = (self.ops32 if self.osize32 else self.ops).get(op)
        if handler is None or (self.asize32 and op in ADDRESS_SIZED):
            handler = self.unusual_handler(op, handler)
        handler(op)
