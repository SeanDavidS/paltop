#!/usr/bin/env python3
"""
palsave.py - read Palworld .sav files (Oodle-compressed GVAS).

A .sav is [u32 uncompressed_len][u32 compressed_len]["PlM1"][Oodle Kraken data].
Oodle is proprietary, but the decompressor in oodle/libooz.so (open-source ooz,
built locally) handles it. Everything here is read-only: files are opened 'rb'
and nothing is ever written back to the game's save directory.
"""

from __future__ import annotations

import ctypes
import glob
import os
import re
import struct

import palpaths

LIBOOZ = palpaths.OODLE_LIB
SAFE_SPACE = 64          # ooz may write past the end of the output buffer


class OodleError(Exception):
    pass


_lib = None


def _oodle():
    global _lib
    if _lib is None:
        if not os.path.exists(LIBOOZ):
            raise OodleError("missing %s - run core/oodle/build.sh "
                                 "(or ./install.sh)" % LIBOOZ)
        lib = ctypes.CDLL(LIBOOZ)
        lib.ooz_decompress.restype = ctypes.c_int
        lib.ooz_decompress.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                       ctypes.c_char_p, ctypes.c_size_t]
        _lib = lib
    return _lib


def decompress_sav(path):
    """Read a .sav and return its raw GVAS bytes."""
    with open(path, "rb") as fh:
        raw = fh.read()
    return decompress_bytes(raw, path)


def decompress_bytes(raw, path="<bytes>"):
    """GVAS bytes from the contents of a .sav file (e.g. read from a backup
    archive without unpacking it)."""
    if len(raw) < 12:
        raise OodleError("%s: too short" % path)
    ulen, clen = struct.unpack_from("<II", raw, 0)
    magic = raw[8:12]
    if magic not in (b"PlM1", b"PlZ1", b"PlZ2"):
        raise OodleError("%s: unexpected magic %r" % (path, magic))
    body = raw[12:12 + clen]
    if magic in (b"PlZ1", b"PlZ2"):          # older, plain zlib
        import zlib
        return zlib.decompress(body)
    out = ctypes.create_string_buffer(ulen + SAFE_SPACE)
    n = _oodle().ooz_decompress(body, len(body), out, ulen)
    if n != ulen:
        raise OodleError("%s: decompressed %d bytes, expected %d"
                         % (path, n, ulen))
    return out.raw[:ulen]


# ---------------------------------------------------------------------------
# GVAS reader
# ---------------------------------------------------------------------------

class Reader:
    __slots__ = ("d", "p")

    def __init__(self, data, pos=0):
        self.d = data
        self.p = pos

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def i64(self):
        v = struct.unpack_from("<q", self.d, self.p)[0]
        self.p += 8
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.d, self.p)[0]
        self.p += 4
        return v

    def f64(self):
        v = struct.unpack_from("<d", self.d, self.p)[0]
        self.p += 8
        return v

    def guid(self):
        v = self.d[self.p:self.p + 16]
        self.p += 16
        return v

    def raw(self, n):
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def string(self):
        n = self.i32()
        if n == 0:
            return ""
        if n > 0:
            s = self.d[self.p:self.p + n]
            self.p += n
            return s.split(b"\0")[0].decode("utf-8", "replace")
        s = self.d[self.p:self.p - 2 * n]
        self.p += -2 * n
        return s.decode("utf-16-le", "replace").split("\0")[0]


def read_header(r):
    """GVAS file header; leaves the reader at the first property."""
    if r.raw(4) != b"GVAS":
        raise ValueError("not a GVAS blob")
    h = {"save_version": r.i32(), "package_version": r.i32()}
    if h["save_version"] >= 3:
        h["package_version_ue5"] = r.i32()
    h["engine"] = (r.raw(2), r.raw(2), r.raw(2), r.u32())
    h["branch"] = r.string()
    h["custom_format"] = r.i32()
    n = r.i32()
    h["custom_versions"] = n
    r.raw(n * 20)                       # guid + int per entry
    h["save_class"] = r.string()
    return h


# UE property tags: everything between <name><type><size> and the value.
# Note there is no int32 array-index field in this format, and the "pad" byte
# after a tag is really an optional-guid flag: if non-zero, a 16-byte guid
# follows it.
def optional_guid(r):
    if r.u8():
        r.guid()


def read_tag(r, ptype):
    """Consume the type-specific tag; return the parts worth keeping."""
    if ptype == "StructProperty":
        st = r.string()
        r.guid()                       # struct guid, always present
        optional_guid(r)
        return {"struct_type": st}
    if ptype == "ArrayProperty":
        at = r.string()
        optional_guid(r)
        return {"array_type": at}
    if ptype == "MapProperty":
        kt = r.string()
        vt = r.string()
        optional_guid(r)
        return {"key_type": kt, "value_type": vt}
    if ptype == "EnumProperty":
        en = r.string()
        optional_guid(r)
        return {"enum_type": en}
    if ptype == "ByteProperty":
        en = r.string()
        optional_guid(r)
        return {"enum_type": en}
    if ptype == "BoolProperty":
        v = r.u8()                     # BoolProperty carries its value in the tag
        optional_guid(r)
        return {"value": bool(v)}
    optional_guid(r)
    return {}


def walk(r, end=None):
    """List a property block as (name, type, size, tag, value_offset),
    skipping over each value. Stops at the 'None' terminator."""
    out = []
    limit = end if end is not None else len(r.d)
    while r.p < limit:
        name = r.string()
        if name in ("None", ""):
            break
        ptype = r.string()
        size = r.i64()
        tag = read_tag(r, ptype)
        vstart = r.p
        if ptype != "BoolProperty":    # bools have no separate value payload
            r.p = vstart + size
        out.append((name, ptype, size, tag, vstart))
    return out


# ---------------------------------------------------------------------------
# Palworld-specific: base camps
# ---------------------------------------------------------------------------

def _ftransform(r):
    """Palworld serialises FTransform as quat + translation + scale, doubles."""
    rot = struct.unpack_from("<4d", r.d, r.p); r.p += 32
    trans = struct.unpack_from("<3d", r.d, r.p); r.p += 24
    scale = struct.unpack_from("<3d", r.d, r.p); r.p += 24
    return rot, trans, scale


def _world_members(data):
    """Return {name: (type, size, tag, value_offset)} for worldSaveData."""
    r = Reader(data)
    read_header(r)
    top = [t for t in walk(r) if t[0] == "worldSaveData"]
    if not top:
        raise ValueError("no worldSaveData in this save")
    _, _, size, _, off = top[0]
    r2 = Reader(data, off)
    return {n: (t, s, tg, o) for n, t, s, tg, o in walk(r2, end=off + size)}


def read_base_camps(path):
    """Every base camp in Level.sav: name, world position and owning guild.

    The camp's core fields live in a packed `RawData` byte array rather than
    as tagged properties, laid out as:
        guid id, FString name, u8 state, FTransform, float area_range,
        guid group_id, ...
    """
    data = decompress_sav(path)
    mem = _world_members(data)
    if "BaseCampSaveData" not in mem:
        return []
    _, size, _, off = mem["BaseCampSaveData"]
    r = Reader(data, off)
    r.u32()                                    # keys-to-remove
    count = r.u32()
    camps = []
    for _ in range(count):
        r.guid()                               # map key
        props = {n: (pt, ps, ptg, po)
                 for n, pt, ps, ptg, po in walk(r, end=off + size)}
        if "RawData" not in props:
            continue
        _, _, _, po = props["RawData"]
        b = Reader(data, po)
        b.u32()                                # byte-array length
        cid = b.guid()
        name = b.string()
        state = b.u8()
        _rot, trans, _scale = _ftransform(b)
        area = b.f32()
        guild = b.guid()
        camps.append({
            "id": cid.hex(), "name": name, "state": state,
            "x": trans[0], "y": trans[1], "z": trans[2],
            "area": area, "guild": guild.hex(),
        })
    return camps


# ---------------------------------------------------------------------------
# Palworld-specific: players and Pals
# ---------------------------------------------------------------------------

def guid_str(b):
    """Format a UE FGuid the way Palworld's REST API prints player ids.

    An FGuid is four little-endian uint32s; the printed form is those four
    words in big-endian hex, so each 4-byte group is reversed.
    """
    return "".join(b[i:i + 4][::-1].hex().upper() for i in range(0, 16, 4))


def _scalar(r, ptype, tag):
    """Read a simple property value; None for anything structured."""
    if ptype in ("StrProperty", "NameProperty", "EnumProperty"):
        return r.string()
    if ptype == "IntProperty":
        return r.i32()
    if ptype == "Int64Property":
        return r.i64()
    if ptype == "FloatProperty":
        return r.f32()
    if ptype == "BoolProperty":
        return tag.get("value")
    if ptype == "ByteProperty":
        # enum_type "None" means a plain byte, otherwise a named enum value
        return r.u8() if tag.get("enum_type") in (None, "None") else r.string()
    return None


TICKS_TO_UNIX = 62135596800          # seconds from 0001-01-01 to 1970-01-01


def _names_array(data, off):
    """An ArrayProperty of Name/Enum values: u32 count, then strings."""
    r = Reader(data, off)
    return [r.string() for _ in range(r.u32())]


def _struct_fields(data, off, size):
    return {a: (b, c, d, e) for a, b, c, d, e in walk(Reader(data, off),
                                                      end=off + size)}


def _slot(data, ptype_tag_off, size):
    """(container guid hex, slot index) from a PalCharacterSlotId struct."""
    _, _, off = ptype_tag_off
    f = _struct_fields(data, off, size)
    cid, idx = None, None
    if "ContainerId" in f:
        _, csize, _, co = f["ContainerId"]
        inner = _struct_fields(data, co, csize)
        if "ID" in inner:
            cid = Reader(data, inner["ID"][3]).guid().hex()
    if "SlotIndex" in f:
        idx = Reader(data, f["SlotIndex"][3]).i32()
    return cid, idx


def _pal_record(data, fields, offsets):
    """Everything paltop shows about one Pal. UE leaves out properties that
    are at their default, so a missing Level is 1 and a missing Rank is 1
    (no condenser stars)."""
    owner = None
    if "OwnerPlayerUId" in offsets:
        owner = guid_str(Reader(data, offsets["OwnerPlayerUId"][2]).guid())

    def arr(name):
        if name in offsets:
            try:
                return [v.split("::")[-1] for v in
                        _names_array(data, offsets[name][2])]
            except (struct.error, UnicodeDecodeError):
                return []
        return []
    hp = None
    if "Hp" in offsets:
        try:
            f = _struct_fields(data, offsets["Hp"][2], _sizes.get("Hp", 0))
        except struct.error:
            f = {}
        if "Value" in f:
            hp = Reader(data, f["Value"][3]).i64() / 1000.0
    caught = None
    if "OwnedTime" in offsets:
        ticks = Reader(data, offsets["OwnedTime"][2]).i64()
        caught = ticks / 10**7 - TICKS_TO_UNIX if ticks > 0 else None
    cid, idx = (None, None)
    if "SlotId" in offsets:
        try:
            cid, idx = _slot(data, offsets["SlotId"], _sizes.get("SlotId", 0))
        except struct.error:
            pass
    return {
        "owner": owner,
        "species": fields.get("CharacterID") or "?",
        "nickname": fields.get("NickName") or "",
        "level": int(fields.get("Level") or 1),
        "exp": int(fields.get("Exp") or 0),
        "gender": (fields.get("Gender") or "").split("::")[-1],
        "hp_iv": int(fields.get("Talent_HP") or 0),
        "atk_iv": int(fields.get("Talent_Shot") or 0),
        "def_iv": int(fields.get("Talent_Defense") or 0),
        "friendship": int(fields.get("FriendshipPoint") or 0),
        "lucky": bool(fields.get("IsRarePal")),
        "awakened": bool(fields.get("bIsAwakening")),
        "rank": int(fields.get("Rank") or 1),
        "soul": {k: int(fields.get("Rank_" + k) or 0)
                 for k in ("HP", "Attack", "Defence", "CraftSpeed")},
        "passives": arr("PassiveSkillList"),
        "skills": arr("EquipWaza"),
        "learned": arr("MasteredWaza"),
        "hp": hp,
        "stomach": fields.get("FullStomach"),
        "sanity": fields.get("SanityValue"),
        "sick": (fields.get("WorkerSick") or "").split("::")[-1] or None,
        "hunger": (fields.get("HungerType") or "").split("::")[-1] or None,
        "skin": fields.get("SkinName") or None,
        "caught": caught,
        "container": cid,
        "slot": idx,
    }


_sizes = {}                           # struct sizes of the Pal being read


def read_characters(path, data=None):
    """Players and Pals from Level.sav's CharacterSaveParameterMap.

    Each entry's fields live in a packed `RawData` byte array holding a
    normal tagged property block, wrapped in a `SaveParameter` struct.
    A Pal's own PlayerUId key is all zeroes; its owner is in OwnerPlayerUId.
    """
    data = data if data is not None else decompress_sav(path)
    mem = _world_members(data)
    if "CharacterSaveParameterMap" not in mem:
        return {"players": [], "pals": []}
    _, size, _, off = mem["CharacterSaveParameterMap"]
    end = off + size
    r = Reader(data, off)
    r.u32()
    count = r.u32()
    players, pals = [], []
    for _ in range(count):
        if r.p >= end:
            break
        key = {n: (pt, ps, tg, po) for n, pt, ps, tg, po in walk(r, end=end)}
        val = {n: (pt, ps, tg, po) for n, pt, ps, tg, po in walk(r, end=end)}
        if "RawData" not in val:
            continue
        uid = None
        if "PlayerUId" in key:
            uid = guid_str(Reader(data, key["PlayerUId"][3]).guid())
        _, _, _, po = val["RawData"]
        rr = Reader(data, po)
        n = rr.u32()
        outer = walk(Reader(data, rr.p), end=rr.p + n)
        sp = [x for x in outer if x[0] == "SaveParameter"]
        if not sp:
            continue
        _, _, spsize, _, spoff = sp[0]
        fields = {}
        offsets = {}
        _sizes.clear()
        for pn, ppt, pps, ptg, ppo in walk(Reader(data, spoff),
                                           end=spoff + spsize):
            offsets[pn] = (ppt, ptg, ppo)
            _sizes[pn] = pps
            v = _scalar(Reader(data, ppo), ppt, ptg)
            if v is not None:
                fields[pn] = v
        if fields.get("IsPlayer"):
            players.append({
                "uid": uid,
                "name": fields.get("NickName") or "",
                "level": int(fields.get("Level") or 1),
                "exp": int(fields.get("Exp") or 0),
            })
        else:
            pal = _pal_record(data, fields, offsets)
            if "InstanceId" in key:
                pal["id"] = Reader(data, key["InstanceId"][3]).guid().hex()
            pals.append(pal)
    return {"players": players, "pals": pals}


# ---------------------------------------------------------------------------
# Palworld-specific: base camp overview
# ---------------------------------------------------------------------------

ZERO_GUID = "0" * 32
WORKER_CONTAINER_OFF = 98      # byte offset of the worker container id in
                               # a base's WorkerDirector RawData


def _character_containers(data, mem):
    """{container guid: (used slots, capacity)} for every Pal container."""
    if "CharacterContainerSaveData" not in mem:
        return {}
    _, size, _, off = mem["CharacterContainerSaveData"]
    end = off + size
    r = Reader(data, off)
    r.u32()
    n = r.u32()
    out = {}
    for _ in range(n):
        key = {a: (b, c, d, e) for a, b, c, d, e in walk(r, end=end)}
        if "ID" not in key:
            break
        cid = bytes(Reader(data, key["ID"][3]).guid())
        val = {a: (b, c, d, e) for a, b, c, d, e in walk(r, end=end)}
        cap = Reader(data, val["SlotNum"][3]).i32() if "SlotNum" in val else 0
        used = 0
        if "Slots" in val:
            _, ssize, _, so = val["Slots"]
            rr = Reader(data, so)
            cnt = rr.u32()
            rr.string(); rr.string(); rr.i64(); rr.string(); rr.guid()
            if rr.u8():
                rr.guid()
            for _ in range(cnt):
                sf = {a: (b, c, d, e) for a, b, c, d, e in walk(rr, end=so + ssize)}
                if "RawData" not in sf:
                    break
                rd = Reader(data, sf["RawData"][3])
                rd.u32()
                b = Reader(data, rd.p)
                b.guid()                        # owning player uid
                if b.guid().hex() != ZERO_GUID:
                    used += 1
        out[cid] = (used, cap)
    return out


def _structures_by_base(data, mem):
    """{base guid: (count, Counter of structure ids)} from MapObjectSaveData.

    Every built object records the base it belongs to; world scenery (rocks,
    trees) carries an all-zero base id and is skipped.
    """
    import collections
    if "MapObjectSaveData" not in mem:
        return {}
    _, size, _, off = mem["MapObjectSaveData"]
    end = off + size
    r = Reader(data, off)
    n = r.u32()
    r.string(); r.string(); r.i64(); r.string(); r.guid()
    if r.u8():
        r.guid()
    per = collections.defaultdict(collections.Counter)
    for _ in range(n):
        if r.p >= end:
            break
        f = {a: (b, c, d, e) for a, b, c, d, e in walk(r, end=end)}
        if "MapObjectId" not in f or "Model" not in f:
            continue
        oid = Reader(data, f["MapObjectId"][3]).string()
        _, msize, _, mo = f["Model"]
        mf = {a: (b, c, d, e) for a, b, c, d, e in walk(Reader(data, mo),
                                                        end=mo + msize)}
        if "RawData" not in mf:
            continue
        rd = Reader(data, mf["RawData"][3])
        rd.u32()
        b = Reader(data, rd.p)
        b.guid()                                # instance id
        b.guid()                                # concrete model id
        base = b.guid().hex()                   # base camp it belongs to
        if base != ZERO_GUID:
            per[base][oid] += 1
    return per


SANITY_LOW = 50.0              # below this a worker is losing the plot
STOMACH_LOW = 40.0             # below this they need feeding


def _worker_status(data, mem):
    """{container guid: worker stats} from CharacterSaveParameterMap.

    UE only serialises properties that differ from their defaults, so a
    healthy, well-fed Pal simply has no WorkerSick / HungerType /
    PhysicalHealth field at all. Their presence *is* the problem signal.
    """
    import collections
    if "CharacterSaveParameterMap" not in mem:
        return {}
    _, size, _, off = mem["CharacterSaveParameterMap"]
    end = off + size
    r = Reader(data, off)
    r.u32()
    n = r.u32()
    out = collections.defaultdict(lambda: {
        "workers": 0, "sick": [], "starving": [], "dying": [],
        "insane": [], "slacking": [], "min_stomach": None})
    for _ in range(n):
        if r.p >= end:
            break
        walk(r, end=end)                                   # key
        v = {a: (b, c, d, e) for a, b, c, d, e in walk(r, end=end)}
        if "RawData" not in v:
            continue
        rd = Reader(data, v["RawData"][3])
        ln = rd.u32()
        outer = walk(Reader(data, rd.p), end=rd.p + ln)
        sp = [x for x in outer if x[0] == "SaveParameter"]
        if not sp:
            continue
        _, _, spsize, _, spoff = sp[0]
        f = {a: (b, c, d, e) for a, b, c, d, e in walk(Reader(data, spoff),
                                                       end=spoff + spsize)}
        if "SlotId" not in f or "CharacterID" not in f:
            continue
        _, ssize, _, so = f["SlotId"]
        cid = None
        for a, b, c, d, e in walk(Reader(data, so), end=so + ssize):
            if a == "ContainerId":
                for a2, b2, c2, d2, e2 in walk(Reader(data, e), end=e + c):
                    if a2 == "ID":
                        cid = bytes(Reader(data, e2).guid())
        if cid is None:
            continue
        rec = out[cid]
        rec["workers"] += 1
        species = Reader(data, f["CharacterID"][3]).string()

        def enum(field):
            if field in f:
                return Reader(data, f[field][3]).string().split("::")[-1]
            return None

        sick = enum("WorkerSick")
        if sick:
            rec["sick"].append((species, sick))
        if enum("HungerType"):
            rec["starving"].append((species, enum("HungerType")))
        phys = enum("PhysicalHealth")
        if phys and phys != "Normal":
            rec["dying"].append((species, phys))
        ev = enum("BaseCampWorkerEventType")
        if ev and "DodgeWork" in ev:
            rec["slacking"].append((species, ev))
        if "SanityValue" in f:
            sv = Reader(data, f["SanityValue"][3]).f32()
            if sv < SANITY_LOW:
                rec["insane"].append((species, round(sv)))
        if "FullStomach" in f:
            fs = Reader(data, f["FullStomach"][3]).f32()
            if rec["min_stomach"] is None or fs < rec["min_stomach"]:
                rec["min_stomach"] = fs
    return out


def read_bases(path):
    """Base camps with their build and worker state."""
    data = decompress_sav(path)
    mem = _world_members(data)
    if "BaseCampSaveData" not in mem:
        return []
    containers = _character_containers(data, mem)
    structures = _structures_by_base(data, mem)
    status = _worker_status(data, mem)
    _, size, _, off = mem["BaseCampSaveData"]
    end = off + size
    r = Reader(data, off)
    r.u32()
    count = r.u32()
    bases = []
    for _ in range(count):
        r.guid()
        props = {a: (b, c, d, e) for a, b, c, d, e in walk(r, end=end)}
        if "RawData" not in props:
            continue
        b = Reader(data, props["RawData"][3])
        b.u32()
        cid = b.guid().hex()
        name = b.string()
        state = b.u8()
        _rot, trans, _scale = _ftransform(b)
        area = b.f32()
        guild = b.guid().hex()

        workers = cap = 0
        health = None
        container = None
        if "WorkerDirector" in props:
            _, wsize, _, wo = props["WorkerDirector"]
            wf = {a: (b2, c, d, e) for a, b2, c, d, e in walk(Reader(data, wo),
                                                              end=wo + wsize)}
            if "RawData" in wf:
                ro = wf["RawData"][3]
                ln = Reader(data, ro).u32()
                blob = data[ro + 4:ro + 4 + ln]
                key = blob[WORKER_CONTAINER_OFF:WORKER_CONTAINER_OFF + 16]
                workers, cap = containers.get(key, (0, 0))
                health = status.get(key)
                container = key.hex()

        kinds = structures.get(cid) or {}
        bases.append({
            "id": cid, "name": name, "state": state,
            "x": trans[0], "y": trans[1], "z": trans[2],
            "area": area, "guild": guild, "container": container,
            "workers": workers, "worker_cap": cap,
            "sick": (health or {}).get("sick", []),
            "starving": (health or {}).get("starving", []),
            "dying": (health or {}).get("dying", []),
            "insane": (health or {}).get("insane", []),
            "slacking": (health or {}).get("slacking", []),
            "min_stomach": (health or {}).get("min_stomach"),
            "structures": sum(kinds.values()),
            "kinds": sorted(kinds.items(), key=lambda kv: -kv[1])[:6],
        })
    bases.sort(key=lambda b: -b["structures"])
    return bases


def _simple_map(data, off, key_type, value_type):
    """A MapProperty with Name/String keys and Int/Bool values as a dict:
    i32 removed entries (always 0 here), i32 count, then key, value pairs."""
    r = Reader(data, off)
    r.i32()
    out = {}
    for _ in range(r.i32()):
        if key_type not in ("NameProperty", "StrProperty", "EnumProperty"):
            return out
        k = r.string()
        if value_type == "IntProperty":
            out[k] = r.i32()
        elif value_type == "BoolProperty":
            out[k] = bool(r.u8())
        else:
            return out
    return out


def _guild_members(blob):
    """The member list at the end of a guild's RawData: (admin uid, [(uid,
    name, last online ticks, role)]), or None. The part before it changes
    between game versions, so it is found from the end: an admin uid, a
    count, then that many (uid, int64, FString, u8) that read cleanly, and
    the admin among them."""
    def fstring(r):
        n = r.i32()
        if n > 0:
            if n > 256 or r.p + n > len(blob) or blob[r.p + n - 1] != 0:
                raise ValueError("no string")
            return r.raw(n)[:-1].decode("utf-8", "replace")
        if n < 0:
            if -n > 256 or r.p - 2 * n > len(blob):
                raise ValueError("no string")
            return r.raw(-2 * n).decode("utf-16-le", "replace").rstrip("\0")
        return ""
    for p in range(len(blob) - 24, 20, -1):
        count = struct.unpack_from("<I", blob, p)[0]
        if not 1 <= count <= 128:
            continue
        admin = blob[p - 16:p]
        r = Reader(blob, p + 4)
        try:
            members = []
            for _ in range(count):
                uid = r.guid()
                last = r.i64()
                name = fstring(r)
                role = r.u8()
                members.append((uid, name, last, role))
        except (ValueError, struct.error, IndexError):
            continue
        if members and any(m[0] == admin for m in members) and \
                all(m[1] for m in members):
            return admin, members, p - 16
    return None


def _guild_name(blob, admin, before):
    """The guild's name. After it come, up to the member list at `before`:
    a uid (who last renamed it, zero if nobody), a count and that many
    60-byte records, a count and that many bytes, and an int32. Each
    FString candidate is checked against exactly that layout, so only the
    real name fits."""
    for q in range(before - 30, 20, -1):
        n = struct.unpack_from("<i", blob, q)[0]
        if 0 < n <= 130:
            text_end = q + 4 + n
            if text_end > before or blob[text_end - 1] != 0:
                continue
            raw = blob[q + 4:text_end - 1]
            try:
                name = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
        elif -65 <= n < 0:
            text_end = q + 4 - 2 * n
            if text_end > before:
                continue
            try:
                name = blob[q + 4:text_end - 2].decode("utf-16-le")
            except UnicodeDecodeError:
                continue
        else:
            continue
        if not name or not name.isprintable():
            continue
        p = text_end + 16                                  # who renamed it
        if p + 4 > before:
            continue
        records = struct.unpack_from("<I", blob, p)[0]
        p += 4 + 60 * records
        if p + 4 > before:
            continue
        extra = struct.unpack_from("<I", blob, p)[0]
        p += 4 + extra + 4
        if p == before:
            return name
    return None


def read_guilds(path, data=None):
    """Guilds in Level.sav: [{id, name, admin, members: [{pid, name, role}]}]
    (player ids as the REST API prints them; role 1 is the guild's master)."""
    data = data or decompress_sav(path)
    mem = _world_members(data)
    if "GroupSaveDataMap" not in mem:
        return []
    _, size, _, off = mem["GroupSaveDataMap"]
    end = off + size
    r = Reader(data, off)
    r.u32()
    out = []
    for _ in range(r.u32()):
        gid = r.guid()
        props = {a: (b, c, d, e) for a, b, c, d, e in walk(r, end=end)}
        if "GroupType" not in props or "RawData" not in props:
            continue
        kind = Reader(data, props["GroupType"][3]).string()
        if not kind.endswith("::Guild"):
            continue
        ro = props["RawData"][3]
        blob = data[ro + 4:ro + 4 + Reader(data, ro).u32()]
        got = _guild_members(blob)
        if not got:
            continue
        admin, members, at = got
        out.append({"id": gid.hex(), "name": _guild_name(blob, admin, at) or "",
                    "admin": guid_str(admin),
                    "members": [{"pid": guid_str(u), "name": n, "role": role}
                                for u, n, _last, role in members]})
    return out


def read_player_containers(players_dir):
    """{player guid: {"team": container, "box": container, "captured":
    {species: times caught}, "paldeck": [species unlocked in the in-game
    Paldeck]}} from Players/<guid>.sav: which containers are the party and
    the Palbox, and what the player has found."""
    out = {}
    for path in glob.glob(os.path.join(players_dir, "*.sav")):
        try:
            data = decompress_sav(path)
            r = Reader(data)
            read_header(r)
            top = {n: (t, s, tg, o) for n, t, s, tg, o in walk(r)}
            if "SaveData" not in top:
                continue
            _, size, _, off = top["SaveData"]
            f = _struct_fields(data, off, size)
            rec = {}
            for key, name in (("team", "OtomoCharacterContainerId"),
                              ("box", "PalStorageContainerId")):
                if name in f:
                    _, csize, _, co = f[name]
                    inner = _struct_fields(data, co, csize)
                    if "ID" in inner:
                        rec[key] = Reader(data, inner["ID"][3]).guid().hex()
            # quests: done (an array of names) and taken but not done (an
            # array of structs, each naming its quest)
            for key, name in (("quests_done", "CompletedQuestArray_FullRelease"),
                              ("quests_active", "OrderedQuestArray_FullRelease")):
                if name in f:
                    _, qsize, _, qo = f[name]
                    rec[key] = sorted({m.decode() for m in re.findall(
                        rb"(?:Main|Sub)_[A-Za-z0-9_]+", data[qo:qo + qsize])})
            if "RecordData" in f:
                _, rsize, _, ro = f["RecordData"]
                rd = _struct_fields(data, ro, rsize)
                for key, name in (("captured", "PalCaptureCount"),
                                  ("paldeck", "PaldeckUnlockFlag")):
                    if name in rd:
                        ptype, _, tag, mo = rd[name]
                        try:
                            m = _simple_map(data, mo, tag.get("key_type"),
                                            tag.get("value_type"))
                        except (struct.error, IndexError):
                            m = {}
                        rec[key] = m if key == "captured" else \
                            sorted(k for k, v in m.items() if v)
                # the rest of the record, for achievements: numbers as they
                # are, and for maps how many entries are set and their sum
                recs = {}
                for name, (ptype, _, tag, mo) in rd.items():
                    try:
                        if ptype == "IntProperty":
                            recs[name] = Reader(data, mo).i32()
                        elif ptype == "MapProperty":
                            m = _simple_map(data, mo, tag.get("key_type"),
                                            tag.get("value_type"))
                            recs[name] = [sum(1 for v in m.values() if v),
                                          sum(int(v) for v in m.values())]
                            if name in ("TowerBossDefeatFlag",
                                        "NormalBossDefeatFlag",
                                        "RaidBossDefeatCount"):
                                rec.setdefault("beaten", {})[name] = sorted(
                                    k for k, v in m.items() if v)
                            if name in ("TowerBossDefeatCount",
                                        "RaidBossDefeatCount",
                                        "SpecificBossDefeatFlag"):
                                rec.setdefault("boss_counts", {})[name] = {
                                    k: int(v) for k, v in m.items() if v}
                    except (struct.error, IndexError, ValueError):
                        continue
                rec["records"] = recs
            if "PlayerUId" in f:
                uid = guid_str(Reader(data, f["PlayerUId"][3]).guid())
                out[uid] = rec
        except Exception:
            continue
    return out


DAY_TICKS = 864000000000          # one in-game day in GameDateTimeTicks


def world_summary(data):
    """{day, players: [(name, level)], pals} from Level.sav's GVAS bytes."""
    mem = _world_members(data)
    day = None
    if "GameTimeSaveData" in mem:
        _, size, _, off = mem["GameTimeSaveData"]
        for n, pt, sz, tg, vo in walk(Reader(data, off), end=off + size):
            if n == "GameDateTimeTicks":
                day = Reader(data, vo).i64() // DAY_TICKS
    chars = read_characters(None, data=data)
    players = sorted(((p["name"], p["level"]) for p in chars["players"]),
                     key=lambda t: -t[1])
    return {"day": day, "players": players, "pals": len(chars["pals"])}


class SaveCache:
    """Re-reads Level.sav only when it changes on disk."""

    def __init__(self, path):
        self.path = path
        self._stamp = None
        self.base_camps = []
        self.players = []
        self.pals = []
        self.containers = {}
        self.guilds = []
        self.error = None
        self.loaded_at = None

    def refresh(self):
        try:
            st = os.stat(self.path)
            stamp = (int(st.st_mtime), st.st_size)
        except OSError as exc:
            self.error = str(exc)
            return False
        if stamp == self._stamp:
            return False
        try:
            self.base_camps = read_bases(self.path)
            chars = read_characters(self.path)
            self.players, self.pals = chars["players"], chars["pals"]
            self.containers = read_player_containers(
                os.path.join(os.path.dirname(self.path), "Players"))
            try:
                self.guilds = read_guilds(self.path)
            except Exception:                   # guilds are a nicety
                self.guilds = []
            self.error = None
        except Exception as exc:                # a torn read while the server
            self.error = "%s: %s" % (type(exc).__name__, exc)   # is saving
            return False
        self._stamp = stamp
        self.loaded_at = st.st_mtime
        return True


if __name__ == "__main__":
    import sys
    path = sys.argv[1]
    data = decompress_sav(path)
    print("%s -> %d bytes GVAS" % (os.path.basename(path), len(data)))
    r = Reader(data)
    h = read_header(r)
    print("header: save_ver=%s pkg=%s class=%s custom_versions=%d" % (
        h["save_version"], h["package_version"], h["save_class"],
        h["custom_versions"]))
    print("first property starts at offset %d" % r.p)
    print("top-level properties:")
    for name, ptype, size, tag, off in walk(r):
        print("  %-24s %-16s %12d  %s" % (name, ptype, size, tag or ""))
