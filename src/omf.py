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
    COMMENT = 0x88
    MOOEND = 0x8A
    MOOEND2 = 0x8B
    EXTDEF = 0x8C
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
    pass


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
    frame: Optional[int]
    target: int

    def __init__(self, val: int) -> None:
        self.frame_by_thread = bool(val & 0x80)
        self.target_by_thread = bool(val & 8)
        self.frame = (val >> 4) & 7
        self.target = val & 7
        self.no_target_displacement = bool(self.target & 4)
        assert not self.frame_by_thread or not self.frame & 4
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
        if not fix_dat.frame_by_thread and not fix_dat.frame & 4:
            if fix_dat.frame == 3:
                frame_datum, val = val[1] << 8 | val[0], val[2:]
            else:
                frame_datum, val = index_create(val)
        if not fix_dat.target_by_thread:
            if fix_dat.target & 3 == 3:
                target_datum, val = val[1] << 8 | val[0], val[2:]
            else:
                target_datum, val = index_create(val)
        if not fix_dat.no_target_displacement:
            target_displacement, val = val[1] << 8 | val[0], val[2:]
        return cls(locat, fix_dat, frame_datum, target_datum, target_displacement), val


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
    obj_type: str | int
    index: int

    @classmethod
    def create(cls, val: bytes, module: "Module") -> tuple[Self, bytes]:
        name, val = NAME.create(val)
        obj_type = val[0]
        ext_index = next(module.ext_numb)
        return cls(name, obj_type, ext_index), val[1:]


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
class Attr(Subrecord):
    locateability: str
    alignment: str
    is_physical: bool
    named: bool
    combination: int
    page_resident: bool
    child: PhysicalAddress | LoadtimeLocateable
    big: bool

    @classmethod
    def Create(cls, body):
        acbp = body[0]
        align_type = acbp >> 5
        combination = (acbp >> 2) & 7
        big = bool(acbp & 2)
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
        back = Attr(locateability, alignment, is_physical, named, combination, page_resident, subbody, big)
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
class LEData(Subrecord):
    seg_ind: int
    offset: int
    body: bytes

    def __init__(self, body: bytes):
        self.seg_ind, body = index_create(body)
        self.offset = body[1] << 8 | body[0]
        self.body = body[2:]
        assert len(self.body) <= 0x400


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
class LIData(Subrecord):
    seg_ind: int
    offset: int
    body: IteratedBlock

    def __init__(self, val: bytes):
        self.seg_ind, val = index_create(val)
        self.offset = val[1] << 8 | val[0]
        self.body, val = IteratedBlock.create(val[2:])
        assert not val


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
        elif rectype in {RecordType.MOOEND, RecordType.MOOEND2}:
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
        self.val = tuple(val)

    def __call__(self) -> tuple[Record, ...]:
        return self.val


thread_dict = dict[str, dict[int, dict]]


@dataclass
class DeserializedModule:
    lnames: list[str]
    typedefs: tuple[str, ...]
    segments: list[dict]
    groups: list[dict]
    publics: list[dict]
    externals: list[dict]
    linenums: dict
    data: list[dict]
    threads: thread_dict

    @staticmethod
    def step(module: tuple[Record, ...]) -> tuple[Record, Subrecord, tuple[Record, ...]]:
        rec, module = module[0], module[1:]
        step = rec.body
        return rec, step, module

    def __init__(self, module: tuple[Record, ...]):
        rec, src, module = self.step(module)
        assert rec.rectype in {RecordType.THEADR, RecordType.LHEADR}
        assert isinstance(rec.body, NAME)
        self.name = rec.body.deserialize()
        rec, src, module = self.step(module)
        if isinstance(src, NAME):
            self.path = src.deserialize()
            rec, src, module = self.step(module)
        assert rec.rectype == RecordType.LNAMES
        assert isinstance(rec.body, list)
        self.lnames = [n.deserialize() for n in rec.body]
        self.segments = []
        while True:
            rec, src, module = self.step(module)
            if not isinstance(src, SegDef):
                break
            segment = vars(src)
            name_tii = ("seg_name", "class_name", "Overlay_name")
            for name_t in name_tii:
                name_ind = segment[name_t]
                if name_ind:
                    segment[name_t] = self.lnames[name_ind - 1]
            segment["name"] = " ".join(segment[name_t] for name_t in name_tii)
            self.segments.append(segment)
        self.typedefs = ()
        self.groups = []
        self.publics = []
        self.externals = []
        self.linenums = {}
        self.data = []
        self.threads = {"frame": {}, "target": {}}
        module = (rec,) + module
        for rec in module:
            src = rec.body
            if isinstance(src, GroupDef):
                group = {"name": '',
                         "descriptors": src.descriptors}
                if src.name_index and self.lnames:
                    # noinspection PyTypeChecker
                    group["name"] = self.lnames[src.name_index - 1]
                self.groups.append(group)
            elif isinstance(src, PubDef):
                pubdef = {"publics": tuple(
                    {'name': pub.name.body.decode("ascii"),
                     'ofsset' : pub.offset
                     }
                    for pub in src.body)
                }
                if src.base.grp_ind and self.groups:
                    # noinspection PyTypeChecker
                    pubdef["group"] = self.groups[src.base.grp_ind - 1]
                    if src.base.seg_ind and self.segments:
                        # noinspection PyTypeChecker
                        pubdef["segment"] = self.segments[src.base.seg_ind - 1]
                if self.typedefs:
                    for pl, pub in enumerate(src.body):
                        if pub.type_index:
                            pubdef["publics"][pl]['type'] = self.typedefs[pub.type_index - 1]
                # noinspection PyTypeChecker
                pubdef["Locatability"] = "logical" if src.base.frame_numb is None else "physical"
                self.publics.append(pubdef)
            elif isinstance(src, ExtDef):
                extdef = {"name": src.name.body.decode("ascii"),
                          }
                if src.obj_type and self.typedefs:
                    extdef["type"] = self.typedefs[src.obj_type - 1]
                self.externals.append(extdef)
            elif isinstance(src, LinNum):
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
            elif isinstance(src, LEData | LIData):
                datum = {"locateability": "logical",
                         "offset": src.offset,
                         "body": src.body,
                         "form": {LEData: "enumerated", LIData: "iterated"}[type(src)]
                         }
                if src.seg_ind and self.segments:
                    # noinspection PyTypeChecker
                    datum["segment"] = self.segments[src.seg_ind - 1]
                self.data.append(datum)
            elif rec.rectype == RecordType.FIXUPP:
                for block in src:
                    if isinstance(block, Thread):
                        if block.thred in self.threads[block.thread_type]:
                            print(self.threads[block.thread_type][block.thred], "discarded", file=sys.stderr)
                        self.threads[block.thread_type][block.thred] = {"method": block.method,
                                                                        "index": block.index}
                    elif isinstance(block, Fixupp):
                        if block.fix_dat.frame_by_thread:
                            frame_method = self.threads["frame"][
                                block.fix_dat.frame]["method"]  # refer to most recent frame thread with this number
                        else:
                            frame_method = block.fix_dat.frame
                        if block.fix_dat.target_by_thread:
                            target_method =  self.threads["target"][
                                block.fix_dat.target]["method"]  # refer to most recent target thread with this number
                        else:
                            target_method = block.fix_dat.target


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

