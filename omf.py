from dataclasses import dataclass
from enum import Enum
from typing import Self


class RecordType(Enum):
    COMMENT = 0x88
    THEADR = 0x80


@dataclass
class Record:
    rectype: RecordType  # byte
    length: int  # 16-bit
    body: bytes

    @classmethod
    def create(cls, val: bytes) -> Self:
        rectype = RecordType(val[0])
        length = val[2] << 8 | val[1]
        body = val[3:-1]
        assert length == len(val[3:])
        assert sum(val) % 0x100 == 0
        return cls(rectype, length, body)
