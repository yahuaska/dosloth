"""Places in a program where Python does the work of the code, for the code to be fast enough.

The emulation of an instruction costs some microseconds, and a few loops of the game take nearly all the instructions
of a run (see the profile, `-p`). A patch is made for such a loop: the first two bytes of the code are replaced by
`INT F0h`, and the handler of that interrupt does what the code would have done, in Python, and then sets the
instruction pointer where the code goes on. A patch is made for one exact piece of code: the bytes that it replaces
are checked against a hash, so that another version of the program, or another program, is left as it is.

The interrupt is ours alone: no table of vectors is touched, and the handler is found by the address of the `INT`.
The memory of a patched program keeps the `INT`, so a saved state has it too, and `install` has to be made in every
machine, patched or not, to know what the `INT` means.

A patch is a module of this package, and the package knows none of them by name. When it is imported (once, as the
emulator starts) every module in it, but those whose names begin with an underscore, is imported, and what it has in
`PATCHES` (pieces of code that are in the program when it is loaded: `Patch`) and `LATE_PATCHES` (pieces that come
later, as the driver of the sound that the player reads from a file: `LatePatch`) is put in the lists of the same names
here. The modules are taken in the order of their names; the order does not matter. To make a new patch is to put
a module here. The modules whose names begin with an underscore are what the patches use: `_base` (how a patch is
made), `_common` (the structures and the end of most of them), `_smacker` (what more than one patch of SMKP.EXE
looks at).
"""
import importlib
import pkgutil

from ..cpu import CPU
from ._base import HOOK, LOOK_EVERY, PATCH_BYTES, LatePatch, Patch

PATCHES = []  # the patches of the code that the program has when it is loaded
LATE_PATCHES = []  # the patches of the code that comes later


def load_patches():
    """Import every module of the package that is a patch, and take its lists of patches."""
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda info: info.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        PATCHES.extend(getattr(module, "PATCHES", ()))
        LATE_PATCHES.extend(getattr(module, "LATE_PATCHES", ()))


load_patches()


def apply(cpu, patches=None):
    """Patch the code in memory that is one of the known pieces (see Patch.matches). Returns the names of what was
    patched. Call it when a program is loaded. All of them are looked at before the first is made: a patch has to be
    found by the code as the program has it, and not as another patch, that is in it, has changed it."""
    found = [patch for patch in (PATCHES if patches is None else patches) if patch.matches(cpu)]
    for patch in found:
        cpu.mem[patch.address:patch.address + len(PATCH_BYTES)] = PATCH_BYTES
    return [patch.name for patch in found]


def apply_late(cpu, patches=None):
    """Patch the pieces of code that came after the program was loaded, those of them that are there. Returns the
    patches that are not found yet."""
    return [patch for patch in (LATE_PATCHES if patches is None else patches) if not patch.apply(cpu)]


def install(cpu, patches=None):
    """Teach the machine the meaning of `INT F0h`: do the work of the patch whose first instruction it stands in for.
    The pieces of code that come later are looked for from time to time, until they are found."""
    table = {patch.address: patch.handler for patch in (PATCHES if patches is None else patches)}
    cpu.patch_sites = {}  # the places of the pieces that came later (their linear addresses)

    def hook(cpu):
        address = (cpu.bases or {}).get(cpu.s[CPU.CS], 0) + cpu.ip - len(PATCH_BYTES)
        handler = cpu.patch_sites.get(address) or table.get(cpu.ip - len(PATCH_BYTES))
        if handler is None:
            raise NotImplementedError(f"INT {HOOK:02X} at {cpu.s[CPU.CS]:04X}:{cpu.start_ip:X}: no patch there")
        handler(cpu)
    cpu.int_handlers[HOOK] = hook
    if LATE_PATCHES:
        waiting = list(LATE_PATCHES)

        def look(instructions):
            waiting[:] = apply_late(cpu, waiting)
            return instructions + LOOK_EVERY if waiting else None
        cpu.event_sources.append(look)
        cpu.wake()
