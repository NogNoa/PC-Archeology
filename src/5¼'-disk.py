"""5¼'-disk"""
import argparse
import dataclasses
import datetime
import math
import os
import pathlib
import time
from abc import abstractmethod
from argparse import ArgumentError
from typing import Optional, Generator, TypeAlias, Self, Any
from collections.abc import Iterator

Sector_sz = 0x200
Cylinders = 40
Reserved_Sectors = 1  # this has to be assumed to find fat-id in the first place
Fat_Numb = 2
Hidden_Sector_Numb = 0
First_Physical_sector = 1  # under CHS
Fat_Offset = Reserved_Sectors + 1

Track_Sectors = (8, 8, 9, 9)
Head_Numb = (2, 1, 2, 1)
Fat_Sectors = (1, 1, 2, 2)
Cluster_Sectors = (2, 1, 2, 1)  # physical sectors in a virtual cluster
Root_Dir_Entries = (0x70, 0x40, 0x70, 0x40)
Dir_Entry_sz = 0x20


@dataclasses.dataclass
class FileEntry:
    name: str  # 8
    ext: str  # 3
    # flags  # 1
    # 2
    create_datetime: datetime.datetime  # 4  # not in 1.0
    access_date: datetime.date  # 2  # not in 1.0
    # 2
    write_datetime: datetime.datetime  # 4
    first_cluster: int  # 2
    size: int  # 4

    physical_index: int

    # flags:
    hidden: bool = False
    system_file: bool = False

    @staticmethod
    def from_image(file: bytes, physical_index: int):
        name = str(file[:8], encoding="ansi").upper().strip()
        ext = str(file[8:0xB], encoding="ansi").upper().strip()
        hidden = bool(file[0xB] | 2)
        system_file = bool(file[0xB] | 4)
        create_datetime = datetime.datetime(**ms_time(file[0xE:0x10]), **ms_date(file[0x10:0x12]))
        access_date = datetime.date(**ms_date(file[0x12:0x14]))
        write_datetime = datetime.datetime(**ms_time(file[0x16:0x18]), **ms_date(file[0x18:0x1A]))
        first_cluster = int.from_bytes(file[0x1A:0x1C], byteorder='little')
        size = int.from_bytes(file[0x1C:], byteorder='little')
        return FileEntry(name, ext, create_datetime, access_date, write_datetime,
                         first_cluster, size, physical_index, hidden, system_file)

    @property
    def full_name(self) -> str:
        return f"{self.name}.{self.ext}"

    def to_image(self) -> bytes:
        back = "{:<8}".format(self.name[:8]).encode("ansi")
        back += "{:<3}".format(self.ext[:3]).encode("ansi")
        back += (2*self.hidden + 4*self.system_file).to_bytes(1)
        back += b'\0'*2
        back += to_ms_time(self.create_datetime)
        back += to_ms_time(self.access_date)
        back += b'\0' * 2
        back += to_ms_time(self.write_datetime)
        back += self.first_cluster.to_bytes(2, "little")
        back += self.size.to_bytes(4, "little")
        return back


image_t : TypeAlias = list[bytes]
fat_t : TypeAlias = list[int]
loc_t : TypeAlias = list[int]
dir_t : TypeAlias = list[FileEntry]
file_desc_t : TypeAlias = tuple[FileEntry, loc_t]


class Disk:

    class OutOfSpace(Exception):
        pass

    def __init__(self, scroll_nom: str | os.PathLike, read_only: bool = True):
        self.read_only = read_only
        self.path = pathlib.Path(scroll_nom)
        self.img = img = Image.from_file(self.path)
        self.struct = struct = DiskStruct(img[1][0])
        fat = img.part_get(Reserved_Sectors, struct.second_fat_floor)
        assert fat() == img[struct.second_fat_floor: struct.root_dir_floor]
        self.fat = Fat12(fat, struct.fat_entrys)
        root_dir = img.part_get(struct.root_dir_floor, struct.files_floor)
        self.root_dir = Directory(root_dir, self.struct.root_dir_entries)
        self.fili_img = self.img.part_get(self.struct.files_floor)

    @property
    def boot(self) -> bytes:
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
        clusteri = (self.fili_img[self.cluster_slice_get(i)] for i in file)
        byti = b"".join(b"".join(cluster) for cluster in clusteri)
        byti = byti[:size] if size else byti.strip(b"\xF6").strip(b"\x00")
        return byti

    def cluster_slice_get(self, sect_ind: int) -> slice:
        this_cluster = sector_from_fat_loc(sect_ind, self.struct)
        next_cluster = this_cluster + self.struct.cluster_sects
        return slice(this_cluster, next_cluster)

    def loci_print(self, loci: Optional[list[loc_t]] = None):
        fili = self.fili_describe(loci)
        for entry, loc in fili:
            loc = loc_list_to_ranges(loc)
            loc = ["{:X}..{:X}".format(*p) for p in loc]
            loc = ", ".join(loc)
            print(entry.full_name, loc)

    def secti_print(self, loci: Optional[list[loc_t]] = None):
        fili = self.fili_describe(loci)
        for entry, loc in fili:
            loc = loc_list_to_ranges(loc)
            loc = ((sector_from_fat_loc(p, self.struct) + self.struct.files_floor for p in pl) for pl in loc)
            loc = ["{:X}..{:X}".format(*p) for p in loc]
            loc = ", ".join(loc)
            print(entry.full_name, loc)

    def disk_offset_print(self, loci: Optional[list[loc_t]] = None):
        fili = self.fili_describe(loci)
        for entry, loc in fili:
            loc = loc_list_to_ranges(loc)
            loc = (((sector_from_fat_loc(p, self.struct) + self.struct.files_floor)
                    * Sector_sz for p in pl) for pl in loc)
            loc = ["{:X}..{:X}".format(*p) for p in loc]
            loc = ", ".join(loc)
            print(entry.full_name, loc)

    def file_add(self, file_nom: str, system=False):
        if self.read_only:
            raise Exception("Tried to write a file to disk opened in read-only mode")
        if file_nom in self.root_dir.fili_names:
            self.file_del(file_nom, flush=False)
        empty = self.fat.fili_locate()[1]
        allocated = []
        sectors = list(file_read(file_nom))
        sectors[-1] += b'\xf6' * (Sector_sz - len(sectors[-1]))
        sectors += [b'\xf6' * Sector_sz] * (-len(sectors) % self.struct.cluster_sects)
        for clust in range(0, len(sectors), self.struct.cluster_sects):
            try:
                pointer, empty = empty[0], empty[1:]
            except IndexError as err:
                raise Disk.OutOfSpace("Not enough space for "+file_nom) from err
            allocated.append(pointer)
            self.fili_img[self.cluster_slice_get(pointer)] = sectors[clust: clust + self.struct.cluster_sects]
        self.fat.file_add(allocated)
        self.sync_other_fats()
        entry_size = self.root_dir.file_add(file_nom, allocated[0], system)
        assert (len(allocated) - self.struct.cluster_sects <=
                entry_size // (Sector_sz * self.struct.cluster_sects) <= len(allocated))
        self.img.flush()

    def file_del(self, nom: str, flush=True):
        entry = self.root_dir[nom]
        self.fat.file_del(entry.first_cluster)
        self.sync_other_fats()
        self.root_dir.file_del(entry)
        if flush: self.img.flush()

    def sync_other_fats(self):
        self.fat.img.flush()
        self.img[self.struct.second_fat_floor: self.struct.root_dir_floor] = self.fat.img[:]

    def format(self, codex_nom: str, fat_id: int):
        disk_format(self, codex_nom, fat_id)


@dataclasses.dataclass
class DiskStruct:
    fat_id: int

    fat_sects: int
    track_sects: int
    cluster_sects: int

    root_dir_entries: int

    head_numb: int

    root_dir_sects: int
    files_clusts: int
    fat_entrys: int
    sector_numb: int

    second_fat_floor: int
    root_dir_floor: int
    files_floor: int

    fat_sz: int
    track_sz: int
    cluster_sz: int
    root_dir_sz: int
    files_sz: int
    disk_sz: int

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
        self.head_numb = Head_Numb[fat_index]

    @property
    def fat_sz(self) -> int:
        return self.fat_sects * Sector_sz

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
    def disk_sz(self) -> int:
        return Sector_sz * self.sector_numb

    @property
    def root_dir_sects(self) -> int:
        return self.root_dir_entries // 0x10

    @property
    def sector_numb(self) -> int:
        return Cylinders * self.head_numb * self.track_sects

    @property
    def second_fat_floor(self) -> int:
        return 1 + self.fat_sects

    @property
    def root_dir_floor(self) -> int:
        return 1 + Fat_Numb * self.fat_sects

    @property
    def files_floor(self) -> int:
        return self.root_dir_floor + self.root_dir_sects

    @property
    def files_clusts(self) -> int:
        return math.ceil((self.sector_numb - self.files_floor) / self.cluster_sects)

    @property
    def fat_entrys(self):
        return self.files_clusts

    @property
    def files_sz(self):
        return Sector_sz * (self.sector_numb - self.files_floor)


class SeqWrapper:
    item_type = any

    @abstractmethod
    def __init__(self):
        self._val = []

    def __len__(self) -> int:
        return len(self._val)

    def __call__(self) -> list:
        return self._val

    def __getitem__(self, index: int) -> item_type:
        return self._val[index]

    def __setitem__(self, index: int, value: item_type):
        self._val[index] = value

    def __contains__(self, item: item_type):
        return item in self._val

    def index(self, item: item_type) -> int:
        return self._val.index(item)


class Image(SeqWrapper):
    item_type = bytes

    def __init__(self, val: image_t):
        self._val = val
        self.file = None
        self._sect_cursor: Optional[int] = None
        self.max = len(self._val)
        self.buffer = bytearray()
        self.subscribers = []

    @classmethod
    def from_file(cls, scroll_nom: os.PathLike) -> Self:
        self = cls(disk_factory(scroll_nom))
        self.file = scroll_nom
        return self

    @classmethod
    def scratch(cls, size: int):
        val = [b'\0' * Sector_sz] * math.ceil(size / Sector_sz)
        return cls(val)

    def part_get(self, offset: int, mx: int = None) -> "Imagepart":
        mx = mx if mx is not None else self.max
        part = Imagepart(self, offset, mx)
        self.subscribers.append(part)
        return part

    def sect_buff(self, sect_index: int = 0):
        assert sect_index >= 0
        self.sect_flush()
        self._sect_cursor = sect_index
        self.buffer = bytearray(self[sect_index])

    def sect_flush(self):
        if self._sect_cursor is None:
            return
        self[self._sect_cursor] = bytes(self.buffer)

    def sect_tell(self) -> Optional[int]:
        return self._sect_cursor

    def iner_flush(self):
        self.sect_flush()
        self._sect_cursor = None
        self.buffer.clear()

    def flush(self):
        if self.file is None: return
        for sub in self.subscribers:
            sub.iner_flush()
        self.iner_flush()
        with open(self.file, "wb") as codex:
            codex.write(b''.join(self._val))


class Imagepart(Image):
    def __init__(self, img: Image, offset: int, mx: int):
        super().__init__(img._val)
        del self._val
        del self.file
        self.mom = img
        self.offset = offset
        self.max = mx
        self._byte_cursor = 0

    def __len__(self) -> int:
        return self.max - self.offset

    def __call__(self) -> image_t:
        return self.mom[self.offset: self.max]

    def __setitem__(self, sect_index: int | slice, value: bytes | image_t):
        if isinstance(sect_index, int):
            if not 0 <= sect_index < self.__len__():
                raise IndexError
            if not isinstance(value, bytes): raise TypeError
            self.mom[self.offset + sect_index] = value
        elif isinstance(sect_index, slice):
            if any((sect_index.start < 0, self.__len__() <= sect_index.stop)): raise IndexError
            if not isinstance(value, list) and isinstance(value[0], bytes): raise TypeError
            self.mom[self.offset + sect_index.start: self.offset + sect_index.stop] = value

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
        else:
            raise TypeError

    def __contains__(self, item) -> bool:
        return item in self()

    def sect_buff(self, sect_index: int = 0):
        if sect_index == self.mom._sect_cursor:
            self.mom.iner_flush()
        if not 0 <= sect_index < self.__len__():
            raise IndexError
        super().sect_buff(sect_index)

    def byte_seek_abs(self, byte_offset: int, auto_sect=True):
        if auto_sect:
            designated_sect = byte_offset // Sector_sz
            if self._sect_cursor != designated_sect:
                self.sect_buff(designated_sect)
        self._byte_cursor = byte_offset % Sector_sz

    def byte_seek_rel(self, byte_offset: int, auto_sect=True):
        self._byte_cursor += byte_offset
        if auto_sect:
            sect_offset = self._byte_cursor // Sector_sz
            if sect_offset:
                self.sect_buff(self._sect_cursor + sect_offset)
        self._byte_cursor %= Sector_sz

    def byte_tell(self) -> int:
        return self._byte_cursor

    def read(self, length: int, advance=True) -> bytes:
        if self._sect_cursor is None:
            raise Exception("image buffer was not initilized. you need to call sect_buff()")
        end = self._byte_cursor + length
        back = self.buffer[self._byte_cursor: end]
        if advance:
            self._byte_cursor = end
        return bytes(back)

    def write(self, value: bytes | int, advance=True):
        if isinstance(value, int): value = value.to_bytes(byteorder="little")
        if self._sect_cursor is None:
            raise Exception("image buffer was not initilized. you need to call sect_buff()")
        end = self._byte_cursor + len(value)
        self.buffer[self._byte_cursor: end] = value
        if advance:
            self._byte_cursor = end

    def flush(self):
        self.iner_flush()


class Fat(SeqWrapper):
    item_type = int

    class ReadError(Exception):
        def __init__(self, code, back):
            opt = "Empty" if code == 0 else "Bad" if code == 0xFF7 else "Reserved"
            message = f"Read Error: {opt} cluster"
            super().__init__(message)
            self.code = code
            self.back = back

    @abstractmethod
    def __init__(self):
        self._val: fat_t = []
        self.img = None
        self.entries = 0

    @abstractmethod
    def file_locate(self, pointer: int) -> loc_t:
        raise StopIteration

    def fili_locate(self) -> tuple[list[loc_t], loc_t]:
        """
        :return: list of locs for all files + a loc of the empty clusters
        """
        unchecked = set(range(Fat_Offset, self.entries))
        fili = []
        empty = []
        while unchecked:
            pointer = min(unchecked)
            try:
                file = self.file_locate(pointer)
            except Fat.ReadError as err:
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
    def file_add(self, allocated: fat_t):
        pass

    @abstractmethod
    def file_del(self, allocated: fat_t):
        pass


class Fat12(Fat):
    def __init__(self, image: Imagepart, entrys: int):
        buffer = image[:]
        buffer = b''.join(buffer)
        self._val = fat12_factory(buffer, entrys)
        self.img = image
        self.entries = entrys

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
                    raise Fat.ReadError(pointer, file)
        return file

    def file_add(self, allocated: fat_t):
        self.img.sect_buff(allocated[0] // Sector_sz)
        for pl, cluster in enumerate(allocated[1:]):
            # the index pl is off by 1 from the index of cluster in allocated
            self[allocated[pl]] = cluster
            self.image_update(allocated[pl], cluster)
        self[allocated[-1]] = 0xfff
        self.image_update(allocated[-1], 0xfff)

    def file_del(self, pointer: int):
        file = self.file_locate(pointer)
        self.img.sect_buff()
        for loc in file:
            self._val[loc] = 0
            self.image_update(loc, 0)

    def image_update(self, loc: int, value: int):
        self.img.byte_seek_abs(loc * 3 // 2)
        b = self.img.read(2, advance=False)
        if loc % 2:
            self.img.write((b[0] % 0x10) +  0x10 * (value % 0x10))
            self.img.write(value // 0x10)
        else:
            self.img.write(value % 0x100)
            self.img.write((value // 0x100) + 0x10 * (b[1] // 0x10))


class Directory(SeqWrapper):
    item_type = int

    class ReadError(Exception):
        pass

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
            raise Directory.ReadError(err_massage)
        return entry

    def file_add(self, file_nom: str, pointer: int, system=False) -> int:
        entry = entry_from_file(file_nom)
        entry.first_cluster = pointer
        entry.hidden = entry.system_file = system
        self.img.sect_buff()
        φ = 0
        while True:
            b = self.img.read(1, advance=False)
            if b in {b'\xe5', b'\0'}:
                break
            else:
                φ += 1
                self.img.byte_seek_rel(0x20)
        entry.physical_index = φ
        self.img.write(entry.to_image())
        self._val.append(entry)
        return entry.size

    def file_del(self, entry: FileEntry):
        virtual_index = self._val.index(entry)
        self.img.sect_buff()
        self.img.byte_seek_abs(entry.physical_index * Dir_Entry_sz)
        candidate = self.img.read(Dir_Entry_sz, False)
        try:
            candidate = FileEntry.from_image(candidate, entry.physical_index)
            assert entry == candidate
        except AssertionError:
            raise Directory.ReadError(f"{entry} \ndiffer from \n{candidate}")
        if virtual_index == len(self._val) - 1:
            self.img.write(b"\0")
        else:
            self.img.write(b'\xE5')
        del self._val[virtual_index]

    @property
    def fili_names(self) -> Iterator[str]:
        return (f.name for f in self._val)


class FatIDError(Exception):
    def __init__(self, fat_id):
        message = f"Fat ID {fat_id:X} is invalid or out of scope due to modernity"
        super().__init__(message)


def disk_factory(scroll_nom: str | os.PathLike) -> image_t:
    with open(scroll_nom, "br") as file:
        scroll = file.read()
    disk = []
    # noinspection PyUnboundLocalVariable
    while scroll:
        sector, scroll = scroll[:Sector_sz], scroll[Sector_sz:]
        disk.append(sector)
    return disk


def fat12_factory(buffer: bytes, entrys: int) -> fat_t:
    table = []
    for _ in range(0, entrys, 2):
        entrii, buffer = buffer[:3], buffer[3:]
        # elements of bytes object are ints
        table.append(entrii[0] + 0x100 * (entrii[1] & 0xF))
        table.append((entrii[1] // 0x10) + 0x10 * entrii[2])
    if entrys % 2 == 0:
        table.append(buffer[0] + 0x100 * (buffer[1] & 0xF))
    return table


def fat12_to_buffer(call: fat_t) -> image_t:
    buffer: bytes = b''
    while True:
        try:
            even, odd, call = call[0], call[1], call[2:]
        except IndexError:
            break
        else:
            buffer += (even + 0x1000 * odd).to_bytes(length=3, byteorder="little")
    if len(call):
        buffer += (call[0]).to_bytes(length=2, byteorder="little")
    buffer += b'\xF6' * (-len(buffer) % Sector_sz)
    return [buffer[i: i + Sector_sz] for i in range(0, len(buffer), Sector_sz)]


def ms_time(call: bytes) -> dict[str, int]:
    hour = call[1] // 8  # 11..16
    return {'second': 2 * call[0] % 0x20,  # 0..5
            'minute': call[0] // 0x20 + 8 * (call[1] % 8),  # 5..11
            'hour'  : hour - 1 if hour else hour}
    # hour need to be converted from 0..24 (0 being dummy) on fat to 0..23 on python


def ms_date(call: bytes) -> dict[str, int]:
    return {'day'  : (call[0] % 0x20) or 1,  # 0..5
            'month': (call[0] // 0x20 + 8 * call[1] % 2) or 1,  # 5..9
            'year' : 1980 + call[1] // 2}  # 9..16


def to_ms_time(call: datetime.datetime | datetime.date) -> bytes:
    back = b''
    # in case of doubt, preserve 0 input (only forces hour, day and month to 0 instead of 1)
    if isinstance(call, datetime.datetime):
        back += (call.second // 2 + 0x20 * call.minute + 0x800 * (call.hour + 1)).to_bytes(2, 'little')
        # if not any((call.second, call.minute, call.hour)):
        if back == b'\x08\0':
            back = b'\0' * 2
    back += (call.day + 0x20 * call.month + 0x200 * (call.year - 1980)).to_bytes(2, 'little')
    # if call.day == call.month == 1 and call.year == 1980:
    if back[-2:] == b'\x21\0':
        back = back[:-2] + b'\0' * 2
    return back


def dir_factory(dir_img: image_t) -> dir_t:
    folder = []
    for pl, sector in enumerate(dir_img):
        while sector:
            entry, sector = sector[:Dir_Entry_sz], sector[Dir_Entry_sz:]
            if entry[0] == 0xe5:
                continue
            elif entry[0] == 0:
                break
            folder.append(FileEntry.from_image(entry, pl))
        else:
            continue
        break
    return folder


def sector_from_fat_loc(pointer: int, struct: DiskStruct) -> int:
    return (pointer - Fat_Offset) * struct.cluster_sects


def fat_loc_from_sector(sect: int, struct: DiskStruct) -> int:
    return sect // struct.cluster_sects + Fat_Offset


def loc_list_to_ranges(loci: loc_t) -> list[tuple[int, int]]:
    back = []
    start = loci[0]
    for pl in range(0, len(loci) - 1):
        end = loci[pl] + 1
        if loci[pl+1] != end:
            back.append((start, end))
            start = loci[pl+1]
    back.append((start, loci[-1] + 1))
    return back


def file_read(file_nom: str) -> Generator[bytes, Any, None]:
    with open(file_nom, mode="rb") as file:
        while sector := file.read(Sector_sz):
            yield sector


def entry_from_file(file_nom: str) -> FileEntry:
    basename = os.path.basename(file_nom)
    basename, _, ext = basename.partition(".")
    create_second = os.path.getctime(file_nom)
    access_second = os.path.getatime(file_nom)
    write_second = os.path.getmtime(file_nom)
    time_structi = tuple(time.localtime(second) for second in (create_second, access_second, write_second))
    yeari = tuple((1980 +  (s.tm_year + 4) % 16) for s in time_structi)
    # the modal 16 year since 1980. 1980 is 12 in mode 16, so we need to add 4,
    # to put the year on the right place in the cycle.
    create_datetime, write_datetime  = ((strct.tm_mon, strct.tm_mday,
                                         strct.tm_hour, strct.tm_min, strct.tm_sec)
                                        for strct in (time_structi[0], time_structi[2]))
    create_datetime = datetime.datetime(yeari[0], *create_datetime)
    access_date = datetime.date(yeari[1], time_structi[1].tm_mon, time_structi[1].tm_mday)
    write_datetime = datetime.datetime(yeari[2], *write_datetime)
    size = os.path.getsize(file_nom)
    if not size:
        with open(file_nom, "r") as file:
            file.seek(0, 2)
            size = file.tell()
    return FileEntry(basename, ext, create_datetime, access_date, write_datetime, 0, size, 0)


def blank_prefix(host: Disk, fat_id: int) -> bytes:
    if fat_id is None:
        codex_struct = host.struct
        fat_id = codex_struct.fat_id
    else:
        codex_struct = DiskStruct(fat_id)
    boot = host.boot
    if fat_id == 0xFF and boot[3] == b'\x08':
        boot = boot[:3] + b'\x10' + boot[4:]
    codex_image = [boot]
    fat = [fat_id | 0xF00, 0xFFF] + [0] * codex_struct.fat_entrys
    codex_image += fat12_to_buffer(fat) * Fat_Numb
    return b''.join(codex_image)


def disk_format(host: Disk, codex_nom: str, fat_id: int = None):
    codex_nom = pathlib.Path(codex_nom)
    if not codex_nom.suffix.startswith(".im"):
        raise ArgumentError(None, "I'll only agree to format files with an im? extention")
    codex_struct = DiskStruct(fat_id) if fat_id is not None else host.struct
    if os.path.exists(codex_nom):
        scroll = Disk(codex_nom, read_only=False)
        root_dir = bytearray(b''.join(scroll.img[codex_struct.root_dir_floor:codex_struct.files_floor]))
        suffix =  b''.join(scroll.img[codex_struct.files_floor:])
    else:
        root_dir = bytearray(b'\xF6' * Sector_sz * codex_struct.root_dir_sects)
        suffix = b'\xF6' * codex_struct.files_sz
    for i in range(0, Sector_sz * codex_struct.root_dir_sects, 0x20):
        root_dir[i:i+1] = b'\xE5'
    prefix = blank_prefix(host, fat_id)
    codex = prefix + bytes(root_dir) + suffix
    with open(codex_nom, "bw+") as file:
        file.write(codex)


def empty_disk(host: Disk, codex_nom: str, fat_id: int = None) -> Disk:
    codex_nom = pathlib.Path(codex_nom)
    if not codex_nom.suffix.startswith(".im"):
        raise ArgumentError(None, "I'll only agree to format files with an im? extention")
    codex_struct = DiskStruct(fat_id) if fat_id is not None else host.struct
    prefix = blank_prefix(host, fat_id)
    root_dir = (b'\xE5' + b'\xF6' * 0x1F) * codex_struct.root_dir_entries
    suffix = b'\xF6' * codex_struct.files_sz
    codex = prefix + root_dir + suffix
    with open(codex_nom, "bw+") as file:
        file.write(codex)
    return Disk(codex_nom, read_only=False)


def folder_to_disk(host, codex_nom: str, fat_id: int):
    codex = empty_disk(host, codex_nom+".img", fat_id)
    codex_index = 0
    folder = [s.upper() for s in os.listdir(codex_nom)]
    for file_nom in ("IBMBIO.COM", "IBMDOS.COM", "COMMAND.COM"):
        file = os.path.join(codex_nom, file_nom)
        if os.path.isfile(file):
            codex.file_add(file, system=True)
            folder.remove(file_nom.upper())
    for file in folder:
        file = os.path.join(codex_nom, file)
        if not os.path.isfile(file): continue
        try:
            codex.file_add(file)
        except Disk.OutOfSpace:
            codex_index += 1
            codex = empty_disk(host, f"{codex_nom}{codex_index}.img", fat_id)
            codex.file_add(file)


if __name__ == "__main__":
    def main():
        parser = argparse.ArgumentParser()
        parser.add_argument("action")
        parser.add_argument("scroll")
        parser.add_argument("-f", "--folder", default=None)
        parser.add_argument("-n", "--new", action="store_true")
        args = parser.parse_args()
        scroll = pathlib.Path(args.scroll)

        disk = Disk(scroll, read_only=False)
        fili, emp = disk.fat.fili_locate()
        disk.disk_offset_print(fili)
        print(f"empty {emp}")
        if args.action.startswith("extract"):
            disk.fili_extract()
        # for arg in sys.argv[2:]:
        #     disk.file_extract(arg)

        # disk.file_add(sys.argv[2])
        if args.action.startswith("create"):
            folder_to_disk(disk, args.folder, 0xff)


    main()
