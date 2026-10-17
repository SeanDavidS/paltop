#!/usr/bin/env python3
"""
palpedia.py - every Pal in the game, for the web page's Palpedia.

One entry per Paldeck number (the game's ZukanIndex plus its suffix, so
Foxcicle and a B variant are separate entries), built from the game data
palpak reads out of the pak:

    stats      base HP, attack, defense and work speed (DT_PalMonsterParameter)
    work       work suitabilities and their levels
    partner    partner skill name and text (DT_PalFirstActivatedInfoText)
    skills     what it learns and when (DT_WazaMasterLevel), with each
               skill's element, power, cooldown and status build-up
    breeding   its breeding power, and every parent pair that gives it
               (the game's rule, special pairs from DT_PalCombiUnique first)
    ranks      where it places among all species for each team builder goal,
               at level 60 with perfect IVs and no passives

and from the save: who on the server has found it - the in-game Paldeck and
capture counts in Players/<guid>.sav - and who owns one now. With spoilers
off, Pals nobody on the server has found are shown as "???".
"""

from __future__ import annotations

import bisect

import palpals
import palteam

RANK_LEVEL = 60


def _display(names, sid):
    return (names or {}).get(sid) or (names or {}).get(sid.lower()) or sid


def entries(game, names=None):
    """[{id, no, suffix, ids, name, rarity, hp, atk, def, craft, work,
    combi, male}] in Paldeck order; `ids` are every species id that is this
    entry (variants from oil rigs and towers count as the same Pal)."""
    groups = {}
    for sid, v in (game.get("species") or {}).items():
        if not v.get("is_pal") or sid != palpals.base_species(sid):
            continue
        if (v.get("zukan") or -1) < 0:
            continue
        groups.setdefault((v["zukan"], v.get("suffix") or ""), []).append(sid)
    out = []
    for (no, suffix), ids in sorted(groups.items()):
        sid = min(ids, key=lambda s: (len(s), s))       # the plain one
        v = game["species"][sid]
        out.append({"id": sid, "no": no, "suffix": suffix, "ids": sorted(ids),
                    "name": _display(names, sid), "rarity": v.get("rarity"),
                    "hp": v.get("hp") or 0, "atk": v.get("atk") or 0,
                    "def": v.get("def") or 0, "craft": v.get("craft") or 0,
                    "work": dict(v.get("work") or {}),
                    "combi": v.get("combi"), "male": v.get("male")})
    return out


def entry_of(game, entries_):
    """{any species id (also BOSS_ and variants), lower case: entry id}.
    The save's records do not always spell ids like the game data does
    (Sheepball, SheepBall)."""
    out = {}
    for e in entries_:
        for sid in e["ids"]:
            out[sid.lower()] = e["id"]
            out["boss_" + sid.lower()] = e["id"]
    return out


def found(saves, of):
    """Who on the server has found each entry: {entry id: {"players":
    {player id: {"caught": n, "deck": bool, "owned": n}}, "owned": n}}.
    `of` maps species ids to entry ids (entry_of)."""
    out = {}

    def rec(sid, pid):
        eid = of.get(sid.lower()) or of.get(palpals.base_species(sid).lower())
        if not eid:
            return None
        e = out.setdefault(eid, {"players": {}, "owned": 0})
        if pid is None:
            return e
        return e["players"].setdefault(pid, {"caught": 0, "deck": False,
                                             "owned": 0})
    for pid, c in (saves.containers if saves else {}).items():
        for sid, n in (c.get("captured") or {}).items():
            r = rec(sid, pid)
            if r is not None:
                r["caught"] += n
        for sid in c.get("paldeck") or []:
            r = rec(sid, pid)
            if r is not None:
                r["deck"] = True
    for p in (saves.pals if saves else []):
        e = rec(p["species"], None)
        if e is None:
            continue
        e["owned"] += 1
        if p.get("owner"):
            rec(p["species"], p["owner"])["owned"] += 1
    return out


def find_rows(game, saves, of, now):
    """(player id, entry id, time, how) for everything the save says each
    player has found: every Pal they own, at the time they caught it
    ("owned"), and every Pal in their in-game Paldeck, at `now` ("seen" -
    the Paldeck keeps no dates, so that is when paltop first saw it).
    Store.record_finds keeps the earliest per player and Pal."""
    rows = []
    for p in (saves.pals if saves else []):
        owner, caught = p.get("owner"), p.get("caught")
        eid = of.get(p["species"].lower()) or \
            of.get(palpals.base_species(p["species"]).lower())
        if owner and caught and eid:
            rows.append((owner, eid, caught, "owned"))
    for pid, c in (saves.containers if saves else {}).items():
        for sid in list(c.get("paldeck") or []) + list(c.get("captured") or {}):
            eid = of.get(sid.lower())
            if eid:
                rows.append((pid, eid, now, "seen"))
    return rows


def first_finds(rows, since):
    """{entry id: (first time, [player ids], how sure)} from the finds
    table. How sure: "seen" when paltop saw it happen (after `since`, when it
    started keeping track), "estimated" before that (the oldest Pal of that
    kind still owned: the Paldeck keeps no dates, and a Pal released since
    leaves a later one), None when only the Paldeck says so."""
    out = {}
    for f in rows:
        known = "seen" if f["first_ts"] > since + 3600 else \
            "estimated" if f["how"] == "owned" else None
        cur = out.get(f["species"])
        if cur is None or f["first_ts"] < cur[0]:
            out[f["species"]] = (f["first_ts"], [f["pid"]], known)
        elif f["first_ts"] == cur[0]:
            cur[1].append(f["pid"])
    return out


def race_counts(found_map, firsts):
    """{player id: {found, firsts, unique, caught}} from found() and
    first_finds()."""
    out = {}

    def rec(pid):
        return out.setdefault(pid, {"found": 0, "firsts": 0, "unique": 0,
                                    "caught": 0})
    for eid, f in found_map.items():
        who = [pid for pid, r in (f.get("players") or {}).items()
               if r.get("deck") or r.get("caught") or r.get("owned")]
        for pid in who:
            rec(pid)["found"] += 1
            rec(pid)["caught"] += f["players"][pid].get("caught") or 0
        if len(who) == 1:
            rec(who[0])["unique"] += 1
    for eid, (ts, pids, known) in firsts.items():
        if known:
            for pid in pids:
                rec(pid)["firsts"] += 1
    return out


def recipes(game):
    """{child species: [(parent a, parent b, special)]} for every pair of
    breedable species, by the game's rule: a special pair from
    DT_PalCombiUnique, else the species whose breeding power is closest to
    (a + b + 1) // 2 (ties to the higher one)."""
    br = palpals.Breeder(game)
    table = game.get("species") or {}
    pool = br.pool                                   # (combi, prio, sid)
    ranks = [c for c, _, _ in pool]
    tribe_of = {sid: (table.get(sid) or {}).get("tribe") for _, _, sid in pool}
    unique_child = {}
    tribes = {}
    for sid, info in table.items():
        if sid == palpals.base_species(sid) and info.get("tribe"):
            tribes.setdefault(info["tribe"], sid)
    for key, rows in br.unique.items():
        unique_child[key] = rows[0][4]
    cache = {}

    def nearest(power):
        hit = cache.get(power)
        if hit is None:
            i = bisect.bisect_left(ranks, power)
            cands = [pool[j] for j in (i - 1, i) if 0 <= j < len(pool)]
            best = min(cands, key=lambda t: (abs(t[0] - power), -t[0]))
            hit = cache[power] = best[2]
        return hit
    out = {}
    sids = [sid for _, _, sid in pool]
    for i, a in enumerate(sids):
        for b in sids[i:]:
            if a == b:
                child, special = a, False
            else:
                key = frozenset((tribe_of[a], tribe_of[b]))
                if key in unique_child:
                    child, special = unique_child[key], True
                else:
                    child = nearest((table[a]["combi"] + table[b]["combi"]
                                     + 1) // 2)
                    special = False
            out.setdefault(child, []).append((a, b, special))
    # special children whose parents are not both in the normal pool
    for ta, _ga, tb, _gb, child in game.get("combos") or []:
        a, b = tribes.get(ta, ta), tribes.get(tb, tb)
        rows = out.setdefault(child, [])
        if not any(set((x, y)) == {a, b} for x, y, _ in rows):
            rows.append((a, b, True))
    return out


def goal_ranks(game, els):
    """{goal: {species: (place, score as % of the best, score)}} over all
    species, at RANK_LEVEL with perfect IVs and no passives."""
    fake = []
    for sid, info in (game.get("species") or {}).items():
        if not info.get("is_pal") or sid != palpals.base_species(sid):
            continue
        if (info.get("zukan") or -1) < 0:
            continue
        fake.append({"species": sid, "level": RANK_LEVEL, "hp_iv": 100,
                     "atk_iv": 100, "def_iv": 100, "passives": [],
                     "skills": [], "gender": "", "rank": 1,
                     "learned": [w for lv, w in palteam.learnset(game, sid)
                                 if lv <= RANK_LEVEL]})
    out = {}
    for goal, _label, _kind in palteam.GOALS:
        g = "fighter" if goal.startswith("vs_") else goal
        ranked = palteam.ranked(game, fake, g, els)
        top = ranked[0][1]["score"] if ranked else 1.0
        out[goal] = {p["species"]: (i + 1, round(100 * ev["score"] / top),
                                    ev["score"])
                     for i, (p, ev) in enumerate(ranked)}
    return out


def learn_rows(game, sid):
    """[{level, name, element, power, cooldown, effects: [[status, value]]}]
    the species learns, in order."""
    skills = game.get("skills") or {}
    status = game.get("status") or {}
    out = []
    for lv, wid in palteam.learnset(game, sid):
        sk = skills.get(wid)
        if not sk:
            continue
        out.append({"level": lv, "name": sk["name"], "element": sk["element"],
                    "power": sk.get("power") or 0,
                    "cooldown": sk.get("cooldown") or 0,
                    "effects": [[(status.get(t) or {}).get("name") or t, v]
                                for t, v in sk.get("effects") or []]})
    return out


def matches(game, e, text, els, skills=False):
    """Search over the name, number, elements, work and partner skill, and
    with `skills` the active skills it learns and the statuses they apply."""
    text = (text or "").strip().lower()
    if not text:
        return True
    hay = [e["name"], e["id"], "%d%s" % (e["no"], e["suffix"]),
           (game.get("partner") or {}).get(e["id"]) or ""]
    hay += [palpals.ELEMENT_NAMES.get(x, x)
            for x in palpals.elements_of(e["id"], els)]
    hay += [palpals.WORK_NAMES.get(k, k) for k in e["work"]]
    if skills:
        hay += palteam.skill_words(game, [w for _, w in
                                          palteam.learnset(game, e["id"])])
    # the partner skill's own text: "... damage to Burned enemies"
    hay.append(palpals.partner(game, e["id"])["text"] or "")
    return any(text in h.lower() for h in hay)
