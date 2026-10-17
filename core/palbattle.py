#!/usr/bin/env python3
"""
palbattle.py - a turn-based battle between two players' Pals, old-school
monster-battle style, for the web page's "battles" background: one against
one, two against two or three against three, each as likely (SIZES).

With more than one a side, what a skill hits is decided skill by skill from the
game's description of it (AREA below): explosions, fans, rain, storms,
shock waves and sweeping beams hit every foe, bites, claws, charges and
single shots one of them (at random among those still standing). Skills
that the descriptions say do something to their user do that too: Implode
and its kin knock the user out, Roly Poly leaves Lamball dizzy for a turn,
Soul Drain heals the user.

Each player fights with one Pal from their pool: the Pals in their party,
and their TOP best by all-round fighter, attack, defense and HP (the team
builder's scores), each Pal once - so a pool is at most 25 and often
fewer. Pals the team builder leaves out as hard to summon (Panthalus) are
left out here too. The first player and their Pal are picked at
random; the opponent is picked to be as even as the server allows: players
of about the same strength (the mean score of their best), and among their
best the Pal closest in score, are much more likely - but not certain, so
now and then there is a mismatch. A much stronger Pal wins fast, as it
would in the game. They take turns; each turn a Pal uses one of its
equipped active skills (or learned ones, if nothing is equipped), and the
damage comes from the numbers paltop already has:

    stats      HP, attack and defense estimated from species, level, IVs
               and passives (palteam.stats)
    skills     power, element, cooldown and status build-up from the game's
               DT_WazaDataTable
    elements   the type chart (palteam.multiplier): double against the element
               it is strong against, half against the one strong against it

    damage = power x attack / defense x SCALE x element x same-element
             bonus x a little randomness, with a critical hit now and then

(the game does not publish its damage formula; this keeps its proportions:
twice the attack or half the defense is twice the damage, and nothing caps a
hit, so a much stronger Pal can win in one)

A skill's cooldown keeps it unused for a few turns after, so Pals mix their
skills. A skill with a status effect may apply it, with the chance growing
with its build-up: Burn and Poison hurt every turn, Freeze, Electrify and
Stun make the target lose a turn, Blind makes it miss now and then, and the
game's combos apply (Soaked foes take more from Electric, Ivy-Covered from
Fire, Electrified from Water, Muddy from Grass - and less: Soaked from Fire,
Burned from Grass, Muddy from Electric). Each turn says what statuses both
have after it, for the page to show. It ends when one faints, or
after MAX_TURNS with the one with more HP left winning.

The whole fight is decided here and sent as a list of turns; the page only
plays it back.
"""

from __future__ import annotations

import math
import random

import palpals
import palteam

SCALE = 2.0                  # evenly matched Pals last some 6 to 12 turns
SAME_ELEMENT = 1.2           # a skill of the Pal's own element
CRIT_CHANCE, CRIT = 0.08, 1.5
MAX_TURNS = 40
COMBO = 1.3                  # a status that makes an element hit harder
COMBO_TURNS = 3              # Soak, Ivy-Covered, Muddy last this many turns
WEAKEN = 0.7                 # ... or softer
# the game's help page: foes with this status take less damage from ...
STATUS_WEAKEN = {"Wetness": "Fire", "Burn": "Leaf", "Muddy": "Electricity"}
DOT = {"Burn": 0.06, "Poison": 0.05}     # of max HP each turn
DOT_TURNS = 3
SKIP = {"Freeze", "Electrical", "Stun"}  # lose the next turn
BLIND_MISS, BLIND_TURNS = 0.35, 2
TOP = 5                     # from each list: fighter, attack, defense, HP
EVEN = 3.0                  # how strongly the opponent is picked to be even:
                            # a player or Pal twice as strong is 8x less likely
STATUS_ONLY = 60            # how often a no-damage status skill is picked:
                            # as often as a skill of this power
BASIC = {"name": "Tackle", "power": 30, "cooldown": 0.0, "effects": []}


# Skills that hit every foe in a 2-vs-2 fight, decided one by one from the
# game's own description of each (DT_SkillDescText_Common): explosions,
# fans, rain, storms, shock waves, sweeping or wide beams, things "around
# the enemy" or "all directions". Every other skill hits one foe: bites,
# claws, charges, single shots ("straight at an enemy", "homes in on an
# enemy"). Line attacks count as one foe.
AREA = {
    "AcidRain", "AirBlade", "Apocalypse", "BeamSlicer", "BlastCanon",
    "BubbleShower", "ChargeCanon", "Commet", "CommetRain", "CreepingBubble",
    "DarkPulse", "DarkWave", "DiversionLaser", "DoubleIcicleThrow",
    "DragonBreath", "DragonWave", "ElecWave", "Eruption", "FireBall",
    "FireSeed", "FlameWall", "HolyBlast", "HydroPump", "HydroSlicer",
    "HyperBeam", "IceAge", "IceWall", "IcicleLine", "IcicleThrow", "Inferno",
    "LightningStrike", "PredatorWave", "Psychokinesis", "RangeThunder",
    "ReflectiveShuriken", "RipTide", "RockBeat", "RootLance", "SandTwister",
    "SeaGush", "SeedMachinegun", "SelfDestruct", "SelfDestruct_Bee",
    "SelfExplosion", "SpreadPulse", "StarMine", "StoneShotgun",
    "ThreeCommet", "ThunderFunnel", "ThunderStorm", "Tremor",
    "Unique_Anubis_GroundPunch", "Unique_Anubis_LowRoundKick",
    "Unique_BirdDragon_FireBreath", "Unique_BirdDragon_Ice_IceBreath",
    "Unique_BlackGriffon_TackleLaser", "Unique_BlackGriffon_TackleLaser2",
    "Unique_BlackMetalDragon_FirePunch", "Unique_BlueSkyDragon_DrainStorm",
    "Unique_BlueSkyDragon_SweepBreath", "Unique_BlueThunderHorse_FlashDash",
    "Unique_CaptainPenguin_Black_BodySlide_Electric",
    "Unique_CaptainPenguin_BodySlide", "Unique_ClownRabbit_TrickShow",
    "Unique_CubeTurtle_Neutral_HolyPress",
    "Unique_DarkMechaDragon_FunnelLaser", "Unique_DarkMechaDragon_SetFunnel",
    "Unique_DarkMechaDragon_WarpComet",
    "Unique_DomeArmorDragon_ExplosiveMissile",
    "Unique_ElecPanda_ElecScratch", "Unique_ElecSnail_ShellCharge",
    "Unique_FairyDragon_FairyTornado", "Unique_FengyunDeeper_CloudTempest",
    "Unique_FengyunDeeper_Electric_ThunderTempest",
    "Unique_FlowerPrince_PoisonGasDance",
    "Unique_FlowerPrince_PoisonGasTackle",
    "Unique_GhostAnglerfish_Fire_SweepBait_Fire",
    "Unique_GhostAnglerfish_SweepBait", "Unique_GhostDragon_PhosphorousBeam",
    "Unique_GoldenHorse_StoneDash", "Unique_GrassGolem_ArmCannon",
    "Unique_GrassGolem_Dark_DarkArmCannon", "Unique_GrassGolem_RocketPunch",
    "Unique_GrassRabbitMan_GrassRoundKick", "Unique_Grassmammoth_Earthquake",
    "Unique_Horus_FlareBird", "Unique_Horus_PerfectStorm",
    "Unique_Horus_Water_AquaStorm", "Unique_IceCrocodile_SpitAttack",
    "Unique_IceHorse_Dark_DarkBladeAttack", "Unique_IceHorse_IceBladeAttack",
    "Unique_IceNarwhal_JumpingHorn", "Unique_JetDragon_JumpBeam",
    "Unique_KingBahamut_ArmSmash", "Unique_KingWhale_AquaTornado",
    "Unique_KingWhale_Breaching", "Unique_KingWhale_HomingBubble",
    "Unique_KingWhale_Maelstrom", "Unique_KingWhale_WaveTackle",
    "Unique_Kirin_Ice_IceTackle", "Unique_Kirin_LightningTackle",
    "Unique_LanternButler_LanternFlame",
    "Unique_LegendDeer_RadiantPurge_Otomo",
    "Unique_LegendDeer_WarpPillarBurst", "Unique_LotusDragon_LotusBloom",
    "Unique_MonochromeQueen_BalletJump", "Unique_Mothman_GiantSpore",
    "Unique_Mothman_SporeScatter", "Unique_NightLady_FlameNightmare",
    "Unique_NightLady_WarpBeam", "Unique_NightLady_WarpBeam_Straight",
    "Unique_Plesiosaur_LongBreath", "Unique_PoseidonOrca_TorrentLaser",
    "Unique_QueenBee_SpinLance", "Unique_RedArmorBird_TriplePeck",
    "Unique_SnakeGirl_SnakeShot", "Unique_SnowTigerBeastman_SnowImpact",
    "Unique_StuffedShark_HiddenWeapon", "Unique_SumoDog_SumoStomp",
    "Unique_ThunderBird_Ice_SnowStrom", "Unique_ThunderBird_ThunderStorm",
    "Unique_ThunderDog_Ice_KoriShorai", "Unique_ThunderDog_InazumaShorai",
    "Unique_VolcanicMonster_Ice_IceAttack",
    "Unique_VolcanicMonster_MagmaAttack",
    "Unique_VolcanoDragon_Ice_IceLaser",
    "Unique_VolcanoDragon_Ice_IcicleSpit", "Unique_VolcanoDragon_MagmaSpit",
    "Unique_VolcanoDragon_VolcanicLaser", "Unique_WhiteDeer_Dark_DarkPillar",
    "Unique_WhiteDeer_HolyPillar", "Unique_WingGolem_Fire_FlameCutter",
    "Unique_WingGolem_RoundCutter",
    "Unique_WorldTreeDragon_PaldiumExplosion",
    "Unique_WorldTreeDragon_PaldiumRain",
    "Unique_YakushimaBoss001_Green_2_PhantasmalDeathray",
    "Unique_YakushimaBoss001_Green_PhantasmalDeathray",
    "Unique_YakushimaBoss002_2_PhantasmalDeathray",
    "Unique_YakushimaBoss002_PhantasmalDeathray",
    "Unique_Yakushima_SummonServant", "Unique_Yeti_Grass_GrassBall",
    "Unique_Yeti_SnowBall", "WallSplash", "WindBurst", "WindEdge",
}
# what a skill does to its user, from the same descriptions
SELF_KO = {"SelfDestruct", "SelfDestruct_Bee", "SelfExplosion"}  # "incapacitated"
DIZZY = {"Unique_SheepBall_Roll"}        # "becomes dizzy and unable to move"
DRAIN = {"Unique_MysteryMask_LifeSteal": 0.5}   # "absorbs the HP": this share
                                                # of the damage (the game does
                                                # not give the number)


def _skills(game, pal, limit=3):
    """The skills it fights with: equipped, else learned, else a tackle
    (a Pal equips three; a tower or raid boss uses its whole list)."""
    table = game.get("skills") or {}
    out = []
    for wid in list(pal.get("skills") or []) or list(pal.get("learned") or []):
        sk = table.get(wid)
        # a damaging skill, or one that only builds up a status (Poison Fog)
        if sk and ((sk.get("power") or 0) > 1 or sk.get("effects")):
            out.append({"id": wid, "name": sk["name"],
                        "element": sk["element"], "power": sk["power"],
                        "cooldown": sk.get("cooldown") or 0,
                        "effects": [list(e) for e in sk.get("effects") or []]})
    return out[:limit]


def fighter(game, pal, els, owner_name, names=None, skill_limit=3):
    st = palteam.stats(game, pal)
    my_el = palteam.elements(pal, els)
    skills = _skills(game, pal, skill_limit) or [dict(BASIC, element=my_el[0])]
    alpha, name = pal_display(pal["species"], names)
    status = game.get("status") or {}
    return {"id": pal.get("id"), "species": palpals.base_species(
                palpals.species_key(game, pal["species"]) or pal["species"]),
            "name": pal.get("nickname") or name, "kind": name,
            "alpha": alpha, "lucky": bool(pal.get("lucky")),
            "stars": max(0, (pal.get("rank") or 1) - 1),
            "awakened": bool(pal.get("awakened")),
            "iv": [pal.get("hp_iv"), pal.get("atk_iv"), pal.get("def_iv")],
            "level": pal["level"], "owner": owner_name, "els": my_el,
            "hp": round(st["hp"]), "atk": round(st["atk"]),
            "def": round(st["def"]),
            "skills": [dict(s, effects=[[t, v, (status.get(t) or {}).get(
                "name") or t] for t, v in s["effects"]]) for s in skills]}


def pal_display(species, names):
    return palpals.pal_display(species, names)


def pool(game, mine, party, els=None):
    """A player's fighters: their party first, then the TOP best by
    all-round fighter, attack, defense and HP, each Pal once."""
    els = els or {}
    out, seen = [], set()

    def add(p):
        key = palteam.pal_key(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    for p in party:
        add(p)
    for goal in ("fighter", "attack", "defense"):
        for p, _ev in palteam.ranked(game, mine, goal, els)[:TOP]:
            add(p)
    for p in sorted(mine, key=lambda q: -palteam.stats(game, q)["hp"])[:TOP]:
        add(p)
    return out


def _pools(game, pals, players, els, containers):
    """{owner: [(pal, fighter score)]} for every player with Pals."""
    by_owner, party = {}, {}
    for p in pals:
        o = p.get("owner")
        if not o or o not in players:
            continue
        info = palpals.species_info(game, p["species"])
        if not info.get("is_pal") or palteam.summon_note(p["species"]):
            continue                 # not a Pal, or hard to summon (Panthalus)
        team = ((containers or {}).get(o) or {}).get("team")
        if team and p.get("container") == team:
            party.setdefault(o, []).append(p)     # the party, at any level
        elif p.get("level", 0) < 5:
            continue
        by_owner.setdefault(o, []).append(p)
    out = {}
    for o, mine in by_owner.items():
        fighters = pool(game, mine, sorted(
            party.get(o, []), key=lambda q: q.get("slot") or 0), els)
        if fighters:
            out[o] = [(p, palteam.evaluate(game, p, "fighter", els or {})
                       ["score"]) for p in fighters]
    return out


# likelier the closer two scores are: exp(-EVEN * |ln(a / b)|)
def _even(a, b):
    return math.exp(-EVEN * abs(math.log(max(a, 1e-6) / max(b, 1e-6))))


def pick(game, pals, players, rng=random, els=None, containers=None, size=1):
    """Two teams of `size` Pals from two different players, from each one's
    pool, the second picked to be as even with the first as it can:
    ([pals a], [pals b]) or None. `players` maps owner id to a name;
    `containers` (the save's) tells which Pals are in each player's party."""
    pools = {o: b for o, b in _pools(game, pals, players, els, containers)
             .items() if len(b) >= size}
    owners = list(pools)
    if len(owners) < 2:
        return None
    strength = {o: sum(sc for _, sc in b) / len(b) for o, b in pools.items()}
    oa = rng.choice(owners)
    others = [o for o in owners if o != oa]
    ob = rng.choices(others, weights=[_even(strength[oa], strength[o])
                                      for o in others])[0]
    team_a = rng.sample(pools[oa], size)
    team_b, left = [], list(pools[ob])
    for _p, sa in team_a:                  # each one's match, one by one
        b = rng.choices(left, weights=[_even(sa, sc) for _, sc in left])[0]
        left.remove(b)
        team_b.append(b)
    return [p for p, _ in team_a], [p for p, _ in team_b]


class Fight:
    """A fight between two sides, one turn at a time: `fighters` is a list of
    fighter dicts with "side" 0 or 1. Each turn:
    {who, skill, element, area, miss, skip, targets: [{to, damage, mult,
    crit, combo, applied}], self, heal, dots, hp: [...], status: [...]}
    with `who` and `to` indexes into `fighters`. The computer picks a skill
    and a target unless they are given (a player's own Pal, in a duel)."""

    def __init__(self, game, fighters, rng=random, max_turns=MAX_TURNS):
        f = self.f = fighters
        self.rng, self.max_turns = rng, max_turns
        n = len(f)
        self.hp = [x["hp"] for x in f]
        labels = game.get("status") or {}
        self.name = lambda k: (labels.get(k) or {}).get("name") or k
        self.cool = [dict() for _ in f]          # skill name -> turns left
        self.state = [dict() for _ in f]         # status -> turns left
        # turns go side by side, the higher level of each side first; the
        # side with the higher level starts (a coin toss between equals)
        self.sides = [sorted([i for i in range(n) if f[i]["side"] == s],
                             key=lambda i: -f[i]["level"]) for s in (0, 1)]
        first = 0 if (f[self.sides[0][0]]["level"], rng.random()) >= \
            (f[self.sides[1][0]]["level"], rng.random()) else 1
        self.order = []
        for k in range(max(len(self.sides[0]), len(self.sides[1]))):
            for s in (first, 1 - first):
                if k < len(self.sides[s]):
                    self.order.append(self.sides[s][k])
        self.turns = []
        self.step = 0

    def alive(self, s):
        return [i for i in self.sides[s] if self.hp[i] > 0]

    def over(self):
        return len(self.turns) >= self.max_turns or not self.alive(0) or \
            not self.alive(1)

    def next_who(self):
        """Who acts next (the fainted are skipped), or None when it is over."""
        if self.over():
            return None
        while self.hp[self.order[self.step % len(self.order)]] <= 0:
            self.step += 1
        return self.order[self.step % len(self.order)]

    def skips(self, who):
        """Does it lose its next turn (frozen, stunned, dizzy)?"""
        return any(x in self.state[who] for x in SKIP | {"Dizzy"})

    def ready(self, who):
        """The names of its skills it can use on its next turn."""
        cool = self.cool[who]
        names = [x["name"] for x in self.f[who]["skills"]
                 if cool.get(x["name"], 0) <= 1]
        return names or [x["name"] for x in self.f[who]["skills"]]

    def turn(self, skill=None, target=None):
        """Play the next turn: `skill` (a name) and `target` (an index) for
        the one who acts, or the computer's choice. Returns the turn."""
        rng, f, hp, cool, state, name = self.rng, self.f, self.hp, \
            self.cool, self.state, self.name
        who = self.next_who()
        if who is None:
            return None
        self.step += 1
        me, side = f[who], f[who]["side"]
        t = {"who": who, "dots": [], "skip": None, "miss": False,
             "skill": None, "element": None, "area": False, "targets": [],
             "self": None, "heal": 0}
        for k in list(cool[who]):
            cool[who][k] -= 1
            if cool[who][k] <= 0:
                del cool[who][k]
        skip = next((x for x in SKIP | {"Dizzy"} if state[who].pop(x, None)),
                    None)
        if skip:
            t["skip"] = skip
        else:
            ready = [x for x in me["skills"] if x["name"] not in cool[who]] \
                or me["skills"]
            chosen = [x for x in ready if x["name"] == skill]
            sk = chosen[0] if chosen else rng.choices(ready, weights=[
                math.sqrt(max(x["power"], STATUS_ONLY)) for x in ready])[0]
            if sk["cooldown"] > 4:
                cool[who][sk["name"]] = min(4, int(sk["cooldown"] // 5) + 1)
            foes = self.alive(1 - side)
            area = sk.get("id") in AREA
            t["skill"], t["element"], t["area"] = sk["name"], sk["element"], \
                area and len(foes) > 1
            blind = state[who].get("Darkness")
            if blind:
                state[who]["Darkness"] = blind - 1
                if state[who]["Darkness"] <= 0:
                    del state[who]["Darkness"]
                if rng.random() < BLIND_MISS:
                    t["miss"] = True
            if not t["miss"]:
                crit = rng.random() < CRIT_CHANCE
                stab = SAME_ELEMENT if sk["element"] in me["els"] else 1.0
                roll = rng.uniform(0.85, 1.0)
                dealt = 0
                aim = [target] if target in foes else [rng.choice(foes)]
                for to in (foes if area else aim):
                    foe = f[to]
                    hit = {"to": to, "combo": None, "applied": None}
                    mult = palteam.multiplier(sk["element"], foe["els"])
                    # the foe's statuses: Soaked takes more from Electric
                    # and less from Fire, and so on (the game's help page)
                    combo = 1.0
                    for st_, el in palteam.STATUS_COMBO.items():
                        if el == sk["element"] and st_ in state[to]:
                            combo *= COMBO
                            hit["combo"] = {"status": name(st_), "up": True,
                                            "element": el}
                    for st_, el in STATUS_WEAKEN.items():
                        if el == sk["element"] and st_ in state[to]:
                            combo *= WEAKEN
                            hit["combo"] = {"status": name(st_), "up": False,
                                            "element": el}
                    dmg = sk["power"] * me["atk"] / max(1.0, foe["def"]) * \
                        SCALE * mult * combo * stab * roll * \
                        (CRIT if crit else 1.0)
                    hit["damage"] = max(1, int(round(dmg)))
                    hit["mult"], hit["crit"] = mult, crit
                    dealt += min(hp[to], hit["damage"])
                    hp[to] = max(0, hp[to] - hit["damage"])
                    for etype, value, label in sk.get("effects") or []:
                        immune = etype in palteam.STATUS_IMMUNE and any(
                            e in palteam.STATUS_IMMUNE[etype]
                            for e in foe["els"])
                        if immune or etype in state[to] or hp[to] <= 0:
                            continue
                        if rng.random() < min(0.6, value / 200.0):
                            state[to][etype] = DOT_TURNS if etype in DOT \
                                else BLIND_TURNS if etype == "Darkness" \
                                else COMBO_TURNS \
                                if etype in palteam.STATUS_COMBO \
                                and etype not in SKIP else 2
                            hit["applied"] = label
                            break
                    t["targets"].append(hit)
                if sk.get("id") in DRAIN and dealt:
                    t["heal"] = min(me["hp"] - hp[who],
                                    int(round(dealt * DRAIN[sk["id"]])))
                    hp[who] += t["heal"]
                    t["self"] = "drain"
            if sk.get("id") in SELF_KO:
                hp[who] = 0
                t["self"] = "ko"
            elif sk.get("id") in DIZZY:
                state[who]["Dizzy"] = 1
                t["self"] = "dizzy"
        # damage over time on the one who just acted
        for etype, frac in DOT.items():
            left = state[who].get(etype)
            if left and hp[who] > 0:
                d = max(1, int(round(me["hp"] * frac)))
                hp[who] = max(0, hp[who] - d)
                t["dots"].append([etype, d])
                state[who][etype] = left - 1
                if state[who][etype] <= 0:
                    del state[who][etype]
        # combo statuses (Soak, Ivy-Covered, Muddy) wear off after a while
        for etype in list(state[who]):
            if etype in palteam.STATUS_COMBO and etype not in SKIP:
                state[who][etype] -= 1
                if state[who][etype] <= 0:
                    del state[who][etype]
        t["hp"] = list(hp)
        # what each has after the turn, by the game's names
        t["status"] = [sorted(name(k) for k in st if k != "Dizzy")
                       for st in state]
        self.turns.append(t)
        return t

    def winner(self):
        a, b = self.alive(0), self.alive(1)
        if a and not b:
            return 0
        if b and not a:
            return 1
        if not a and not b:        # both gone at once: the one who blew
            return 1 - self.f[self.turns[-1]["who"]]["side"]  # itself up loses
        # out of turns: more HP left wins
        share = [sum(self.hp[i] / self.f[i]["hp"] for i in self.sides[s]) /
                 len(self.sides[s]) for s in (0, 1)]
        return 0 if share[0] >= share[1] else 1


def simulate(game, fighters, rng=random):
    """A whole fight, the computer on both sides: {turns, winner}."""
    fight = Fight(game, fighters, rng)
    while not fight.over():
        fight.turn()
    return {"turns": fight.turns, "winner": fight.winner()}


SIZES = (1, 2, 3)            # Pals a side, picked with equal chance


def battle(game, pals, players, els, names=None, rng=random, containers=None,
           size=None):
    """A whole battle ready for the page: one, two or three Pals a side,
    each as likely (or `size` a side when given; fewer when the players do
    not have that many), or None when fewer than two players own Pals."""
    if size not in SIZES:
        size = rng.choice(SIZES)
    got = None
    for n in range(size, 0, -1):
        got = pick(game, pals, players, rng, els, containers, n)
        if got:
            break
    if not got:
        return None
    fighters = []
    for side, team in enumerate(got):
        for p in team:
            fighters.append(dict(fighter(game, p, els, players[p["owner"]],
                                         names), side=side))
    out = simulate(game, fighters, rng)
    out["fighters"] = fighters
    out["size"] = len(got[0])
    return out
