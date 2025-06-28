import sys
from dataclasses import dataclass
from enum import Enum
from typing import Self
from pathlib import Path


class RecordType(Enum):
    THEADR = 0x80
    LHEADR = 0x82
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


@dataclass
class NAME(Subrecord):
    length: int
    body: bytes

    def check(self):
        assert len(self.body) == self.length


class NUMBER(Subrecord):
    val: int

    def __init__(self, body):
        assert len(body) == 4
        self.val = body[3] << 0x18 | body[2] << 0x10 | body[1] << 8 | body[0]


@dataclass
class Record:
    rectype: RecordType | int   # byte
    typehex: str
    length: int  # 16-bit
    body: Subrecord | list[Subrecord]

    @classmethod
    def create(cls, val: bytes) -> tuple[Self, bytes]:
        try:
            rectype = RecordType(val[0])
        except ValueError as err:
            print(err)
            rectype = val[0]
        length = val[2] << 8 | val[1]
        val, rest = val[:length+3], val[length+3:]
        if rectype in {RecordType.THEADR, RecordType.LHEADR}:
            body = NAME(length=val[3], body=val[4:-1])
            body.check()
        elif rectype in {RecordType.LNAMES}:
            body = []
            while val:
                length = val[0]
                body.append(NAME(length=length, body=val[1:length+1]))
                val = val[length+1:]
        else:
            body = Subrecord(val)
        assert sum(val) % 0x100 == 0
        return cls(rectype, '%x' % rectype.value , length, body), rest


module: list[Record] = []
scroll_path = Path(sys.argv[1])
codex_path = Path(scroll_path.with_suffix('.record'))
with open(scroll_path, "rb") as file:
    scroll = file.read()
if 'scroll' not in locals() or not scroll:
    # pycharm complained that scroll may not initialize
    # but I don't think this is possible.
    print(scroll_path.name, "not found", file=sys.stderr)
while scroll:
    rec, scroll = Record.create(scroll)
    module.append(rec)
with open(codex_path, "w") as file:
    file.writelines((str(m).replace(', ', ',\t') + '\n' for m in module))

