sector_sz = 0x200
track_sz = 0x1000

disk_t = tuple[bytes, ...]
fat_t = tuple[int, ...]

# in 0x200 bytes or 0x1000 bits there are E3 12-bit entries. So actually only up to 0x0E3 is meaningfull


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
    def __init__(self, enum):
        enum = "Empty" if enum == 0 else "Bad" if enum == 0xFF7 else "Reserved"
        message = f"Read Error: {enum} cluster"
        super().__init__(message)


def file_locate(fat: fat_t, head: int):
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
                raise DiskReadError(pointer)
    return file


def file_get(disk: disk_t, fat: fat_t, head: int):
    file = file_locate(fat, head)
    return b"".join(disk[i] for i in file)


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
