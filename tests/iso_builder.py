"""Makes ISO 9660 images for the tests: just enough of the format, the way a level 1 disc has it."""
import struct

SECTOR = 2048
DEFAULT_DATE = bytes([97, 3, 14, 12, 30, 45, 0])  # 1997-03-14 12:30:45, at the time of Greenwich


class Item:
    """A file with what its directory record can say besides the name."""

    def __init__(self, data=b"", hidden=False, date=DEFAULT_DATE):
        self.data, self.hidden, self.date = data, hidden, date


def both(number, size):
    """The number in the form ISO 9660 uses for the ones it keeps twice: little-endian, then big-endian."""
    return number.to_bytes(size, "little") + number.to_bytes(size, "big")


def record(name, extent, size, date, flags):
    """A directory record: `name` is the bytes of the name as they are on the disc."""
    length = 33 + len(name)
    length += length % 2  # the records have even lengths
    data = bytearray(length)
    data[0] = length
    data[2:10] = both(extent, 4)
    data[10:18] = both(size, 4)
    data[18:25] = date
    data[25] = flags
    data[28:32] = both(1, 2)
    data[32] = len(name)
    data[33:33 + len(name)] = name
    return bytes(data)


def disc_name(name, is_dir):
    """The name as the disc has it: a file is NAME.EXT;1, and NAME. when it has no extension."""
    if is_dir:
        return name.encode("ascii")
    return (name if "." in name else name + ".").encode("ascii") + b";1"


def sectors_for(records):
    """How many sectors the records of a directory take: none is cut by the end of one."""
    count, used = 1, 0
    for item in records:
        if used + len(item) > SECTOR:
            count, used = count + 1, 0
        used += len(item)
    return count


def pack_records(records, count):
    data = bytearray(count * SECTOR)
    sector, used = 0, 0
    for item in records:
        if used + len(item) > SECTOR:
            sector, used = sector + 1, 0
        at = sector * SECTOR + used
        data[at:at + len(item)] = item
        used += len(item)
    return bytes(data)


def build(tree, system_area=None, boot_record=False, volume="TESTDISC"):
    """An image of a disc with `tree`: a dict of names, each a bytes (a file), an Item, or a dict (a directory).

    `system_area` is what the first 16 sectors hold (cut or filled with zeros to 32 KB). With `boot_record` a volume
    descriptor of another kind comes before the primary one, as on a bootable disc.
    """
    first_free = 16 + (2 if not boot_record else 3)  # the descriptors, and the terminator after them

    def plan_directory(directory):
        """The entries of a directory as (name, is a directory, content), and how many sectors its records take.
        That does not depend on where what is in it will be."""
        entries = [(child, isinstance(content, dict), content) for child, content in sorted(directory.items())]
        probe = [record(b"\0", 0, 0, DEFAULT_DATE, 2), record(b"\1", 0, 0, DEFAULT_DATE, 2)]
        probe += [record(disc_name(child, is_dir), 0, 0, DEFAULT_DATE, 0) for child, is_dir, _ in entries]
        return entries, sectors_for(probe)

    # first pass: the extents of everything
    layout = {}  # id of a directory dict -> (extent, sectors, entries)
    order = [tree]  # the directories, the root first, and then each after the one that holds it
    extent = first_free
    for directory in order:  # the list grows while it is walked
        entries, count = plan_directory(directory)
        layout[id(directory)] = (extent, count, entries)
        extent += count
        order += [content for _, is_dir, content in entries if is_dir]
    file_extents = {}
    for directory in order:
        for child, is_dir, content in layout[id(directory)][2]:
            if not is_dir:
                item = content if isinstance(content, Item) else Item(content)
                file_extents[id(content)] = (extent, item)
                extent += max((len(item.data) + SECTOR - 1) // SECTOR, 0)
    total = extent

    image = bytearray(total * SECTOR)
    if system_area:
        image[0:min(len(system_area), 16 * SECTOR)] = system_area[:16 * SECTOR]
    at = 16 * SECTOR
    if boot_record:
        image[at] = 0
        image[at + 1:at + 6] = b"CD001"
        image[at + 6] = 1
        image[at + 7:at + 30] = b"EL TORITO SPECIFICATION"
        at += SECTOR
    root_extent, root_sectors, _ = layout[id(tree)]
    image[at] = 1
    image[at + 1:at + 6] = b"CD001"
    image[at + 6] = 1
    image[at + 40:at + 40 + len(volume)] = volume.encode()
    image[at + 80:at + 88] = both(total, 4)
    image[at + 128:at + 132] = both(SECTOR, 2)
    image[at + 156:at + 190] = record(b"\0", root_extent, root_sectors * SECTOR, DEFAULT_DATE, 2)[:34]
    at += SECTOR
    image[at] = 255
    image[at + 1:at + 6] = b"CD001"
    image[at + 6] = 1

    # second pass: the directories and the files
    parents = {id(tree): id(tree)}
    for directory in order:
        extent, count, entries = layout[id(directory)]
        records = [record(b"\0", extent, count * SECTOR, DEFAULT_DATE, 2),
                   record(b"\1", layout[parents[id(directory)]][0], layout[parents[id(directory)]][1] * SECTOR,
                          DEFAULT_DATE, 2)]
        for child, is_dir, content in entries:
            if is_dir:
                parents[id(content)] = id(directory)
                child_extent, child_sectors, _ = layout[id(content)]
                records.append(record(disc_name(child, True), child_extent, child_sectors * SECTOR, DEFAULT_DATE, 2))
            else:
                file_extent, item = file_extents[id(content)]
                records.append(record(disc_name(child, False), file_extent, len(item.data), item.date,
                                      1 if item.hidden else 0))
                image[file_extent * SECTOR:file_extent * SECTOR + len(item.data)] = item.data
        image[extent * SECTOR:(extent + count) * SECTOR] = pack_records(records, count)
    return bytes(image)
