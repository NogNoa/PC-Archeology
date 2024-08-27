import dataclasses
import datetime
import os
import pathlib
import sys
from abc import abstractmethod
from typing import Optional, Generator
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
class FileEntry:
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
    def full_name(self) -> str:
        return f"{self.name}.{self.ext}"


image_t = list[bytes]
fat_t = list[int]
loc_t = list[int]
dir_t = list[FileEntry]
file_desc_t = tuple[FileEntry, loc_t]

Whence_Start = 0
Whence_Cursor = 1
Whence_End = 2


class Disk:
    def __init__(self, scroll_nom: str | os.PathLike, read_only: bool = True):
        self.read_only = read_only
        self.path = pathlib.Path(scroll_nom)
        self.img = img = Image(self.path)
        self.struct = struct = DiskStruct(img[1][0])
        fat = img.part_get(Reserved_Sectors, struct.second_fat_floor)
        assert fat() == img[struct.second_fat_floor: struct.root_dir_floor]
        self.fat = Fat12(fat, struct.fat_sz)
        root_dir = img.part_get(struct.root_dir_floor, struct.files_floor)
        self.root_dir = Directory(root_dir, self.struct.root_dir_entries)

    @property
    def boot(self) -> image_t:
        return self.img[0]

    def dir(self):
        back = ((e.name, e.ext, e.size, e.write_datetime) for e in self.root_dir)
        back = ("\t".join(str(v) for v in e) for e in back)
        print("\n".join(back))
        print(f"{len(self.root_dir)} Files(s)")

    def _file_extract_internal(self, folder: pathlib.Path, entry: FileEntry, loc: loc_t):
        with open(folder / entry.full_name, 'wb') as codex:
            codex.write(self.file_get(loc, entry.size))

    def file_extract(self, nom: str):
        entry = self.root_dir[nom]
        loc = self.fat.file_locate(entry.first_cluster)
        self._file_extract_internal(self.path.parent, entry, loc)

    def fili_extract(self, loci: Optional[list[loc_t]] = None):
        descri = self.fili_describe(loci)
        folder = self.path.parent / self.path.stem
        try:
            os.mkdir(folder)
        except FileExistsError:
            pass
        for couple in descri:
            self._file_extract_internal(folder, *couple)

    def fili_describe(self, loci: Optional[list[loc_t]] = None) -> Iterator[file_desc_t]:
        loci = loci or self.fat.fili_locate()[0]
        return ((self.root_dir[loc[0]], loc) for loc in loci)

    def file_get(self, file: loc_t, size: Optional[int] = None) -> bytes:
        clusteri = (self.file_cluster_get(i) for i in file)
        byti = b"".join(b"".join(cluster) for cluster in clusteri)
        byti = byti[:size] if size else byti.strip(b"\xF6").strip(b"\x00")
        return byti

    def file_cluster_get(self, sect_ind: int) -> image_t:
        files_img = self.img.part_get(self.struct.files_floor, len(self.img))
        this_cluster = (sect_ind - Fat_Offset) * self.struct.cluster_sects
        next_cluster = this_cluster + self.struct.cluster_sects
        return files_img[this_cluster:next_cluster]

    def loci_print(self, loci: Optional[list[loc_t]] = None):
        fili = self.fili_describe(loci)
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
        self.fat.update(allocated)
        dir_plan = self.root_dir.update(file_nom)

    def file_del(self, nom: str):
        entry = self.root_dir[nom]
        self.fat.file_del(entry.first_cluster)
        self.img[self.struct.second_fat_floor: self.struct.root_dir_floor] = self.fat.img[:]
        # codex.seek(self.struct.root_dir_floor * Sector_sz, Whence_Start)
        self.root_dir.file_del(entry)
        self.img.flush()


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
            raise FatIDError(fat_id) from err
        self.track_sects = Track_Sectors[fat_index]
        self.cluster_sects = Cluster_Sectors[fat_index]
        self.root_dir_entries = Root_Dir_Entries[fat_index]

    @property
    def fat_sz(self) -> int:
        return self.fat_sects * Sector_sz

    @property
    def fati_clusters(self) -> int:
        return self.fat_sects * Fat_Numb // self.cluster_sects

    @property
    def track_sz(self) -> int:
        return self.track_sects * Sector_sz

    @property
    def cluster_sz(self) -> int:
        return self.cluster_sects * Sector_sz

    @property
    def root_dir_sz(self) -> int:
        return self.root_dir_entries * Dir_Entry_sz

    @property
    def root_dir_sects(self) -> int:
        return self.root_dir_entries // 0x10

    @property
    def second_fat_floor(self) -> int:
        return 1 + self.fat_sects

    @property
    def root_dir_floor(self) -> int:
        return 1 + Fat_Numb * self.fat_sects

    @property
    def files_floor(self) -> int:
        return self.root_dir_floor + self.root_dir_sects


# class ImagePart(image_t):
#     def __init__(self, img: image_t, offset: int):
#         I wanted to use the part like a c pointer that can change the image from the middle
#         but python is just not like that. The object will have its own local copy of the image.

class SeqWrapper:
    @abstractmethod
    def __init__(self):
        self._val = []

    def __len__(self) -> int:
        return len(self._val)

    def __call__(self) -> list:
        return self._val

    def __getitem__(self, index: int) -> any:
        return self._val[index]

    def __contains__(self, item):
        return item in self._val

    def index(self, item) -> int:
        return self._val.index(item)


class Image(SeqWrapper):
    def __init__(self, scroll_nom: os.PathLike):
        self._val = disk_factory(scroll_nom)
        self.file = scroll_nom
        self._sect_cursor: Optional[int] = None
        self._byte_cursor = 0
        self.max = len(self._val)
        self.buffer = bytearray()
        self.subscribers = []

    def __setitem__(self, sect_index: int, value: bytes):
        self._val[sect_index] = value

    def part_get(self, offset: int, mx: int) -> "Imagepart":
        mx = mx if mx is not None else self.max
        part = Imagepart(self, offset, mx)
        self.subscribers.append(part)
        return part

    def sect_buff(self, sect_index: int = 0):
        self.sect_flush()
        self._sect_cursor = sect_index
        self.buffer = bytearray(self[sect_index])

    def sect_flush(self):
        if self._sect_cursor is None:
            return
        self[self._sect_cursor] = bytes(self.buffer)

    def byte_seek_abs(self, byte_offset: int):
        assert abs(byte_offset) < Sector_sz
        self._byte_cursor = byte_offset % Sector_sz

    def byte_seek_rel(self, byte_offset: int):
        assert abs(byte_offset) < Sector_sz
        self._byte_cursor += byte_offset
        if self._byte_cursor < 0:
            self.sect_buff(self._sect_cursor - 1)
        elif self._byte_cursor >= Sector_sz:
            self.sect_buff(self._sect_cursor + 1)
        self._byte_cursor %= Sector_sz

    def sect_tell(self) -> Optional[int]:
        return self._sect_cursor

    def byte_tell(self) -> int:
        return self._byte_cursor

    def read(self, byte_offset: int, advance=True) -> bytes:
        if self._sect_cursor is None:
            raise Exception("image buffer was not initilized. you need to call sect_buff()")
        end = self._byte_cursor + byte_offset
        back = self.buffer[self._byte_cursor: end]
        if advance:
            self._byte_cursor = end
        return bytes(back)

    def write(self, value: bytes, advance=True):
        end = self._byte_cursor + len(value)
        self.buffer[self._byte_cursor: end] = value
        if advance:
            self._byte_cursor = end

    def iner_flush(self):
        self.sect_flush()
        self._sect_cursor = None
        self.buffer.clear()

    def flush(self):
        for sub in self.subscribers:
            sub.iner_flush()
        self.iner_flush()
        with open(self.file, "wb") as codex:
            codex.write(b''.join(self._val))


class Imagepart(Image):
    def __init__(self, img: Image, offset: int, mx: int):
        super().__init__(img.file)
        del self._val
        del self.file
        self.mom = img
        self.offset = offset
        self.max = mx

    def __len__(self) -> int:
        return self.max - self.offset

    def __call__(self) -> image_t:
        return self.mom[self.offset: self.max]

    def __setitem__(self, sect_index: int, value: bytes):
        self.mom[self.offset + sect_index] = value

    def __getitem__(self, index: int | slice) -> image_t:
        if isinstance(index, int):
            if not 0 <= index < self.__len__():
                raise IndexError
            return self.mom[self.offset + index]
        elif isinstance(index, slice):
            if index.start is None:
                start = self.offset
            elif index.start < 0:
                raise IndexError
            else:
                start = self.offset + index.start
            if index.stop is None:
                stop = self.max
            elif index.stop > self.__len__():
                raise IndexError
            else:
                stop = self.offset + index.stop
            return self.mom[start: stop: index.step]

    def __contains__(self, item) -> bool:
        return item in self()

    def sect_buff(self, sect_index: int = 0):
        if sect_index == self.mom._sect_cursor:
            self.mom.iner_flush()
        if not 0 <= sect_index < self.__len__():
            raise IndexError
        super().sect_buff(sect_index)


class Fat(SeqWrapper):
    @abstractmethod
    def __init__(self):
        self._val: fat_t = []
        self.img = None

    @abstractmethod
    def file_locate(self, pointer: int) -> loc_t:
        raise StopIteration

    def fili_locate(self) -> tuple[list[loc_t], loc_t]:
        """
        :return: list of locs for all files + a loc of the empty clusters
        """
        unchecked = set(range(Fat_Offset, Fat12_Entries))
        fili = []
        empty = []
        while unchecked:
            pointer = min(unchecked)
            try:
                file = self.file_locate(pointer)
            except FatReadError as err:
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

    @abstractmethod
    def update(self, allocated: fat_t):
        pass


class Fat12(Fat):
    def __init__(self, image: Imagepart, fat_sz: int, ):
        buffer = image[:]
        buffer = b''.join(buffer)
        self._val = fat12_factory(buffer, fat_sz)
        self.img = image

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
                    raise FatReadError(pointer, file)
        return file

    def update(self, allocated: fat_t):
        pass

    def file_del(self, pointer: int):
        file = self.file_locate(pointer)
        fat_cursor = -2
        self.img.sect_buff()
        for loc in file:
            self._val[loc] = 0
            offset = loc * 3 // 2 - fat_cursor
            self.img.byte_seek_rel(offset)
            b = self.img.read(1, advance=False)
            if loc % 2:
                self.img.write((b[0] % 0x10).to_bytes())
                self.img.write(b'\0')
            else:
                self.img.write(b'\0')
                self.img.write((b[0] >> 4 << 4).to_bytes())
            fat_cursor = 2


class Directory(SeqWrapper):
    def __init__(self, img: Imagepart, max_size: int):
        buffer: image_t = img[:]
        self._val = dir_factory(buffer)
        self.max_size = max_size
        self.img = img

    def __getitem__(self, item: int | str) -> FileEntry:
        if isinstance(item, str):
            nom = item.upper()
            sieve = lambda e: nom in {e.name, e.full_name}
            err_massage = f"file {item} doesn't exist"
        elif isinstance(item, int):
            sieve = lambda e: item == e.first_cluster
            err_massage = f"file not identified for cluster {item}"
        else:
            raise TypeError
        try:
            entry = tuple(filter(sieve, self._val))[0]
        except IndexError:
            print(err_massage, sys.stderr)
            raise
        return entry

    def update(self, file_nom: str):
        pass

    def file_del(self, entry: FileEntry):
        index = self._val.index(entry)
        j = index
        self.img.sect_buff()
        for _ in range(self.max_size):
            if not j:
                break
            # j has to be finite positive smaller then len(self._val)
            b = self.img.read(1, False)
            if b in {b'', b'\0'}:
                raise DirReadError(f"{entry.name}; read {b} at entry {index - j} "
                                   f"address directory:{self.img.byte_tell(): x}")
            elif b[0] != 0xE5:
                j -= 1
            self.img.byte_seek_rel(Dir_Entry_sz)
        else:
            raise DirReadError(f"got to the absolute end of the directory and haven't found {entry.name}")
        candidate = self.img.read(Dir_Entry_sz, False)
        try:
            assert entry == FileEntry(candidate)
        except AssertionError:
            raise DirReadError(f"{entry} \ndiffer from \n{FileEntry(candidate)}")
        if index == len(self._val) - 1:
            self.img.write(b"\0")
        else:
            self.img.write(b'\xE5')
        del self._val[index]


class FatIDError(Exception):
    def __init__(self, fat_id):
        message = f"Fat ID {fat_id:X} is invalid or out of scope due to modernity"
        super().__init__(message)


def disk_factory(scroll_nom: str | os.PathLike) -> image_t:
    with open(scroll_nom, "br") as file:
        scroll = file.read()
    disk = []
    while scroll:
        sector, scroll = scroll[:Sector_sz], scroll[Sector_sz:]
        disk.append(sector)
    return disk


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
    return table


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
    folder = [FileEntry(entry) for entry in folder]
    return folder


class FatReadError(Exception):
    def __init__(self, code, back):
        opt = "Empty" if code == 0 else "Bad" if code == 0xFF7 else "Reserved"
        message = f"Read Error: {opt} cluster"
        super().__init__(message)
        self.code = code
        self.back = back


class DirReadError(Exception):
    pass


def adress_from_fat_index(pointer: int, struct: DiskStruct) -> int:
    return (pointer - Fat_Offset) * struct.cluster_sects


def write_sector(disk: Disk, sector: bytes, pointer: int):
    pass


def file_read(file_nom: str) -> Generator[any, bytes, None]:
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
    disk.fili_extract()
    disk.file_del("donkey.bas")


main()
