import dataclasses
import datetime
import os
import pathlib
import sys
from enum import Enum
from typing import Optional
from collections.abc import Iterator

Sector_sz = 0x200
Fat12_Entries = 0x155  # sector_sz * 2 // 3
Cylinders = 40
Reserved_Sectors = 1  # this has to be assumed to find fat-id in the first place
Fat_Numb = 2
Hidden_Sector_Numb = 0
First_Physical_sector = 1  # under CHS
Fat_Offset = Reserved_Sectors + 1

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
    def __init__(self, scroll_nom: str | os.PathLike, read_only: bool):
        self.img = img = disk_factory(scroll_nom, read_only)
        self.read_only = read_only
        self.boot = img[0]
        self.struct = struct = DiskStruct(img[1][0])
        fat = img[1:1 + struct.fat_sects]
        assert fat == img[struct.second_fat_floor: struct.root_dir_floor]
        self.fat = Fat12(b''.join(fat), struct.fat_sz)
        root_dir = self.img[struct.root_dir_floor: struct.files_floor]
        self.root_dir = dir_factory(root_dir)

    def dir(self):
        back = ((e.name, e.ext, e.size, e.write_datetime) for e in self.root_dir)
        back = ("\t".join(str(v) for v in e) for e in back)
        print("\n".join(back))
        print(f"{len(self.root_dir)} Files(s)")

    def file_extract(self, path: pathlib.Path, nom: str):
        entry = file_entry_from_name(self.root_dir, nom)
        file = self.fat.file_locate(entry.first_cluster)
        with open(path.parent / nom, 'wb') as codex:
            codex.write(loc_get(self.img, self.struct, file, entry.size))

    def fili_extract(self, path: pathlib.Path, loci: Optional[list[loc_t]] = None):
        fili = loci or self.fili_get()
        folder = path.parent / path.stem
        try:
            os.mkdir(folder)
        except FileExistsError:
            pass
        for entry, loc in fili:
            with open(folder / entry.full_name, 'wb') as codex:
                codex.write(loc_get(self.img, self.struct, loc, entry.size))

    def fili_get(self, loci: Optional[list[loc_t]] = None) -> Iterator[tuple[file_entry, loc_t]]:
        loci = loci or self.fat.fili_locate()[0]
        return ((file_entry_from_pointer(self.root_dir, loc[0]), loc) for loc in loci)

    def loci_print(self, loci: Optional[list[loc_t]] = None):
        fili = self.fili_get(loci)
        for entry, loc in fili:
            print(entry.full_name, loc)

    def file_add(self, file_nom: str):
        if self.read_only:
            raise Exception("Tried to write a file to disk opened in read-only mode")
        empty = self.fat.fili_locate()[1]
        allocated = []
        files_plan = {}
        for sector in file_read(file_nom):
            pointer, empty = empty[0], empty[1:]
            files_plan[pointer] = sector
            allocated.append(pointer)
        # noinspection PyTypeChecker
        self.fat.update(allocated)
        dir_plan = dir_update(self.root_dir, file_nom)

    def file_del(self, nom: str):
        entry = file_entry_from_name(self.root_dir, nom)


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
    def fati_clusters(self):
        return self.fat_sects * Fat_Numb / self.cluster_sects

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


class Fat:
    def __init__(self):
        # stub
        self._val = ()

    def __len__(self):
        return len(self._val)

    def __call__(self):
        return self._val

    def __getitem__(self, index: int):
        return self._val[index]

    def file_locate(self, pointer: int) -> loc_t:
        # stub
        raise StopIteration

    def fili_locate(self) -> tuple[list[loc_t], loc_t]:
        unchecked = set(range(Fat_Offset, Fat12_Entries))
        fili = []
        empty = []
        while unchecked:
            pointer = min(unchecked)
            try:
                file = self.file_locate(pointer)
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

    def update(self, allocated: fat_t):
        # stub
        pass


class Fat12(Fat):
    def __init__(self, buffer: bytes, fat_sz: int):
        super().__init__()
        self._val = fat12_factory(buffer, fat_sz)

    def file_locate(self, pointer: int) -> loc_t:
        file = []
        while True:
            file.append(pointer)
            try:
                pointer = self._val[pointer]
            except IndexError:
                raise StopIteration
            if pointer >= 0xFF0 or not pointer:
                if pointer >= 0xFF8:
                    break
                else:
                    raise DiskReadError(pointer, file)
        return file

    def update(self, allocated: fat_t):
        pass


class Fat_ID_Error(Exception):
    def __init__(self, fat_id):
        message = f"Fat ID {fat_id:X} is invalid or out of scope due to modernity"
        super().__init__(message)


def disk_factory(scroll_nom: str | os.PathLike, read_only: bool) -> image_t:
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
            'hour'  : hour - 1 if hour else hour}
    # hour need to be converted from 0..25 (0 being dummy) on fat to 0..24 on python


def ms_date(call: bytes) -> dict[str, int]:
    return {'day'  : call[0] % 0x20 or 1,  # 0..5
            'month': call[0] // 0x20 + 0x20 * call[1] % 2 or 1,  # 5..9
            'year' : 1980 + call[1] // 2}  # 9..16


def dir_factory(dir_img: image_t) -> dir_t:
    folder = []
    for sector in dir_img:
        while sector:
            entry, sector = sector[:Dir_Entry_sz], sector[Dir_Entry_sz:]
            if entry[0] == 0xe5:
                continue
            elif entry[0] == 0:
                break
            folder.append(entry)
        else:
            continue
        break
    folder = [file_entry(entry) for entry in folder]
    return tuple(folder)


class DiskReadError(Exception):
    def __init__(self, code, back):
        opt = "Empty" if code == 0 else "Bad" if code == 0xFF7 else "Reserved"
        message = f"Read Error: {opt} cluster"
        super().__init__(message)
        self.code = code
        self.back = back


def adress_from_fat_index(pointer: int, struct: DiskStruct):
    return (pointer - Fat_Offset) * struct.cluster_sects


def loc_get(disk_img: image_t, struct: DiskStruct, file: loc_t, size: Optional[int] = None) -> bytes:
    files_img = disk_img[struct.files_floor:]
    back = []
    for i in file:
        i = adress_from_fat_index(i, struct)
        back += files_img[i:i + struct.cluster_sects]
    back = b"".join(back)
    back = back[:size] if size else back.strip(b"\xF6").strip(b"\x00")
    return back


def file_entry_from_name(folder: dir_t, nom: str) -> file_entry:
    nom = nom.upper()
    try:
        entry = tuple(filter(lambda n: nom in {n.name, n.full_name}, folder))[0]
    except IndexError:
        print(f"file {nom} doesn't exist", sys.stderr)
        raise
    return entry


def file_entry_from_pointer(folder: dir_t, pointer: int) -> file_entry:
    try:
        entry = tuple(filter(lambda e: pointer == e.first_cluster, folder))[0]
    except IndexError:
        print(f"file not identified for cluster {pointer}", file=sys.stderr)
        raise
    return entry


def write_sector(disk: Disk, sector: bytes, pointer: int):
    pass


class Whence(Enum):
    start = 0
    cursor = 1
    end = 2


def dir_update(folder: dir_t, file_nom: str):
    pass


def file_read(file_nom: str):
    with open(file_nom, mode="rb") as file:
        while sector := file.read(Sector_sz):
            yield sector


"""
empty space locate
file_add
format
"""


def main():
    scrollnom = sys.argv[1]
    scroll = pathlib.Path(scrollnom)

    disk = Disk(scroll, read_only=False)
    fili, emp = disk.fat.fili_locate()
    disk.loci_print(fili)
    print(f"empty {emp}")
    disk.fili_extract(scroll)
    disk.file_add(sys.argv[2])


main()
