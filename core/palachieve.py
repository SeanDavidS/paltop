#!/usr/bin/env python3
"""
palachieve.py - achievements for the players on the server.

Each achievement has three tiers - bronze, silver and gold - at thresholds of
one number per player, and a tier is worth 1, 2 or 3 points. The numbers
come from what paltop already reads:

    the player's record   Players/<guid>.sav RecordData: towers and field
                          bosses beaten, raids, catches, effigies, fast travel
                          points, areas, dungeons, oil rigs, fish, camps,
                          treasure, arena wins, ...
    their Pals            the save: lucky and alpha Pals, perfect IVs, fully
                          condensed Pals, Pals with four top passives
    paltop's own history  level, time played, and the Paldeck race (Pals
                          found first, Pals only they have)

When a tier is reached is noted in the database (table achievements) the
first time paltop sees it; tiers a player already had when paltop started
keeping track have no real date. Server firsts - the first player to reach a
level - come from the level history.
"""

from __future__ import annotations

import palpals

TIER_NAMES = ("bronze", "silver", "gold")
FIRST_LEVELS = (20, 30, 40, 50, 60, 70, 80)

# key, title, icon, what it counts, (bronze, silver, gold), where the number
# comes from: ("rec", name, 0|1) is the record's number, or for a map the
# entries set (0) or the sum of its values (1); ("ctx", name) is computed
# below from the Pals and paltop's history
ACHIEVEMENTS = (
    ("collector", "Pal Collector", "\U0001F4D6", "Pals in your Paldeck", (50, 150, 250), ("rec", "PaldeckUnlockFlag", 0)),
    ("catcher", "Catch 'Em All", "\U0001F3AF", "Pals caught", (250, 1000, 2500), ("rec", "PalCaptureCount", 1)),
    ("towers", "Tower Breaker", "\U0001F3F0", "tower bosses beaten", (1, 5, 10), ("rec", "TowerBossDefeatFlag", 0)),
    ("alphas", "Alpha Slayer", "⚔️", "field bosses beaten", (10, 40, 90), ("rec", "NormalBossDefeatFlag", 0)),
    ("raids", "Raid Leader", "\U0001F525", "raid bosses beaten", (1, 5, 15), ("rec", "RaidBossDefeatCount", 1)),
    ("predators", "Predator Hunter", "\U0001F441️", "predator Pals beaten", (5, 15, 25), ("rec", "PredatorDefeatCount", 0)),
    ("effigies", "Effigy Hunter", "\U0001F5FF", "Lifmunk effigies found", (10, 30, 60), ("rec", "RelicObtainForInstanceFlag", 0)),
    ("travel", "Globetrotter", "\U0001F9ED", "fast travel points unlocked", (20, 80, 150), ("rec", "FastTravelPointUnlockFlag", 0)),
    ("areas", "Cartographer", "\U0001F5FA️", "areas discovered", (20, 60, 100), ("rec", "FindAreaFlagMap", 0)),
    ("dungeons", "Dungeon Diver", "\U0001F573️", "dungeons and sealed realms cleared", (5, 20, 40), ("ctx", "dungeons")),
    ("oilrigs", "Oil Rig Raider", "\U0001F6E2️", "oil rigs cleared", (1, 3, 10), ("rec", "OilrigClearCount", 0)),
    ("fishing", "Angler", "\U0001F3A3", "fish caught", (10, 50, 150), ("rec", "FishingCountMap", 1)),
    ("camps", "Camp Conqueror", "\U0001F6A9", "enemy camps conquered", (1, 5, 15), ("rec", "CampConqueredCount", 0)),
    ("treasure", "Treasure Hunter", "\U0001F4B0", "treasure chests found", (3, 10, 30), ("rec", "FoundTreasureCount", 0)),
    ("arena", "Arena Champion", "\U0001F3C6", "arena challenges won", (1, 3, 6), ("rec", "ArenaSoloClearCount", 0)),
    ("condenser", "Condenser Pro", "✨", "Pals condensed", (10, 50, 150), ("rec", "PalRankupCount", 1)),
    ("level", "Veteran", "⭐", "player level", (25, 50, 75), ("ctx", "level")),
    ("hours", "Dedicated", "⏳", "hours played (as paltop has seen)", (10, 50, 150), ("ctx", "hours")),
    ("lucky", "Lucky Star", "\U0001F31F", "lucky Pals owned", (1, 10, 30), ("ctx", "lucky")),
    ("alphakeeper", "Alpha Keeper", "\U0001F451", "alpha Pals owned", (5, 25, 75), ("ctx", "alpha")),
    ("perfect", "Perfectionist", "\U0001F48E", "Pals with 100/100/100 IVs", (1, 5, 15), ("ctx", "perfect")),
    ("condensed", "Fully Condensed", "\U0001F506", "Pals at four condenser stars", (1, 10, 40), ("ctx", "condensed")),
    ("passives", "Gold Standard", "\U0001F947", "Pals with four top-rank passives", (1, 5, 15), ("ctx", "passives")),
    ("firsts", "Trailblazer", "\U0001F6A9", "Pals found first on the server", (10, 50, 100), ("ctx", "firsts")),
    ("unique", "One of a Kind", "\U0001F984", "Pals nobody else on the server has", (1, 5, 15), ("ctx", "unique")),
)


def _pal_numbers(game, pals):
    """The numbers achievements take from a player's own Pals."""
    out = {"lucky": 0, "alpha": 0, "perfect": 0, "condensed": 0, "passives": 0}
    for p in pals:
        out["lucky"] += bool(p.get("lucky"))
        out["alpha"] += p["species"][:5].upper() == "BOSS_"
        out["perfect"] += (p.get("hp_iv"), p.get("atk_iv"), p.get("def_iv")) == \
            (100, 100, 100)
        out["condensed"] += (p.get("rank") or 1) >= 5
        ranks = [palpals.passive(game, q).get("rank") or 0
                 for q in p.get("passives") or []]
        out["passives"] += len(ranks) == 4 and min(ranks) >= 3
    return out


def tier_of(value, thresholds):
    """0 (none) to 3 (gold)."""
    return sum(1 for t in thresholds if value >= t)


def compute(game, saves, extra):
    """{player id: [{key, title, icon, desc, value, tier, next, tiers}]} for
    everyone in the save. `extra` maps player id to {level, hours, firsts,
    unique} from paltop's own history."""
    out = {}
    owned = {}
    for p in (saves.pals if saves else []):
        if p.get("owner"):
            owned.setdefault(p["owner"], []).append(p)
    for pid, c in (saves.containers if saves else {}).items():
        rec = c.get("records") or {}
        ctx = dict(_pal_numbers(game, owned.get(pid, [])), **(extra.get(pid) or {}))
        ctx["dungeons"] = (rec.get("NormalDungeonClearCount") or 0) + \
            (rec.get("FixedDungeonClearCount") or 0)
        rows = []
        for key, title, icon, desc, tiers, src in ACHIEVEMENTS:
            if src[0] == "rec":
                v = rec.get(src[1]) or 0
                if isinstance(v, list):
                    v = v[src[2]]
            else:
                v = ctx.get(src[1]) or 0
            v = int(v)
            t = tier_of(v, tiers)
            rows.append({"key": key, "title": title, "icon": icon, "desc": desc,
                         "value": v, "tier": t, "tiers": list(tiers),
                         "next": tiers[t] if t < 3 else None})
        out[pid] = rows
    return out


def points(rows):
    return sum(r["tier"] for r in rows)
