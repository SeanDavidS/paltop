#!/usr/bin/env python3
"""
paltraits.py - the traitpedia: every passive trait a Pal can have.

For each trait, from the game's data tables (palpak.read_game_data) and the
save:

    tier        the game's rank: 1-3 the usual good ones, 4 the rainbow
                ones, 5 the World Tree ones, below 0 the bad ones
    effects     what it does, in numbers, and the game's description
    where       random     can appear on any Pal (caught or bred)
                species    the species that are always born with it (Legend
                           on Frostallion, ...)
                surgery    the Pal Surgery Table: its price in gold, and the
                           implant it needs - a Disposable Implant is used up,
                           an Implant is a key item that is kept - with where
                           the implant comes from when the data says so (the
                           Ancient Relic Recycler, with the chance per roll)
                breeding   a parent passes its traits on to the child
    on server   how many Pals each player has with it

Traits that are neither random, nor implantable, nor born with a species,
nor on any Pal on the server (partner-skill and internal passives) are left
out.
"""

from __future__ import annotations

import palpals

TIERS = {5: "World Tree", 4: "rainbow", 3: "III", 2: "II", 1: "I",
         -1: "bad I", -2: "bad II", -3: "bad III"}

# effect type -> what it is good for, in the order the page shows them
USE = (
    ("ShotAttack", "attack"), ("MeleeAttack", "attack"),
    ("Defense", "defense"), ("MaxHP", "HP"), ("LifeSteal", "life steal"),
    ("AutoHPRegeneRate", "HP regeneration"), ("CraftSpeed", "work"),
    ("MoveSpeed", "speed"), ("SwimSpeed", "speed"),
    ("RideJumpCount", "mount"), ("Stamina", "mount"),
    ("FullStomatch_Decrease", "hunger"), ("Sanity_Decrease", "sanity"),
    ("BreedSpeed", "breeding"), ("PalEggHatchingSpeed", "breeding"),
    ("ElementBoost", "element damage"), ("ElementResist", "element resist"),
    ("ResistAdditionalEffect", "status resist"),
)


def uses(game, pid):
    """What a trait helps with: ["attack", "defense", ...]."""
    out = []
    for etype, value in palpals.passive(game, pid)["effects"]:
        for key, label in USE:
            if etype.startswith(key) and label not in out:
                out.append(label)
    return out


def born_with(game):
    """{trait id: [species ids]} for the traits a species always has."""
    out = {}
    for sp, info in (game.get("species") or {}).items():
        if sp[:5].upper() == "BOSS_" or sp.startswith(("GYM_", "RAID_")) \
                or not info.get("is_pal"):
            continue
        for pid in info.get("passives") or []:
            out.setdefault(pid, []).append(sp)
    return out


def entries(game, pals, players, names=None, me=None):
    """[trait] for the page, best tier first. `pals` are the save's Pals,
    `players` maps owner id to name, `me` is the viewer's player id."""
    passives = game.get("passives") or {}
    born = born_with(game)
    owners = {}                       # trait -> {owner id: count}
    for p in pals:
        o = p.get("owner")
        if not o or o not in players:
            continue
        for pid in p.get("passives") or []:
            c = owners.setdefault(pid, {})
            c[o] = c.get(o, 0) + 1
    out = []
    for pid, ps in passives.items():
        if not (ps.get("roll") or ps.get("surgery") or pid in born
                or pid in owners):
            continue
        sg = ps.get("surgery")
        rank = ps.get("rank") or 0
        species = sorted({palpals.base_species(sp) for sp in born.get(pid, [])})
        have = owners.get(pid, {})
        out.append({
            "id": pid, "name": ps.get("name") or pid, "tier": rank,
            "tier_label": TIERS.get(rank, str(rank)),
            "effects": palpals.effect_texts(game, pid),
            "desc": palpals.passive_description(game, pid),
            "uses": uses(game, pid),
            "random": bool(ps.get("roll")),
            "lucky": pid == "Rare",
            "species": [palpals.pal_display(sp, names)[1] for sp in species],
            "surgery": {
                "price": sg.get("price") or 0,
                "item": sg.get("item"),
                "consumable": bool(sg.get("consumable")),
                "from": [{"where": f["where"],
                          "chance": f["chance"][1]} for f in
                         sg.get("from") or []],
            } if sg else None,
            "owners": sorted([[players[o], n] for o, n in have.items()],
                             key=lambda t: -t[1]),
            "mine": have.get(me, 0) if me else 0,
            "total": sum(have.values()),
        })
    out.sort(key=lambda t: (-t["tier"], t["name"].lower()))
    return out
