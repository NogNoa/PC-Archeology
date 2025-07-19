import itertools
import sys
from copy import copy
from dataclasses import dataclass
from enum import Enum
from types import NoneType
from typing import Self, Optional
from pathlib import Path

BIG_SEGMENT = 0x1000


@dataclass
class PhysicalAddress:
    segment: int  # 16-bit
    offset: int  # 16-bit

    def deserialize(self):
        return self


@dataclass
class LoadtimeLocateable:
    ltl_dat: int  # byte
    max_length: int  # 16-bit
    group_offset: int  # 16-bit

    def __init__(self, val: bytes):
        self.ltl_dat = val[0]
        self.max_length = val[2] << 8 | val[1]
        self.group_offset = val[4] << 8 | val[3]
        bsm = self.ltl_dat & 1
        assert not self.ltl_dat & 0b1111110
        if bsm:
            assert self.max_length == 0
            self.max_length = BIG_SEGMENT

    def deserialize(self):
        back = copy(vars(self))
        back["in_group"] = self.ltl_dat == 0x80
        assert back["in_group"] or self.group_offset == 0
        del back["ltl_dat"]
        return back


class RecordType(Enum):
    THEADR = 0x80
    LHEADR = 0x82
    COMMENT = 0x88
    MODEND = 0x8A
    MOOEND2 = 0x8B
    EXTDEF = 0x8C
    TYPDEF = 0x8E
    PUBDEF = 0x90
    PUBDEF2 = 0x91
    LINNUM = 0x94
    LINNUM2 = 0x95
    LNAMES = 0x96
    SEGDEF = 0x98
    SEGDEF2 = 0x99
    GRPDEF = 0x9A
    FIXUPP = 0x9C
    LEDATA = 0xA0
    LEDATA2 = 0xA1
    LIDATA = 0xA2
    LIDATA2 = 0xA3
    LEXTDEF = 0xB4
    LPUBDEF = 0xB6


class DescriptorType(Enum):
    SI = 0xFF


class CommClass(Enum):
    Translator = 0
    IntelCopyright = 1
    MsDosVer = 0x9c
    MemoryModel = 0x9d
    DOSSEG = 0x9e
    LibIndicator = 0x9f
    OmfExtens = 0xa0
    SymoblDebugInfo = 0xA1
    LinkPass = 0xA2
    LIBMOD = 0xA3
    EXESTR = 0xA4
    INCERR = 0xA6
    NOPAD = 0xA7
    WKEXT = 0xA8
    LZEXT = 0xA9
    PHARLAP = 0xAA
    IPADATA = 0xAE
    IDMDLL = 0xAF


def index_create(body: bytes) -> tuple[int, bytes]:
    val = body[0]
    if val & 0x80:
        return (val ^ 0x80) << 8 | body[1], body[2:]
    else:
        return val, body[1:]


class Subrecord:
    def deserialize(self, *args, **kwargs):
        return copy(vars(self))


@dataclass
class NAME(Subrecord):
    length: int  # byte
    body: bytes

    @classmethod
    def create(cls, val: bytes) -> tuple[Self, bytes]:
        length = val[0]
        return cls(length, val[1:length + 1]), val[length + 1:]

    def deserialize(self) -> str:
        return self.body.decode("ascii")


@dataclass
class Thread(Subrecord):
    thread_type: str = ''
    method: int = 0
    thred: int = 0
    index: Optional[int] = None

    @classmethod
    def create(cls, val: bytes) -> tuple[Self, bytes]:
        trd_dat = val[0]
        assert not trd_dat & 0x20
        thread_type = 'frame' if trd_dat & 0x40 else 'target'
        method = (trd_dat >> 2) & 7
        assert not (method == 7 and thread_type == 'frame')
        if method in {3, 7}:
            index = val[2] << 8 | val[1]
            val = val[3:]
        elif thread_type == 'frame' and method in range(4, 7):
            index = None
            val = val[1:]
        else:
            index, val = index_create(val[1:])
        thred = trd_dat & 3
        return cls(thread_type, method, thred, index), val

    def deserialize(self, *args, **kwargs):
        thread = super().deserialize()
        thread["method"] = self.thread_type[0] + str(thread["method"])
        del thread["thread_type"]
        del thread["thred"]
        return thread


@dataclass
class Locat:
    relativity: str
    loc: str | int
    data_record_offset: int

    def __init__(self, val: bytes) -> None:
        assert val[0] & 0x80
        self.relativity = "segment" if val[0] & 0x40 else "self"
        if val[0] & 0x20:
            pass
        self.loc = (val[0] >> 2) & 7
        if self.loc <= 5:
            self.loc = ("lobyte", "offset", "base", "pointer", "hibyte", "loader-resolved offset")[self.loc]
        elif self.loc & 9:  # newer expanion
            self.loc = ("32_offset", "48_pointer", "32_loader-resolved offset")[self.loc ^ 8 >> 1]
        else:
            print("unknown loc", self.loc, file=sys.stderr)
        self.data_record_offset = ((val[0] & 3) << 8) | val[1]
        assert self.data_record_offset < 0x400


@dataclass
class FixDat:
    frame_by_thread: bool
    target_by_thread: bool
    no_target_displacement: bool
    frame_method: int
    target_method: int

    def __init__(self, val: int) -> None:
        self.frame_by_thread = bool(val & 0x80)
        self.target_by_thread = bool(val & 8)
        self.frame_method = (val >> 4) & 7
        self.target_method = val & 7
        self.no_target_displacement = bool(self.target_method & 4)
        assert not self.frame_by_thread or not self.frame_method & 4
        assert not self.target_by_thread or not self.no_target_displacement


@dataclass
class Fixupp(Subrecord):
    locat: Locat
    fix_dat: FixDat
    frame_datum: Optional[int]
    target_datum: Optional[int]
    target_displacement: Optional[int]

    @classmethod
    def create(cls, val: bytes) -> tuple[Self, bytes]:
        frame_datum = target_datum = target_displacement = None
        locat = Locat(val[:2])
        fix_dat = FixDat(val[2])
        val = val[3:]
        if not fix_dat.frame_by_thread and not fix_dat.frame_method & 4:
            if fix_dat.frame_method == 3:
                frame_datum, val = val[1] << 8 | val[0], val[2:]
            else:
                frame_datum, val = index_create(val)
        if not fix_dat.target_by_thread:
            if fix_dat.target_method & 3 == 3:
                target_datum, val = val[1] << 8 | val[0], val[2:]
            else:
                target_datum, val = index_create(val)
        if not fix_dat.no_target_displacement:
            target_displacement, val = val[1] << 8 | val[0], val[2:]
        return cls(locat, fix_dat, frame_datum, target_datum, target_displacement), val

    def deserialize(self, threads: dict, *args, **kwargs):
        fixup = super().deserialize()
        if self.fix_dat.frame_by_thread:
            fixup["fixdat"].frame_method = threads["frame"][
                self.fix_dat.frame_method]["method"]  # refer to most recent frame thread with this number
        if self.fix_dat.target_by_thread:
            fixup["fixdat"].target_method = threads["target"][
                self.fix_dat.target_method][
                "method"]  # refer to most recent target thread with this number
        return fixup


@dataclass
class Comment(Subrecord):
    com_class: CommClass  | int  # byte
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
        if self.com_class not in range(2, 0x9c) and self.com_class < 0xc0:
            self.com_class = CommClass(self.com_class)
        self.body = body[2:]


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
                self.start_addr, body = Fixupp.create(body[1:])
                assert not body
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
    obj_type: int
    index: int

    @classmethod
    def create(cls, val: bytes, module: "Module") -> tuple[Self, bytes]:
        name, val = NAME.create(val)
        obj_type = val[0]
        ext_index = next(module.ext_numb)
        return cls(name, obj_type, ext_index), val[1:]

    def deserialize(self, lnames: dict, typedefs: dict, *args, **kwargs) -> dict[str, str]:
        back = {"name": self.name.deserialize()}
        if self.obj_type and typedefs:
            back["type"] = typedefs[self.obj_type - 1]["name"]
        return back


@dataclass
class Base:
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
            base.frame_numb, body = body[1] << 8 | body[0], body[2:]
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

    def deserialize(self, lnames: dict, typedefs: dict, segments: list[dict], groups, *args, **kwargs) -> dict:
        pubdef = super().deserialize()
        for pub in pubdef["body"]:
            pub.name = pub.name.deserialize()
        if self.base.grp_ind and groups:
            pubdef["group"] = groups[self.base.grp_ind - 1]["name"]
            if self.base.seg_ind and segments:
                pubdef["segment"] = segments[self.base.seg_ind - 1]["name"]
        if typedefs:
            for pl, pub in enumerate(self.body):
                if pub.type_index:
                    pubdef["body"][pl]['type'] = typedefs[pub.type_index - 1]["name"]
        pubdef["Locatability"] = "logical" if self.base.frame_numb is None else "physical"
        return pubdef


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
            offset = val[3] << 8 | val[2]
            body.append((line_num, offset))
            val = val[4:]
        # noinspection PyTypeChecker
        self.body = tuple(body)


@dataclass
class SegAttr(Subrecord):
    align_type: int
    combination: int
    page_resident: bool
    big: bool
    child: PhysicalAddress | LoadtimeLocateable | NoneType

    @classmethod
    def Create(cls, body):
        acbp = body[0]
        align_type = acbp >> 5
        combination = (acbp >> 2) & 7
        big = bool(acbp & 2)
        page_resident = bool(acbp & 1)
        assert align_type != 7
        is_physical = align_type in {0, 5}
        if is_physical:
            assert combination == 0
            subbody = PhysicalAddress(body[2] << 8 | body[1], body[3])
            rest = body[4:]
        elif align_type == 6:
            subbody, rest = LoadtimeLocateable(body[1:6]), body[6:]
        else:
            subbody = None
            rest = body[1:]
        back = SegAttr(align_type, combination, page_resident, big, subbody)
        return back, rest

    def deserialize(self):
        back = super().deserialize()
        is_physical = self.align_type in {0, 5}
        back["is_physical"] = is_physical
        back["locateability"] = "absolute" if is_physical else \
            "load-time locateable" if self.align_type == 6 else \
            "relocateable"
        back["alignment"] = "byte" if self.align_type == 1 else \
            "word" if self.align_type == 2 else \
            "paragraph" if self.align_type in {3, 6} else \
            "page" if self.align_type == 4 else \
            "unknown"
        del back["align_type"]
        back["named"] = self.named
        if back["child"] is not None:
            back["child"] = self.child.deserialize()
        else:
            del back["child"]
        return back

    @property
    def named(self) -> bool:
        return self.align_type != 5


@dataclass
class SegDef(Subrecord):
    index: int  # 31-bit
    seg_attr: SegAttr
    length: int
    seg_name: int
    class_name: int
    Overlay_name: int

    # noinspection PyUnresolvedReferences
    def __init__(self, index, body: bytes):
        self.index = index
        self.seg_attr, body = SegAttr.Create(body)
        self.length = body[1] << 8 | body[0]
        body = body[2:]
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

    def deserialize(self, lnames: list[str]) -> dict[str, str | int | dict]:
        segment = super().deserialize()
        name_tii = ("seg_name", "class_name", "Overlay_name")
        for name_t in name_tii:
            name_ind = segment[name_t]
            if name_ind:
                segment[name_t] = lnames[name_ind - 1]
        segment["name"] = " ".join(segment[name_t] for name_t in name_tii)
        segment["seg_attr"] = self.seg_attr.deserialize()
        del segment["index"]
        return segment


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
    name: int
    descriptors: list[GroupComponentDescriptor]

    def __init__(self, body: bytes):
        self.name, body = index_create(body)
        self.descriptors = []
        while body:
            descriptor, body = GroupComponentDescriptor.Create(body)
            self.descriptors.append(descriptor)

    def deserialize(self, lnames, *args):
        back = super().deserialize()
        if self.name:
            # noinspection PyTypeChecker
            back["name"] = lnames[self.name - 1]
        return back


class TypDef(Subrecord):
    pass


@dataclass
class DataRec(Subrecord):
    segment: int
    offset: int

    def __init__(self, body: bytes):
        self.segment, body = index_create(body)
        self.offset = body[1] << 8 | body[0]
        self.body = body[2:]

    def deserialize(self, segments: list[dict]):
        datum = super().deserialize()
        if self.segment and segments:
            # noinspection PyTypeChecker
            datum["segment"] = segments[self.segment - 1]["name"]
        return datum


@dataclass
class LEData(DataRec):
    body: bytes

    def __init__(self, body: bytes):
        super().__init__(body)
        assert len(self.body) <= 0x400

    def deserialize(self, segments: list[dict], *args):
        datum = super().deserialize(segments)
        datum["locateability"] = "logical"
        datum["form"] = "enumerated"
        return datum


class IteratedBlock:
    repeats: int  # 16-bit
    blocks: int   # 16-bit
    content: bytes | list[Self]

    @classmethod
    def create(cls, body: bytes):
        self = cls()
        self.repeats = body[1] << 8 | body[0]
        assert self.repeats > 0
        self.blocks = body[3] << 8 | body[2]
        if not self.blocks:
            length = body[4]
            self.content = body[5:5+length]
            body = body[5+length:]
        else:
            self.content = []
            body = body[4:]
            for _ in range(self.blocks):
                blck, body = IteratedBlock.create(body)
                # noinspection PyTypeChecker
                self.content.append(blck)
        return self, body

    def __len__(self):
        if isinstance(self.content, list):
            internal_length = sum(len(b) for b in self.content)
        else:
            internal_length = len(self.content)
        return self.repeats * internal_length

    def __iter__(self):
        if isinstance(self.content, list):
            content = itertools.chain.from_iterable(self.content)
        else:
            content = self.content
        for _ in range(self.repeats):
            yield from iter(content)


@dataclass
class LIData(DataRec):
    body: IteratedBlock

    def __init__(self, val: bytes):
        super().__init__(val)
        # noinspection PyTypeChecker
        self.body, val = IteratedBlock.create(self.body)
        assert not val

    def deserialize(self, segments: list[dict[str, str]], *args):
        datum = super().deserialize(segments)
        datum["locateability"] = "logical",
        datum["form"] = "Iterated"
        return datum


@dataclass
class Record:
    rectype: RecordType | int   # byte
    typehex: str
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
        return cls(rectype, typehex , body), rest

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
        elif rectype in {RecordType.MODEND, RecordType.MOOEND2}:
            body = ModEnd(val)
        elif rectype in {RecordType.SEGDEF, RecordType.SEGDEF2}:
            body = SegDef(next(module.seg_numb), val)
        elif rectype == RecordType.COMMENT:
            body = Comment(val)
        elif rectype == RecordType.GRPDEF:
            body = GroupDef(val)
        elif rectype in {RecordType.PUBDEF, RecordType.PUBDEF2, RecordType.LPUBDEF}:
            body = PubDef(val)
        elif rectype in {RecordType.EXTDEF, RecordType.LEXTDEF}:
            body = []
            while val:
                external, val = ExtDef.create(val, module)
                body.append(external)
        elif rectype in {RecordType.LINNUM, RecordType.LINNUM2}:
            body = LinNum(val)
        elif rectype in {RecordType.LEDATA, RecordType.LEDATA2}:
            body = LEData(val)
        elif rectype in {RecordType.LIDATA, RecordType.LIDATA2}:
            body = LIData(val)
        elif rectype == RecordType.FIXUPP:
            body = []
            while val:
                head = val[0]
                if head & 0x80:
                    block, val = Fixupp.create(val)
                else:
                    block, val = Thread.create(val)
                body.append(block)
        else:
            print(rectype)
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
        for pl, rec in enumerate(val):
            if rec.rectype in {RecordType.EXTDEF, RecordType.LEXTDEF} and isinstance(rec.body, list):
                val[pl:pl+1] = (Record(rec.rectype, rec.typehex, ext) for ext in rec.body)
        self.val = tuple(val)

    def __call__(self) -> tuple[Record, ...]:
        return self.val


thread_dict = dict[str, dict[int, dict]]
ContextDef = ExtDef | TypDef | GroupDef
DatDef = ExtDef | PubDef | TypDef


@dataclass
class DeserializedModule:
    typedefs: list[dict]
    segments: list[dict]
    groups: list[dict]
    publics: list[dict]
    externals: list[dict]
    linenums: dict
    data: list[dict]
    threads: thread_dict
    end: ModEnd

    @staticmethod
    def step(module: tuple[Record, ...]) -> tuple[Record, Subrecord | list[Subrecord], tuple[Record, ...]]:
        rec, module = module[0], module[1:]
        src = rec.body
        return rec, src, module

    def __init__(self, module: tuple[Record, ...]):
        rec, src, module = self.step(module)
        assert rec.rectype in {RecordType.THEADR, RecordType.LHEADR}
        self.name = src.deserialize()
        rec, src, module = self.step(module)
        if isinstance(src, NAME):
            self.path = src.deserialize()
            rec, src, module = self.step(module)
        self.lnames = []
        while rec.rectype == RecordType.LNAMES:
            assert isinstance(rec.body, list)
            self.lnames.extend([n.deserialize() for n in rec.body])
            rec, src, module = self.step(module)
        self.segments = []
        while rec.rectype == RecordType.SEGDEF:
            segment = src.deserialize(self.lnames)
            self.segments.append(segment)
            rec, src, module = self.step(module)
        self.typedefs = []
        self.groups = []
        self.externals = []
        while isinstance(src, ContextDef):
            definition = src.deserialize(self.lnames, self.typedefs)
            if rec.rectype == RecordType.TYPDEF:
                self.typedefs.append(definition)
            elif rec.rectype == RecordType.GRPDEF:
                self.groups.append(definition)
            elif rec.rectype == RecordType.EXTDEF:
                self.externals.append(definition)
            rec, src, module = self.step(module)
        self.data = []
        self.publics = []
        self.threads = {"frame": {}, "target": {}}
        while True:
            # data item
            if isinstance(src, DataRec):
                # content_def item
                datum = src.deserialize(self.segments)
                self.data.append(datum)
                rec, src, module = self.step(module)
                while rec.rectype == RecordType.FIXUPP:
                    for block in src:
                        if isinstance(block, Thread):
                            self.thread_deserialize(block)
                        elif isinstance(block, Fixupp):
                            fixup = block.deserialize()
                    rec, src, module = self.step(module)
                module = (rec,) + module
            elif rec.rectype == RecordType.FIXUPP and all((isinstance(block, Thread) for block in src)):
                # thread_def item
                for block in src:
                    self.thread_deserialize(block)
            elif isinstance(src, DatDef):
                definition = src.deserialize(lnames=self.lnames, typedefs=self.typedefs,
                                             segments=self.segments, groups=self.groups)
                if rec.rectype == RecordType.TYPDEF:
                    self.typedefs.append(definition)
                elif rec.rectype == RecordType.PUBDEF:
                    self.publics.append(definition)
                elif rec.rectype == RecordType.EXTDEF:
                    self.externals.append(definition)
            elif rec.rectype != RecordType.COMMENT:
                break
            rec, src, module = self.step(module)
        self.linenums = {}
        assert isinstance(src, ModEnd)
        self.end = src
        assert not module
        for rec in module:
            src = rec.body
            if isinstance(src, LinNum):
                linenums = {}
                if src.base.grp_ind and self.groups:
                    linenums["group"] = self.groups[src.base.grp_ind - 1]
                if src.base.seg_ind and self.segments:
                    linenums["segment"] = self.segments[src.base.seg_ind - 1]
                if src.base.frame_numb is not None:
                    linenums["frame_number"] = src.base.frame_numb
                    linenums["Locatability"] = "physical"
                else:
                    linenums["Locatability"] = "logical"
                self.linenums = linenums

    def thread_deserialize(self, block: Thread):
        back = block.deserialize()
        if block.thred in self.threads[block.thread_type]:
            print(self.threads[block.thread_type][block.thred], "discarded", file=sys.stderr)
        self.threads[block.thread_type][block.thred] = back


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
    # print(f"{scroll.name}:", *(r.rectype.name for r in module()), sep=",\t")
    print(str(DeserializedModule(module())).replace(', ', ',\t'))


# todo: body should be one subrecord. do something else for list of subrecord
