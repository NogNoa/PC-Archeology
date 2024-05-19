sector_sz = 0x200
track_sz = 0x1000
fat12_sz = 0x155

disk_t = tuple[bytes, ...]
fat_t = tuple[int, ...]
loc_t = list[int]

def disk_factory(scroll_nom: str, read_only) -> disk_t:
    mode = "br" if read_only else "br+"
    with open(scroll_nom, mode) as file:
        scroll = file.read()
    disk = []
    while scroll:
        sector, scroll = scroll[:sector_sz], scroll[sector_sz:]
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
        pointer = fat[pointer]
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
    unchecked = set(range(fat12_sz))
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
        else:
            rem = file
            fili.append(file)
        unchecked -= rem
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
assert disk[1] == disk[2]
fat12 = fat12_factory(disk[1])
pass
