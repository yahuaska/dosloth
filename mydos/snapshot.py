"""Saving the state of the machine to a file, and putting it back: stop a run, and go on later from the same place.

A snapshot is a zip file with `state.json` (everything but the big pieces of bytes) and one member for each of the
bytes: the memory, the planes of the video card and so on. It is not a Python pickle: the machine holds functions
that cannot be saved, and the file must go on being readable when the code changes.

To go on from a snapshot, a machine is built as for a normal run, without a program, and then what the snapshot
says is put over it. Whatever the snapshot does not mention (a part that did not exist yet when it was made) is
left as the new machine made it. The memory comes back whole (only what is not zero is in the file), and the parts
of it that are ROM on a real machine are written again from the code, so that a snapshot of an older run points to
the newest data.

Not in a snapshot: the files of the host that a program changed since. They are what they are now, and the files
that were open are opened again at the place they were at. A file that is gone is an error, said in plain words.
"""
import json
import os
import sys
import zipfile
from collections import deque
from dataclasses import asdict

from . import machine as machine_module
from . import pages
from .filesystem import Entry, FsError, MemFs
from .flat import REAL_MODE
from .handles import Console, HandleTable, NullDevice, OpenFile
from .process import Parent
from .search import Searches

VERSION = 2  # 2: the memory is in runs of what is not zero; 1 had it all, the first megabyte
STATE_MEMBER = "state.json"
BLOB_MEMBER = "blob/{}"


class SnapshotError(Exception):
    """The snapshot cannot be made, read or used; the message says why, for a person."""


# --- values: what JSON cannot say is tagged, and bytes go to their own members ---

def pack(value, blobs):
    """`value` as something JSON can hold. Bytes are put in `blobs` and named by their number in it."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        blobs.append(bytes(value))
        return {"$bytearray" if isinstance(value, bytearray) else "$bytes": len(blobs) - 1}
    if isinstance(value, tuple):
        return {"$tuple": [pack(item, blobs) for item in value]}
    if isinstance(value, list):
        return [pack(item, blobs) for item in value]
    if isinstance(value, deque):
        return {"$deque": [pack(item, blobs) for item in value]}
    if isinstance(value, dict):
        return {"$dict": [[pack(key, blobs), pack(item, blobs)] for key, item in value.items()]}
    raise TypeError(f"cannot save a {type(value).__name__}")


def unpack(value, blobs):
    """What `pack` made, as it was."""
    if isinstance(value, list):
        return [unpack(item, blobs) for item in value]
    if not isinstance(value, dict):
        return value
    (tag, content), = value.items()
    if tag == "$bytes":
        return blobs[content]
    if tag == "$bytearray":
        return bytearray(blobs[content])
    if tag == "$tuple":
        return tuple(unpack(item, blobs) for item in content)
    if tag == "$deque":
        return deque(unpack(item, blobs) for item in content)
    if tag == "$dict":
        return {unpack(key, blobs): unpack(item, blobs) for key, item in content}
    raise SnapshotError(f"the snapshot has a value of a kind this version does not know: {tag}")


# --- the parts of the machine ---

def fingerprint(config):
    """What of the configuration a snapshot depends on: which drives there are, and what is in them."""
    return [[number, disk.type, disk.mount.type if disk.mount else None]
            for number, disk in sorted(config.disks.items())]


class OpenFiles:
    """The files that are open, each once, whichever tables hold them: a child shares them with its parent."""

    def __init__(self):
        self.numbers = {}  # id of an OpenFile -> its number
        self.items = []

    def number(self, entry):
        if id(entry) not in self.numbers:
            self.numbers[id(entry)] = len(self.items)
            self.items.append({"path": entry.dos_path, "position": entry.file.tell(),
                               "readable": entry.readable, "writable": entry.writable})
        return self.numbers[id(entry)]


def table_state(table, files):
    """What the 20 slots of a table hold, with the files given as numbers."""
    slots = []
    for entry in table.slots:
        if entry is None:
            slots.append(None)
        elif isinstance(entry, Console):
            slots.append({"console": entry.stream})
        elif isinstance(entry, NullDevice):
            slots.append({"device": entry.name})
        elif isinstance(entry, OpenFile):
            slots.append({"file": files.number(entry)})
        else:
            raise TypeError(f"cannot save a handle of {type(entry).__name__}")
    return slots


def make_table(slots, files):
    table = HandleTable()
    for number, slot in enumerate(slots):
        if slot is None:
            table.slots[number] = None
        elif "console" in slot:
            table.slots[number] = Console(slot["console"])
        elif "device" in slot:
            table.slots[number] = NullDevice(slot["device"])
        else:
            table.slots[number] = files[slot["file"]]
    return table


def count_users(tables):
    """Tell every table how many slots, in all of them, hold each file: a file closes when the last lets go."""
    users = {}
    for table in tables:
        for entry in table.slots:
            if isinstance(entry, OpenFile):
                users[id(entry)] = users.get(id(entry), 0) + 1
    for table in tables:
        table.users = users


def dos_state(state, files):
    memory = state.memory
    return {
        "break_checking": state.break_checking, "dta": state.dta, "pending_scan": state.pending_scan,
        "psp_seg": state.psp_seg, "exit_status": state.exit_status,
        "memory": {"blocks": memory.blocks, "owners": memory.owners},
        "searches": {"last": state.searches.last,
                     "rest": {number: [asdict(entry) for entry in entries]
                              for number, entries in state.searches.rest.items()}},
        "handles": table_state(state.handles, files),
        "parents": [{"psp_seg": parent.psp_seg, "registers": parent.registers, "segments": parent.segments,
                     "ip": parent.ip, "flags": parent.flags, "dta": parent.dta, "high": parent.high,
                     "mode": parent.mode,
                     "handles": table_state(parent.handles, files)} for parent in state.parents],
    }


def capture(machine, rewind=False):
    """The state of the machine as plain data, with the bytes in a list: (data, blobs).

    A snapshot is taken at a moment when no instruction is half done, except by whoever says `rewind`: the machine is
    inside an instruction then (waiting for a key in INT 16h, or dead from an error), and the snapshot is made to go
    on from the start of that instruction, which does it all again.
    """
    cpu = machine.cpu
    files = OpenFiles()
    volumes = machine.volumes
    data = {
        "version": VERSION,
        "program": machine.program,
        "disks": fingerprint(machine.config),
        "cpu": {"mem": {"size": len(cpu.mem), "runs": pages.runs(cpu.mem)}, "r": cpu.r, "rh": cpu.rh, "s": cpu.s,
                "ip": cpu.start_ip if rewind else cpu.ip, "flags": cpu.flags, "eflags_high": cpu.eflags_high,
                "instructions": cpu.instructions - bool(rewind), "mode": cpu.get_mode()},
        "vga": dict(vars(machine.card)),
        "timer": [dict(vars(counter)) for counter in machine.timer.counters],
        "keyboard": machine.keyboard.buffer,
        "mouse": dict(vars(machine.mouse)),
        "sysport": {"value": machine.sysport.value},
        "dos": dos_state(machine.state, files),
        "volumes": {"current_drive": volumes.current_drive, "directories": volumes.directories,
                    "memory_disks": {number: fs.dump() for number, fs in volumes.filesystems.items()
                                     if isinstance(fs, MemFs)}},
    }
    data["files"] = files.items
    blobs = []
    return pack(data, blobs), blobs


def set_attributes(target, saved):
    """Put the saved values in the attributes of `target` that it has; what it does not have is not made."""
    for name, value in saved.items():
        if hasattr(target, name):
            setattr(target, name, value)


def reopen(volumes, item):
    """The OpenFile of a saved file: opened again, at the place it was."""
    try:
        where = volumes.resolve(item["path"])
        file = where.filesystem.open(where.parts, write=item["writable"])
        file.seek(item["position"])
    except FsError as error:
        raise SnapshotError(f"cannot open {item['path']} again, which the program had open: {error}") from None
    return OpenFile(file, item["path"], item["readable"], item["writable"])


def restore(machine, packed, blobs):
    """Put a snapshot over a machine that was just built. Raises SnapshotError if they do not belong together.

    A part that the snapshot does not have stays as the new machine made it.
    """
    data = unpack(packed, blobs)
    if data.get("version", 0) > VERSION:
        raise SnapshotError(f"the snapshot is of version {data['version']}, this program reads up to {VERSION}")
    if data["disks"] != fingerprint(machine.config):
        raise SnapshotError("the snapshot was made with other drives than these (the configuration must have the same "
                            f"drives with the same kinds of disk and mount; it had {data['disks']})")
    cpu, volumes, state = machine.cpu, machine.volumes, machine.state
    saved = data["cpu"]
    memory = saved["mem"]
    if isinstance(memory, (bytes, bytearray)):  # version 1: all of the first megabyte, in one piece
        if len(memory) > len(cpu.mem):
            raise SnapshotError("the memory of the snapshot is not the size of the memory of this machine")
        cpu.mem[:len(memory)] = memory
    else:
        if memory["size"] != len(cpu.mem):
            raise SnapshotError("the memory of the snapshot is not the size of the memory of this machine")
        pages.erase(cpu.mem)
        for address, run in memory["runs"]:
            cpu.mem[address:address + len(run)] = run
    cpu.r[:] = saved["r"]
    cpu.s[:len(saved["s"])] = saved["s"]  # a snapshot from before FS and GS has four: those stay 0
    cpu.rh[:] = saved.get("rh", cpu.rh)  # a snapshot of before the 386 has no high halves: they stay 0
    cpu.eflags_high = saved.get("eflags_high", cpu.eflags_high)
    cpu.set_mode(saved.get("mode", REAL_MODE))  # a snapshot from before the flat mode is one of real mode
    cpu.ip, cpu.flags, cpu.instructions = saved["ip"], saved["flags"], saved["instructions"]
    cpu.start_ip = cpu.ip
    machine_module.install_rom(cpu)  # after the memory: what is ROM comes from the code, not from the snapshot

    saved_vga = dict(data.get("vga", {}))
    for plane, content in zip(machine.card.planes, saved_vga.pop("planes", [])):
        plane[:] = content  # in place: whoever shows the picture holds the planes
    set_attributes(machine.card, saved_vga)
    machine.card.changed = True  # whoever shows the picture draws it at once
    for counter, saved_counter in zip(machine.timer.counters, data.get("timer", [])):
        set_attributes(counter, saved_counter)
    if "keyboard" in data:
        machine.keyboard.buffer = data["keyboard"]
    set_attributes(machine.mouse, data.get("mouse", {}))
    set_attributes(machine.sysport, data.get("sysport", {}))

    saved_volumes = data.get("volumes", {})
    volumes.current_drive = saved_volumes.get("current_drive", volumes.current_drive)
    volumes.directories = saved_volumes.get("directories", volumes.directories)
    for number, tree in saved_volumes.get("memory_disks", {}).items():
        volumes.filesystems[number].load(tree)  # first: the files that were open there are opened in it again
    files = [reopen(volumes, item) for item in data.get("files", [])]
    saved_dos = data.get("dos", {})
    for name in ("break_checking", "dta", "pending_scan", "psp_seg", "exit_status"):
        setattr(state, name, saved_dos.get(name, getattr(state, name)))
    memory = saved_dos.get("memory", {})
    state.memory.blocks = memory.get("blocks", state.memory.blocks)
    state.memory.owners = memory.get("owners", state.memory.owners)
    if "searches" in saved_dos:
        state.searches = Searches()
        state.searches.last = saved_dos["searches"]["last"]
        state.searches.rest = {number: [Entry(**entry) for entry in entries]
                               for number, entries in saved_dos["searches"]["rest"].items()}
    if "handles" in saved_dos:
        state.handles = make_table(saved_dos["handles"], files)
    state.parents = [Parent(parent["psp_seg"], parent["registers"], parent["segments"], parent["ip"], parent["flags"],
                            parent["dta"], make_table(parent["handles"], files), parent.get("high"),
                            parent.get("mode"))
                     for parent in saved_dos.get("parents", [])]
    count_users([state.handles] + [parent.handles for parent in state.parents])
    machine.program = data.get("program")


# --- files ---

def save(path, machine, rewind=False):
    """Write a snapshot of the machine to `path`. The file appears whole or not at all."""
    packed, blobs = capture(machine, rewind)
    temporary = path + ".part"
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(STATE_MEMBER, json.dumps(packed))
            for number, blob in enumerate(blobs):
                archive.writestr(BLOB_MEMBER.format(number), blob)
        os.replace(temporary, path)
    except OSError:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def load(path, machine):
    """Put the snapshot in the file `path` over the machine."""
    try:
        with zipfile.ZipFile(path) as archive:
            packed = json.loads(archive.read(STATE_MEMBER))
            blobs = [archive.read(BLOB_MEMBER.format(number)) for number in range(len(archive.namelist()) - 1)]
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as error:
        raise SnapshotError(f"cannot read the snapshot {path}: {error}") from None
    restore(machine, packed, blobs)


def save_near(machine, program, suffix, rewind=False):
    """Save a snapshot beside the program, or, if that cannot be written to (a CD-ROM), in the current directory of the
    emulator under the name of the program. Says where on stderr; returns the path, or None if it could not be done.
    Never raises: a snapshot that fails must not hide what came before it."""
    name = os.path.basename(program.replace("\\", "/"))
    problems = []
    here = os.path.join(os.getcwd(), name)
    for base in dict.fromkeys((here,) if "\\" in program else (program, here)):  # a DOS path is no place on the host
        try:
            save(base + suffix, machine, rewind)
        except Exception as error:  # whatever it is, it must not take the place of the error that came first
            problems.append(f"{base}{suffix}: {getattr(error, 'strerror', None) or error}")
            continue
        print(f"state saved: {base}{suffix}", file=sys.stderr)
        return base + suffix
    print("no state could be saved: " + "; ".join(problems), file=sys.stderr)
    return None


class QuickSave:
    """What F12 does: asks for a snapshot of the machine, which is made as soon as the emulator can (see `__call__`)."""

    def __init__(self, machine, suffix=".state"):
        self.machine = machine
        self.suffix = suffix
        self.asked = False

    def ask(self):
        self.asked = True

    def __call__(self, inside):
        """Called at every tick of the monitor. `inside` says that the program is in the middle of a call that waits for
        a key: the snapshot then goes on from the start of that call."""
        if self.asked:
            self.asked = False
            save_near(self.machine, self.machine.program, self.suffix, rewind=inside)
