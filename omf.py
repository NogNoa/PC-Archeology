import itertools
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Self, Optional
from pathlib import Path

BIG_SEGMENT = 0x1000


@dataclass
class PhysicalAddress:
    segment: int  # 16-bit
    offset: int  # 16-bit


@dataclass
class LoadtimeLocateable:
    ltl_dat: int  # byte
    in_group: bool
    max_length: int  # 16-bit
    group_offset: int  # 16-bit

    def __init__(self, ltl_dat: int, max_length: int, group_offset: int):
        bsm = ltl_dat & 1
        self.in_group = ltl_dat == 0x80
        assert not ltl_dat & 0b1111110
        if bsm:
            assert max_length == 0
            max_length = BIG_SEGMENT
        assert self.in_group or group_offset == 0
        self.ltl_dat = ltl_dat
        self.max_length = max_length
        self.group_offset = group_offset


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
    LIDATA = 0xA2


class DescriptorType(Enum):
    SI = 0xFF


def index_create(body: bytes) -> tuple[int, bytes]:
    val = body[0]
    if val & 0x80:
        return (val ^ 0x80) << 8 | body[1], body[2:]
    else:
        return val, body[1:]


class Subrecord:
    pass


@dataclass
class NAME(Subrecord):
    length: int  # byte
    body: bytes

    @classmethod
    def create(cls, val: bytes) -> tuple[Self, bytes]:
        length = val[0]
        return cls(length, val[1:length + 1]), val[length + 1:]


class NUMBER(Subrecord):
    val: int  # 32-bit

    def __init__(self, body):
        assert len(body) == 4
        self.val = body[3] << 0x18 | body[2] << 0x10 | body[1] << 8 | body[0]

    def __str__(self):
        return str(self.val)

    def __repr__(self):
        return repr(self.val)


class Fixupp(Subrecord):

    def __init__(self, *args):
        pass


@dataclass
class ModEnd(Subrecord):
    main: bool
    locateability: str
    start_addr: Optional[PhysicalAddress | Fixupp]

    def __init__(self, body: bytes):
        mod_typ = body[0]
        self.is_logical = mod_typ & 1
        self.locateability = "logical" if self.is_logical & 1 else "physical"
        mattr = mod_typ >> 6
        assert not (mod_typ & ((1 << 6) - 2))  # xx00000x
        self.main = bool(mattr & 2)
        self.has_start_addrs = bool(mattr & 1)
        if self.has_start_addrs:
            if self.is_logical:
                self.start_addr = Fixupp(body[1:])
            else:
                assert len(body) == 5
                segment = body[2] << 8 | body[1]
                offset = body[4] << 8 | body[3]
                self.start_addr = PhysicalAddress(segment, offset)
        else:
            self.start_addr = None
            self.locateability = "N/A"


@dataclass
class ExtDef(Subrecord):
    name: NAME
    obj_type: str | int
    index: int

    @classmethod
    def create(cls, val: bytes, module: "Module") -> tuple[Self, bytes]:
        name, val = NAME.create(val)
        obj_type = val[0]
        ext_index = next(module.ext_numb)
        return cls(name, obj_type, ext_index), val[1:]


@dataclass
class Comment(Subrecord):
    com_class: int  # byte
    is_purgable: bool
    to_list: bool
    class_is_reserverd: bool
    body: bytes

    def __init__(self, body: bytes):
        head = body[0]
        assert not head & (0x40 - 1)
        self.is_purgable = not (head & 0x80)
        self.to_list = not (head & 0x40)
        self.com_class = body[1]
        self.class_is_reserverd = not (self.com_class & 0x80)
        self.body = body[2:]


@dataclass
class Base(Subrecord):
    grp_ind: int = 0
    seg_ind: int = 0
    frame_numb: Optional[int] = None

    @classmethod
    def create(cls, body: bytes):
        base = cls()
        base.grp_ind, body = index_create(body)
        base.seg_ind, body = index_create(body)
        if not base.seg_ind:
            assert not base.grp_ind
            base.frame_numb, body = body[:2], body[2:]
        return base, body


@dataclass
class Public(Subrecord):
    name: NAME
    offset: int
    type_index: int

    @classmethod
    def create(cls, body: bytes):
        name, body = NAME.create(body)
        offset = body[1] << 8 | body[0]
        type_index, body = index_create(body[2:])
        return cls(name, offset, type_index), body


@dataclass
class PubDef(Subrecord):
    base: Base
    body: tuple[Public, ...]

    def __init__(self, val: bytes):
        self.base, val = Base.create(val)
        body = []
        while val:
            pub, val = Public.create(val)
            body.append(pub)
        self.body = tuple(body)


@dataclass
class LinNum(Subrecord):
    base: Base
    body: tuple[tuple[int, int]]

    def __init__(self, val: bytes):
        self.base, val = Base.create(val)
        body = []
        while val:
            line_num = val[1] << 8 | val[0]
            assert line_num < 0x8000
            offset = val[2] << 8 | val[1]
            body.append((line_num, offset))


@dataclass
class Attr(Subrecord):
    locateability: str
    alignment: str
    is_physical: bool
    named: bool
    combination: int
    page_resident: bool
    child: PhysicalAddress | LoadtimeLocateable

    @classmethod
    def Create(cls, body):
        acbp = body[0]
        align_type = acbp >> 5
        combination = (acbp >> 2) & 7
        big = acbp & 2
        page_resident = bool(acbp & 1)
        assert align_type != 7
        is_physical = align_type in {0, 5}
        locateability = "absolute" if is_physical else "load-time locateable" if align_type == 6 else "relocateable"
        alignment = "byte" if align_type == 1 else "word" if align_type == 2 else \
            "paragraph" if align_type in {3, 6} else "page" if align_type == 4 else \
            "unknown"
        named = align_type != 5
        if is_physical:
            assert combination == 0
            subbody = PhysicalAddress(body[2] << 8 | body[1], body[3])
            rest = body[4:]
        elif align_type == 6:
            subbody = LoadtimeLocateable(body[1], body[3] << 8 | body[2], body[5] << 8 | body[4])
            rest = body[6:]
        else:
            subbody = b''
            rest = body[1:]
        back = Attr(locateability, alignment, is_physical, named, combination, page_resident, subbody)
        back.big = big
        return back, rest


@dataclass
class SegDef(Subrecord):
    index: int  # 31-bit
    seg_attr: Attr
    length: int
    seg_name: int
    class_name: int
    Overlay_name: int

    # noinspection PyUnresolvedReferences
    def __init__(self, index, body: bytes):
        self.index = index
        self.seg_attr, body = Attr.Create(body)
        self.length = body[1] << 8 | body[0]
        body = body[2:]
        try:
            self.seg_attr.big
        except AttributeError:
            return
        if self.seg_attr.big:
            assert self.length == 0
            self.length = BIG_SEGMENT
        if isinstance(self.seg_attr.child, LoadtimeLocateable):
            assert self.length <= self.seg_attr.child.max_length
        if self.seg_attr.named:
            defnames = []
            for _ in range(3):
                name_index, body = index_create(body)
                defnames.append(name_index)
            self.seg_name, self.class_name, self.Overlay_name = defnames
        else:
            self.seg_name, self.class_name, self.Overlay_name = (0,) * 3


@dataclass
class GroupComponentDescriptor:
    desc_type: int | DescriptorType
    body: bytes | int

    @classmethod
    def Create(cls, val: bytes):
        try:
            desctype = DescriptorType(val[0])
        except ValueError as err:
            print(err)
            desctype = val[0]
        body = b''
        if desctype == DescriptorType.SI:
            body = val[1]
            val = val[2:]
        return cls(desctype, body), val


@dataclass
class GroupDef(Subrecord):
    name_index: int
    descriptors: list[GroupComponentDescriptor]

    def __init__(self, body: bytes):
        self.name_index, body = index_create(body)
        self.descriptors = []
        while body:
            descriptor, body = GroupComponentDescriptor.Create(body)
            self.descriptors.append(descriptor)


@dataclass
class Record:
    rectype: RecordType | int   # byte
    typehex: str
    length: int  # 16-bit
    body: bytes | Subrecord | list[Subrecord]

    @classmethod
    def create(cls, parent, val: bytes) -> tuple[Self, bytes]:
        try:
            rectype = RecordType(val[0])
        except ValueError as err:
            print(err)
            rectype = val[0]
        length = val[2] << 8 | val[1]
        val, rest = val[:length+3], val[length+3:]
        body = val[3:-1]
        body = cls.body_parse(rectype, body, parent)
        assert sum(val) % 0x100 == 0
        try:
            typehex = '%x' % rectype.value
        except AttributeError:
            typehex = '%x' % rectype
        return cls(rectype, typehex , length, body), rest

    @staticmethod
    def body_parse(rectype: RecordType, val: bytes, module: "Module") -> list[Subrecord]:
        if rectype in {RecordType.THEADR, RecordType.LHEADR}:
            body, remainder = NAME.create(val)
            assert not remainder
        elif rectype == RecordType.LNAMES:
            body = []
            while val:
                name, val = NAME.create(val)
                body.append(name)
        elif rectype == RecordType.MOOEND:
            body = ModEnd(val)
        elif rectype == RecordType.SEGDEF:
            body = SegDef(next(module.seg_numb), val)
        elif rectype == RecordType.COMMENT:
            body = Comment(val)
        elif rectype == RecordType.GRPDEF:
            body = GroupDef(val)
        elif rectype == RecordType.PUBDEF:
            body = PubDef(val)
        elif rectype == RecordType.EXTDEF:
            body = []
            while val:
                external, val = ExtDef.create(val, module)
                body.append(external)
        elif rectype == RecordType.LINNUM:
            body = LinNum(val)
        else:
            body = val
        return body


class Module:
    def __init__(self, body: bytes):
        val: list[Record] = []
        self.seg_numb = itertools.count(start=1)
        self.ext_numb = itertools.count(start=1)
        while body:
            rec, body = Record.create(self, body)
            val.append(rec)
        self.val = tuple(val)

    def __call__(self) -> tuple[Record, ...]:
        return self.val


@dataclass
class DeserializedModule:
    lnames: tuple[str, ...] = ''
    typedefs: tuple[str, ...] = ()
    segments: list[dict] = ()
    groups: list[dict] = ()
    publics: list[dict] = ()
    externals: list[dict] = ()

    def __init__(self, module: tuple[Record, ...]):
        self.segments = []
        self.groups = []
        self.publics = []
        self.externals = []
        for rec in module:
            src = rec.body
            if isinstance(src, GroupDef):
                group = {"descriptors": src.descriptors}
                if src.name_index and self.lnames:
                    group["name"] = self.lnames[src.name_index - 1]
                else:
                    # noinspection PyTypeChecker
                    group["name"] = ''
                self.groups.append(group)
            elif isinstance(src, PubDef):
                pubdef = {"publics": tuple(
                    {'name': str(pub.name.body),
                     'ofsset' : pub.offset
                     }
                    for pub in src.body)
                }
                if src.base.grp_ind and self.groups:
                    # noinspection PyTypeChecker
                    pubdef["group"] = self.groups[src.base.grp_ind - 1]
                    if src.base.grp_ind and self.segments:
                        # noinspection PyTypeChecker
                        pubdef["segment"] = self.segments[src.base.grp_ind - 1]
                if self.typedefs:
                    for pl, pub in enumerate(src.body):
                        if pub.type_index:
                            pubdef["publics"][pl]['type'] = self.typedefs[pub.type_index - 1]
                self.publics.append(pubdef)
            elif isinstance(src, SegDef):
                segment = {"name": '',
                           "attrs": src.seg_attr}
                if self.lnames:
                    for name_ind in (src.seg_name, src.class_name, src.Overlay_name):
                        if name_ind:
                            segment["name"] += " " + str(self.lnames[name_ind - 1])
                segment["name"] = segment["name"].strip()
                self.segments.append(segment)
            elif isinstance(src, ExtDef):
                extdef = {"name": str(src.name.body),
                          "type": src.obj_type
                          }
                if src.obj_type and self.typedefs:
                    obj_type = self.typedefs[src.obj_type - 1]
            elif rec.rectype == RecordType.LNAMES:
                self.lnames = tuple(n.body for n in rec.body)


scroll_path = Path(sys.argv[1])
# noinspection PyUnresolvedReferences
for scroll in scroll_path.iterdir():
    if scroll.suffix.lower() != ".obj":
        continue
    # noinspection PyUnresolvedReferences
    codex_path = Path(scroll.with_suffix('.record'))
    with open(scroll, "rb") as f:
        content = f.read()
    module = Module(content)
    with open(codex_path, "w") as f:
        f.writelines((str(m).replace(', ', ',\t') + '\n' for m in module()))
    print(DeserializedModule(module()))

