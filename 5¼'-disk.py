import dataclasses
import datetime
import pathlib
import sys
from typing import Optional

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
Fat_Sectors = (1, 1, 2, 2)
Cluster_Sectors = (2, 1, 2, 1)  # virtual cluster in physical sectors
Root_Dir_Entries = (0x70, 0x40, 0x70, 0x40)
Dir_Entry_sz = 0x20


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
        self.name = str(file[:8], encoding="ansi").upper().strip()
        self.ext = str(file[8:0xB], encoding="ansi").upper().strip()
        self.create_datetime = datetime.datetime(**ms_time(file[0xE:0x10]), **ms_date(file[0x10:0x12]))
        self.access_date = datetime.date(**ms_date(file[0x12:0x14]))
        self.write_datetime = datetime.datetime(**ms_time(file[0x16:0x18]), **ms_date(file[0x18:0x1A]))
        self.first_cluster = int.from_bytes(file[0x1A:0x1C], byteorder='little')
        self.size = int.from_bytes(file[0x1C:], byteorder='little')

    @property
    def full_name(self):
        return f"{self.name}.{self.ext}"


image_t = tuple[bytes, ...]
fat_t = tuple[int, ...]
loc_t = list[int]
dir_t = tuple[file_entry, ...]


class Disk:
    def __init__(self, *args, **kwargs):
        self.img = img = disk_factory(*args, **kwargs)
        self.boot = img[0]
        self.struct = struct = DiskStruct(img[1][0])
        fat = img[1:1 + struct.fat_sects]
        assert fat == img[struct.second_fat_floor: struct.root_dir_floor]
        self.fat = fat12_factory(b''.join(fat), struct.fat_sz)
        root_dir = self.img[struct.root_dir_floor: struct.files_floor]
        self.root_dir = root_dir_factory(root_dir)

    def dir(self):
        back = ((e.name, e.ext, e.size, e.write_datetime) for e in self.root_dir)
        back = ("\t".join(str(v) for v in e) for e in back)
        print("\n".join(back))
        print(f"{len(self.root_dir)} Files(s)")


@dataclasses.dataclass
class DiskStruct:
    fat_id: int
    fat_sects: int
    track_sects: int
    cluster_sects: int
    root_dir_entries: int

    fat_sz: int
    track_sz: int
    cluster_sz: int
    root_dir_sz: int
    root_dir_sects: int

    def __init__(self, fat_id):
        self.fat_id = fat_id
        fat_index = 0xff - fat_id
        try:
            self.fat_sects = Fat_Sectors[fat_index]
        except IndexError as err:
            raise Fat_ID_Error(fat_id) from err
        self.track_sects = Track_Sectors[fat_index]
        self.cluster_sects = Cluster_Sectors[fat_index]
        self.root_dir_entries = Root_Dir_Entries[fat_index]

    @property
    def fat_sz(self):
        return self.fat_sects * Sector_sz

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

    @property
    def second_fat_floor(self):
        return 1 + self.fat_sects

    @property
    def root_dir_floor(self):
        return 1 + Fat_Numb * self.fat_sects

    @property
    def files_floor(self):
        return self.root_dir_floor + self.root_dir_sects


class Fat_ID_Error(Exception):
    def __init__(self, fat_id):
        message = f"Fat ID {fat_id} is invalid out of scope due to modernity"
        super().__init__(message)


def disk_factory(scroll_nom: str, read_only) -> image_t:
    mode = "br" if read_only else "br+"
    with open(scroll_nom, mode) as file:
        scroll = file.read()
    disk = []
    while scroll:
        sector, scroll = scroll[:Sector_sz], scroll[Sector_sz:]
        disk.append(sector)
    return tuple(disk)


def fat12_factory(buffer: bytes, fat_sz: int) -> fat_t:
    table = []
    end = fat_sz % 3
    buffer, last = buffer[:-end], buffer[-end:]
    while buffer:
        entrii, buffer = buffer[:3], buffer[3:]
        # elements of bytes object are ints
        table.append(entrii[0] + 0x100 * (entrii[1] % 0x10))
        table.append((entrii[1] // 0x10) + 0x10 * entrii[2])
    if end == 2:
        table.append(last[0] + 0x100 * (last[1] % 0x10))
    elif end == 1:
        table.append(last[0])
    return tuple(table)


def ms_time(call: bytes) -> dict[str, int]:
    hour = call[1] // 8  # 11..16
    return {'second': 2 * call[0] % 0x20,  # 0..5
            'minute': call[0] // 0x20 + 0x20 * call[1] % 8,  # 5..11
            'hour': hour - 1 if hour else hour}
    # hour need to be converted from 0..25 (0 being dummy) on fat to 0..24 on python


def ms_date(call: bytes) -> dict[str, int]:
    return {'day': call[0] % 0x20 or 1,  # 0..5
            'month': call[0] // 0x20 + 0x20 * call[1] % 2 or 1,  # 5..9
            'year': 1980 + call[1] // 2}  # 9..16


def root_dir_factory(dir_img: image_t) -> dir_t:
    root_dir = []
    for sector in dir_img:
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


def file_get(disk: Disk, pointer: int, size: Optional[int] = None) -> bytes:
    file = file_locate(disk.fat, pointer)
    files_img = disk.img[disk.struct.files_floor:]
    back = []
    for i in file:
        i = (i - 2) * disk.struct.cluster_sects
        back += files_img[i:i + disk.struct.cluster_sects]
    back = b"".join(back)
    back = back[:size] if size else back.strip(b"\xF6").strip(b"\x00")
    return back


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


def file_in_folder_get(folder: dir_t, nom: str) -> tuple[int, int]:
    nom = nom.upper()
    try:
        entry = tuple(filter(lambda n: nom in {n.name, n.full_name}, folder))[0]
    except IndexError:
        print(f"file {nom} doesn't exist", sys.stderr)
        raise
    return entry.first_cluster, entry.size


def file_extract(disk: Disk, nom: str, path: pathlib.Path):
    with open(path.parent / nom, 'wb') as codex:
        codex.write(file_get(disk, *file_in_folder_get(disk.root_dir, nom)))


"""
fili_get
file_firsti_get
empty space locate
file_add
format
"""

scroll = pathlib.Path(r"D:\Computing\86Box-Optimized-Skylake-32-c3294fcf\disks\Microsoft MS-DOS 1.25 [CDP OEM R2.11] "
                      r"and Basic (5.25)\Images\CDPDOS.IMG")

disk = Disk(
    scroll,
    read_only=True)
fili, emp = fili_locate(disk.fat)
print("\n".join(str(f) for f in fili))
print(f"empty: {emp}")
disk.dir()
file_extract(disk, "ibmbio.com", scroll)
