import sys
from dataclasses import dataclass
from enum import Enum
from typing import Self
from pathlib import Path


class RecordType(Enum):
    THEADR = 0x80
    MOOEND = 0x8A
    EXTDEF = 0x8C
    COMMENT = 0x88
    PUBDEF = 0x90
    LINNUM = 0x94
    LNAMES = 0x96
    SEGDEF = 0x98
    GRPDEF = 0x9A
    FIXUPP = 0x9C
    LEDATA = 0xA0


@dataclass
class Subrecord:
    body: bytes


class NAME(Subrecord):
    length: int
    body: bytes

@dataclass
class Record:
    rectype: RecordType | int  # byte
    length: int  # 16-bit
    body: Subrecord

    @classmethod
    def create(cls, val: bytes) -> tuple[Self, bytes]:
        try:
            rectype = RecordType(val[0])
        except ValueError as err:
            print(err)
            rectype = val[0]
        length = val[2] << 8 | val[1]
        val, rest = val[:length+3], val[length+3:]
        body = Subrecord(val[3:-1])
        assert sum(val) % 0x100 == 0
        return cls(rectype, length, body), rest


module: list[Record] = []
scroll_path = Path(sys.argv[1])
codex_path = Path(scroll_path.with_suffix('.record'))
with open(scroll_path, "rb") as file:
    scroll = file.read()
if scroll not in locals() or not scroll:
    # pycharm complained that scroll may not initialize
    # but I don't think this is possible.
    print(scroll_path.name, "not found", file=sys.stderr)
while scroll:
    rec, scroll = Record.create(scroll)
    module.append(rec)
with open(codex_path, "w") as file:
    file.writelines((str(m).replace(', ', ',\t') + '\n' for m in module))

