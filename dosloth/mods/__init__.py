"""Changes to what a program does, that the configuration asks for by name (`[mods]`, see config.py).

A mod is made as a patch is (see the package `patches`): the first bytes of a piece of code are replaced by `INT F0h`,
and Python does the work there. But where a patch does what the code does, only faster, a mod does something else,
so it is never made unless the configuration says so: a run without it is the program as it is, and the logs and the
dumps of such a run are those of the program.

A mod is a module of this package with a dict `MODS`: the name of the mod -> its patches. The modules are found as
those of `patches` are, and `MODS` here holds them all. Those of the mods that the machine is to have (`cpu.mods`, from the
configuration) are made when a program is loaded (see loader.py). The machine knows what the `INT` of every mod means,
asked for or not (see machine.py), as a saved state may have it in its memory.
"""
import importlib
import pkgutil

MODS = {}  # the name of a mod -> its patches (patches.Patch)


def load_mods():
    """Import every module of the package that is a mod, and take its mods."""
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda info: info.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        for name, patches in getattr(module, "MODS", {}).items():
            if name in MODS:
                raise ValueError(f"two mods are named {name!r}")
            MODS[name] = patches


load_mods()


def asked_for(cpu):
    """The patches of the mods that the machine is to have."""
    return [patch for name in sorted(getattr(cpu, "mods", ())) for patch in MODS[name]]


def every_patch():
    """The patches of all the mods, for the machine to know what their INTs mean."""
    return [patch for patches in MODS.values() for patch in patches]
