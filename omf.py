import sys
from dataclasses import dataclass
from enum import Enum
from typing import Self


class RecordType(Enum):
    COMMENT = 0x88
    THEADR = 0x80


@dataclass
class Record:
    rectype: RecordType | int  # byte
    length: int  # 16-bit
    body: bytes

    @classmethod
    def create(cls, val: bytes) -> tuple[Self, bytes]:
        try:
            rectype = RecordType(val[0])
        except ValueError as err:
            print(err)
            rectype = val[0]
        length = val[2] << 8 | val[1]
        val, rest = val[:length+3], val[length+3:]
        body = val[3:-1]
        assert sum(val) % 0x100 == 0
        return cls(rectype, length, body), rest


module: list[Record] = []
with open(sys.argv[1], "rb") as file:
    scroll = file.read()
    if scroll not in locals() or not scroll:
        # pycharm complained that scroll may not initialize
        # but I don't think this is possible.
        print(file, "not found", file=sys.stderr)
while scroll:
    rec, scroll = Record.create(scroll)
    module.append(rec)
print(*module, sep='\n')

