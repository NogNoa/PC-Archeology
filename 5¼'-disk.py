import dataclasses
import datetime

Sector_sz = 0x200
Fat12_Entries = 0x155  # sector_sz * 2 // 3
Cylinders = 40
# Reserved_Sectors = 1  # this has to be assumed to find fat-id in the first place
Fat_Numb = 2
Hidden_Sector_Numb = 0
First_Physical_sector = 1  # under CHS

Capacity = (320, 160, 360, 180)  # in KB
Track_Sectors = (8, 8, 9, 9)
Heads = (2, 1, 2, 1)
Cluster_Sectors = (2, 1, 2, 1)
Root_Dir_Entries = (0x70, 0x40, 0x70, 0x40)
Dir_Entry_sz = 0x20

disk_t = tuple[bytes, ...]
fat_t = tuple[int, ...]
loc_t = list[int]


class Disk:
    def __init__(self, *args, **qwargs):
        val = disk_factory(*args, **qwargs)
        self.val = val
        self.boot = val[0]
        assert val[1] == val[2]
        fat = val[1]
        self.struct = DiskStruct(fat[0])
        self.fat = fat12_factory(fat)
        self.root_dir = root_dir_factory(val, self.struct)


@dataclasses.dataclass
class DiskStruct:
    fat_id: int
    track_sects: int
    cluster_sects: int
    root_dir_entries: int

    track_sz: int
    cluster_sz: int
    root_dir_sz: int
    root_dir_sects: int

    def __init__(self, fat_id):
        self.fat_id = fat_id
        fat_index = 0xff - fat_id
        try:
            self.track_sects = Track_Sectors[fat_index]
        except IndexError as err:
            raise Fat_ID_Error(fat_id) from err
        self.cluster_sects = Cluster_Sectors[fat_index]
        self.root_dir_entries = Root_Dir_Entries[fat_index]

    @property
    def track_sz(self):
        return self.track_sects * Sector_sz

    @property
    def cluster_sz(self):
        return self.cluster_sects * Sector_sz

    @property
    def root_dir_sz(self):
        return self.root_dir_entries * Dir_Entry_sz

    @property
    def root_dir_sects(self):
        return self.root_dir_entries // 0x10


class Fat_ID_Error(Exception):
    def __init__(self, fat_id):
        message = f"Fat ID {fat_id} is invalid out of scope due to modernity"
        super().__init__(message)


def disk_factory(scroll_nom: str, read_only) -> disk_t:
    mode = "br" if read_only else "br+"
    with open(scroll_nom, mode) as file:
        scroll = file.read()
    disk = []
    while scroll:
        sector, scroll = scroll[:Sector_sz], scroll[Sector_sz:]
        disk.append(sector)
    return tuple(disk)


def fat12_factory(sector: bytes) -> fat_t:
    table = []
    sector, last = sector[:-2], sector[-2:]  # 0x200 % 3 = 2
    while sector:
        entrii, sector = sector[:3], sector[3:]
        # elements of bytes object are ints
        table.append(entrii[0] + 0x100 * (entrii[1] % 0x10))
        table.append((entrii[1] // 0x10) + 0x10 * entrii[2])
    table.append(last[0] + 0x100 * (last[1] % 0x10))
    return tuple(table)


def ms_time(call: bytes) -> tuple[int, int, int]:
    sec = 2 * call[0] % 0x10
    minute = call[0] // 0x10 + 0x10 * call[1] % 4
    hour = call[1] // 4
    return hour, minute, sec


def ms_date(call: bytes):
    day = call[0] % 0x10 or 1
    month = call[0] // 0x10 or 1
    year = 1980 + call[1]
    return year, month, day


@dataclasses.dataclass
class file_entry:
    name: str  # 8
    ext: str  # 3
    # 3
    create_datetime: datetime.datetime  # 4
    access_date: datetime.date  # 2
    # 2
    write_datetime: datetime.datetime  # 4
    first_cluster: int  # 2
    size: int  # 4

    def __init__(self, file: bytes):
        self.name = str(file[:8])
        self.ext = str(file[8:0xB])
        self.create_datetime = datetime.datetime(*ms_date(file[0xE:0x10]), *ms_time(file[0x10:0x12]))
        self.access_date = datetime.date(*ms_date(file[0x12:0x14]))
        self.write_datetime = datetime.datetime(*ms_date(file[0x16:0x18]), *ms_time(file[0x18:0x1A]))
        self.first_cluster = int.from_bytes(file[0x1A:0x1C], byteorder='little')
        self.size = int.from_bytes(file[0x1C:], byteorder='little')


def root_dir_factory(disk: disk_t, struct: DiskStruct) -> tuple[file_entry, ...]:
    dir_floor = 1 + Fat_Numb
    root_dir_on_disk = disk[dir_floor: dir_floor + struct.root_dir_sects]
    root_dir = []
    for sector in root_dir_on_disk:
        while sector:
            entry, sector = sector[:Dir_Entry_sz], sector[Dir_Entry_sz:]
            if entry[0] == 0xe5:
                continue
            elif entry[0] == 0:
                break
            root_dir.append(entry)
        else:
            continue
        break
    root_dir = [file_entry(entry) for entry in root_dir]
    return tuple(root_dir)


class DiskReadError(Exception):
    def __init__(self, code, back):
        opt = "Empty" if code == 0 else "Bad" if code == 0xFF7 else "Reserved"
        message = f"Read Error: {opt} cluster"
        super().__init__(message)
        self.code = code
        self.back = back


def file_locate(fat: fat_t, first: int) -> loc_t:
    """
    :param fat: array of pointers
    :param first: first logical sector
    """
    pointer = first
    file = []
    while True:
        file.append(pointer)
        try:
            pointer = fat[pointer]
        except IndexError:
            raise StopIteration
        if pointer >= 0xFF0 or not pointer:
            if pointer >= 0xFF8:
                break
            else:
                raise DiskReadError(pointer, file)
    return file


def file_get(disk: disk_t, fat: fat_t, pointer: int):
    file = file_locate(fat, pointer)
    return b"".join(disk[i] for i in file)


def fili_locate(fat: fat_t) -> tuple[list[loc_t], list[int]]:
    unchecked = set(range(1, Fat12_Entries))
    fili = []
    empty = []
    while unchecked:
        pointer = min(unchecked)
        try:
            file = file_locate(fat, pointer)
        except DiskReadError as err:
            rem = err.back
            if err.code == 0:
                empty.append(pointer)
        except StopIteration:
            break
        else:
            rem = file
            fili.append(file)
        unchecked -= set(rem)
    return fili, empty


"""
fili_get
file_firsti_get
empty space locate
file_add
format
"""

disk = Disk(
    r"D:\Computing\86Box-Optimized-Skylake-32-c3294fcf\disks\IBM PC-DOS 1.10 (5.25-160k)\Images\Raw\DISK01.IMA",
    read_only=True)
fili, emp = fili_locate(disk.fat)
print("\n".join(str(f) for f in fili))
print(f"empty: {emp}")
print(disk.root_dir)
