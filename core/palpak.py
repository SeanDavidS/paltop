#!/usr/bin/env python3
"""
palpak.py - read individual assets out of the game pak.

This module decompresses entries of the game pak, using the same locally built Oodle decompressor as palsave.py, so the game's
own data tables can be read - currently DT_PalMonsterParameter, which gives
each Pal species its element type(s).

The pak is opened read-only and never written to.
"""

from __future__ import annotations

import ctypes
import json
import os
import struct

import palpaths

LIBOOZ = palpaths.OODLE_LIB
PAK_MAGIC = 0x5A6F12E1

_lib = None


def _oodle():
    global _lib
    if _lib is None:
        lib = ctypes.CDLL(LIBOOZ)
        lib.ooz_decompress.restype = ctypes.c_int
        lib.ooz_decompress.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                       ctypes.c_char_p, ctypes.c_size_t]
        _lib = lib
    return _lib


def _fstring(b, o):
    n, = struct.unpack_from("<i", b, o)
    o += 4
    if n >= 0:
        return b[o:o + n].split(b"\0")[0].decode("utf-8", "replace"), o + n
    return (b[o:o - 2 * n].decode("utf-16-le", "replace").split("\0")[0],
            o - 2 * n)


class Pak:
    """Minimal reader for an unencrypted-index UE pak."""

    def __init__(self, path=None):
        path = path or palpaths.pak()
        if not path:
            raise OSError("the game's pak file is not found (is the server "
                          "container running? PALTOP_PAK sets it)")
        self.path = path
        self.f = open(path, "rb")
        self.f.seek(0, 2)
        size = self.f.tell()
        self.f.seek(size - 512)
        tail = self.f.read(512)
        spots = [i for i in range(len(tail) - 4)
                 if struct.unpack_from("<I", tail, i)[0] == PAK_MAGIC]
        if not spots:
            raise ValueError("not a UE pak")
        idx_off, idx_size = struct.unpack_from("<QQ", tail, max(spots) + 8)
        self.f.seek(idx_off)
        idx = self.f.read(idx_size)
        o = 0
        _, o = _fstring(idx, o)
        o += 4 + 8
        has, = struct.unpack_from("<i", idx, o)
        o += 4 + (36 if has else 0)
        has, = struct.unpack_from("<i", idx, o)
        o += 4
        fd_off, fd_size = struct.unpack_from("<qq", idx, o)
        o += 16 + 20
        esz, = struct.unpack_from("<i", idx, o)
        o += 4
        self.entries = idx[o:o + esz]
        self.f.seek(fd_off)
        fd = self.f.read(fd_size)
        o = 0
        ndirs, = struct.unpack_from("<i", fd, o)
        o += 4
        self.files = {}
        for _ in range(ndirs):
            d, o = _fstring(fd, o)
            nf, = struct.unpack_from("<i", fd, o)
            o += 4
            for _ in range(nf):
                fn, o = _fstring(fd, o)
                eo, = struct.unpack_from("<i", fd, o)
                o += 4
                self.files[d + fn] = eo

    def close(self):
        try:
            self.f.close()
        except OSError:
            pass

    def read(self, path):
        """Decompress one entry, using the header stored with the data."""
        eo = self.files[path]
        flags, = struct.unpack_from("<I", self.entries, eo)
        p = eo + 4
        if flags & (1 << 31):
            off, = struct.unpack_from("<I", self.entries, p)
        else:
            off, = struct.unpack_from("<Q", self.entries, p)
        self.f.seek(off)
        hdr = self.f.read(8192)
        q = 24                                  # offset, size, uncompressed
        usize = struct.unpack_from("<q", hdr, 16)[0]
        method, = struct.unpack_from("<i", hdr, q)
        q += 4 + 20                             # method + sha1
        blocks = []
        if method:
            n, = struct.unpack_from("<i", hdr, q)
            q += 4
            for _ in range(n):
                s, e = struct.unpack_from("<qq", hdr, q)
                q += 16
                blocks.append((s, e))
        q += 1                                  # encrypted flag
        bsize, = struct.unpack_from("<I", hdr, q)
        q += 4
        if not method:
            self.f.seek(off + q)
            return self.f.read(usize)
        out = bytearray()
        for s, e in blocks:                     # block offsets are entry-relative
            self.f.seek(off + s)
            comp = self.f.read(e - s)
            want = min(bsize, usize - len(out))
            buf = ctypes.create_string_buffer(want + 64)
            if _oodle().ooz_decompress(comp, len(comp), buf, want) != want:
                raise ValueError("%s: bad Oodle block" % path)
            out += buf.raw[:want]
        return bytes(out)

    def find(self, needle):
        return [k for k in self.files if needle in k]


# ---------------------------------------------------------------------------
# UE package reading, enough for a DataTable
# ---------------------------------------------------------------------------

def name_table(uasset):
    """The package's FName table, which .uexp refers to by index."""
    o = 4
    legacy, = struct.unpack_from("<i", uasset, o)
    o += 4 + 4 + 4                              # legacy, ue3, ue4 versions
    if legacy <= -8:
        o += 4                                  # ue5 version
    o += 4                                      # licensee version
    ncv, = struct.unpack_from("<i", uasset, o)
    o += 4 + ncv * 20
    o += 4                                      # total header size
    _, o = _fstring(uasset, o)                  # folder name
    o += 4                                      # package flags
    count, = struct.unpack_from("<i", uasset, o)
    o += 4
    offset, = struct.unpack_from("<i", uasset, o)
    names = []
    q = offset
    for _ in range(count):
        s, q = _fstring(uasset, q)
        q += 4                                  # two uint16 hashes
        names.append(s)
    return names


class UexpReader:
    """Tagged-property reader for .uexp, where names are name-table indices."""

    def __init__(self, data, names, pos=0):
        self.d, self.n, self.p = data, names, pos

    def i32(self):
        v, = struct.unpack_from("<i", self.d, self.p)
        self.p += 4
        return v

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def name(self):
        i = self.i32()
        num = self.i32()                        # FName number: Foo_1 is "Foo", 2
        base = self.n[i] if 0 <= i < len(self.n) else "?%d" % i
        return "%s_%d" % (base, num - 1) if num > 0 else base

    def properties(self, limit):
        out = []
        while self.p < limit:
            nm = self.name()
            if nm == "None":
                break
            ty = self.name()
            size = self.i32()
            self.i32()                          # array index
            if ty == "StructProperty":
                self.name()
                self.p += 16
            elif ty == "BoolProperty":
                self.u8()
            elif ty in ("ByteProperty", "EnumProperty", "ArrayProperty",
                        "SetProperty"):
                self.name()
            elif ty == "MapProperty":
                self.name()
                self.name()
            if self.u8():
                self.p += 16                    # optional property guid
            start = self.p
            if ty != "BoolProperty":
                self.p = start + size
            out.append((nm, ty, start))
        return out


def read_elements(pak=None, cache=None):
    """{species id: (element1, element2)} from DT_PalMonsterParameter.

    The table's rows follow the DataTable object's own properties: a row count
    and then, per row, an FName row key plus a tagged property block.
    """
    if cache and os.path.exists(cache):
        try:
            with open(cache) as fh:
                blob = json.load(fh)
            if blob.get("version") == 1 and blob.get("elements"):
                return {k: tuple(v) for k, v in blob["elements"].items()}
        except (OSError, ValueError):
            pass
    own = pak is None
    pak = pak or Pak()
    try:
        ua = pak.find("DT_PalMonsterParameter.uasset")
        ue = pak.find("DT_PalMonsterParameter.uexp")
        if not ua or not ue:
            return {}
        names = name_table(pak.read(ua[0]))
        uexp = pak.read(ue[0])
        r = UexpReader(uexp, names)
        r.properties(len(uexp))                 # the DataTable's own properties
        r.p += 4                                # unknown int32
        nrows = r.i32()
        out = {}
        for _ in range(nrows):
            if r.p >= len(uexp):
                break
            row = r.name()
            fields = r.properties(len(uexp))
            els = []
            for fn, ft, fo in fields:
                if fn in ("ElementType1", "ElementType2"):
                    els.append(UexpReader(uexp, names, fo).name().split("::")[-1])
            e1 = els[0] if els else "None"
            e2 = els[1] if len(els) > 1 else "None"
            if e1 != "None" or e2 != "None":
                out[row] = (e1, e2)
    finally:
        if own:
            pak.close()
    if cache and out:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w") as fh:
                json.dump({"version": 1, "elements": out}, fh)
        except OSError:
            pass
    return out


def _text_table(pak, asset, prefix):
    """{row key minus prefix: source text} for a localisation DataTable."""
    out = {}
    names = name_table(pak.read(asset))
    uexp = pak.read(asset[:-7] + ".uexp")
    r = UexpReader(uexp, names)
    r.properties(len(uexp))
    r.p += 4
    nrows = r.i32()
    for _ in range(nrows):
        if r.p >= len(uexp):
            break
        row = r.name()
        for fn, ft, fo in r.properties(len(uexp)):
            if ft != "TextProperty":
                continue
            o = fo + 4 + 1                      # flags, history type
            _ns, o = _fstring(uexp, o)
            _key, o = _fstring(uexp, o)
            src, o = _fstring(uexp, o)
            if row.startswith(prefix) and src:
                out[row[len(prefix):]] = src
    return out


def read_conditions(pak=None, cache=None, lang="en"):
    """{WorkerSick enum value: in-game condition name}.

    e.g. GastricUlcer -> "Ulcer", DepressionSprain -> "Depressed". These live
    in the shared UI text table rather than with the Pal names.
    """
    if cache and os.path.exists(cache):
        try:
            with open(cache) as fh:
                blob = json.load(fh)
            if blob.get("version") == 1 and blob.get("lang") == lang:
                return blob.get("conditions") or {}
        except (OSError, ValueError):
            pass
    own = pak is None
    pak = pak or Pak()
    out = {}
    try:
        want = ("L10N/%s/Pal/DataTable/Text/DT_UI_Common_Text_Common.uasset"
                % lang)
        hits = [k for k in pak.files if k.endswith(want)]
        if hits:
            out = _text_table(pak, hits[0], "COMMON_CONDITION_NAME_")
    finally:
        if own:
            pak.close()
    if cache and out:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w") as fh:
                json.dump({"version": 1, "lang": lang, "conditions": out}, fh)
        except OSError:
            pass
    return out


def read_pal_names(pak=None, cache=None, lang="en"):
    """{species id: in-game display name} from the localisation table.

    Keys are also indexed lowercase, because the save files and the text
    table do not always agree on capitalisation.

    DT_PalNameText_Common has one row per species keyed `PAL_NAME_<id>`,
    holding a TextProperty. A TextProperty is 4 flag bytes, a history-type
    byte, then three strings: namespace, key and the source text - the last
    of which is the displayed name.
    """
    if cache and os.path.exists(cache):
        try:
            with open(cache) as fh:
                blob = json.load(fh)
            if blob.get("version") == 1 and blob.get("lang") == lang:
                return blob.get("names") or {}
        except (OSError, ValueError):
            pass
    own = pak is None
    pak = pak or Pak()
    out = {}
    try:
        want = "L10N/%s/Pal/DataTable/Text/DT_PalNameText_Common.uasset" % lang
        hits = [k for k in pak.files if k.endswith(want)]
        if not hits:
            return {}
        uasset = hits[0]
        names = name_table(pak.read(uasset))
        uexp = pak.read(uasset[:-7] + ".uexp")
        r = UexpReader(uexp, names)
        r.properties(len(uexp))
        r.p += 4
        nrows = r.i32()
        for _ in range(nrows):
            if r.p >= len(uexp):
                break
            row = r.name()
            for fn, ft, fo in r.properties(len(uexp)):
                if ft != "TextProperty":
                    continue
                o = fo + 4 + 1                  # flags, history type
                _ns, o = _fstring(uexp, o)
                _key, o = _fstring(uexp, o)
                src, o = _fstring(uexp, o)
                if row.startswith("PAL_NAME_") and src:
                    out[row[len("PAL_NAME_"):]] = src
    finally:
        if own:
            pak.close()
    # the save files and the text table disagree on capitalisation for a few
    # species (SheepBall vs Sheepball, GhostAnglerfish vs GhostAnglerFish),
    # so index every key by its lowercase form as well
    for key in list(out):
        out.setdefault(key.lower(), out[key])
    if cache and out:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w") as fh:
                json.dump({"version": 1, "lang": lang, "names": out}, fh)
        except OSError:
            pass
    return out


if __name__ == "__main__":
    els = read_elements()
    nms = read_pal_names()
    print("species with element data: %d, display names: %d" % (len(els), len(nms)))
    for k in ("Kitsunebi", "Suzaku", "Anubis", "JetDragon", "SnowPeafowl",
              "GhostAnglerfish_Fire", "AmaterasuWolf_Dark", "BOSS_JetDragon"):
        print("  %-22s %s" % (k, els.get(k)))


# ---------------------------------------------------------------------------
# Pal details: passives, active skills, species stats and breeding
# ---------------------------------------------------------------------------

def _row_values(r, uexp, names, limit):
    """One DataTable row as {field: value} for the scalar types; anything
    structured comes back as None."""
    out = {}
    while r.p < limit:
        nm = r.name()
        if nm == "None":
            break
        ty = r.name()
        size = r.i32()
        r.i32()                                 # array index
        val = None
        enum = None
        if ty == "StructProperty":
            r.name()
            r.p += 16
        elif ty == "BoolProperty":
            val = bool(r.u8())
        elif ty in ("ByteProperty", "EnumProperty"):
            enum = r.name()
        elif ty in ("ArrayProperty", "SetProperty"):
            r.name()
        elif ty == "MapProperty":
            r.name()
            r.name()
        if r.u8():
            r.p += 16
        start = r.p
        if ty != "BoolProperty":
            try:
                if ty in ("NameProperty", "EnumProperty"):
                    val = UexpReader(uexp, names, start).name()
                elif ty == "ByteProperty":
                    val = (uexp[start] if size == 1 else
                           UexpReader(uexp, names, start).name())
                elif ty == "IntProperty":
                    val, = struct.unpack_from("<i", uexp, start)
                elif ty == "FloatProperty":
                    val, = struct.unpack_from("<f", uexp, start)
                elif ty == "StrProperty":
                    val, _ = _fstring(uexp, start)
            except (struct.error, IndexError):
                val = None
            r.p = start + size
        if isinstance(val, str) and "::" in val:
            val = val.split("::")[-1]
        out[nm] = val
    return out


def _table(pak, asset):
    """[(row name, {field: value})] for a DataTable asset path."""
    names = name_table(pak.read(asset))
    uexp = pak.read(asset[:-7] + ".uexp")
    r = UexpReader(uexp, names)
    r.properties(len(uexp))
    r.p += 4
    rows = []
    for _ in range(r.i32()):
        if r.p >= len(uexp):
            break
        row = r.name()
        rows.append((row, _row_values(r, uexp, names, len(uexp))))
    return rows


WORK_KINDS = ("EmitFlame", "Watering", "Seeding", "GenerateElectricity",
              "Handcraft", "Collection", "Deforest", "Mining",
              "OilExtraction", "ProductMedicine", "Cool", "Transport",
              "MonsterFarm")
GAME_DATA_VERSION = 10
SELF_TARGETS = {"ToSelf", "ToSelfAndTrainer", "ToActiveOtomo",
                "ToTrainerAndOtomo", "None"}


def _pak_stamp(path=None):
    path = path or palpaths.pak()
    if not path:
        return None
    try:
        st = os.stat(path)
        return [int(st.st_mtime), st.st_size]
    except OSError:
        return None


def read_game_data(pak=None, cache=None, lang="en"):
    """Names and numbers the Pal detail view needs, from the game's pak:

        passives  {id: {name, rank, effects: [[type, value], ...]}}
        skills    {waza id: {name, element, power, cooldown, category,
                             effects: [[status, build-up], ...]}}
        learn     {species: [[level, waza id], ...]}  what it learns when
        status    {status: {name, desc}}  Burn, Wetness, ... as the game
                  names and explains them
        items     {item id: {name, desc, type, kind, rarity, price, weight}}
                  for the items partner skills and passives mention
        field_bosses  [{spawner, species, level}] the alpha Pals in the
                  world (DT_PalWildSpawner, FieldBoss)
        boss_names    {text id: text} the towers' and their bosses' names
        partner   {species: partner skill name}
        species   {id: {tribe, combi, prio, rarity, zukan, work, hp, atk,
                        def, craft, male, ignore_combi, is_pal}}
        combos    [[tribe a, gender a, tribe b, gender b, child species]]

    Cached as JSON and rebuilt when the pak changes (a game update).
    """
    stamp = _pak_stamp()
    if cache and os.path.exists(cache):
        try:
            with open(cache) as fh:
                blob = json.load(fh)
            if (blob.get("version") == GAME_DATA_VERSION
                    and blob.get("lang") == lang
                    and blob.get("stamp") == stamp):
                return blob
        except (OSError, ValueError):
            pass
    own = pak is None
    pak = pak or Pak()
    try:
        def asset(tail):
            hits = [k for k in pak.files if k.endswith(tail)]
            return hits[0] if hits else None
        text = asset("L10N/%s/Pal/DataTable/Text/DT_SkillNameText_Common"
                     ".uasset" % lang)
        names = dict((row, v.get("TextData")) for row, v in
                     _text_rows(pak, text)) if text else {}

        passives = {}
        for row, v in _table(pak, asset(
                "Pal/DataTable/PassiveSkill/DT_PassiveSkill_Main.uasset")):
            # effects on the Pal itself, and those aimed elsewhere (the
            # player, the party, the base) which do not change its own stats
            eff, other = [], []
            for i in range(1, 5):
                t = v.get("EffectType%d" % i)
                if t and t != "no":
                    val = round(v.get("EffectValue%d" % i) or 0, 1)
                    target = v.get("TargetType%d" % i) or "ToSelf"
                    if target in SELF_TARGETS:
                        eff.append([t, val])
                    else:
                        other.append([t, val, target])
            nm = names.get("PASSIVE_" + (v.get("OverrideNameTextId")
                                         if v.get("OverrideNameTextId")
                                         not in (None, "None") else row))
            desc_id = v.get("OverrideDescMsgID")
            passives[row] = {
                "name": nm or row, "rank": v.get("Rank") or 0, "effects": eff,
                "roll": bool(v.get("AddPal")),     # can appear on any Pal
                "other": other,
                "values": [round(v.get("EffectValue%d" % i) or 0, 1)
                           for i in range(1, 5)],
                "desc": desc_id if desc_id not in (None, "None")
                else "PASSIVE_" + row}

        skills = {}
        for row, v in _table(pak, asset(
                "Pal/DataTable/Waza/DT_WazaDataTable.uasset")):
            wid = v.get("WazaType")
            if not wid or wid in skills:
                continue
            skills[wid] = {"name": names.get("ACTION_SKILL_" + wid) or wid,
                           "element": v.get("Element") or "Normal",
                           "power": v.get("Power") or 0,
                           "cooldown": round(v.get("CoolTime") or 0, 1),
                           "category": v.get("Category") or "",
                           # status effects it builds up on the target
                           "effects": [[v["EffectType%d" % i],
                                        v.get("EffectValue%d" % i) or 0]
                                       for i in (1, 2)
                                       if v.get("EffectType%d" % i)
                                       not in (None, "None", "no")]}

        learn = {}
        for row, v in _table(pak, asset(
                "Pal/DataTable/Waza/DT_WazaMasterLevel.uasset")):
            if v.get("PalID") and v.get("WazaID"):
                learn.setdefault(v["PalID"], []).append(
                    [v.get("Level") or 1, v["WazaID"]])
        for rows in learn.values():
            rows.sort()

        species, partner = {}, {}
        for row, v in _table(pak, asset(
                "Pal/DataTable/Character/DT_PalMonsterParameter.uasset")):
            work = {k: v.get("WorkSuitability_" + k) or 0 for k in WORK_KINDS}
            species[row] = {
                "tribe": v.get("Tribe") or "", "combi": v.get("CombiRank"),
                "prio": v.get("CombiDuplicatePriority"),
                "rarity": v.get("Rarity"), "zukan": v.get("ZukanIndex"),
                "suffix": v.get("ZukanIndexSuffix") or "",
                "work": {k: n for k, n in work.items() if n},
                "hp": v.get("HP"), "atk": v.get("ShotAttack"),
                "def": v.get("Defense"), "craft": v.get("CraftSpeed"),
                "male": v.get("MaleProbability"),
                "ignore_combi": bool(v.get("IgnoreCombi")),
                "is_pal": bool(v.get("IsPal")),
                "boss": bool(v.get("IsBoss")),
                # traits every Pal of the species is born with (Legend, ...)
                "passives": [v.get("PassiveSkill%d" % i) for i in range(1, 5)
                             if v.get("PassiveSkill%d" % i) not in
                             (None, "", "None")],
            }
            skill = names.get("PARTNERSKILL_" + row)
            if skill:
                partner[row] = skill

        # descriptions: passive skills, and partner skills (the text a Pal
        # shows the first time it is sent out), plus every name they refer to
        def text_table(tail):
            a = asset(tail)
            return dict((r, v.get("TextData") or "") for r, v in
                        _text_rows(pak, a)) if a else {}
        skill_desc = text_table("L10N/%s/Pal/DataTable/Text/"
                                "DT_SkillDescText_Common.uasset" % lang)
        passive_desc = {k: v for k, v in skill_desc.items()
                        if k.startswith("PASSIVE_")}
        first = text_table("L10N/%s/Pal/DataTable/Text/"
                           "DT_PalFirstActivatedInfoText.uasset" % lang)
        partner_desc = {k[len("PAL_FIRST_SPAWN_DESC_"):]: v
                        for k, v in first.items()
                        if k.startswith("PAL_FIRST_SPAWN_DESC_")}
        import re
        wanted = set()
        for t in list(passive_desc.values()) + list(partner_desc.values()):
            wanted.update(re.findall(r"id=\|([^|]+)\|", t))
            wanted.update(re.findall(r"\[([A-Za-z0-9_]+)\]", t))
        refs = {}
        items = text_table("L10N/%s/Pal/DataTable/Text/"
                           "DT_ItemNameText_Common.uasset" % lang)
        ui = text_table("L10N/%s/Pal/DataTable/Text/"
                        "DT_UI_Common_Text_Common.uasset" % lang)
        for w in wanted:
            for src in (items.get("ITEM_NAME_" + w), ui.get(w),
                        names.get("ACTION_SKILL_" + w), names.get(w)):
                if src:
                    refs[w] = src
                    break

        # status effects: the game's names, and its help page about them
        status = {}
        for k, v in ui.items():
            if k.startswith("ADDITIONAL_EFFECT_") and v:
                status[k[len("ADDITIONAL_EFFECT_"):]] = {"name": v, "desc": ""}
        helps = text_table("L10N/%s/Pal/DataTable/Text/"
                           "DT_HelpGuideDescText.uasset" % lang)
        for t in helps.values():
            parts = re.split(r"^- (.+?) -\s*$", t, flags=re.M)
            for title, body in zip(parts[1::2], parts[2::2]):
                title = title.strip()
                for st in status.values():
                    n = st["name"]
                    if title == n or title.startswith(n[:4]) and \
                            n.startswith(title[:4]):
                        st["desc"] = st["desc"] or " ".join(body.split())

        # items the partner skills and passives mention (what a Pal drops),
        # for a page to explain them: DT_ItemDataTable and its texts
        item_desc = text_table("L10N/%s/Pal/DataTable/Text/"
                               "DT_ItemDescriptionText_Common.uasset" % lang)
        item_rows = dict(_table(pak, asset(
            "Pal/DataTable/Item/DT_ItemDataTable.uasset")))
        mentioned = set()
        for t in list(passive_desc.values()) + list(partner_desc.values()):
            mentioned.update(re.findall(r"<itemName id=\|([^|]+)\|", t))

        objects = text_table("L10N/%s/Pal/DataTable/Text/"
                             "DT_MapObjectNameText_Common.uasset" % lang)
        pal_names = text_table("L10N/%s/Pal/DataTable/Text/"
                               "DT_PalNameText_Common.uasset" % lang)

        def plain(t):                     # item texts: tags to names
            def name(m):
                kind, key = m.group(1).lower(), m.group(2)
                if kind == "itemname":
                    return items.get("ITEM_NAME_" + key) or key
                if kind == "mapobjectname":
                    return objects.get("MAPOBJECT_NAME_" + key) or key
                if kind == "uicommon":
                    return ui.get(key) or key.split("_")[-1]
                if kind == "activeskillname":
                    return names.get("ACTION_SKILL_" + key) or key
                if kind == "charactername":
                    return pal_names.get("PAL_NAME_" + key) or key
                return key
            t = re.sub(r"<(\w+) id=\|([^|]+)\|[^>]*/>", name, t or "")
            t = re.sub(r"<[^>]+>", "", t)
            return " ".join(t.split())
        item_info = {}
        by_lower = {k.lower(): k for k in item_rows}
        for iid in sorted(mentioned):
            real = by_lower.get(iid.lower(), iid)    # the texts say "Iceorgan"
            row = item_rows.get(real) or {}
            desc = item_desc.get("ITEM_DESC_" + real)
            item_info[iid] = {
                "name": items.get("ITEM_NAME_" + real) or real,
                "desc": plain(desc) if desc and desc != "None" else "",
                "type": row.get("TypeA") or "", "kind": row.get("TypeB") or "",
                "rarity": row.get("Rarity") or 0, "price": row.get("Price") or 0,
                "weight": round(row.get("Weight") or 0, 2)}

        # bosses: the alpha Pals in the world, with their level, and the
        # towers' names (the boss planner)
        field_bosses = []
        for row, v in _table(pak, asset(
                "Pal/DataTable/Spawner/DT_PalWildSpawner.uasset")):
            sp = v.get("Pal_1") or ""
            if v.get("SpawnerType") == "FieldBoss" and sp.startswith("BOSS_"):
                field_bosses.append({"spawner": v.get("SpawnerName") or row,
                                     "species": sp,
                                     "level": v.get("LvMax_1") or
                                     v.get("LvMin_1") or 1})
        prefix = text_table("L10N/%s/Pal/DataTable/Text/"
                            "DT_NamePrefixText_Common.uasset" % lang)
        boss_names = {k: v for k, v in list(ui.items()) + list(prefix.items())
                      if k.startswith(("BOSS_BATTLE_NAME_", "GYM_NAME_"))}

        # quests: DT_PalQuestData names each quest's blueprint, whose name
        # table holds its title and description text ids
        quests = []
        talk = text_table("L10N/%s/Pal/DataTable/Text/"
                          "DT_NpcTalkText_Common.uasset" % lang)
        qa = asset("Pal/DataTable/Quest/DT_PalQuestData.uasset")
        if qa:
            qnames = name_table(pak.read(qa))
            qexp = pak.read(qa[:-7] + ".uexp")
            qr = UexpReader(qexp, qnames)
            qr.properties(len(qexp))
            qr.p += 4
            qrows = []
            for _ in range(qr.i32()):
                if qr.p >= len(qexp):
                    break
                qid = qr.name()
                kind, bp = "", ""
                for fn, ft, fo in qr.properties(len(qexp)):
                    if fn == "QuestType":
                        kind = UexpReader(qexp, qnames, fo).name()
                    elif fn == "QuestData" and ft == "SoftObjectProperty":
                        bp = UexpReader(qexp, qnames, fo).name()
                qrows.append((qid, kind.split("::")[-1], bp))
            all_ids = {q for q, _, _ in qrows}
        for qid, kind, bp in (qrows if qa else []):
            if kind not in ("Main", "Sub") or qid.startswith("Test_") \
                    or qid.endswith(("_Old", "_Replay")):
                continue
            title = desc = ""
            nxt = []
            seen_bp = set()
            while bp.startswith("/Game/") and bp not in seen_bp and \
                    not (title and desc):
                seen_bp.add(bp)          # its own, then what it is made from
                bp_asset = asset(bp[len("/Game/"):] + ".uasset")
                if not bp_asset:
                    break
                keys = name_table(pak.read(bp_asset))
                if not seen_bp - {bp}:   # the quests it hands out next
                    nxt = [k for k in keys if k in all_ids and k != qid]
                for key in keys:
                    low = key.lower()
                    if not low.startswith("quest_"):
                        continue
                    text_of = ui.get(key) or talk.get(key) or ""
                    if not title and ("title" in low or "questname" in low):
                        title = text_of
                    elif not desc and "desc" in low:
                        desc = text_of
                parents = [k for k in keys if k.startswith("/Game/Pal/Blue"
                           "print/Quest/") and k.endswith("Base")]
                bp = parents[0] if parents else ""
            if not title:                # tidy the id: Capture30Pal
                title = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[a-z])(?=[0-9])",
                               " ", qid.split("_", 1)[1]).replace("_", " ")
            elif qid.startswith("Sub_PalDisplay_"):
                title += " (%s)" % qid.split("_")[2]
            quests.append({
                "id": qid, "type": kind, "next": nxt,
                "title": plain(title), "desc": re.sub(
                    r"\{[A-Za-z]+\}", "...", plain(desc))})

        # the Pal surgery table: passives that can be implanted
        surgery_asset = asset("MapObject/DT_OperatingTablePassiveSkillData"
                              "Table.uasset")
        implants = {}
        for row, v in (_table(pak, surgery_asset) if surgery_asset else []):
            pid = v.get("PassiveSkill") or row
            if pid in passives:
                item = v.get("RequireItemId")
                item = item if item and item != "None" else None
                passives[pid]["surgery"] = {
                    "price": v.get("Price") or 0,
                    "item": (items.get("ITEM_NAME_" + item) or item)
                    if item else None,
                    "item_id": item,
                    # a Disposable Implant is used up; an Implant is a key
                    # item that is kept and can be used again
                    "consumable": bool(item and "_Consumable_" in item),
                    "item_desc": plain(
                        item_desc.get((item_rows.get(item) or {}).get(
                            "OverrideDescription") or "") or
                        item_desc.get("ITEM_DESC_" + item) or "")
                    if item else "",
                    "from": []}
                if item:
                    implants[item] = pid
        # where the implants come from: the item lottery (what a machine or
        # a chest gives), with the chance per roll of its slot
        lottery = asset("Pal/DataTable/Item/DT_ItemLotteryDataTable.uasset")
        slots, hits = {}, []
        for row, v in (_table(pak, lottery) if lottery and implants else []):
            key = (v.get("FieldName"), v.get("SlotNo"))
            slots[key] = slots.get(key, 0.0) + (v.get("WeightInSlot") or 0)
            if v.get("StaticItemId") in implants:
                hits.append((v.get("StaticItemId"), key,
                             v.get("WeightInSlot") or 0))
        found = {}
        for item, (field, slot), w in hits:
            place = re.sub(r"_?\d+$", "", field or "")
            where = place.split("_")[0]
            label = objects.get("MAPOBJECT_NAME_" + where) or \
                re.sub(r"(?<=[a-z])(?=[A-Z])", " ", where)
            chance = w / slots[(field, slot)] if slots.get((field, slot)) \
                else 0
            entry = found.setdefault((item, label), [])
            entry.append(chance)
        for (item, label), chances in found.items():
            passives[implants[item]]["surgery"]["from"].append({
                "where": label, "chance": [min(chances), max(chances)],
                "kinds": len(chances)})
        try:
            partner_params = read_partner_params(pak)
        except Exception:                        # a format change must not
            partner_params = {}                  # take the whole view down
        combos = []
        for row, v in _table(pak, asset(
                "Pal/DataTable/Character/DT_PalCombiUnique.uasset")):
            combos.append([v.get("ParentTribeA"), v.get("ParentGenderA"),
                           v.get("ParentTribeB"), v.get("ParentGenderB"),
                           v.get("ChildCharacterID")])
    finally:
        if own:
            pak.close()
    blob = {"version": GAME_DATA_VERSION, "lang": lang, "stamp": stamp,
            "passives": passives, "skills": skills, "partner": partner,
            "species": species, "combos": combos,
            "passive_desc": passive_desc, "partner_desc": partner_desc,
            "refs": refs, "partner_params": partner_params,
            "learn": learn, "status": status, "items": item_info,
            "field_bosses": field_bosses, "boss_names": boss_names,
            "quests": quests}
    if cache:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w") as fh:
                json.dump(blob, fh)
        except OSError:
            pass
    return blob


def _text_rows(pak, asset):
    """[(row, {"TextData": source text})] for a localisation table."""
    out = []
    names = name_table(pak.read(asset))
    uexp = pak.read(asset[:-7] + ".uexp")
    r = UexpReader(uexp, names)
    r.properties(len(uexp))
    r.p += 4
    for _ in range(r.i32()):
        if r.p >= len(uexp):
            break
        row = r.name()
        text = None
        for fn, ft, fo in r.properties(len(uexp)):
            if ft == "TextProperty":
                o = fo + 4 + 1
                _ns, o = _fstring(uexp, o)
                _key, o = _fstring(uexp, o)
                text, o = _fstring(uexp, o)
        out.append((row, {"TextData": text}))
    return out


def _nested(r, uexp, names, limit):
    """A tagged property block as a nested dict, including arrays of
    structs, names and floats - enough for DT_PartnerSkillParameter."""
    out = {}
    while r.p < limit:
        nm = r.name()
        if nm == "None":
            break
        ty = r.name()
        size = r.i32()
        r.i32()
        sub = None
        if ty == "StructProperty":
            sub = r.name()
            r.p += 16
        elif ty == "BoolProperty":
            out[nm] = bool(r.u8())
        elif ty in ("ByteProperty", "EnumProperty", "ArrayProperty",
                    "SetProperty"):
            sub = r.name()
        elif ty == "MapProperty":
            r.name()
            r.name()
        if r.u8():
            r.p += 16
        start = r.p
        if ty == "BoolProperty":
            continue
        val = None
        if ty == "NameProperty":
            val = UexpReader(uexp, names, start).name()
        elif ty == "IntProperty":
            val, = struct.unpack_from("<i", uexp, start)
        elif ty == "FloatProperty":
            val, = struct.unpack_from("<f", uexp, start)
        elif ty == "EnumProperty":
            val = UexpReader(uexp, names, start).name().split("::")[-1]
        elif ty == "StructProperty" and sub not in (
                "Vector", "Guid", "LinearColor", "Vector2D", "Rotator"):
            val = _nested(UexpReader(uexp, names, start), uexp, names,
                          start + size)
        elif ty == "ArrayProperty":
            rr = UexpReader(uexp, names, start)
            n = rr.i32()
            if sub == "StructProperty":
                rr.name()
                rr.name()
                rr.i32()
                rr.i32()
                rr.name()
                rr.p += 16
                if rr.u8():
                    rr.p += 16
                val = [_nested(rr, uexp, names, start + size)
                       for _ in range(n)]
            elif sub in ("NameProperty", "EnumProperty"):
                val = [rr.name() for _ in range(n)]
            elif sub == "FloatProperty":
                val = list(struct.unpack_from("<%df" % n, uexp, rr.p))
            elif sub == "IntProperty":
                val = list(struct.unpack_from("<%di" % n, uexp, rr.p))
        out[nm] = val
        r.p = start + size
    return out


def read_partner_params(pak):
    """{species: {"passives": [[ids] per rank], "refs": [[ids] per rank],
    "active": [value per rank]}} from DT_PartnerSkillParameter - what the
    placeholders in a partner skill's description stand for."""
    hits = [k for k in pak.files if k.endswith(
        "PassiveSkill/DT_PartnerSkillParameter.uasset")]
    if not hits:
        return {}
    names = name_table(pak.read(hits[0]))
    uexp = pak.read(hits[0][:-7] + ".uexp")
    r = UexpReader(uexp, names)
    r.properties(len(uexp))
    r.p += 4
    out = {}
    for _ in range(r.i32()):
        if r.p >= len(uexp):
            break
        row = r.name()
        v = _nested(r, uexp, names, len(uexp))

        def keys(block, field):
            ids = []
            for item in block.get(field) or []:
                key = (item.get("SkillName") or {}).get("Key") \
                    if "SkillName" in item else item.get("Key")
                if key and key != "None":
                    ids.append(key)
            return ids
        passives = [keys(rank, "SkillAndParametersArray")
                    for rank in v.get("PassiveSkills") or []]
        refs = [keys(rank, "PassiveSkillIds")
                for rank in v.get("TextReferencePassiveSkills") or []]
        active = (v.get("ActiveSkill") or {}).get(
            "ActiveSkill_MainValueByRank") or []
        if any(passives) or any(refs) or active:
            out[row] = {"passives": passives, "refs": refs,
                        "active": [round(a, 2) for a in active]}
    return out
