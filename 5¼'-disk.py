sector_sz = 0x200
track_sz = 0x1000


def disk_factory(scroll_nom: str) -> tuple[bytes, ...]:
    with open(scroll_nom, "br") as file:
        scroll = file.read()
    disk = []
    while scroll:
        sector, scroll = scroll[:sector_sz], scroll[sector_sz:]
        disk.append(sector)
    return tuple(disk)


def fat_factory(sector: bytes) -> tuple[int, ...]:
    table = []
    while sector:
        entrii, sector = sector[:3], sector[3:]
        entrii = tuple(int(e) for e in entrii)
        table.append(entrii[0] + 0x100 * (entrii[1] % 0x10))
        table.append((entrii[1] // 0x10) + 0x100 * entrii[2])
    return tuple(table)

disk = disk_factory(
    r"D:\Computing\86Box-Optimized-Skylake-32-c3294fcf\disks\IBM PC-DOS 1.10 (5.25-160k)\Images\Raw\DISK01.IMA")
assert disk[1] == disk[2]
fat12 = fat_factory(disk[1])
pass
