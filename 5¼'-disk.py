Sector_sz = 0x200
Fat12_sz = 0x155
Cylinders = 40
Reserved_Sectors = 1
Fat_Numb = 2
Hidden_Sector_Numb = 0
First_Physical_sector = 1

Capacity = (320, 160, 360, 180)  # in KB
Track_Sectors = (8, 8, 9, 9)
Heads = (2, 1, 2, 1)
Cluster_Sectors = (2, 1, 2, 1)
Root_Dir_Entries = (0x70, 0x40, 0x70, 0x40)


disk_t = tuple[bytes, ...]
fat_t = tuple[int, ...]
loc_t = list[int]


class Disk:
    def __init__(self, *args):
        val = disk_factory(*args)
        self.val = val
        self.boot = val[0]
        assert val[1] == val[2]
        self.fat = val[1]
        self.fat_id = self.fat[0]
        fat_index = 0xff - self.fat_id
        self.track_sz = Track_Sectors[fat_index] * Sector_sz
        self.cluster_sz = Cluster_Sectors * Sector_sz
        self.root_dir = root_dir_factory(val)


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
    sector, tail = sector[:-2], sector[-2:]  # 0x200 % 3 = 2
    while sector:
        entrii, sector = sector[:3], sector[3:]
        entrii = tuple(int(e) for e in entrii)
        table.append(entrii[0] + 0x100 * (entrii[1] % 0x10))
        table.append((entrii[1] // 0x10) + 0x10 * entrii[2])
    table.append(tail[0] + 0x100 * (tail[1] % 0x10))
    return tuple(table)

def root_dir_factory(disk: disk_t):
    dir = []
    

class DiskReadError(Exception):
    def __init__(self, code, back):
        opt = "Empty" if code == 0 else "Bad" if code == 0xFF7 else "Reserved"
        message = f"Read Error: {opt} cluster"
        super().__init__(message)
        self.code = code
        self.back = back


def file_locate(fat: fat_t, head: int) -> loc_t:
    """
    :param fat: array of pointers
    :param head: first logical sector
    """
    pointer = head
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


def file_get(disk: disk_t, fat: fat_t, head: int):
    file = file_locate(fat, head)
    return b"".join(disk[i] for i in file)


def fili_locate(fat: fat_t) -> tuple[list[loc_t], list[int]]:
    unchecked = set(range(1, Fat12_sz))
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
file_headi_get
empty space locate
file_add
format
"""

disk = disk_factory(
    r"D:\Computing\86Box-Optimized-Skylake-32-c3294fcf\disks\IBM PC-DOS 1.10 (5.25-160k)\Images\Raw\DISK01.IMA",
    read_only=True)

fat12 = fat12_factory(disk[1])
fili, emp = fili_locate(fat12)
print("\n".join(str(f) for f in fili))
print(f"empty: {emp}")