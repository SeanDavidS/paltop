#!/usr/bin/env python3
"""
palpals.py - what paltop can tell about one Pal, and who it can breed with.

Pal records come from palsave.read_characters (the save file); names and
numbers come from palpak.read_game_data (the game's own data tables). This
module only combines the two; it reads nothing itself.

Breeding follows the game's rule: two different species give the species
whose CombiRank is closest to the average of the parents' (rounded down, after
adding one; a tie goes to the higher CombiRank), with
the special pairs in DT_PalCombiUnique taking precedence, and the same species
always giving itself. Species that only come from a special pair are left out
of the normal lookup.
"""

from __future__ import annotations

import re

# the game's English names for its internal element ids
ELEMENT_NAMES = {
    "Normal": "Neutral", "Fire": "Fire", "Water": "Water", "Leaf": "Grass",
    "Electricity": "Electric", "Ice": "Ice", "Earth": "Ground",
    "Dark": "Dark", "Dragon": "Dragon",
}

WORK_NAMES = {
    "EmitFlame": "Kindling", "Watering": "Watering", "Seeding": "Planting",
    "GenerateElectricity": "Electricity", "Handcraft": "Handiwork",
    "Collection": "Gathering", "Deforest": "Lumbering", "Mining": "Mining",
    "OilExtraction": "Oil", "ProductMedicine": "Medicine",
    "Cool": "Cooling", "Transport": "Transporting", "MonsterFarm": "Farming",
}

EFFECT_NAMES = {
    "ShotAttack": "attack", "MeleeAttack": "melee attack",
    "Defense": "defense", "CraftSpeed": "work speed",
    "MoveSpeed": "movement speed", "MaxHP": "max HP",
    "FullStomatch_Decrease": "hunger rate", "Sanity_Decrease": "SAN drain",
    "ItemWeightReduction": "item weight", "MaxInventoryWeight":
    "carry weight", "CollectItemDrop": "gathering drops",
    "GainItemDrop": "item drops", "Mining": "mining efficiency",
    "Logging": "logging efficiency", "PalExp_Increase": "Pal EXP",
    "Regene_HP_Rate": "HP regen", "LifeSteal": "life steal",
    "AttackSpeedUp": "attack speed", "SwimSpeed": "swim speed",
    "BreedSpeed_InBaseCamp": "breeding speed",
    "PalEggHatchingSpeed": "egg hatching speed",
    "AutoHPRegeneRate": "HP regeneration", "BreedSpeed": "breeding speed",
    "ExplosionResist": "explosion resistance",
    "ResistAdditionalEffect_Burn": "Burn resistance",
    "ResistAdditionalEffect_Poison": "Poison resistance",
    "PalSP_Increase": "Pal stamina", "PlayerSP_DecreaseRate": "player stamina use",
    "ReloadSpeedUp": "reload speed",
    "SelfDeathAddItemDrop": "items dropped when it is defeated",
    "ShopBuyPrice_Money_Increase": "shop buying prices",
    "ShopSellPrice_Money_Increase": "shop selling prices",
    "WorldTreeDecayImmunity": "World Tree decay immunity",
    "KnockbackInvalid_ForPassiveSkill": "knockback immunity",
    "LeanBackInvalid_ForPassiveSkill": "flinch immunity",
}
# effects whose value says how much something goes down
EFFECT_DOWN = {"ActiveSkillCoolTime_Decrease": "active skill cooldown"}

# what the stat summary adds up, from each passive's effects
BONUS_KEYS = (("ShotAttack", "attack"), ("Defense", "defense"),
              ("CraftSpeed", "work speed"), ("MoveSpeed", "move speed"))

PREFIXES = ("BOSS_", "GYM_", "RAID_", "PREDATOR_", "SUMMON_", "QUEST_")


def base_species(sid):
    """BOSS_Boar -> Boar, Boss_LazyCatFish -> LazyCatFish."""
    for pre in PREFIXES:
        if sid[:len(pre)].upper() == pre:
            return sid[len(pre):]
    return sid


def species_key(game, sid):
    """The species row for a save id: the normal species first (an alpha's
    BOSS_ row describes the wild boss, not the caught Pal), then the exact id,
    both also case-insensitively (the save and the tables do not always agree
    on capitalisation)."""
    table = game.get("species") or {}
    for key in (base_species(sid), sid):
        if key in table:
            return key
    lower = {k.lower(): k for k in table}
    for key in (base_species(sid), sid):
        if key.lower() in lower:
            return lower[key.lower()]
    return None


def species_info(game, sid):
    key = species_key(game, sid)
    return (game.get("species") or {}).get(key) or {}


def effect_text(etype, value):
    """'+15% attack' for one passive effect."""
    if etype.startswith("WorkSuitabilityAddRank_"):
        kind = etype.split("_", 1)[1]
        return "%+g %s level" % (value, WORK_NAMES.get(kind, kind))
    if etype in EFFECT_DOWN:
        return "%+g%% %s" % (-value, EFFECT_DOWN[etype])
    if etype == "RideJumpCount_Increase":
        return "%+g jumps while mounted" % value
    name = EFFECT_NAMES.get(etype)
    if not name:
        m = re.match(r"Element(Boost|Resist)_(\w+)", etype)
        if m:
            el = ELEMENT_NAMES.get(m.group(2), m.group(2))
            name = "%s damage" % el if m.group(1) == "Boost" \
                else "%s resistance" % el
        else:
            name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", etype).replace(
                "_", " ").lower()
    return "%+g%% %s" % (value, name)


TARGET_WORDS = {"ToTrainer": "player", "ToOtomo": "party Pals'",
                "ToBaseCampPal": "base Pals'", "ToBuildObject": "buildings'"}


def effect_texts(game, pid):
    """Every effect of a passive as text, those on someone else marked:
    '+10% player attack'."""
    ps = passive(game, pid)
    out = [effect_text(t, v) for t, v in ps["effects"] if v]
    for t, v, target in ps.get("other") or []:
        if not v:
            continue
        txt = effect_text(t, v)
        num, _, rest = txt.partition(" ")
        out.append("%s %s %s" % (num, TARGET_WORDS.get(target, "others'"),
                                 rest))
    return out


def passive(game, pid):
    """{name, rank, effects} for a passive id, or a stand-in."""
    return (game.get("passives") or {}).get(pid) or {
        "name": pid, "rank": 0, "effects": []}


def bonuses(game, pal):
    """{stat label: total %} from the Pal's passives. Condenser stars and
    soul upgrades come on top of this; they are shown as levels, not folded
    in, because their exact percentages are not in the data paltop reads."""
    out = {label: 0.0 for _, label in BONUS_KEYS}
    for pid in pal.get("passives") or []:
        for etype, value in passive(game, pid)["effects"]:
            for key, label in BONUS_KEYS:
                if etype == key:
                    out[label] += value
    return out


def location(pal, containers, bases):
    """(short, long) for where a Pal is: team, Palbox, a base, or storage."""
    cid = pal.get("container")
    mine = containers.get(pal.get("owner") or "") or {}
    slot = pal.get("slot")
    if cid and cid == mine.get("team"):
        return "team", "in the team (slot %d)" % ((slot or 0) + 1)
    if cid and cid == mine.get("box"):
        box = (slot or 0) // 30 + 1
        return "box", "Palbox, page %d" % box
    for i, b in enumerate(bases or []):
        if cid and cid == b.get("container"):
            return "base%d" % (i + 1), "working at base %d" % (i + 1)
    return "other", "other storage (guild chest, egg incubator or similar)"


def score(pal, game):
    """A sort key for "best": lucky, condenser stars, good passives, IVs."""
    good = sum(max(0, passive(game, p)["rank"]) for p in pal["passives"])
    return (pal["lucky"], pal["rank"], good,
            pal["hp_iv"] + pal["atk_iv"] + pal["def_iv"], pal["level"])


# -- breeding ---------------------------------------------------------------

class Breeder:
    """Child species for two parents, per the game's breeding rule."""

    def __init__(self, game):
        self.game = game
        table = game.get("species") or {}
        self.unique = {}
        only_unique = set()
        for ta, ga, tb, gb, child in game.get("combos") or []:
            self.unique.setdefault(frozenset((ta, tb)), []).append(
                (ta, ga, tb, gb, child))
            only_unique.add(child)
        self.pool = sorted(
            (v["combi"], v.get("prio") or 0, k) for k, v in table.items()
            if v.get("is_pal") and v.get("combi") and v["combi"] > 0
            and not v.get("ignore_combi") and (v.get("zukan") or -1) >= 0
            and k == base_species(k) and k not in only_unique)

    def child(self, a, b, gender_a=None, gender_b=None):
        """Species id of the child of save species ids a and b, or None."""
        ka, kb = species_key(self.game, a), species_key(self.game, b)
        if not ka or not kb:
            return None
        ka, kb = base_species(ka), base_species(kb)
        sa, sb = species_info(self.game, ka), species_info(self.game, kb)
        if ka == kb:
            return ka
        pair = [(sa.get("tribe"), gender_a), (sb.get("tribe"), gender_b)]
        for ta, ga, tb, gb, child in self.unique.get(
                frozenset((sa.get("tribe"), sb.get("tribe"))), []):
            for (t1, g1), (t2, g2) in (pair, pair[::-1]):
                if t1 == ta and t2 == tb and ga in (None, "None", g1) \
                        and gb in (None, "None", g2):
                    return child
        if not sa.get("combi") or not sb.get("combi"):
            return None
        power = (sa["combi"] + sb["combi"] + 1) // 2
        # equally close: the higher CombiRank wins (Lamball + Cattiva is
        # Daedream at 2910, not Tanzee at 2900)
        best = min(self.pool, key=lambda t: (abs(t[0] - power), -t[0]),
                   default=None)
        return best[2] if best else None

    def options(self, pal, others, limit=8):
        """What `pal` can have with each of `others` (opposite gender):
        [(child species, partner record)], one per child, best first."""
        seen = {}
        for o in others:
            if o is pal or o.get("gender") == pal.get("gender"):
                continue
            if not o.get("gender") or not pal.get("gender"):
                continue
            c = self.child(pal["species"], o["species"], pal.get("gender"),
                           o.get("gender"))
            if not c:
                continue
            prev = seen.get(c)
            if prev is None or score(o, self.game) > score(prev[1], self.game):
                seen[c] = (c, o)
        out = list(seen.values())
        # rarer first; among equals, the lower CombiRank is the stronger Pal
        out.sort(key=lambda t: (
            -(species_info(self.game, t[0]).get("rarity") or 0),
            species_info(self.game, t[0]).get("combi") or 0))
        return out[:limit]


# -- descriptions -----------------------------------------------------------

ELEMENT_LETTERS = {"Normal": "N", "Fire": "F", "Water": "W", "Leaf": "Gr",
                   "Electricity": "E", "Ice": "I", "Earth": "Gd",
                   "Dark": "Dk", "Dragon": "Dr"}

_TAG_ID = re.compile(r"<[A-Za-z]+ id=\|([^|]+)\|[^>]*/>")
# the same, keeping the tag's kind, for rich text
_TAG_KIND = re.compile(r"<([A-Za-z]+) id=\|([^|]+)\|[^>]*/>")


def _mark(kind, key, text):
    """A piece of rich text: \x03kind|key\x04text\x05, for a page to
    colour (an element, an item it can explain, a status, ...)."""
    return "\x03%s|%s\x04%s\x05" % (kind, key, text)


def _rich_tag(game, names, m):
    tag, key = m.group(1).lower(), m.group(2)
    text = _ref(game, names, key)
    if tag == "img" or not text:
        return ""
    if key.startswith("COMMON_ELEMENT_NAME_"):
        return _mark("el", key[len("COMMON_ELEMENT_NAME_"):], text)
    if key.startswith("ADDITIONAL_EFFECT_"):
        return _mark("status", key[len("ADDITIONAL_EFFECT_"):], text)
    kind = {"itemname": "item", "charactername": "pal",
            "activeskillname": "skill", "mapobjectname": "work"}.get(tag)
    return _mark(kind, key, text) if kind else text
_TAG_PAIR = re.compile(r"<[A-Za-z0-9_]+>([^<]*)</>")
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")


def _ref(game, names, key):
    if key.startswith("ElemIcon_"):
        return ""
    if key.startswith("COMMON_ELEMENT_NAME_"):
        el = key[len("COMMON_ELEMENT_NAME_"):]
        return ELEMENT_NAMES.get(el, el)
    if key in WORK_NAMES:
        return WORK_NAMES[key]
    if names:
        hit = names.get(key) or names.get(key.lower())
        if hit:
            return hit
    return (game.get("refs") or {}).get(key) or key


def render(game, raw, values=None, names=None, rich=False):
    """The game's rich text as plain text: tags resolved to names, and
    {placeholders} filled from `values` (unknown ones become '?'). With
    `rich`, elements, items, statuses, Pals, skills, work and the numbers
    the game highlights come marked (see _mark) for a page to colour."""
    if not raw:
        return ""
    t = raw.replace("\r\n", " ").replace("\n", " ")
    if rich:
        t = _TAG_KIND.sub(lambda m: _rich_tag(game, names, m), t)
        t = _TAG_PAIR.sub(lambda m: _mark("num", "", m.group(1)), t)
    else:
        t = _TAG_ID.sub(lambda m: _ref(game, names, m.group(1)), t)
        t = _TAG_PAIR.sub(lambda m: m.group(1), t)
    t = re.sub(r"\[([A-Za-z0-9_]+)\]", lambda m: _ref(game, names,
                                                      m.group(1)), t)

    def fill(m):
        key = m.group(1)
        if key.startswith("ReferenceMsgId"):
            return ""
        v = (values or {}).get(key)
        if v is None:
            return "?"
        return ("%g" % v) if isinstance(v, (int, float)) else str(v)
    t = _PLACEHOLDER.sub(fill, t)
    return re.sub(r"\s+", " ", t).replace(" .", ".").strip()


def passive_description(game, pid):
    """The game's own description of a passive, or one made from its
    effects when the game has none."""
    ps = passive(game, pid)
    raw = (game.get("passive_desc") or {}).get(ps.get("desc") or "")
    if raw:
        vals = {"EffectValue%d" % (i + 1): abs(v)
                for i, v in enumerate(ps.get("values") or [])}
        return render(game, raw, vals)
    return ", ".join(effect_texts(game, pid))


def _partner_values(game, species, rank):
    """Numbers for a partner skill's placeholders at a condenser rank (1-5),
    from DT_PartnerSkillParameter: {PassiveN_EffectValueM} is effect M of
    the Nth passive at that rank, {ReferencePassiveN_...} the same for the
    passives the text refers to, {ActiveSkillMainValueByRank} the active
    skill's value."""
    params = (game.get("partner_params") or {}).get(species)
    if not params:
        return {}
    table = game.get("passives") or {}
    i = max(1, min(5, rank or 1)) - 1
    out = {}
    for prefix, lists in (("Passive", params.get("passives") or []),
                          ("ReferencePassive", params.get("refs") or [])):
        ids = lists[min(i, len(lists) - 1)] if lists else []
        for n, pid in enumerate(ids, 1):
            for m, v in enumerate((table.get(pid) or {}).get("values") or [],
                                  1):
                out["%s%d_EffectValue%d" % (prefix, n, m)] = abs(v)
    active = params.get("active") or []
    if active:
        out["ActiveSkillMainValueByRank"] = active[min(i, len(active) - 1)]
    return out


def partner_scaled(game, species, names=None, mark=None, rich=False):
    """The partner skill's text with every number that grows with the
    condenser written as its whole scale, 0 to 4 stars: "by 50/53/56/60/65%".
    Numbers that do not change stay single. With `mark` (a rank 1-5), that
    rank's number is wrapped in \x01...\x02 so a page can highlight it."""
    key = species_key(game, species) or species
    base = base_species(key)
    raw = (game.get("partner_desc") or {}).get(key) or \
        (game.get("partner_desc") or {}).get(base) or ""
    ranks = [_partner_values(game, key, r) or _partner_values(game, base, r)
             for r in range(1, 6)]
    merged = {}
    for k in {k for v in ranks for k in v}:
        seq = [v.get(k) for v in ranks]
        fmt = lambda x: "?" if x is None else "%g" % x
        if len(set(seq)) == 1:
            merged[k] = seq[0]
        else:
            merged[k] = "/".join(
                ("\x01%s\x02" if mark == i + 1 else "%s") % fmt(x)
                for i, x in enumerate(seq))
    return render(game, re.sub(r"\s+", " ", raw), merged, names, rich)


PARTNER_TAGS = (
    (r"flying mount", "flying"),
    (r"travel on water|swim", "water mount"),
    (r"can be ridden", "mount"),
    (r"restores? the health of the player and party pals", "healer"),
    (r"pal fighting alongside you", "buffs team"),
    (r"while in party, increases [^.]* of <img id=\|elemicon_", "buffs team"),
    (r"for each other <img id=\|elemicon_", "team synergy"),
    (r"player's attack type|increases? (?:player )?attack", "buffs player"),
    (r"carrying capacity", "carry weight"),
    (r"when assigned to|at a base|at the base", "base"),
)


_partner_cache = {}


def partner(game, species, rank=1, names=None):
    """Cached: the same species and rank always give the same answer, and
    the team builder asks thousands of times."""
    key = (id(game), species, rank, id(names))
    hit = _partner_cache.get(key)
    if hit is None:
        if len(_partner_cache) > 20000:
            _partner_cache.clear()
        hit = _partner_cache[key] = _partner(game, species, rank, names)
    return hit


def _partner(game, species, rank=1, names=None):
    """{name, text, tags, boost_self, boost_team, alongside, pair} for a
    species' partner skill. boost_self: elements whose teammates make this
    Pal stronger; boost_team: elements of teammates this Pal makes
    stronger."""
    key = species_key(game, species) or species
    base = base_species(key)
    raw = (game.get("partner_desc") or {}).get(key) or \
        (game.get("partner_desc") or {}).get(base) or ""
    raw = re.sub(r"\s+", " ", raw)
    name = (game.get("partner") or {}).get(base) or \
        (game.get("partner") or {}).get(key)
    low = raw.lower()
    tags = []
    for rx, tag in PARTNER_TAGS:
        if re.search(rx, low) and tag not in tags:
            tags.append(tag)
    if ("flying" in tags or "water mount" in tags) and "mount" in tags:
        tags.remove("mount")

    def elements_after(marker):
        """Elements named right after `marker`, and the stats the same
        sentence raises (attack, defense, speed)."""
        els, stats = [], set()
        for m in re.finditer(marker, raw, re.I):
            tail = raw[m.end():m.end() + 160]
            els += re.findall(r"COMMON_ELEMENT_NAME_(\w+)\|", tail)[:2]
            start = raw.rfind(".", 0, m.start()) + 1
            sentence = raw[start:m.end()].lower()
            for word, stat in (("attack", "attack"), ("defense", "defense"),
                               ("movement", "speed")):
                if word in sentence:
                    stats.add(stat)
        return sorted(set(els)), sorted(stats)
    boost_self, self_stats = elements_after(r"for each other ")
    boost_team, team_stats = elements_after(
        r"while in party, increases [^.]*? of ")
    pair = re.findall(r"id=\|([A-Za-z_]+)\|/> in your party", raw)
    # a skill that only grows the Pal's speed with its teammates is not a
    # fighting synergy: label it as what it is
    if "team synergy" in tags and not {"attack", "defense"} & set(self_stats):
        tags[tags.index("team synergy")] = "team speed"
    # buffs for "the Pal fighting alongside you", and party heals
    vals = _partner_values(game, key, rank) or _partner_values(game, base,
                                                               rank)
    along = None
    if "pal fighting alongside you" in low:
        m = re.search(r"stacks? up to (\w+) times", low)
        words = {"two": 2, "three": 3, "four": 4, "five": 5, "ten": 10}
        stacks = 1
        if m:
            stacks = int(m.group(1)) if m.group(1).isdigit() else \
                words.get(m.group(1), 1)
        pct = vals.get("Passive1_EffectValue1") or \
            vals.get("ReferencePassive1_EffectValue1") or 0
        at = low.find("alongside you")
        sentence = low[max(0, at - 200):at + 20]
        stats = [st for st in ("attack", "defense") if st in sentence]
        along = {"pct": pct, "stacks": stacks, "stats": stats or ["attack"],
                 "hp_loss": "loses health" in low,
                 "per": "kill" if "defeat an enemy" in low else
                 "hit" if "hitting an enemy" in low else None}
    heal = vals.get("ActiveSkillMainValueByRank") \
        if "restore the health of the player and party pals" in low else None
    if along and along["hp_loss"]:
        tags.append("drains HP")     # the Pal beside you pays in Health
    return {"name": name, "tags": tags, "alongside_buff": along,
            "heal": heal,
            "text": render(game, raw, vals, names),
            "boost_self": boost_self, "self_stats": self_stats,
            "boost_team": boost_team, "team_stats": team_stats,
            "alongside": "pal fighting alongside you" in low,
            "pair": pair}


def elements_of(sid, els):
    """Both elements of a species (one or two), from the element table."""
    for key in (sid, base_species(sid), "BOSS_" + base_species(sid)):
        hit = els.get(key)
        if hit:
            return [e for e in hit if e and e != "None"] or ["Normal"]
    return ["Normal"]


def matches(game, pal, text, names=None, els=None, skills=False):
    """True when `text` appears in the Pal's name, nickname, species,
    elements, passives or partner skill (case-insensitive); with `skills`,
    also its active skills and the statuses they apply."""
    text = (text or "").strip().lower()
    if not text:
        return True
    key = species_key(game, pal["species"]) or pal["species"]
    display = (names or {}).get(base_species(key)) or \
        (names or {}).get(base_species(key).lower()) or base_species(key)
    hay = [pal.get("nickname") or "", display, pal["species"],
           (game.get("partner") or {}).get(base_species(key)) or ""]
    hay += [passive(game, p)["name"] for p in pal.get("passives") or []]
    hay += [ELEMENT_NAMES.get(e, e) for e in elements_of(pal["species"],
                                                         els or {})]
    if skills:      # its active skills and the statuses they apply
        import palteam
        hay += palteam.skill_words(game, list(pal.get("skills") or []) +
                                   list(pal.get("learned") or []))
    return any(text in h.lower() for h in hay)


# trailing _<element> marks an elemental variant; the element itself comes
# from the game's data table, so the suffix is redundant in the name
VARIANT_SUFFIXES = ("Fire", "Water", "Dark", "Grass", "Leaf", "Electric",
                    "Electricity", "Ice", "Ground", "Earth", "Dragon",
                    "Normal", "Neutral", "Lightning", "otomo")


def pal_display(species, names=None):
    """(is_alpha, display name) for a raw Palworld species id.

    `names` is the game's own localisation table (see palpak.read_pal_names);
    without it, or for an id that table does not cover, the internal id is
    tidied up instead. Boss forms rarely have their own row, so fall back
    through the id's variants the way the element lookup does.
    """
    alpha = species[:5].upper() == "BOSS_"   # the save uses both cases
    stripped = species[5:] if alpha else species
    base = stripped
    for suf in VARIANT_SUFFIXES:
        if base.endswith("_" + suf):
            base = base[:-(len(suf) + 1)]
            break
    if names:
        for key in (species, stripped, base):
            hit = names.get(key) or names.get(key.lower())
            if hit:
                return alpha, hit
    return alpha, base


def element_of(species, els):
    """Element for a species id, falling back through its variants.

    Not every boss form has its own row (BOSS_Anubis does not, Anubis does),
    so try the exact id, then without the BOSS_ prefix, then without the
    elemental suffix, then without either.
    """
    alpha, base = pal_display(species)
    stripped = species[5:] if species[:5].upper() == "BOSS_" else species
    for key in (species, stripped, "BOSS_" + base, base):
        hit = els.get(key)
        if hit:
            return hit[0]
    return "Normal"
