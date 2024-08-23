import dataclasses
import datetime
import os
import pathlib
import sys
from abc import abstractmethod
from typing import Optional, BinaryIO
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
    def full_name(self):
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
        fat: image_t = img[Reserved_Sectors: struct.second_fat_floor]
        assert fat == img[struct.second_fat_floor: struct.root_dir_floor]
        self.fat = Fat12(b''.join(fat), struct.fat_sz)
        root_dir: image_t = img[struct.root_dir_floor: struct.files_floor]
        self.root_dir = Directory(root_dir, self.struct.root_dir_entries)

    @property
    def boot(self):
        return self.img[0]

    def dir(self):
        back = ((e.name, e.ext, e.size, e.write_datetime) for e in self.root_dir)
        back = ("\t".join(str(v) for v in e) for e in back)
        print("\n".join(back))
        print(f"{len(self.root_dir)} Files(s)")

    def _file_extract_internal(self, folder: pathlib.Path, entry: FileEntry, loc: loc_t):
        with open(folder / entry.full_name, 'wb') as codex:
            codex.write(self.img.file_get(self.struct, loc, entry.size))

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
        fat_image = ImagePart(self.img, Reserved_Sectors, self.struct.second_fat_floor)
        self.fat.file_del(fat_image, entry.first_cluster)
        self.img[self.struct.second_fat_floor: self.struct.root_dir_floor] = fat_image[:]
        root_dir_image = ImagePart(self.img, self.struct.root_dir_floor, self.struct.files_floor)
        # codex.seek(self.struct.root_dir_floor * Sector_sz, Whence_Start)
        self.root_dir.file_del(root_dir_image, entry)
        # codex.flush()


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


# class ImagePart(image_t):
#     def __init__(self, img: image_t, offset: int):
#         I wanted to use the part like a c pointer that can change the image from the middle
#         but python is just not like that. The object will have its own local copy of the image.

class SeqWrapper:
    @abstractmethod
    def __init__(self):
        self._val = []

    def __len__(self):
        return len(self._val)

    def __call__(self):
        return self._val

    def __getitem__(self, index: int):
        return self._val[index]

    def __contains__(self, item):
        return item in self._val

    def index(self, item):
        return self._val.index(item)


class Image(SeqWrapper):
    def __init__(self, scroll_nom: os.PathLike):
        self._val = disk_factory(scroll_nom)
        self.file = scroll_nom
        self._sect_cursor = None
        self._byte_cursor = 0
        self.max = len(self._val)
        self.buffer = bytearray()

    def __setitem__(self, sect_index: int, value: bytes):
        self[sect_index] = value

    def part_get(self, offset: int, mx: int):
        return ImagePart(self, offset, mx)

    def file_get(self, struct: DiskStruct, file: loc_t, size: Optional[int] = None) -> bytes:
        back = [self.file_cluster_get(struct, i) for i in file]
        back = b"".join(back)
        back = back[:size] if size else back.strip(b"\xF6").strip(b"\x00")
        return back

    def file_cluster_get(self, struct: DiskStruct, index: int):
        files_img = ImagePart(self, struct.files_floor, len(self))
        i = (index - Fat_Offset) * struct.cluster_sects
        j = i + struct.cluster_sects
        return files_img[i:j]

    def sect_buff(self, sect_index: int):
        self.sect_flush()
        self._sect_cursor = sect_index
        self.buffer = bytearray(self[sect_index])

    def sect_flush(self):
        if self._sect_cursor is None:
            return
        self[self._sect_cursor] = bytes(self.buffer)

    def byte_seek(self, byte_offset: int, whence: Optional[int] = 0):
        match whence:
            case 0:
                self._byte_cursor = byte_offset
            case 1:
                self._byte_cursor += byte_offset
            case 2:
                self._byte_cursor = Sector_sz + byte_offset
        self._byte_cursor %= Sector_sz

    def sect_tell(self) -> Optional[int]:
        return self._sect_cursor

    def byte_tell(self):
        return self._byte_cursor

    def read(self, byte_offset: int, advance=True):
        end = self._byte_cursor + byte_offset
        back = self.buffer[self._byte_cursor: end]
        if advance:
            self._byte_cursor = end
        return back

    def write(self, value: bytes, advance=True):
        end = self._byte_cursor + len(value)
        self.buffer[self._byte_cursor: end] = value
        if advance:
            self._byte_cursor = end


class ImagePart(Image):
    def __init__(self, img: Image, offset: int, mx: int):
        super().__init__(img.file)
        del self._val
        self.mom = img
        self.offset = offset
        self.max = mx

    def __len__(self):
        return self.max - self.offset

    def __call__(self):
        return self.mom[self.offset: self.max]

    def __getitem__(self, index: int | slice):
        if isinstance(index, int):
            if not 0 < index < self.__len__():
                raise IndexError
            return self.mom[self.offset + index]
        elif isinstance(index, slice):
            if index.start < 0 or index.stop > self.__len__():
                raise IndexError
            return self.mom[self.offset + index.start: self.offset + index.stop: index.step]

    def __contains__(self, item):
        return item in self()


class Fat(SeqWrapper):
    @abstractmethod
    def __init__(self):
        self._val: fat_t = []

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

    @abstractmethod
    def update(self, allocated: fat_t):
        pass


class Fat12(Fat):
    def __init__(self, buffer: bytes, fat_sz: int):
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

    def file_del(self, fat_image: ImagePart, pointer: int):
        file = self.file_locate(pointer)
        fat_cursor = -2
        fat_image.sect_buff(0)
        for loc in file:
            self._val[loc] = 0
            offset = (loc - fat_cursor) * 3 // 2
            fat_image.byte_seek(offset, Whence_Cursor)
            b = fat_image.read(1, advance=False)
            if loc % 2:
                fat_image.write((b[0] % 0x10).to_bytes())
                fat_image.write(b'\0')
            else:
                fat_image.write(b'\0')
                fat_image.write((b[0] >> 4 << 4).to_bytes())
            fat_cursor = loc + 4 / 3


class Directory(SeqWrapper):
    def __init__(self, img: image_t, max_size: int):
        self._val = dir_factory(img)
        self.max_size = max_size

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

    def file_del(self, codex: ImagePart, entry: FileEntry):
        index = self._val.index(entry)
        j = index
        for _ in range(self.max_size):
            if not j:
                break
            # j has to be finite positive smaller then len(self._val)
            b = codex.read(1, False)
            if b in {b'', b'\0'}:
                raise DiskReadError
            elif b[0] != 0xE5:
                j -= 1
            codex.byte_seek(Dir_Entry_sz, Whence_Cursor)
        else:
            raise DiskReadError

        if index == len(self._val) - 1:
            codex.write(b"\0")
        else:
            codex.write(b'\xE5')
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


class DiskReadError(Exception):
    def __init__(self, code, back):
        opt = "Empty" if code == 0 else "Bad" if code == 0xFF7 else "Reserved"
        message = f"Read Error: {opt} cluster"
        super().__init__(message)
        self.code = code
        self.back = back


def adress_from_fat_index(pointer: int, struct: DiskStruct):
    return (pointer - Fat_Offset) * struct.cluster_sects


def write_sector(disk: Disk, sector: bytes, pointer: int):
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

    disk = Disk(scroll, read_only=True)
    fili, emp = disk.fat.fili_locate()
    disk.loci_print(fili)
    print(f"empty {emp}")
    disk.fili_extract()
    disk.file_add(sys.argv[2])


main()
