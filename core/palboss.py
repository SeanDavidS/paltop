#!/usr/bin/env python3
"""
palboss.py - the boss planner: which of a player's Pals to bring to a boss.

The bosses come from the game data:

    towers        the GYM_ Pals (the tower bosses' Pals), matched to the
                  TowerBossDefeatFlag keys in the player records
    raids         the RAID_ Pals, matched to RaidBossDefeatCount
                  (PalSummon_<id>); the extra hands and heads are left out
    field bosses  DT_PalWildSpawner's FieldBoss spawners, whose names are the
                  keys of NormalBossDefeatFlag, with the spawner's level

Boss HP and tower and raid levels are not in the game's data tables, so the
level is picked on the page (towers and raids start at the player's level)
and so is an HP multiplier. Everything uses paltop's battle model
(palbattle): damage = power x attack / defense x element x same element.

For each of the player's Pals paltop works out the damage it does each turn
against the boss, how many turns it lasts against the boss's skills, and
from that the share of the boss's HP it takes alone ("share"; five Pals whose
shares add up to 100% should win one after another). The best ones are also
fought out in palbattle.simulate, one against the boss, to give a win %.
"""

from __future__ import annotations

import math
import re
import random

import palbattle
import palpals
import palteam

# tower flag key -> (the boss's Pal, the name key of the tower's leader)
TOWERS = (
    ("GrassBoss", "GYM_ElecPanda", "GYM_NAME_Meadow"),
    ("ForestBoss", "GYM_LilyQueen", "GYM_NAME_Forest"),
    ("DesertBoss", "GYM_Horus", "GYM_NAME_Desert"),
    ("ElectricBoss", "GYM_ThunderDragonMan", "GYM_NAME_Volcano"),
    ("SnowBoss", "GYM_BlackGriffon", "GYM_NAME_Snow"),
    ("SakurajimaBoss", "GYM_MoonQueen", "GYM_NAME_Sakurajima"),
    ("VikingBoss", "GYM_SnowTigerBeastman", "GYM_NAME_Viking"),
    ("SorajimaBoss", "GYM_BlueSkyDragon", "GYM_NAME_Sorajima"),
    ("WorldTreeBoss", "GYM_WorldTreeDragon", "GYM_NAME_LastBoss"),
)

# the first word(s) of a spawner name -> the region shown
REGIONS = (
    ("81_1_grass", "Windswept Hills"), ("1_10_plain", "Windswept Hills"),
    ("81_1_forest", "forest"), ("81_2_dessert", "desert"),
    ("desertisland", "desert island"), ("81_3_snow", "snow"),
    ("snow_", "snow"), ("81_3_volcano", "volcano"),
    ("volcanoiskand", "volcano island"), ("81_4_Sakurajima", "Sakurajima"),
    ("sakura_", "Sakurajima"), ("81_5_Yamijima", "Feybreak"),
    ("yamijima", "Feybreak"), ("81_6_Ocean", "ocean"),
    ("skyisland", "Sky Islands"), ("worldtree", "World Tree"),
    ("sanctuary", "wildlife sanctuary"), ("remainsIsland", "ruins island"),
    ("5_2_island", "ice island"),
)

KINDS = ("tower", "raid", "field")
NAME_FIX = {"YakushimaBoss001_Green": "Yakushima raid boss 1 (green)",
            "YakushimaBoss002": "Yakushima raid boss 2"}  # no names in the data
HP_TIMES = (1, 2, 3, 5, 10)
SIMS = 40                 # battles fought for each win %
SIM_TOP = 10              # ... for this many of the best Pals
IV = 50                   # a boss's IVs (not in the data either)
ROLL = 0.925              # the battle model's mean random roll
CRIT_MEAN = 1 + palbattle.CRIT_CHANCE * (palbattle.CRIT - 1)


def region(spawner):
    for prefix, name in REGIONS:
        if spawner.startswith(prefix):
            return name
    return ""


def _name(species, names):
    for prefix in ("GYM_", "RAID_"):
        if species.startswith(prefix):
            species = species[len(prefix):]
    if species in NAME_FIX:
        return NAME_FIX[species]
    return palbattle.pal_display(species, names)[1]


def _tidy(skill_id):
    """Unique_WorldTreeDragon_PaldiumCannon -> Paldium Cannon"""
    last = skill_id.split("_")[-1]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", last)


def boss_list(game, names=None):
    """[{id, kind, hard, species, name, title, level, region}] - `id` is the
    key the player records use, `level` None where the data has none. Hard
    mode bosses are the _2 forms of the tower and raid Pals (stronger, with
    more skills); the records keep tower wins as <key>_Normal and <key>_Hard
    in TowerBossDefeatCount."""
    labels = game.get("boss_names") or {}
    species = game.get("species") or {}
    out = []
    for key, sp, title in TOWERS:
        for hard, form in ((False, sp), (True, sp + "_2")):
            if form not in species:
                continue
            leader = (labels.get(title + "_Hard") if hard else None) or \
                (labels.get(title) or "").strip()
            out.append({"id": "BOSS_BATTLE_NAME_" + key + ("_Hard" if hard
                                                           else ""),
                        "kind": "tower", "hard": hard, "species": form,
                        "name": _name(sp, names),
                        "title": leader or labels.get("BOSS_BATTLE_NAME_" + key)
                        or key,
                        "tower": labels.get("BOSS_BATTLE_NAME_" + key) or "",
                        "level": None, "region": ""})
    for sp in species:
        if not sp.startswith("RAID_") or "_Hand" in sp or "_Head" in sp:
            continue
        hard = sp.endswith("_2")
        base = sp[:-2] if hard else sp
        out.append({"id": "PalSummon_" + sp[5:], "kind": "raid", "hard": hard,
                    "species": sp, "name": _name(base, names), "title": "raid",
                    "tower": "", "level": None, "region": ""})
    for fb in game.get("field_bosses") or []:
        out.append({"id": fb["spawner"], "kind": "field", "hard": False,
                    "species": fb["species"], "name": _name(fb["species"], names),
                    "title": "field boss", "tower": "", "level": fb.get("level"),
                    "region": region(fb["spawner"])})
    out.sort(key=lambda b: (KINDS.index(b["kind"]), b["hard"]))
    return out


def beaten_by(containers, players):
    """{boss id: [player names]} from the player records."""
    out = {}
    for pid, c in (containers or {}).items():
        who = players.get(pid)
        if not who:
            continue
        keys = set()
        for ks in (c.get("beaten") or {}).values():
            keys.update(ks)
        towers = (c.get("boss_counts") or {}).get("TowerBossDefeatCount") or {}
        for k in towers:          # GrassBoss_Normal, GrassBoss_Hard
            if k.endswith("_Hard"):
                keys.add("BOSS_BATTLE_NAME_" + k)
        for k in keys:
            out.setdefault(k, []).append(who)
    return out


def _el_keys(sp):
    """The ids to look the elements up by: a hard mode form (_2) has its
    own row, or falls back to the normal one."""
    keys = [sp, palpals.base_species(sp)]
    if sp.endswith("_2"):
        keys += [sp[:-2], palpals.base_species(sp[:-2])]
    return keys


def boss_elements(sp, els):
    """The boss's elements, or [] when the data does not say."""
    for key in _el_keys(sp):
        hit = els.get(key)
        if hit:
            return [e for e in hit if e and e != "None"]
    return []


def boss_skills(game, boss, level):
    """The skills it fights with, hardest-hitting first: a tower or raid boss
    uses its whole list (its "levels" just count 1, 2, 3... - hard mode
    Bastigor has Water and Dark skills besides its Ice ones), a field boss
    the three best the species has learned by its level, like any wild Pal."""
    table = game.get("skills") or {}
    rows = palteam.learnset(game, boss["species"])
    if boss["kind"] == "field":
        rows = [r for r in rows if r[0] <= level]
    ids = [w for _, w in rows if (table.get(w) or {}).get("power", 0) > 1]
    ids.sort(key=lambda w: -table[w]["power"])
    return ids[:3] if boss["kind"] == "field" else ids


def boss_fighter(game, boss, level, els, names, hp_times=1):
    pal = {"species": boss["species"], "level": level, "hp_iv": IV,
           "atk_iv": IV, "def_iv": IV, "passives": [], "rank": 1,
           "skills": boss_skills(game, boss, level)}
    # the boss's own row (GYM_, RAID_, BOSS_), not the normal species'
    # that palpals.species_info picks first for caught Pals
    table = game.get("species") or {}
    if boss["species"] in table:
        game = dict(game, species=dict(table, **{
            palpals.base_species(boss["species"]): table[boss["species"]]}))
    f = palbattle.fighter(game, pal, els, "boss", names,
                          skill_limit=len(pal["skills"]) or 3)
    for sk in f["skills"]:
        if sk["name"].startswith("Unique_"):   # no name in the data
            sk["name"] = _tidy(sk["name"])
    f["els"] = boss_elements(boss["species"], els)
    f["hp"] = int(f["hp"] * hp_times)
    f["name"] = boss["name"]
    return f


def _hit(sk, me, foe):
    """Mean damage of one use of a skill."""
    stab = palbattle.SAME_ELEMENT if sk["element"] in me["els"] else 1.0
    return sk["power"] * me["atk"] / max(1.0, foe["def"]) * palbattle.SCALE * \
        palteam.multiplier(sk["element"], foe["els"]) * stab * ROLL * CRIT_MEAN


def per_turn(me, foe):
    """Mean damage a turn, with the skills picked the way the battle model
    picks them (weighted by the square root of their power)."""
    w = [math.sqrt(max(s["power"], palbattle.STATUS_ONLY)) for s in me["skills"]]
    return sum(wi * _hit(s, me, foe) for wi, s in zip(w, me["skills"])) / \
        max(1e-9, sum(w))


def _statuses(game, skills):
    """[(status, label)] the skills can apply."""
    status = game.get("status") or {}
    out = []
    for s in skills:
        for etype, _v, *_ in s.get("effects") or []:
            if etype in palteam.STATUS_LABEL and etype not in [x for x, _ in out]:
                out.append((etype, (status.get(etype) or {}).get("name") or
                            palteam.STATUS_LABEL[etype]))
    return out


def boss_card(game, boss, level, els, names, hp_times=1):
    f = boss_fighter(game, boss, level, els, names, hp_times)
    weak, strong = [], []
    if f["els"]:
        for e in palteam.ELEMENTS:
            m = palteam.multiplier(e, f["els"])
            if m > 1:
                weak.append([e, m])
            elif m < 1:
                strong.append([e, m])
    sts = _statuses(game, f["skills"])
    return dict(boss, level=level, hp_times=hp_times, fighter=f,
                els=f["els"], els_known=bool(f["els"]),
                weak=weak, resist=strong,
                statuses=[{"key": k, "name": n,
                           "immune": [palpals.ELEMENT_NAMES.get(e, e) for e in
                                      palteam.STATUS_IMMUNE.get(k, ())]}
                          for k, n in sts])


def _learned_better(game, pal, me, foe):
    """A skill the Pal has learned but not equipped that would hit the boss
    harder than its best equipped one: {name, element, gain %} or None."""
    table = game.get("skills") or {}
    have = {s.get("id") for s in me["skills"]}
    best_now = max((_hit(s, me, foe) for s in me["skills"]), default=0)
    best = None
    for wid in pal.get("learned") or []:
        sk = table.get(wid)
        if wid in have or not sk or (sk.get("power") or 0) <= 1:
            continue
        d = _hit({"power": sk["power"], "element": sk["element"]}, me, foe)
        if d > best_now * 1.1 and (not best or d > best[0]):
            best = (d, sk)
    if not best:
        return None
    return {"name": best[1]["name"], "element": best[1]["element"],
            "gain": round(100 * (best[0] / max(1e-9, best_now) - 1))}


def plan(game, boss, level, pals, els, names, hp_times=1, rng=None,
         hard=False):
    """The boss card and the player's Pals ranked against it."""
    rng = rng or random.Random(level * 7919 + len(pals))
    card = boss_card(game, boss, level, els, names, hp_times)
    bf = card["fighter"]
    boss_sts = [k for k, _ in _statuses(game, bf["skills"])]
    rows = []
    for p in pals:
        info = palpals.species_info(game, p["species"])
        if not info.get("is_pal"):
            continue
        note = palteam.summon_note(p["species"])
        if note and not hard:
            continue
        me = palbattle.fighter(game, p, els, "", names)
        dealt = per_turn(me, bf)
        taken = per_turn(bf, me)
        turns = me["hp"] / max(1.0, taken)
        share = dealt * turns / max(1, bf["hp"])
        best = max(me["skills"], key=lambda s: _hit(s, me, bf))
        immune = [palteam.STATUS_LABEL.get(k, k) for k in boss_sts
                  if any(e in palteam.STATUS_IMMUNE.get(k, ()) for e in me["els"])]
        rows.append({"pal": p, "fighter": me, "dealt": round(dealt),
                     "taken": round(taken), "turns": round(turns, 1),
                     "share": share, "best": {
                         "name": best["name"], "element": best["element"],
                         "mult": palteam.multiplier(best["element"], bf["els"])},
                     "defense": min((palteam.multiplier(s["element"], me["els"])
                                     for s in bf["skills"]), default=1),
                     "worst": max((palteam.multiplier(s["element"], me["els"])
                                   for s in bf["skills"]), default=1),
                     "immune": immune, "hard": note,
                     "learned": _learned_better(game, p, me, bf)})
    rows.sort(key=lambda r: -r["share"])
    for r in rows[:SIM_TOP]:
        wins = 0
        for _ in range(SIMS):
            res = palbattle.simulate(game, [dict(r["fighter"], side=0),
                                            dict(bf, side=1)], rng)
            wins += res["winner"] == 0
        r["win"] = round(100 * wins / SIMS)
    team = rows[:5]
    return {"boss": card, "rows": rows,
            "team": {"share": sum(r["share"] for r in team),
                     "ids": [r["pal"].get("id") for r in team]}}
