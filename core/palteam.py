#!/usr/bin/env python3
"""
palteam.py - which Pals are best for a job, and a team builder.

Every Pal is scored for a goal (attack, defense, a fight against one element,
a kind of base work, ...) from four things:

    species   base HP / attack / defense / work speed and work suitability,
              from the game's DT_PalMonsterParameter
    IVs       0-100 each; the commonly cited formula gives up to +30% of the
              species' contribution to that stat
    level     the Pal's own level, or - in "at your level" mode - the owner's,
              to compare what each Pal would be once levelled
    traits    the passives' own effects from DT_PassiveSkill_Main: +% attack,
              defense, work speed, element damage and resistance, extra work
              levels, and the negative ones

Status goals (Burn, Soak, Ivy-Covered, ...) score how fast a Pal builds up
that status on its targets: each active skill's build-up (DT_WazaDataTable
EffectType/EffectValue) per second of cooldown, over the skills it has
equipped or learned, weighted by how well it fights. Where the game says a
status makes foes take more damage from an element (Soaked foes from
Electric, ...), Pals with strong skills of that element count too, and a
partner skill that raises everyone's damage to afflicted foes is worth a
place in the team.

The stat formulas are estimates (the game does not publish them); they rank
Pals against each other well, but the numbers are not the game's exact ones.
Condenser stars and soul upgrades are shown but not counted, since their exact
effect is not in the data paltop reads.
"""

from __future__ import annotations

import math

import palpals

TEAM_SIZE = 5

# attacker element -> the elements it deals double damage to
STRONG = {
    "Fire": ("Leaf", "Ice"), "Water": ("Fire",), "Electricity": ("Water",),
    "Earth": ("Electricity",), "Leaf": ("Earth",), "Ice": ("Dragon",),
    "Dragon": ("Dark",), "Dark": ("Normal",),
}
ELEMENTS = ("Normal", "Fire", "Water", "Leaf", "Electricity", "Ice", "Earth",
            "Dark", "Dragon")

WORK_EFFECTS = {"CraftSpeed"}
BREED_EFFECTS = {"BreedSpeed", "BreedSpeed_InBaseCamp", "PalEggHatchingSpeed"}
MAX_PASSIVES = 4

# species that are awkward to use in a team, by the game's species id. The
# game data has no "where can it be summoned" field, so this list is kept by
# hand; add a species here to keep the team builder from suggesting it.
HARD_TO_SUMMON = {
    "KingWhale": "Panthalus is huge and can only be summoned in a few places",
}


# status effects a skill can build up, by the game's id, in the builder's
# order; the labels are the game's names (ADDITIONAL_EFFECT_*) plus a hint
STATUSES = ("Burn", "Wetness", "Electrical", "Freeze", "IvyCling", "Muddy",
            "Darkness", "Poison", "Stun")
STATUS_LABEL = {"Burn": "Burn", "Wetness": "Soak (wet)",
                "Electrical": "Electrify (shock)", "Freeze": "Freeze",
                "IvyCling": "Ivy-Covered (vines)", "Muddy": "Muddy",
                "Darkness": "Blind (darkness)", "Poison": "Poison",
                "Stun": "Stun"}
# the game's help page: foes with this status take more damage from ...
STATUS_COMBO = {"Wetness": "Electricity", "IvyCling": "Fire",
                "Electrical": "Water", "Muddy": "Leaf"}
# foes with the status, as the game would say it
STATUS_FOES = {"Burn": "Burned", "Wetness": "Soaked",
               "Electrical": "Electrified", "Freeze": "Frozen",
               "IvyCling": "Ivy-Covered", "Muddy": "Muddy",
               "Darkness": "Blinded", "Poison": "Poisoned", "Stun": "Stunned"}
# ... and cannot get it at all
STATUS_IMMUNE = {"Burn": ("Fire", "Water")}
COMBO_WEIGHT = 1.2         # a combo-element skill of power 100 = build-up 1.2/s
FILLER_WEIGHT = 0.5        # Pals without the status still fill a team


def skill_words(game, skill_ids):
    """Words to search a Pal by, from its skills: their names and elements,
    and the statuses they apply in every way a player might write them
    (Blind, Blinded, darkness; Soak, Soaked, wet; ...)."""
    skills = game.get("skills") or {}
    status = game.get("status") or {}
    out = []
    for wid in skill_ids:
        sk = skills.get(wid)
        if not sk:
            continue
        out.append(sk["name"])
        for t, _v in sk.get("effects") or []:
            out += [t, (status.get(t) or {}).get("name") or "",
                    STATUS_LABEL.get(t, ""), STATUS_FOES.get(t, "")]
    return out


def status_of(goal):
    return goal[len("status_"):] if goal.startswith("status_") else None


def summon_note(sid):
    """Why a species is hard to use in a team, or None."""
    key = palpals.base_species(sid)
    for suffix in ("_otomo",):
        if key.endswith(suffix):
            key = key[:-len(suffix)]
    return HARD_TO_SUMMON.get(key)


def usable(ranking, include_hard=False):
    """The ranking without hard-to-summon Pals (unless asked for), and the
    names of the species that were left out."""
    if include_hard:
        return ranking, []
    out, left = [], []
    for p, ev in ranking:
        note = summon_note(p["species"])
        if note:
            if note not in left:
                left.append(note)
        else:
            out.append((p, ev))
    return out, left


def goals():
    """[(id, label, kind)] in the order the builder cycles through."""
    out = [("attack", "strongest attack", "combat"),
           ("defense", "best defense", "combat"),
           ("tank", "tank: HP x defense", "combat"),
           ("fighter", "all-round fighter", "combat"),
           ("synergy", "most synergy (buffs between Pals)", "combat")]
    for el in ELEMENTS:
        out.append(("vs_" + el, "fight vs %s" % palpals.ELEMENT_NAMES[el],
                    "combat"))
    for st in STATUSES:
        out.append(("status_" + st, "status: %s" % STATUS_LABEL[st],
                    "combat"))
    out.append(("work", "fastest worker", "work"))
    out.append(("breed", "breeding farm (egg speed)", "work"))
    for k in palpals.WORK_NAMES:
        out.append(("work_" + k, "base: %s" % palpals.WORK_NAMES[k], "work"))
    return out


GOALS = goals()
GOAL_LABEL = {g: label for g, label, _ in GOALS}
SORT_GOALS = ("attack", "defense", "tank", "fighter", "work")


def multiplier(attacker, defender_elements):
    """Damage multiplier of an attacker element against a Pal's elements."""
    m = 1.0
    for d in defender_elements:
        if d in STRONG.get(attacker, ()):
            m *= 2.0
        elif attacker in STRONG.get(d, ()):
            m *= 0.5
    return m


def passive_totals(game, pal):
    """{effect type: summed value} over the Pal's passives."""
    out = {}
    for pid in pal.get("passives") or []:
        for etype, value in palpals.passive(game, pid)["effects"]:
            out[etype] = out.get(etype, 0.0) + value
    return out


def elements(pal, els):
    for key in (pal["species"], palpals.base_species(pal["species"])):
        hit = els.get(key)
        if hit:
            return [e for e in hit if e and e != "None"]
    return ["Normal"]


# what makes a caught Pal stronger besides its level, IVs and traits:
#   condenser stars   each star (Rank 2-5) +5% HP, attack and defense
#   souls             each level at the Statue of Power +3% of that stat, 20
#                     levels, so at most +60% HP, attack and defense
#                     (Rank_HP, Rank_Attack, Rank_Defence, Rank_CraftSpeed)
#   awakening         all stats x AwakeningStatusMultiply (1.1, the game's
#                     BP_PalGameSetting)
# The star and soul numbers are not in the data tables (they are in the game's
# code); these are the values the game shows.
STAR_BONUS = 0.05
SOUL_BONUS = 0.03
SOUL_MAX = 0.60
AWAKENING = 1.1


def growth(pal):
    """{hp, atk, def, craft} multipliers from stars, souls and awakening."""
    stars = max(0, (pal.get("rank") or 1) - 1)
    soul = pal.get("soul") or {}
    awake = AWAKENING if pal.get("awakened") else 1.0
    star = 1 + STAR_BONUS * stars

    def souls(key):
        return 1 + min(SOUL_MAX, SOUL_BONUS * (soul.get(key) or 0))
    return {"hp": star * souls("HP") * awake,
            "atk": star * souls("Attack") * awake,
            "def": star * souls("Defence") * awake,
            "craft": souls("CraftSpeed") * awake}


def stats(game, pal, level=None):
    """Estimated HP, attack, defense and work speed: level and IVs, then the
    traits' %, condenser stars, souls and awakening (growth)."""
    info = palpals.species_info(game, pal["species"])
    lv = level or pal["level"]
    t = passive_totals(game, pal)
    g = growth(pal)

    def iv(v):
        return 1 + 0.3 * v / 100.0
    hp = 500 + 5 * lv + (info.get("hp") or 0) * 0.5 * lv * iv(pal["hp_iv"])
    atk = 100 + (info.get("atk") or 0) * 0.075 * lv * iv(pal["atk_iv"])
    dfn = 50 + (info.get("def") or 0) * 0.075 * lv * iv(pal["def_iv"])
    craft = info.get("craft") or 100
    return {
        "level": lv,
        "hp": hp * (1 + t.get("MaxHP", 0) / 100.0) * g["hp"],
        "atk": atk * (1 + t.get("ShotAttack", 0) / 100.0) * g["atk"],
        "def": dfn * (1 + t.get("Defense", 0) / 100.0) * g["def"],
        "craft": craft * (1 + t.get("CraftSpeed", 0) / 100.0) * g["craft"],
        "growth": g,
        "atk_pct": t.get("ShotAttack", 0), "def_pct": t.get("Defense", 0),
        "craft_pct": t.get("CraftSpeed", 0), "hp_pct": t.get("MaxHP", 0),
        "totals": t, "info": info,
    }


def _trait_notes(game, pal, relevant):
    """(good, bad) passive names for the effect types a goal cares about."""
    good, bad = [], []
    for pid in pal.get("passives") or []:
        ps = palpals.passive(game, pid)
        vals = [v for t, v in ps["effects"] if relevant(t)]
        if not vals:
            continue
        # SAN drain and hunger are good when negative
        sign = -1 if any(t in ("Sanity_Decrease", "FullStomatch_Decrease")
                         for t, _ in ps["effects"] if relevant(t)) else 1
        (good if sum(vals) * sign > 0 else bad).append(ps["name"])
    return good, bad


def status_skills(game, pal, status, level=None):
    """The Pal's skills that build up `status`, best first:
    [{id, name, element, value, cooldown, rate, equipped}], counting the
    skills it has equipped or learned (and, levelled up, what it learns by
    then), at most three since that is all a Pal can equip."""
    skills = game.get("skills") or {}
    have = list(pal.get("skills") or [])
    known = have + [w for w in pal.get("learned") or [] if w not in have]
    if level and level > pal.get("level", 0):
        for lv, w in learnset(game, pal["species"]):
            if lv <= level and w not in known:
                known.append(w)
    out = []
    for wid in known:
        sk = skills.get(wid)
        if not sk:
            continue
        value = sum(v for t, v in sk.get("effects") or [] if t == status)
        if value <= 0:
            continue
        cd = max(1.5, sk.get("cooldown") or 0)
        out.append({"id": wid, "name": sk["name"], "element": sk["element"],
                    "value": value, "cooldown": sk.get("cooldown") or 0,
                    "rate": value / cd, "equipped": wid in have})
    out.sort(key=lambda x: -x["rate"])
    return out[:3]


_learn_lower = {}


def learnset(game, species):
    """[(level, skill id)] the species learns, from DT_WazaMasterLevel (whose
    ids are not always spelt like the species table's: LazyCatFish and
    LazyCatfish, so any case matches)."""
    learn = game.get("learn") or {}
    lower = _learn_lower.get(id(learn))
    if lower is None:
        lower = _learn_lower[id(learn)] = {k.lower(): v for k, v in learn.items()}
    for key in (species, palpals.base_species(species)):
        rows = learn.get(key) or lower.get(key.lower())
        if rows:
            return [tuple(x) for x in rows]
    return []


def status_partner(game, pal, status):
    """What the Pal's partner skill does for a status at its condenser rank:
    {"damage": % more damage to afflicted foes for the party,
     "player": build-up the player's attacks add}."""
    pp = game.get("partner_params") or {}
    params = pp.get(pal["species"]) or \
        pp.get(palpals.base_species(pal["species"])) or {}
    ranks = params.get("passives") or []
    if not ranks:
        return {}
    ids = ranks[min(len(ranks), max(1, pal.get("rank") or 1)) - 1]
    out = {}
    for pid in ids:
        ps = (game.get("passives") or {}).get(pid) or {}
        for e in list(ps.get("effects") or []) + list(ps.get("other") or []):
            etype, value = e[0], e[1]
            if etype == "DamageRateIfDefender_" + status:
                out["damage"] = max(out.get("damage", 0), value)
            elif etype == "AdditionalEffect_" + status:
                out["player"] = max(out.get("player", 0), value)
    return out


def _status_eval(game, pal, status, st, level):
    """(score, why, relevant effect test) for a status goal."""
    names = STATUS_LABEL[status].split(" (")[0]
    # a status is only worth as much as the Pal applying it survives and
    # hits: the fighter score, a little flattened
    fight = 30.0 * (st["atk"] * math.sqrt(st["hp"] * st["def"]) / 1e6) ** 0.75
    t = st["totals"]
    why, elems = [], set()
    own = status_skills(game, pal, status, level)
    rate = 0.0
    for sk in own:
        boost = 1 + t.get("ElementBoost_" + sk["element"], 0) / 100.0
        rate += sk["rate"] * boost
        elems.add(sk["element"])
        why.append("%s: %s %g every %gs%s" % (
            sk["name"], names, sk["value"], sk["cooldown"],
            "" if sk["equipped"] else " (learned - equip it)"))
    # skills it will learn later, as a hint
    if not level or level <= pal.get("level", 0):
        known = set(pal.get("skills") or []) | set(pal.get("learned") or [])
        for lv, wid in learnset(game, pal["species"]):
            sk = (game.get("skills") or {}).get(wid) or {}
            if lv > pal.get("level", 0) and wid not in known and any(
                    e == status for e, _ in sk.get("effects") or []):
                why.append("learns %s at lv %d" % (sk.get("name", wid), lv))
                break
    score = rate * fight
    combo = STATUS_COMBO.get(status)
    if combo:
        best = 0.0, None
        for wid in (pal.get("skills") or []) + (pal.get("learned") or []):
            sk = (game.get("skills") or {}).get(wid) or {}
            if sk.get("element") == combo and sk.get("power", 0) > best[0]:
                best = sk["power"], sk["name"]
        if best[1]:
            elems.add(combo)
            score += best[0] / 100.0 * COMBO_WEIGHT * fight
            why.append("%s (%s) hits %s foes harder" % (
                best[1], palpals.ELEMENT_NAMES.get(combo, combo),
                STATUS_FOES[status]))
    ps = status_partner(game, pal, status)
    if ps.get("damage"):
        why.append("partner skill: the party deals +%g%% damage to %s foes"
                   % (ps["damage"], STATUS_FOES[status]))
    if ps.get("player"):
        why.append("partner skill: your attacks add %s build-up %g"
                   % (names, ps["player"]))
    if score <= 0:
        why.insert(0, "no %s skills - only fills the team" % names)
    score += FILLER_WEIGHT * fight
    rel = lambda e: e == "ShotAttack" or any(
        e == "ElementBoost_" + x for x in elems)
    return score, why, rel


def evaluate(game, pal, goal, els, level=None):
    """{score, why, good, bad, st} for one Pal and one goal."""
    if goal == "synergy":            # one Pal alone: an all-round fighter
        goal = "fighter"
    st = stats(game, pal, level)
    t = st["totals"]
    my_el = elements(pal, els)
    why = []
    if status_of(goal):
        score, why, rel = _status_eval(game, pal, status_of(goal), st, level)
    elif goal == "attack":
        score = st["atk"]
        why.append("attack ≈ %.0f" % st["atk"])
        rel = lambda e: e in ("ShotAttack",) or e.startswith("ElementBoost")
    elif goal == "defense":
        score = st["def"]
        why.append("defense ≈ %.0f" % st["def"])
        rel = lambda e: e in ("Defense", "MaxHP") or \
            e.startswith("ElementResist")
    elif goal == "tank":
        score = math.sqrt(st["hp"] * st["def"])
        why.append("HP ≈ %.0f, defense ≈ %.0f" % (st["hp"],
                                                             st["def"]))
        rel = lambda e: e in ("Defense", "MaxHP") or \
            e.startswith("ElementResist")
    elif goal == "fighter":
        score = st["atk"] * math.sqrt(st["hp"] * st["def"]) / 1000.0
        why.append("attack ≈ %.0f, HP ≈ %.0f, defense ≈ %.0f"
                   % (st["atk"], st["hp"], st["def"]))
        rel = lambda e: e in ("ShotAttack", "Defense", "MaxHP")
    elif goal.startswith("vs_"):
        target = goal[3:]
        best, best_name = 0.0, None
        skills = game.get("skills") or {}
        for sid in pal.get("skills") or []:
            sk = skills.get(sid)
            if not sk:
                continue
            m = multiplier(sk["element"], [target])
            boost = 1 + t.get("ElementBoost_" + sk["element"], 0) / 100.0
            v = sk["power"] * m * boost
            if v > best:
                best, best_name = v, "%s (%s%s)" % (
                    sk["name"], palpals.ELEMENT_NAMES.get(sk["element"],
                                                          sk["element"]),
                    ", super effective" if m > 1 else
                    ", not effective" if m < 1 else "")
        taken = multiplier(target, my_el)
        resist = 1 - t.get("ElementResist_" + target, 0) / 100.0
        survive = math.sqrt(st["hp"] * st["def"]) / max(0.1, taken * resist)
        score = st["atk"] * best * math.sqrt(survive) / 10000.0
        why.append("best skill %s" % (best_name or "none equipped"))
        if taken > 1:
            why.append("weak to %s: takes double damage"
                       % palpals.ELEMENT_NAMES[target])
        elif taken < 1:
            why.append("resists %s" % palpals.ELEMENT_NAMES[target])
        rel = lambda e: e in ("ShotAttack", "Defense") or \
            e.endswith("_" + target) or e.startswith("ElementBoost")
    elif goal == "breed":
        pct = sum(v for e, v in t.items() if e in BREED_EFFECTS)
        score = 100.0 * (1 + pct / 100.0)
        why.append("breeding and hatching speed %+g%%" % pct)
        rel = lambda e: e in BREED_EFFECTS
    else:                                               # work goals
        kind = goal[5:] if goal.startswith("work_") else None
        work = dict(st["info"].get("work") or {})
        for e, v in t.items():
            if e.startswith("WorkSuitabilityAddRank_"):
                k = e.split("_", 1)[1]
                if k in work or kind == k:
                    work[k] = work.get(k, 0) + v
        if kind:
            level_ = work.get(kind, 0)
            score = level_ * st["craft"]
            why.append("%s level %d, work speed ≈ %.0f" % (
                palpals.WORK_NAMES[kind], level_, st["craft"]))
        else:
            score = st["craft"] * (1 + 0.1 * sum(work.values()))
            why.append("work speed ≈ %.0f, %d kinds of work" % (
                st["craft"], len(work)))
        # base life: SAN drain and hunger matter as much as speed
        san = t.get("Sanity_Decrease", 0)
        hunger = t.get("FullStomatch_Decrease", 0)
        score *= (1 - san / 200.0) * (1 - hunger / 200.0)
        rel = lambda e: e in ("CraftSpeed", "Sanity_Decrease",
                              "FullStomatch_Decrease") or \
            e.startswith("WorkSuitabilityAddRank")
    good, bad = _trait_notes(game, pal, rel)
    return {"score": score, "why": why, "good": good, "bad": bad, "st": st}


def ranked(game, pals, goal, els, level_for=None):
    """[(pal, evaluation)] best first. level_for(pal) -> level or None."""
    out = []
    for p in pals:
        info = palpals.species_info(game, p["species"])
        if not info.get("is_pal"):
            continue                    # captured humans
        ev = evaluate(game, p, goal, els, level_for(p) if level_for else None)
        if ev["score"] > 0:
            out.append((p, ev))
    out.sort(key=lambda t: -t[1]["score"])
    return out


def pal_key(p):
    """A Pal's identity: its instance id from the save, which a "what if"
    copy with other passives keeps."""
    return p.get("id") or id(p)


def is_work(goal):
    return goal.startswith("work") or goal == "breed"


SYNERGY_POOL = 30          # best Pals by score considered by the search
SYNERGY_EXTRA = 25         # plus this many with a partner skill for the team
BEAM = 24                  # partial teams kept at each step of the search


def objective(total, base, goal):
    """What the team search maximises: the team's total score with its
    synergy - and for "most synergy" the synergy counts twice, so teams
    whose Pals buff each other beat equally strong teams that do not."""
    if goal == "synergy" and base:
        return total * (total / base)
    return total


def _synergy_pool(ranking, game):
    """The candidates for the synergy search: the best individual of each
    species, the strongest first, plus the best with a team partner skill."""
    best_each, seen = [], set()
    for p, ev in ranking:
        kind = palpals.base_species(p["species"])
        if kind not in seen:
            seen.add(kind)
            best_each.append(p)
    pool = best_each[:SYNERGY_POOL]
    extra = [p for p in best_each[SYNERGY_POOL:] if _has_team_skill(game, p)]
    return pool + extra[:SYNERGY_EXTRA]


def build_synergy(ranking, els, size=TEAM_SIZE, locked=None, game=None,
                  goal="synergy"):
    """A team found by beam search rather than one Pal at a time, so Pals
    that are only good together - neither would be picked first on its own -
    are found too. Locked slots stay as they are."""
    locked = dict(locked or {})
    scores = {pal_key(p): ev["score"] for p, ev in ranking}
    fixed = [p for i, p in sorted(locked.items()) if i < size]
    pool = [p for p in _synergy_pool(ranking, game)
            if pal_key(p) not in {pal_key(q) for q in fixed}]
    free = size - len(fixed)

    def value(members):
        total, base = team_value(game, members, els, goal, scores)
        return objective(total, base, goal)
    beam = [(value(fixed) if fixed else 0.0, fixed)]
    for _ in range(max(0, free)):
        grown = {}
        for _v, members in beam:
            kinds = {palpals.base_species(p["species"]) for p in members}
            keys = {pal_key(p) for p in members}
            for c in pool:
                if pal_key(c) in keys or \
                        palpals.base_species(c["species"]) in kinds:
                    continue
                team = members + [c]
                sig = frozenset(pal_key(p) for p in team)
                if sig not in grown:
                    grown[sig] = (value(team), team)
        if not grown:
            break
        beam = sorted(grown.values(), key=lambda t: -t[0])[:BEAM]
    best = beam[0][1] if beam else fixed
    team = [None] * size
    for i, p in locked.items():
        if i < size:
            team[i] = p
    rest = sorted([p for p in best if pal_key(p) not in
                   {pal_key(q) for q in fixed}],
                  key=lambda p: -scores.get(pal_key(p), 0))
    for i in range(size):
        if team[i] is None and rest:
            team[i] = rest.pop(0)
    return team


def pairs(game, pals, els, goal, scores, names=None, limit=5):
    """Pairs that are better together than apart: [(a, b, gain, why)] with
    gain the extra score from their partner skills as a fraction of the two
    scores. Only pairs with a real link (one buffs the other) count."""
    out = []
    for i, a in enumerate(pals):
        for b in pals[i + 1:]:
            if palpals.base_species(a["species"]) == \
                    palpals.base_species(b["species"]):
                continue
            both, base = team_value(game, [a, b], els, goal, scores)
            alone = team_value(game, [a], els, goal, scores)[0] + \
                team_value(game, [b], els, goal, scores)[0]
            if base and both > alone * 1.01:
                why = _received(game, a, [a, b], els, goal, names)[1] + \
                    _received(game, b, [a, b], els, goal, names)[1]
                why = [w for w in why if not w.startswith("party heal")] \
                    or why
                out.append((a, b, (both - alone) / base, why))
    out.sort(key=lambda t: -t[2])
    seen, top = set(), []
    for a, b, g, why in out:
        sig = frozenset((palpals.base_species(a["species"]),
                         palpals.base_species(b["species"])))
        if sig not in seen:
            seen.add(sig)
            top.append((a, b, g, why))
        if len(top) >= limit:
            break
    return top


def build(ranking, goal, els, size=TEAM_SIZE, locked=None, game=None):
    """A team from a ranking: the best, one of each species, and for combat
    goals a nudge towards covering more elements. `locked` maps slot index to
    a Pal the user picked, kept in place."""
    if goal == "synergy" and game:
        return build_synergy(ranking, els, size, locked, game, goal)
    locked = locked or {}
    team = [None] * size
    used = set()
    for i, p in locked.items():
        if i < size:
            team[i] = p
            used.add(pal_key(p))
    kinds = {palpals.base_species(p["species"]) for p in team if p}
    combat = not is_work(goal)
    scores = {pal_key(p): ev["score"] for p, ev in ranking}
    for i in range(size):
        if team[i]:
            continue
        have = {e for p in team if p for e in elements(p, els)}
        best, best_v = None, -1
        for p, ev in ranking[:300]:
            if pal_key(p) in used or \
                    palpals.base_species(p["species"]) in kinds:
                continue
            v = ev["score"]
            if combat and goal in ("fighter", "attack") and \
                    not set(elements(p, els)) <= have:
                v *= 1.08               # a new element in the team
            if combat and game:         # partner skills that work together
                factor, gift, _ = synergy(game, p, [q for q in team if q],
                                          els, goal, scores)
                v = v * factor + gift
            if v > best_v:
                best, best_v = p, v
        if best is None:
            break
        team[i] = best
        used.add(pal_key(best))
        kinds.add(palpals.base_species(best["species"]))
    if status_of(goal) and game:
        team = _add_status_booster(game, team, ranking, status_of(goal),
                                   locked, scores)
    return team


# what a player can ask the team to have ("wishes"), each met by at least one
# Pal: key -> (label, group). Mounts and roles come from the partner skill's
# text (palpals.PARTNER_TAGS), elements from the species.
WISHES = {
    "fly": ("flying mount", "mount"),
    "swim": ("swimming mount", "mount"),
    "ride": ("ground mount", "mount"),
    "healer": ("heals the party", "role"),
    "carry": ("more carry weight", "role"),
    "buff_team": ("buffs the team", "role"),
    "buff_player": ("buffs the player", "role"),
}
WISHES.update({"el_" + e: (palpals.ELEMENT_NAMES[e], "element")
               for e in ELEMENTS})
WISH_TAG = {"fly": "flying", "swim": "water mount", "ride": "mount",
            "healer": "healer", "carry": "carry weight",
            "buff_team": "buffs team", "buff_player": "buffs player"}


def wish_met(game, pal, els, wish):
    """Does this Pal meet the wish?"""
    if wish.startswith("el_"):
        return wish[3:] in elements(pal, els)
    tag = WISH_TAG.get(wish)
    if not tag or not game:
        return False
    return tag in palpals.partner(game, pal["species"],
                                  pal.get("rank") or 1)["tags"]


def build_wishes(ranking, goal, els, wishes, size=TEAM_SIZE, locked=None,
                 game=None):
    """A team that meets the wishes, then the best for the goal. The Pals
    already there (locked) count first; each open wish is then met by the
    Pal that meets the most of the open ones, the better for the goal among
    equals, so as few slots as possible go to wishes. The other slots are
    filled the usual way (build). Returns (team, [wishes nobody can meet])."""
    wishes = [w for w in wishes if w in WISHES]
    team = [None] * size
    for i, p in (locked or {}).items():
        if i < size:
            team[i] = p
    top = max((ev["score"] for _, ev in ranking), default=1.0) or 1.0
    open_ = [w for w in wishes
             if not any(p and wish_met(game, p, els, w) for p in team)]
    impossible = [w for w in open_
                  if not any(wish_met(game, p, els, w) for p, _ in ranking)]
    open_ = [w for w in open_ if w not in impossible]
    while open_ and None in team:
        keys = {pal_key(p) for p in team if p}
        kinds = {palpals.base_species(p["species"]) for p in team if p}
        best, best_v = None, None
        for p, ev in ranking:
            if pal_key(p) in keys or \
                    palpals.base_species(p["species"]) in kinds:
                continue
            n = sum(1 for w in open_ if wish_met(game, p, els, w))
            if not n:
                continue
            v = n + ev["score"] / top          # wishes first, then score
            if best_v is None or v > best_v:
                best, best_v = p, v
        if best is None:
            break
        team[team.index(None)] = best
        open_ = [w for w in open_ if not wish_met(game, best, els, w)]
    fixed = {i: p for i, p in enumerate(team) if p}
    team = build(ranking, goal, els, size, locked=fixed, game=game)
    return team, impossible + open_


def wish_report(game, team, els, wishes):
    """[{key, label, group, by: [slot indexes]}] for the wishes asked."""
    return [{"key": w, "label": WISHES[w][0], "group": WISHES[w][1],
             "by": [i for i, p in enumerate(team)
                    if p and wish_met(game, p, els, w)]}
            for w in wishes if w in WISHES]


def _add_status_booster(game, team, ranking, status, locked, scores):
    """A partner skill that raises the whole party's damage to foes with the
    status (it does not stack): swap one in for the weakest unlocked Pal
    when the team is better for it."""
    boost = lambda p: status_partner(game, p, status).get("damage", 0)
    if any(p and boost(p) for p in team):
        return team
    total = sum(scores.get(pal_key(p), 0) for p in team if p)
    free = [i for i, p in enumerate(team) if p is not None
            and i not in (locked or {})]
    if not free or not total:
        return team
    weakest = min(free, key=lambda i: scores.get(pal_key(team[i]), 0))
    kinds = {palpals.base_species(p["species"]) for i, p in enumerate(team)
             if p and i != weakest}
    best, best_total = None, total
    for p, ev in ranking[:300]:
        pct = boost(p)
        if not pct or palpals.base_species(p["species"]) in kinds:
            continue
        new = (total - scores.get(pal_key(team[weakest]), 0)
               + ev["score"]) * (1 + pct / 100.0)
        if new > best_total:
            best, best_total = p, new
    if best is not None:
        team = list(team)
        team[weakest] = best
    return team


def status_info(game, team, status, names=None):
    """For the team builder: what the status does (the game's help text) and
    how the team uses it: {name, desc, appliers, combo, finishers, booster,
    immune}, Pals given by name."""
    info = (game.get("status") or {}).get(status) or {}
    members = [p for p in team if p]
    skills = game.get("skills") or {}
    combo = STATUS_COMBO.get(status)
    nm = lambda p: p.get("nickname") or _name(game, p["species"], names)
    finishers = [nm(p) for p in members if combo and any(
        (skills.get(w) or {}).get("element") == combo
        for w in (p.get("skills") or []) + (p.get("learned") or []))]
    booster = next(((nm(p), status_partner(game, p, status)["damage"])
                    for p in members
                    if status_partner(game, p, status).get("damage")), None)
    return {"name": info.get("name") or STATUS_LABEL[status].split(" (")[0],
            "desc": info.get("desc") or "",
            "appliers": [nm(p) for p in members
                         if status_skills(game, p, status)],
            "combo": palpals.ELEMENT_NAMES.get(combo, combo) if combo else None,
            "finishers": finishers, "booster": booster,
            "immune": [palpals.ELEMENT_NAMES.get(e, e)
                       for e in STATUS_IMMUNE.get(status, ())]}


def weaknesses(team, els):
    """{element: how many team members take double damage from it}."""
    out = {}
    for el in ELEMENTS:
        n = sum(1 for p in team if p and multiplier(el, elements(p, els)) > 1)
        if n:
            out[el] = n
    return out


def best_species(game, goal, els, level=60, limit=3, include_hard=False):
    """The strongest species in the whole game for a goal, at a given level
    with perfect IVs and no traits - what to aim for."""
    table = game.get("species") or {}
    fake = []
    for sid, info in table.items():
        if not info.get("is_pal") or sid != palpals.base_species(sid):
            continue
        if (info.get("zukan") or -1) < 0:
            continue
        if not include_hard and summon_note(sid):
            continue
        fake.append({"species": sid, "level": level, "hp_iv": 100,
                     "atk_iv": 100, "def_iv": 100, "passives": [],
                     "skills": [], "gender": "",
                     "learned": [w for lv, w in learnset(game, sid)
                                 if lv <= level]})
    if goal.startswith("vs_"):
        goal = "fighter"                # skills depend on the individual
    out = ranked(game, fake, goal, els)
    return [p["species"] for p, _ in out[:limit]]


def candidates(ranking, goal, els, team, slot, limit=15, match=None):
    """Who could go in `slot`, best first, and what each would do to the
    team: [{pal, ev, in_slot, same_species, gains, loses, weak}].

    in_slot       always None: Pals already in the team are left out, since
                  moving one to another slot changes nothing
    same_species  the slot of another Pal of the same species (None if none)
    gains/loses   elements the team would gain or lose with it in this slot
    weak          [(element, how many would be weak to it)] for 2 or more
    """
    others = [p for i, p in enumerate(team) if p is not None and i != slot]
    have = {e for p in others for e in elements(p, els)}
    now = set(have) | (set(elements(team[slot], els))
                       if slot < len(team) and team[slot] else set())
    out = []
    for p, ev in ranking:
        if len(out) >= limit:
            break
        in_slot = next((i for i, q in enumerate(team)
                        if q is not None and pal_key(q) == pal_key(p)), None)
        if in_slot is not None:
            continue          # already in the team: moving it changes nothing
        if match is not None and not match(p):
            continue
        kind = palpals.base_species(p["species"])
        same = next((i for i, q in enumerate(team) if q is not None
                     and pal_key(q) != pal_key(p) and i != slot
                     and palpals.base_species(q["species"]) == kind), None)
        # if it is already in another slot it moves here, so count it once
        rest = [q for q in others if pal_key(q) != pal_key(p)]
        after = {e for q in rest for e in elements(q, els)} | \
            set(elements(p, els))
        weak = weaknesses(rest + [p], els)
        out.append({"pal": p, "ev": ev, "in_slot": in_slot,
                    "same_species": same,
                    "gains": sorted(after - now), "loses": sorted(now - after),
                    "weak": sorted([(e, n) for e, n in weak.items() if n >= 2],
                                   key=lambda t: -t[1])})
    return out


ALONGSIDE_BONUS = 0.05     # a partner skill that buffs the Pal beside you
PAIR_BONUS = 0.10          # a partner skill that calls a named teammate


def _pct(game, pal, key="Passive1_EffectValue1"):
    vals = palpals._partner_values(
        game, palpals.species_key(game, pal["species"]) or pal["species"],
        pal.get("rank") or 1)
    return vals.get(key) or 0


TYPICAL_STACKS = 1 / 3.0   # stacking buffs are counted at a third of full
# a beside-you buff paid for in Health (Celesdir Noct: the Pal fighting beside
# you keeps losing Health for +40-80% attack). The game data has no drain
# rate, and in play it wears the Pal down all fight, so it counts as DRAIN_HP
# less HP and, for the attack goal, DRAIN_ATTACK less attack (the Pal is out
# of the fight sooner). What makes up for it is weighed for each Pal that
# gets the buff:
#   a party healer in the team      takes DRAIN_HEALER of the drain away
#   its own life steal (Vampiric,   each 1% of damage healed takes
#     Lifesteal, Immortality)       LIFESTEAL_OFFSET away, more the harder it
#                                   hits - and the buff makes it hit harder
#   its own HP regeneration         each 1% faster takes REGEN_OFFSET away
#     (Idiosyncratic, Immortality)
#   "attack/defense up below 50%    the drain keeps it there: counted at half
#     HP" traits                    their value
DRAIN_HP = 0.55
DRAIN_ATTACK = 0.30
DRAIN_HEALER = 0.55
LIFESTEAL_OFFSET = 0.08
REGEN_OFFSET = 0.003
DRAIN_OFFSET_MAX = 0.85   # it never quite pays for itself
HEAL_AS_HP = 0.25          # a party heal of X% counts as X/4 % more HP


def stat_factor(goal, atk=0.0, dfn=0.0, hp=0.0):
    """How much a goal's score grows when attack, defense and HP grow by the
    given fractions (0.1 = +10%), following the score formulas above."""
    a, d, h = 1 + atk, 1 + dfn, 1 + hp
    if goal == "attack":
        return a
    if goal == "defense":
        return d
    if goal == "tank":
        return math.sqrt(h * d)
    if goal in ("fighter", "synergy"):
        return a * math.sqrt(h * d)
    if goal.startswith("vs_"):
        return a * (h * d) ** 0.25
    if goal.startswith("status_"):           # the "fight" weight
        return (a * math.sqrt(h * d)) ** 0.75
    return 1.0


def _name(game, sid, names):
    key = palpals.base_species(palpals.species_key(game, sid) or sid)
    return (names or {}).get(key) or (names or {}).get(key.lower()) or key


def _along(game, p):
    """(fraction counted, fraction at full stacks, stats, info) for a Pal's
    'buffs the Pal fighting beside you' skill, or None."""
    part = palpals.partner(game, p["species"], p.get("rank") or 1)
    buf = part.get("alongside_buff")
    if not buf or not buf["pct"]:
        return None
    counted = max(1, round(buf["stacks"] * TYPICAL_STACKS)) \
        if buf["stacks"] > 1 else 1
    return (buf["pct"] * counted / 100.0, buf["pct"] * buf["stacks"] / 100.0,
            buf["stats"], dict(buf, counted=counted))


def drain_cost(game, pal, buff, healed=False):
    """(share of DRAIN_HP left, attack and defense the drain gives back,
    [what softens it]) for `pal` getting a Health-draining buff."""
    counted = buff[0]
    left, why = 1.0, []
    t = passive_totals(game, pal) if game and pal else {}
    if healed:
        left *= 1 - DRAIN_HEALER
        why.append("the healer")
    steal = t.get("LifeSteal", 0)
    if steal:
        hits = (1 + t.get("ShotAttack", 0) / 100.0) * (1 + counted)
        left *= 1 - min(DRAIN_OFFSET_MAX, steal * LIFESTEAL_OFFSET * hits)
        why.append("%g%% life steal" % steal)
    regen = t.get("AutoHPRegeneRate", 0)
    if regen:
        left *= 1 - min(DRAIN_OFFSET_MAX, regen * REGEN_OFFSET)
        why.append("+%g%% HP regeneration" % regen)
    left = max(1 - DRAIN_OFFSET_MAX, left)
    back_a = t.get("AttackRateHPThreshold", 0) / 200.0
    back_d = t.get("DefenseRateHPThreshold", 0) / 200.0
    if back_a or back_d:
        why.append("its low-HP trait")
    return left, back_a, back_d, why


def _along_stats(buff, goal, healed=False, pal=None, game=None):
    """(attack, defense, HP) fractions a beside-you buff is worth to `pal`,
    with the Health it drains counted against it."""
    counted, _full, stats, info = buff
    a = counted if "attack" in stats else 0.0
    d = counted if "defense" in stats else 0.0
    h = 0.0
    if info["hp_loss"]:
        left, back_a, back_d, _why = drain_cost(game, pal, buff, healed)
        h = -DRAIN_HP * left
        a += back_a
        d += back_d
        if goal == "attack":
            a = (1 + a) * (1 - DRAIN_ATTACK * left) - 1
    return a, d, h


def active_buffs(game, team, goal):
    """[(buff, Pal)] for the beside-you buffs that apply in this party: each
    different partner skill once, from the Pal with the strongest version
    (the most condenser stars)."""
    best = {}
    for q in team:
        if not q:
            continue
        b = _along(game, q)
        if not b:
            continue
        skill = palpals.partner(game, q["species"], q.get("rank") or 1)["name"]
        key = skill or palpals.base_species(q["species"])
        if key not in best or b[0] > best[key][0][0]:
            best[key] = (b, q)
    return list(best.values())


def drains(game, p):
    """Does this Pal's partner skill drain the Health of the Pal fighting
    beside you?"""
    b = _along(game, p)
    return bool(b and b[3]["hp_loss"])


def _heal(game, p):
    h = palpals.partner(game, p["species"], p.get("rank") or 1).get("heal")
    return (h or 0) / 100.0


def _received(game, pal, team, els, goal, names=None):
    """(factor, notes) for what `pal` gets from the whole team, itself
    included: its own skill growing with teammates, teammates boosting its
    element, every beside-you buff in the party, the strongest party heal, and a
    teammate that calls it into battle."""
    others = [q for q in team if pal_key(q) != pal_key(pal)]
    mine = palpals.partner(game, pal["species"], pal.get("rank") or 1)
    my_el = set(elements(pal, els))
    tot = {"a": 0.0, "d": 0.0, "h": 0.0}
    notes = []
    fight = {"attack", "defense"}

    def add(note, a=0.0, d=0.0, h=0.0):
        tot["a"] += a
        tot["d"] += d
        tot["h"] += h
        if note and abs(stat_factor(goal, a, d, h) - 1) > 1e-4:
            notes.append(note)
    if mine["boost_self"] and fight & set(mine["self_stats"]):
        n = sum(1 for q in others
                if set(elements(q, els)) & set(mine["boost_self"]))
        per = _pct(game, pal)
        if n and per:
            stats = fight & set(mine["self_stats"])
            els_txt = "/".join(palpals.ELEMENT_NAMES.get(e, e)
                               for e in mine["boost_self"])
            add("its own partner skill: +%g%% %s %s" % (
                per, " and ".join(sorted(stats)),
                "for each of %d %s teammates" % (n, els_txt) if n > 1
                else "for its %s teammate" % els_txt),
                per * n / 100.0 if "attack" in stats else 0,
                per * n / 100.0 if "defense" in stats else 0)
    for q in others:
        theirs = palpals.partner(game, q["species"], q.get("rank") or 1)
        stats = fight & set(theirs["team_stats"])
        if theirs["boost_team"] and my_el & set(theirs["boost_team"]) and \
                stats:
            per = _pct(game, q)
            add("+%g%% %s from %s (boosts %s Pals)" % (
                per, " and ".join(sorted(stats)), _name(game, q["species"],
                                                        names),
                "/".join(palpals.ELEMENT_NAMES.get(e, e)
                         for e in theirs["boost_team"])),
                per / 100.0 if "attack" in stats else 0,
                per / 100.0 if "defense" in stats else 0)
        if theirs["pair"] and palpals.base_species(pal["species"]) in \
                theirs["pair"]:
            add("%s's partner skill calls it into battle" %
                _name(game, q["species"], names), a=PAIR_BONUS)
    # every different beside-you buff in the party applies at once; "does
    # not stack" means the same skill twice counts once (the best of them).
    # A Health-draining one (Celesdir Noct) works on the other Pal out with
    # you, never on the one that gives it: it neither drains nor buffs itself
    healed = any(_heal(game, q) for q in team)
    for buff, q in active_buffs(game, team, goal):
        if buff[3]["hp_loss"] and pal_key(q) == pal_key(pal):
            continue
        counted, full, stats, info = buff
        a, d, h = _along_stats(buff, goal, healed, pal, game)
        own = pal_key(q) == pal_key(pal)  # its own buff is told by gives()
        if info["hp_loss"]:
            why = drain_cost(game, pal, buff, healed)[3]
            add("\u26a0 %s's partner skill: +%.0f%% %s, but it keeps "
                "draining its Health (counted as -%.0f%% HP%s)" % (
                    _name(game, q["species"], names),
                    counted * 100, " and ".join(stats), -h * 100,
                    ", less thanks to " + " and ".join(why) if why else
                    "; a healer in the team or life steal (Vampiric) "
                    "softens it"), a, d, h)
        else:
            add(None if own else "+%.0f%% %s from %s's beside-you buff%s" % (
                    counted * 100, " and ".join(stats),
                    _name(game, q["species"], names),
                    " (%d of %d stacks counted, +%.0f%% at full)" % (
                        info["counted"], info["stacks"], full * 100)
                    if info["stacks"] > 1 else ""), a, d, h)
    heals = [(h, q) for q in team for h in [_heal(game, q)] if h]
    if heals and goal in ("tank", "fighter") or \
            (heals and goal.startswith("vs_")):
        h, q = max(heals, key=lambda t: t[0])
        add(None if pal_key(q) == pal_key(pal) else
            "party heal %.0f%% from %s (counted as +%.0f%% HP)" % (
                h * 100, _name(game, q["species"], names),
                h * HEAL_AS_HP * 100), h=h * HEAL_AS_HP)
    if mine["pair"] and any(palpals.base_species(q["species"]) in mine["pair"]
                            for q in others):
        add("its partner skill calls a teammate into battle", a=PAIR_BONUS)
    return stat_factor(goal, tot["a"], tot["d"], tot["h"]), notes


def drain_hint(game, pal, team, els, goal, names=None):
    """When a teammate's buff drains `pal`'s Health and `pal` has no life
    steal: what Vampiric would be worth to it, in words, or None."""
    if not game or is_work(goal) or not pal:
        return None
    members = [q for q in team if q]
    drainers = [q for q in members if drains(game, q)
                and pal_key(q) != pal_key(pal)]
    if not drainers or passive_totals(game, pal).get("LifeSteal"):
        return None
    now = _received(game, pal, members, els, goal)[0]
    # what Vampiric itself adds, whichever passive it would replace
    trial = dict(pal, passives=list(pal.get("passives") or []) + ["Vampire"])
    after = _received(game, trial, [trial if q is pal else q
                                    for q in members], els, goal)[0]
    gain = after / now - 1 if now else 0
    if gain < 0.03:
        return None
    whose = _name(game, drainers[0]["species"], names) + "'s"
    return ("Vampiric would soften %s Health drain: about +%.0f%% for "
            "this goal (Disposable Implant: Vampiric at the surgery table, "
            "or breed it in)" % (whose, gain * 100))


def synergy(game, pal, others, els, goal="fighter", scores=None,
            names=None):
    """(factor, gift, notes) for adding `pal` to a team of `others`:

      factor  how much its own score grows from the team (and itself)
      gift    how much it adds to the others' scores through its partner
              skill, in score points (needs `scores`: pal key -> score)
      notes   what it gets, in words"""
    if not game or is_work(goal):
        return 1.0, 0.0, []
    team = list(others) + [pal]
    factor, notes = _received(game, pal, team, els, goal, names)
    gift = 0.0
    if scores:
        for q in others:
            before = _received(game, q, others, els, goal)[0]
            after = _received(game, q, team, els, goal)[0]
            gift += scores.get(pal_key(q), 0.0) * (after / before - 1)
    return factor, gift, notes


def gives(game, pal, team, els, goal, names=None):
    """What `pal`'s partner skill does for the rest of `team`, in words."""
    out = []
    if not game or is_work(goal):
        return out
    b = _along(game, pal)
    if b:
        counted, full, stats, info = b
        out.append("gives the Pal fighting beside you +%.0f%% %s%s" % (
            counted * 100, " and ".join(stats),
            " (%s, %d of %d stacks counted, +%.0f%% at full)" % (
                "per kill" if info["per"] == "kill" else "per bullet hit"
                if info["per"] == "hit" else "stacking", info["counted"],
                info["stacks"], full * 100) if info["stacks"] > 1 else
            " - but that Pal keeps losing Health for it, so it needs a "
            "healer and is often not worth it" if info["hp_loss"] else ""))
    h = _heal(game, pal)
    if h:
        out.append("heals the player and party by %.0f%% when activated "
                   "(counted as +%.0f%% HP for the team)" % (
                       h * 100, h * HEAL_AS_HP * 100))
    part = palpals.partner(game, pal["species"], pal.get("rank") or 1)
    if part["boost_team"]:
        who = [q for q in team if pal_key(q) != pal_key(pal) and
               set(elements(q, els)) & set(part["boost_team"])]
        if who:
            out.append("boosts %s of %d %s teammate%s" % (
                " and ".join(part["team_stats"]), len(who), "/".join(
                    palpals.ELEMENT_NAMES.get(e, e)
                    for e in part["boost_team"]), "s" * (len(who) > 1)))
    return out


def team_synergy(game, team, els, goal="fighter", names=None):
    """[notes] per slot for the finished team: what each gets and gives."""
    members = [p for p in team if p is not None]
    out = []
    for p in team:
        if p is None:
            out.append([])
            continue
        got = _received(game, p, members, els, goal, names)[1] \
            if game and not is_work(goal) else []
        out.append(got + gives(game, p, members, els, goal, names))
    return out


# -- passive planner --------------------------------------------------------

def passive_pool(game, server_pals=()):
    """{passive id: [sources]} for every passive worth offering:
    "random" (can appear on any Pal), "surgery" (can be implanted) and
    "server" (a Pal on this server has it, so it can be bred over).
    Lucky is left out: it comes only with a lucky Pal."""
    seen = {pid for p in server_pals for pid in p.get("passives") or []}
    out = {}
    for pid, ps in (game.get("passives") or {}).items():
        if pid == "Rare":
            continue
        src = []
        if ps.get("roll"):
            src.append("random")
        if ps.get("surgery"):
            src.append("surgery")
        if pid in seen:
            src.append("server")
        if src:
            out[pid] = src
    return out


def _with(pal, pids):
    return dict(pal, passives=[p for p in pids if p][:MAX_PASSIVES])


def score_with(game, pal, pids, goal, els, level=None):
    return evaluate(game, _with(pal, pids), goal, els, level)


def best_passives(game, pal, goal, els, pool, level=None, keep=()):
    """The best set of up to four passives for this Pal and goal, keeping
    `keep`: picked one at a time, each the one that adds most, stopping when
    nothing adds anything."""
    chosen = [p for p in keep if p]
    base = score_with(game, pal, chosen, goal, els, level)["score"]
    while len(chosen) < MAX_PASSIVES:
        best, best_score = None, base
        for pid in pool:
            if pid in chosen:
                continue
            sc = score_with(game, pal, chosen + [pid], goal, els,
                            level)["score"]
            if sc > best_score * 1.001:
                best, best_score = pid, sc
        if best is None:
            break
        chosen.append(best)
        base = best_score
    return chosen


def slot_options(game, pal, goal, els, pool, planned, slot, level=None,
                 match=None, limit=40):
    """What each passive would do in `slot` of the planned set:
    [(passive id, score)] best first."""
    planned = list(planned) + [None] * (MAX_PASSIVES - len(planned))
    out = []
    for pid in pool:
        if pid in planned and planned[slot] != pid:
            continue
        if match is not None and not match(pid):
            continue
        trial = list(planned)
        trial[slot] = pid
        out.append((pid, score_with(game, pal, trial, goal, els,
                                    level)["score"]))
    out.sort(key=lambda t: -t[1])
    return out[:limit]


def contributions(game, pal, goal, els, planned, level=None):
    """How much each planned passive adds, as a fraction of the score
    without it (negative: it costs)."""
    full = score_with(game, pal, planned, goal, els, level)["score"]
    out = []
    for i, pid in enumerate(planned):
        if not pid:
            out.append(None)
            continue
        rest = [q for j, q in enumerate(planned) if j != i]
        without = score_with(game, pal, rest, goal, els, level)["score"]
        out.append((full - without) / without if without else 0.0)
    return out


def passive_sources(game, pid, owner_pals, species, names=None):
    """Where to get a passive: [text] - the surgery table, and the owner's
    own Pals that have it (same species first, for breeding it over)."""
    ps = (game.get("passives") or {}).get(pid) or {}
    out = []
    if ps.get("surgery"):
        sg = ps["surgery"]
        cost = []
        if sg.get("price"):
            cost.append("%s gold" % "{:,}".format(sg["price"]).replace(
                ",", " "))
        if sg.get("item"):
            cost.append(sg["item"])
        out.append("surgery table: " + (" + ".join(cost) or "free"))
    carriers = [p for p in owner_pals if pid in (p.get("passives") or [])]
    same = palpals.base_species(species)
    carriers.sort(key=lambda p: (palpals.base_species(p["species"]) != same,
                                 -p["level"]))
    if carriers:
        def label(p):
            key = palpals.base_species(p["species"])
            key = key[:-6] if key.endswith("_otomo") else key
            return p.get("nickname") or (names or {}).get(key) or \
                (names or {}).get(key.lower()) or key
        out.append("breed with: " + ", ".join(
            "%s%s lv %d" % (label(p), " (same species)"
                            if palpals.base_species(p["species"]) == same
                            else "", p["level"]) for p in carriers[:3]) +
            (" +%d more" % (len(carriers) - 3) if len(carriers) > 3 else ""))
    elif ps.get("roll"):
        out.append("can appear on any Pal (catch or breed more)")
    return out


PASSIVE_MODES = ("real", "plans", "best")
MODE_LABEL = {"real": "as they are", "plans": "with my plans",
              "best": "with the best passives"}
SHORTLIST = 40             # Pals given their best passives in "best" mode


def what_if(game, pals, goal, els, mode, plans=None, pool=None,
            level_for=None):
    """The Pals to build a team from, with the passives the mode asks for:

      real   as they are
      plans  those with a plan (pal id -> passive ids) carry the planned
             passives; the rest as they are
      best   each carries its best set of four for the goal - worked out for
             a shortlist (the best by species, level and IVs alone, and the
             best as they are), since doing it for hundreds of Pals is slow

    Changed Pals are copies with "real_passives" and "planned": True."""
    if mode == "plans":
        out = []
        for p in pals:
            planned = (plans or {}).get(p.get("id"))
            if planned is not None:
                out.append(dict(p, passives=[x for x in planned if x],
                                real_passives=p["passives"], planned=True))
            else:
                out.append(p)
        return out
    if mode != "best":
        return list(pals)
    usable_pals = [p for p in pals
                   if palpals.species_info(game, p["species"]).get("is_pal")]

    def lv(p):
        return level_for(p) if level_for else None
    bare = sorted(usable_pals, key=lambda p: -score_with(
        game, p, [], goal, els, lv(p))["score"])[:SHORTLIST]
    real = sorted(usable_pals, key=lambda p: -evaluate(
        game, p, goal, els, lv(p))["score"])[:SHORTLIST // 2]
    out, seen = [], set()
    for p in bare + real:
        if pal_key(p) in seen:
            continue
        seen.add(pal_key(p))
        mine = dict(pool or {})
        for x in p["passives"]:
            mine.setdefault(x, ["this Pal"])
        best = best_passives(game, p, goal, els, mine, lv(p))
        out.append(dict(p, passives=best, real_passives=p["passives"],
                        planned=True))
    return out


# -- breeding helper ---------------------------------------------------------

BREED_ROLES = (
    ("EggAlphaConversion", "alpha eggs",
     "chance that an egg you pick up becomes an Alpha egg",
     "in your party when you pick up eggs"),
    ("EggObtainExtraEgg", "extra eggs",
     "chance of one extra egg when you pick one up",
     "in your party when you pick up eggs"),
    ("BreedSpeed_InBaseCamp", "egg production",
     "faster egg production at the breeding farm",
     "anywhere in the base"),
    ("PalEggHatchingSpeed", "incubation",
     "shorter time to hatch eggs",
     "anywhere in the base"),
)


def _role_value(game, species, rank, etype):
    """The % a species' partner skill gives for an effect at a condenser
    rank, from DT_PartnerSkillParameter; None if it does not."""
    params = (game.get("partner_params") or {}).get(species)
    if not params:
        return None
    lists = params.get("passives") or []
    if not lists:
        return None
    ids = lists[max(0, min(len(lists) - 1, (rank or 1) - 1))]
    for pid in ids:
        ps = (game.get("passives") or {}).get(pid) or {}
        for t, v, *_ in (ps.get("effects") or []) + (ps.get("other") or []):
            if t == etype:
                return v
    return None


def breed_role_species(game):
    """{effect type: [species]} - which species' partner skills help
    breeding, found in the game data rather than listed by hand."""
    out = {role[0]: [] for role in BREED_ROLES}
    for sp in (game.get("partner_params") or {}):
        if sp != palpals.base_species(sp) or sp.endswith(("_2",)):
            continue
        info = (game.get("species") or {}).get(sp) or {}
        if not info.get("is_pal") or (info.get("zukan") or -1) < 0:
            continue
        for etype in out:
            if _role_value(game, sp, 1, etype):
                out[etype].append(sp)
    return out


def find_parents(breeder, target, pals, limit=3):
    """Pairs from `pals` (opposite genders) whose child is `target`:
    [(a, b)], best parents (highest level) first. Works on species pairs,
    so it is quick even for hundreds of Pals."""
    by_species = {}
    for p in pals:
        if p.get("gender") in ("Male", "Female"):
            by_species.setdefault(palpals.base_species(p["species"]),
                                  []).append(p)
    kinds = sorted(by_species)
    out = []
    for i, a in enumerate(kinds):
        for b in kinds[i:]:
            if breeder.child(a, b) != target:
                continue
            for pa in sorted(by_species[a], key=lambda p: -p["level"]):
                mate = next((pb for pb in sorted(
                    by_species[b], key=lambda p: -p["level"])
                    if pb is not pa and pb["gender"] != pa["gender"]), None)
                if mate:
                    out.append((pa, mate))
                    break
            if len(out) >= limit:
                return out
    return out


def special_recipes(game, target):
    """Special parent pairs that give `target` (DT_PalCombiUnique):
    [(species a, species b)], by the species ids the tables use."""
    tribes = {}
    for sid, info in (game.get("species") or {}).items():
        if sid == palpals.base_species(sid) and info.get("tribe"):
            tribes.setdefault(info["tribe"], sid)
    out = []
    for ta, _ga, tb, _gb, child in game.get("combos") or []:
        if child == target and ta != tb:
            out.append((tribes.get(ta, ta), tribes.get(tb, tb)))
    return out


def breeding_setup(game, pals, breeder=None):
    """Who to use for breeding: for each role (alpha eggs, extra eggs, egg
    production, incubation) the owner's best Pal and what it gives now and at
    four condenser stars, the species that can do it, and - when the owner has
    none - pairs of their own Pals that breed one. Plus the parents with
    Philanthropist or Babysitter, which breed faster at the farm."""
    roles = []
    species_for = breed_role_species(game)
    for etype, label, what, where in BREED_ROLES:
        cands = []
        for p in pals:
            key = palpals.species_key(game, p["species"]) or p["species"]
            key = palpals.base_species(key)
            if key.endswith("_otomo"):
                key = key[:-6]
            v = _role_value(game, key, p.get("rank") or 1, etype)
            if v:
                cands.append((v, _role_value(game, key, 5, etype) or v, p))
        cands.sort(key=lambda t: (-t[0], -t[2]["level"]))
        missing = [sp for sp in species_for.get(etype, [])
                   if not any(palpals.base_species(c[2]["species"]) == sp
                              for c in cands)]
        how = []
        if breeder is not None:
            for sp in missing:
                how += [(sp, a, b) for a, b in find_parents(
                    breeder, sp, pals, limit=2)]
        recipes = {sp: special_recipes(game, sp) for sp in missing}
        roles.append({"effect": etype, "label": label, "what": what,
                      "where": where, "have": cands, "species":
                      species_for.get(etype, []), "missing": missing,
                      "breed": how, "recipes": recipes})
    parents = [p for p in pals if any(
        e[0] in BREED_EFFECTS for x in p.get("passives") or []
        for e in (game.get("passives") or {}).get(x, {}).get("effects", []))]
    parents.sort(key=lambda p: -p["level"])
    return {"roles": roles, "parents": parents}


# -- team synergy overview -------------------------------------------------

def team_value(game, team, els, goal, scores):
    """The team's total score with its partner-skill synergy, and without."""
    members = [p for p in team if p is not None]
    base = sum(scores.get(pal_key(p), 0.0) for p in members)
    total = sum(scores.get(pal_key(p), 0.0) *
                _received(game, p, members, els, goal)[0] for p in members)
    return total, base


RECOMMEND_POOL = 120       # candidates weighed for the recommendations
WISH_WEIGHT = 0.15         # an open wish met counts as this much team growth


def recommend(game, ranking, team, els, goal, wishes=(), names=None,
              limit=8):
    """Who to add next to the Pals already picked, best first: for each
    candidate, how much the team's total (with synergy) grows with it, and
    why - what it gets from the team, what it gives the team, the elements,
    weak spots and wishes it covers, and how it copes with a teammate's
    Health drain. [{pal, ev, gain, share, reasons}] where `gain` is the
    team's growth in score points and `share` that growth against the team
    as it is."""
    members = [p for p in team if p]
    if not members or len(members) >= len(team):
        return []
    scores = {pal_key(p): ev["score"] for p, ev in ranking}
    total0, base0 = team_value(game, members, els, goal, scores)
    now = objective(total0, base0, goal)
    keys = {pal_key(p) for p in members}
    kinds = {palpals.base_species(p["species"]) for p in members}
    have_el = {e for p in members for e in elements(p, els)}
    weak0 = weaknesses(members, els)
    open_wishes = [w for w in wishes if w in WISHES and
                   not any(wish_met(game, p, els, w) for p in members)]
    drainer = next((p for p in members if drains(game, p)), None)
    healer = any(_heal(game, p) for p in members)
    combat = not is_work(goal)
    pool = []
    for p, ev in ranking:
        kind = palpals.base_species(p["species"])
        if pal_key(p) in keys or kind in kinds:
            continue
        pool.append((p, ev))
        kinds = kinds | {kind}          # the best of each species only
        if len(pool) >= RECOMMEND_POOL:
            break
    extra, seen = [], set(kinds)
    for p, ev in ranking:               # further down, but good for the team
        kind = palpals.base_species(p["species"])
        if kind not in seen and pal_key(p) not in keys and \
                _has_team_skill(game, p):
            seen.add(kind)
            extra.append((p, ev))
    out = []
    for p, ev in pool + extra[:SYNERGY_EXTRA]:
        after = members + [p]
        total, base = team_value(game, after, els, goal, scores)
        gain = objective(total, base, goal) - now
        reasons = []
        if combat:
            _f, got = _received(game, p, after, els, goal, names)
            # the drain note only when the candidate's own traits soften
            # it; otherwise it is the same for every candidate
            reasons += [n for n in got if "keeps draining" not in n
                        or any(w in n.partition("less thanks to")[2]
                               for w in ("life steal", "regeneration",
                                         "low-HP"))]
            for line in gives(game, p, after, els, goal, names):
                reasons.append(line)
            new_el = [e for e in elements(p, els) if e not in have_el]
            if new_el:
                reasons.append("adds %s to the team's elements" % ", ".join(
                    palpals.ELEMENT_NAMES.get(e, e) for e in new_el))
            weak1 = weaknesses(after, els)
            worse = [e for e, n in weak1.items() if n >= 2 and
                     n > weak0.get(e, 0)]
            if worse:
                reasons.append("\u26a0 one more weak to %s" % ", ".join(
                    palpals.ELEMENT_NAMES.get(e, e) for e in worse))
            if drainer and pal_key(drainer) != pal_key(p):
                if _heal(game, p) and not healer:
                    reasons.append("its heal takes much of %s's Health drain "
                                   "off the team" % _name(
                                       game, drainer["species"], names))
                elif passive_totals(game, p).get("LifeSteal"):
                    reasons.append("its life steal copes with %s's Health "
                                   "drain" % _name(game, drainer["species"],
                                                   names))
            if drains(game, p) and not healer:
                reasons.append("\u26a0 drains the others' Health: better "
                               "with a healer or Vampiric teammates")
        met = [w for w in open_wishes if wish_met(game, p, els, w)]
        if met:
            reasons.append("meets your wish%s: %s" % (
                "es" if len(met) > 1 else "",
                ", ".join(WISHES[w][0] for w in met)))
        out.append({"pal": p, "ev": ev, "gain": gain,
                    "share": gain / now if now else 0.0, "reasons": reasons,
                    "wishes": met})
    # what it adds to the team, and an open wish it meets counts extra
    out.sort(key=lambda r: -(r["share"] + WISH_WEIGHT * len(r["wishes"])))
    return out[:limit]


def _has_team_skill(game, p):
    part = palpals.partner(game, p["species"], p.get("rank") or 1)
    return bool(part["boost_team"] or part["boost_self"] or part["pair"]
                or part.get("alongside_buff") or part.get("heal"))


def synergy_overview(game, team, els, goal, ranking, names=None,
                     locked=(), swaps=3):
    """Everything the team's partner skills do together:

      gain    how much stronger the team is with its synergy (fraction)
      links   [{from, to, effect}] - who gives what to whom
      unused  skills in the team that add nothing (a stronger one of the
              same kind is already there, or no teammate they help)
      swaps   [{slot, out, in, gain, why}] - changes that would add most,
              from the owner's other Pals"""
    members = [p for p in team if p is not None]
    if not game or is_work(goal) or not members:
        return {"gain": 0.0, "links": [], "unused": [], "swaps": []}
    scores = {pal_key(p): ev["score"] for p, ev in ranking}
    nm = lambda p: p.get("nickname") or _name(game, p["species"], names)
    total, base = team_value(game, members, els, goal, scores)
    goal_value = objective(total, base, goal)
    links, unused = [], []

    def everyone(src):
        return "the whole team" if len(members) > 1 else nm(src)
    active = active_buffs(game, members, goal)
    for (counted, full, stats, info), q in active:
        text = "+%.0f%% %s to the Pal fighting beside you%s" % (
            counted * 100, " and ".join(stats),
            " (%d of %d stacks counted, +%.0f%% at full)" % (
                info["counted"], info["stacks"], full * 100)
            if info["stacks"] > 1 else
            " - which keeps losing Health for it" if info["hp_loss"] else "")
        links.append({"from": nm(q), "to": "the others" if info["hp_loss"]
                      else everyone(q), "effect": text})
    used = {pal_key(q) for _, q in active}
    for q in members:
        if _along(game, q) and pal_key(q) not in used:
            other = next((nm(x) for _, x in active if palpals.partner(
                game, x["species"])["name"] == palpals.partner(
                game, q["species"])["name"]), "another")
            unused.append("%s's beside-you buff does nothing here: %s has "
                          "the same skill, stronger, and it does not stack"
                          % (nm(q), other))
    heals = sorted([(h, q) for q in members for h in [_heal(game, q)] if h],
                   key=lambda t: -t[0])
    for k, (h, q) in enumerate(heals):
        if k == 0 and goal in ("tank", "fighter") or \
                (k == 0 and goal.startswith("vs_")):
            links.append({"from": nm(q), "to": everyone(q),
                          "effect": "party heal %.0f%% (counted as +%.0f%% "
                          "HP)" % (h * 100, h * HEAL_AS_HP * 100)})
        elif k > 0:
            unused.append("%s's heal adds little next to %s's" % (
                nm(q), nm(heals[0][1])))
    fight = {"attack", "defense"}
    for q in members:
        part = palpals.partner(game, q["species"], q.get("rank") or 1)
        per = _pct(game, q)
        counts = lambda st: abs(stat_factor(
            goal, per / 100.0 if "attack" in st else 0,
            per / 100.0 if "defense" in st else 0) - 1) > 1e-4
        stats = fight & set(part["team_stats"])
        if part["boost_team"] and stats and counts(stats):
            got = [p for p in members if pal_key(p) != pal_key(q) and
                   set(elements(p, els)) & set(part["boost_team"])]
            if got:
                links.append({"from": nm(q), "to": ", ".join(nm(p) for p in got),
                              "effect": "+%g%% %s (%s Pals)" % (
                                  per, " and ".join(sorted(stats)), "/".join(
                                      palpals.ELEMENT_NAMES.get(e, e)
                                      for e in part["boost_team"]))})
            else:
                unused.append("%s boosts %s Pals, but there are none in the "
                              "team" % (nm(q), "/".join(
                                  palpals.ELEMENT_NAMES.get(e, e)
                                  for e in part["boost_team"])))
        stats = fight & set(part["self_stats"])
        if part["boost_self"] and stats and counts(stats):
            got = [p for p in members if pal_key(p) != pal_key(q) and
                   set(elements(p, els)) & set(part["boost_self"])]
            if got:
                links.append({"from": ", ".join(nm(p) for p in got),
                              "to": nm(q), "effect": "+%g%% %s each, through "
                              "%s's own partner skill" % (
                                  per, " and ".join(sorted(stats)), nm(q))})
            else:
                unused.append("%s gets stronger with %s teammates, and has "
                              "none" % (nm(q), "/".join(
                                  palpals.ELEMENT_NAMES.get(e, e)
                                  for e in part["boost_self"])))
        if part["pair"]:
            mates = [p for p in members if palpals.base_species(p["species"])
                     in part["pair"]]
            if mates:
                links.append({"from": nm(q), "to": nm(mates[0]),
                              "effect": "calls it into battle"})
            else:
                unused.append("%s calls %s into battle - not in the team" % (
                    nm(q), ", ".join(_name(game, sp, names)
                                     for sp in part["pair"])))

    # swaps that would add most: owner's other Pals with a team skill
    out = []
    in_team = {pal_key(p) for p in members}
    kinds = {palpals.base_species(p["species"]) for p in members}
    pool = [p for p, _ in ranking[:200] if pal_key(p) not in in_team and
            _has_team_skill(game, p)]
    # every slot may be suggested: "use" locks the whole resulting team, so
    # skipping locked slots would stop suggestions after the first one
    free = [i for i, p in enumerate(team) if p is not None]
    for c in pool:
        best = None
        for i in free:
            trial = list(team)
            if palpals.base_species(c["species"]) in kinds and \
                    palpals.base_species(c["species"]) != \
                    palpals.base_species(team[i]["species"]):
                continue                     # one of each species
            trial[i] = c
            t2, b2 = team_value(game, [p for p in trial if p], els, goal,
                                scores)
            v2 = objective(t2, b2, goal)
            if best is None or v2 > best[0]:
                best = (v2, i)
        if best and best[0] > goal_value * 1.005:
            out.append({"slot": best[1], "out": team[best[1]], "in": c,
                        "gain": best[0] / goal_value - 1 if goal_value
                        else 0.0,
                        "why": gives(game, c, [c if j == best[1] else p
                                               for j, p in enumerate(team)
                                               if p is not None], els, goal,
                                     names) or
                        _received(game, c, [c if j == best[1] else p
                                            for j, p in enumerate(team)
                                            if p is not None], els, goal,
                                  names)[1]})
    out.sort(key=lambda t: -t["gain"])
    in_pairs = pairs(game, members, els, goal, scores, names, limit=4)
    # "most synergy" trades some raw strength for buffs: say how much
    vs_fighter = None
    if goal == "synergy":
        strong = build(ranking, "fighter", els, len(team), game=game)
        t_f, _ = team_value(game, [p for p in strong if p], els, goal,
                            scores)
        vs_fighter = (t_f / total - 1) if total else None
    owned_pairs = pairs(game, _synergy_pool(ranking, game), els, goal,
                        scores, names, limit=5)
    seen, best_each = set(), []
    for sw in out:                        # the best individual per species
        kind = palpals.base_species(sw["in"]["species"])
        if kind not in seen:
            seen.add(kind)
            best_each.append(sw)
    out = best_each
    return {"gain": total / base - 1 if base else 0.0, "links": links,
            "unused": unused, "swaps": out[:swaps],
            "vs_fighter": vs_fighter,
            "pairs": in_pairs, "best_pairs": [
                t for t in owned_pairs
                if not ({pal_key(t[0]), pal_key(t[1])} <= in_team)]}
