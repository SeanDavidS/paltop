#!/usr/bin/env python3
"""
palduel.py - a battle you fight yourself, on the web page's battles tab.

You pick one, two or three of your own Pals; paltop finds another player's
Pals to match them (the pools and the even matching of palbattle.pick) and
the fight goes one turn at a time (palbattle.Fight). On your Pals' turns you
pick the skill and, for a single-target skill, which foe; the other side is
the computer, as in the battles that play by themselves. A frozen, stunned
or dizzy Pal of yours loses its turn by itself.

You do not see all of the other side at first: in 3 v 3 one of their three
is hidden, in 2 v 2 one of two, in 1 v 1 the one. A hidden Pal shows itself
the first time it acts or is hit. What the page is sent never gives a hidden
Pal away: no name, species, level, HP or status until then.

Duels live in the web page's memory only (Site keeps one per user) and change
nothing in the game.
"""

from __future__ import annotations

import random
import secrets
import time

import palbattle
import palpals
import palteam

DUEL_TURNS = 80            # a duel may take longer than a battle on its own
HIDDEN = {1: 1, 2: 1, 3: 1}           # size -> how many of theirs are hidden
IDLE = 1800                # seconds a duel is kept without a move


def choices(game, mine, els, names=None, limit=80):
    """Your Pals to pick from, the best all-round fighters first."""
    out = []
    for p, ev in palteam.ranked(game, mine, "fighter", els):
        if palteam.summon_note(p["species"]) or not p.get("id"):
            continue
        f = palbattle.fighter(game, p, els, "", names)
        out.append({"id": p["id"], "name": f["name"], "kind": f["kind"],
                    "species": f["species"], "level": f["level"],
                    "els": f["els"], "stars": f["stars"],
                    "awakened": f["awakened"], "iv": f["iv"],
                    "hp": f["hp"], "atk": f["atk"], "def": f["def"],
                    "skills": [[s["name"], s["element"], s["power"]]
                               for s in f["skills"]],
                    "score": round(ev["score"])})
        if len(out) >= limit:
            break
    return out


def _opponent(game, pals, players, els, containers, me, team, rng):
    """Another player's Pals, as even with `team` as the pools allow."""
    pools = {o: b for o, b in palbattle._pools(game, pals, players, els,
                                               containers).items()
             if o != me and len(b) >= len(team)}
    if not pools:
        return None
    mine = [palteam.evaluate(game, p, "fighter", els)["score"] for p in team]
    avg = sum(mine) / len(mine)
    owners = list(pools)
    strength = {o: sum(sc for _, sc in b) / len(b) for o, b in pools.items()}
    ob = rng.choices(owners, weights=[palbattle._even(avg, strength[o])
                                      for o in owners])[0]
    theirs, left = [], list(pools[ob])
    for sa in mine:                       # each one's match, one by one
        b = rng.choices(left, weights=[palbattle._even(sa, sc)
                                       for _, sc in left])[0]
        left.remove(b)
        theirs.append(b[0])
    return theirs


class Duel:
    def __init__(self, game, fighters, hidden, rng):
        self.id = secrets.token_hex(8)
        self.fight = palbattle.Fight(game, fighters, rng, DUEL_TURNS)
        self.hidden = set(hidden)
        self.seen = 0                     # turns the page has been sent
        self.touched = time.time()

    # -- what the page may see ----------------------------------------------

    def _fighter(self, i):
        f = self.fight.f[i]
        if i in self.hidden:
            return {"hidden": True, "side": f["side"], "name": "???",
                    "kind": "???", "owner": f["owner"], "els": ["Normal"],
                    "species": "", "level": None, "hp": None, "skills": []}
        return f

    def _mask(self, t):
        t = dict(t)
        t["hp"] = [None if i in self.hidden else v
                   for i, v in enumerate(t["hp"])]
        t["status"] = [[] if i in self.hidden else v
                       for i, v in enumerate(t["status"])]
        return t

    def _reveal(self, t):
        """A hidden Pal that acts or is hit shows itself in that turn."""
        show = {t["who"]} | {h["to"] for h in t.get("targets") or []}
        out = {}
        for i in sorted(show & self.hidden):
            self.hidden.discard(i)
            out[i] = self.fight.f[i]
        if out:
            t["reveal"] = out
        return t

    def _mine_to_move(self):
        """Your Pal whose turn it is (not one that loses its turn), or None."""
        who = self.fight.next_who()
        if who is None or self.fight.f[who]["side"] != 0 or \
                self.fight.skips(who):
            return None
        return who

    def _advance(self):
        """The computer's turns, and your lost ones, until it is your move."""
        while not self.fight.over() and self._mine_to_move() is None:
            self.fight.turn()

    def view(self, full=False):
        """What the page is sent: the turns it has not had yet (each with
        the Pals it reveals), your move if it is yours, and how it ended."""
        new = []
        for t in self.fight.turns[self.seen:]:
            new.append(self._mask(self._reveal(dict(t))))
        self.seen = len(self.fight.turns)
        out = {"id": self.id, "turns": new, "over": self.fight.over()}
        if full:
            out["fighters"] = [self._fighter(i)
                               for i in range(len(self.fight.f))]
            out["size"] = len(self.fight.sides[0])
        if out["over"]:
            out["winner"] = self.fight.winner()
            return out
        who = self._mine_to_move()
        if who is not None:
            ready = set(self.fight.ready(who))
            out["move"] = {
                "who": who,
                "skills": [{"name": s["name"], "element": s["element"],
                            "power": s["power"], "ready": s["name"] in ready,
                            "area": s.get("id") in palbattle.AREA,
                            "effects": [e[2] for e in s.get("effects") or []]}
                           for s in self.fight.f[who]["skills"]],
                "targets": self.fight.alive(1)}
        return out

    # -- your move ------------------------------------------------------------

    def act(self, skill, target):
        """Your Pal uses `skill` (on `target`, a foe's index, when it hits
        one): the page's answer, or an error text."""
        self.touched = time.time()
        who = self._mine_to_move()
        if who is None:
            return None, "it is not your move"
        if skill not in self.fight.ready(who):
            return None, "that skill is still cooling down"
        if target is not None and target not in self.fight.alive(1):
            target = None
        self.fight.turn(skill, target)
        self._advance()
        return self.view(), None


def start(game, pals, players, els, containers, me, pal_ids, names=None,
          rng=None):
    """A new duel with your Pals `pal_ids` (1 to 3): (Duel, None) or
    (None, error text)."""
    rng = rng or random.Random()
    mine = {p.get("id"): p for p in pals if p.get("owner") == me}
    team = [mine[i] for i in dict.fromkeys(pal_ids) if i in mine][:3]
    if not team:
        return None, "pick one to three of your Pals"
    if any(not palpals.species_info(game, p["species"]).get("is_pal")
           for p in team):
        return None, "only Pals can fight"
    theirs = _opponent(game, pals, players, els, containers, me, team, rng)
    if not theirs:
        return None, "no other player has enough Pals for a %d v %d" % (
            len(team), len(team))
    fighters = [dict(palbattle.fighter(game, p, els, players.get(me, "you"),
                                       names), side=0) for p in team]
    fighters += [dict(palbattle.fighter(game, p, els, players[p["owner"]],
                                        names), side=1) for p in theirs]
    size = len(team)
    foes = list(range(size, 2 * size))
    duel = Duel(game, fighters, rng.sample(foes, HIDDEN[size]), rng)
    duel._advance()
    return duel, None
