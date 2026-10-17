#!/usr/bin/env python3
"""
palweb.py - paltop in a browser, for everyone who plays on the server.

    python3 palweb.py [--port 8765] [--http]

Every player has their own user: their in-game name and a password they
choose. To make one, a player first types the *server* password (the one
players type to join, read from the container's SERVER_PASSWORD), then their
in-game name and the password they want. The name must be a player with a
character on the server; the user is linked to that player, and each name
and each player can only have one user. Users live in the history database
(state/paltop.db, tables web_users and web_sessions); passwords are stored as
salted scrypt hashes. The admin can list or remove users:

    python3 palweb.py --list-users
    python3 palweb.py --remove-user NAME

What it serves, after logging in:

    overview  server status, who is on, alerts, fps and players over 24 h
    pals      every player's Pals with details, role rankings and breeding,
              and the team builder
    bases     base camps and workers that need attention
    crashes   the crash log

It is read-only by construction: it has no admin actions, and its only
call to the Palworld REST API is the chat's announce (a logged-in player's
message, shown in the game; see palchat). Live data comes from state/web.json, which the background collector writes every 30 s
through an allow-list (no IP addresses, Steam ids or admin commands), and Pal
data from the save file on disk.

Security: HTTPS with a self-signed certificate made on first start (in
state/web/), a random session cookie (HttpOnly, SameSite=Strict, Secure; only
its hash is stored), login attempts limited per address, and strict browser
security headers.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections import deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_core():
    """core/ (the shared modules): PALTOP_CORE, or next to this part, as in
    the repository and in an install (<paltop>/core beside <paltop>/web)."""
    here = os.path.dirname(os.path.realpath(__file__))
    for d in (os.environ.get("PALTOP_CORE"), os.path.join(here, "..", "core")):
        if d and os.path.isfile(os.path.join(d, "palsource.py")):
            return os.path.abspath(d)
    sys.exit("paltop: cannot find core/ (set PALTOP_CORE to where it is)")


sys.path.insert(0, _find_core())

import palachieve  # noqa: E402
import palbattle  # noqa: E402
import palboss  # noqa: E402
import palchat  # noqa: E402
import palduel  # noqa: E402
import palpals  # noqa: E402
import palpedia  # noqa: E402
import palquests  # noqa: E402
import palsource  # noqa: E402
import palteam  # noqa: E402
import paltraits  # noqa: E402
from palpals import element_of, pal_display  # noqa: E402

STATE = palsource.STATE_DIR
WEB_DIR = os.path.join(HERE, "static")
TLS_DIR = os.path.join(STATE, "web")
SNAPSHOT = os.environ.get("PALTOP_WEB_SNAPSHOT") or \
    os.path.join(STATE, "web.json")          # override for testing
OLD_SESSIONS = os.path.join(TLS_DIR, "sessions.json")   # before users
DB = os.environ.get("PALTOP_DB") or \
    os.path.join(STATE, "paltop.db")          # override for testing
SESSION_DAYS = 30
COOKIE = "paltop_session"
PORT = 8765                    # the router keeps 8443 for its own admin page
FAIL_WINDOW = 600              # seconds
FAIL_PER_ADDR = 5              # failed logins per address per window
FAIL_TOTAL = 30                # failed logins from anywhere per window
MIN_PASSWORD = 6
PER_PAGE = 50                  # Pals per page in the list
REGISTER_MINUTES = 15          # to finish a new user after the server password
SCRYPT = (2 ** 14, 8, 1)       # n, r, p: about 16 MB and 50 ms per hash
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/bg.js": ("bg.js", "text/javascript; charset=utf-8"),
          "/music.js": ("music.js", "text/javascript; charset=utf-8")}
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; "
                               "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                               "frame-ancestors 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def _pal_name(sid, names):
    return pal_display(sid, names)[1]


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(pw):
    n, r, p = SCRYPT
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (n, r, p, salt.hex(), h.hex())


def check_password(pw, stored):
    try:
        kind, n, r, p, salt, want = stored.split("$")
        if kind != "scrypt":
            return False
        h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n),
                           r=int(r), p=int(p), dklen=len(want) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(h.hex(), want)


_DUMMY = hash_password(secrets.token_urlsafe(16))


class Site:
    """Everything the handlers share: password, sessions, save data."""

    def __init__(self, container, secure):
        self.container = container
        self.secure = secure
        self.lock = threading.Lock()
        self.fails = deque()                     # (time, address)
        self.tickets = {}                        # step one of a new user
        self.seen = {}                           # who is on the page now
        self._pw, self._pw_at = None, 0.0
        # discovery only: finds the save folder and the game's name tables.
        # sample() is never called; the REST API is used only for the
        # chat's announce (chat_send).
        self.src = palsource.PalSource(container=container, use_net=False,
                                       db_path=DB)
        self.db = self.src.db                    # users and sessions
        try:
            os.remove(OLD_SESSIONS)              # server-password logins
        except OSError:
            pass
        self._rank = {}
        self._breeder = None

    # -- auth --------------------------------------------------------------

    def password(self):
        """The server's join password, re-read every 5 minutes so a change
        in docker-compose.yml takes effect without restarting the site."""
        if time.monotonic() - self._pw_at > 300 or self._pw is None:
            out = subprocess.run(
                ["docker", "inspect", "-f",
                 "{{range .Config.Env}}{{println .}}{{end}}", self.container],
                capture_output=True, text=True, timeout=10).stdout
            pw = None
            for line in out.splitlines():
                if line.startswith("SERVER_PASSWORD="):
                    pw = line.split("=", 1)[1].strip().strip('"').strip("'")
            if pw or self._pw is None:
                self._pw = pw
            self._pw_at = time.monotonic()
        return self._pw

    def limited(self, addr):
        now = time.time()
        with self.lock:
            while self.fails and now - self.fails[0][0] > FAIL_WINDOW:
                self.fails.popleft()
            mine = sum(1 for _, a in self.fails if a == addr)
            return mine >= FAIL_PER_ADDR or len(self.fails) >= FAIL_TOTAL

    def _failed(self, addr, why):
        with self.lock:
            self.fails.append((time.time(), addr))
        log("%s from %s" % (why, addr))

    def players(self):
        """{name key: player} for every player with a character in the
        save: {name, pid, level, pals}. pid is the id the Pals are kept by."""
        out = {}
        for p in self.overview().get("players") or []:
            if p.get("name") and p.get("pid") and p["pid"] != "None":
                key = self.db.user_key(p["name"])
                if key in out and (out[key]["pals"] or 0) >= (p.get("pals") or 0):
                    continue          # the same player twice: the one with Pals
                out[key] = {"name": p["name"], "pid": p["pid"],
                            "level": p.get("level"), "pals": p.get("pals")}
        return out

    def _session(self, name, addr, how):
        token = secrets.token_urlsafe(32)
        self.db.add_web_session(_token_hash(token), name,
                                time.time() + SESSION_DAYS * 86400)
        log("%s: %s from %s" % (how, name, addr))
        return token

    def login(self, addr, name, given):
        """A session token, None for a wrong name or password."""
        user = self.db.web_user(name)
        # hash even for an unknown name, so the time does not tell
        ok = check_password(given, user["pw"] if user else _DUMMY)
        if not user or not ok:
            self._failed(addr, "login failed for %r" % name[:40])
            return None
        return self._session(user["name"], addr, "login")

    def register_start(self, addr, server_pw):
        """Step one of making a user: the server password. Returns a ticket
        for step two (valid REGISTER_MINUTES, from this address), or None."""
        pw = self.password()
        if not pw or not hmac.compare_digest(server_pw.encode(), pw.encode()):
            self._failed(addr, "new user with a wrong server password")
            return None
        ticket = secrets.token_urlsafe(24)
        now = time.time()
        with self.lock:
            self.tickets = {k: v for k, v in self.tickets.items()
                            if v[0] > now}
            self.tickets[_token_hash(ticket)] = (
                now + REGISTER_MINUTES * 60, addr)
        return ticket

    def register(self, addr, ticket, name, given):
        """Step two: (token, player, None) or (None, None, error message).
        The name must be a player on the server; the user is linked to it."""
        key = _token_hash(ticket or "")
        with self.lock:
            exp, where = self.tickets.get(key, (0, None))
        if exp < time.time() or where != addr:
            return None, None, "type the server password again first"
        player = self.players().get(self.db.user_key(name))
        if not player:
            return None, None, (
                "found no player called \"%s\" on the server - type your "
                "name exactly as it is in the game (you need to have played "
                "on the server)" % name[:40])
        if len(given) < MIN_PASSWORD:
            return None, None, ("choose a password of at least %d characters"
                                % MIN_PASSWORD)
        if given == self.password():
            return None, None, ("choose your own password, not the server "
                                "password")
        if not self.db.add_web_user(player["name"], hash_password(given),
                                    player["pid"]):
            return None, None, ("%s already has a user - log in, or ask the "
                                "admin if it is not yours" % player["name"])
        with self.lock:
            self.tickets.pop(key, None)
        return self._session(player["name"], addr, "new user"), player, None

    no_login = False                # --test-no-login, for looking at the page

    def user(self, token):
        """The logged-in user {key, name}, or None."""
        if self.no_login:
            return {"key": "", "name": "test"}
        if not token:
            return None
        return self.db.web_session(_token_hash(token))

    def me(self, user):
        """Who is logged in, and the player their user is linked to. A user
        made before linking existed is linked now (linked_now: tell them)."""
        if self.no_login:                   # PALTOP_TEST_PLAYER: act as them
            p = self.players().get(self.db.user_key(
                os.environ.get("PALTOP_TEST_PLAYER") or ""))
            return {"name": "test", "pid": p and p["pid"], "player": p}
        now = False
        pid = user.get("player")
        if not pid:
            p = self.players().get(user["key"])
            if p and self.db.link_web_user(user["name"], p["pid"]):
                pid, now = p["pid"], True
                log("linked user %s to its player" % user["name"])
        player = next((p for p in self.players().values()
                       if p["pid"] == pid), None) if pid else None
        return {"name": user["name"], "pid": pid, "player": player,
                "linked_now": now}

    def logout(self, token):
        if token:
            self.db.remove_web_session(_token_hash(token))

    # -- data --------------------------------------------------------------

    def overview(self):
        try:
            with open(SNAPSHOT) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {"error": "no data yet - is the paltop collector running?"}
        data["stale"] = time.time() - (data.get("t") or 0) > 120
        return data

    def _saves(self):
        saves = self.src.saves
        if saves:
            with self.lock:
                saves.refresh()
        return saves

    def _game(self):
        return self.src.gamedata or {}

    def _player_pals(self, pid):
        saves = self._saves()
        if not saves:
            return []
        return [p for p in saves.pals if (p.get("owner") or "") == pid]

    def _owner_level(self, pid):
        saves = self._saves()
        for p in (saves.players if saves else []):
            if p.get("uid") == pid:
                return p.get("level")
        return None

    def ranking(self, pid, goal, at_level):
        saves = self._saves()
        key = (pid, goal, at_level, saves.loaded_at if saves else 0)
        with self.lock:
            hit = self._rank.get(key)
        if hit is None:
            lvl = self._owner_level(pid) if at_level else None
            hit = palteam.ranked(self._game(), self._player_pals(pid), goal,
                                 self.src.elements,
                                 (lambda p: max(p["level"], lvl))
                                 if lvl else None)
            with self.lock:
                if len(self._rank) > 64:
                    self._rank.clear()
                self._rank[key] = hit
        return hit

    def _row(self, p, loc, score=None):
        names = self.src.pal_names
        alpha, name = pal_display(p["species"], names)
        part = palpals.partner(self._game(), p["species"], p["rank"], names)
        return {"id": p.get("id"), "name": p["nickname"] or name,
                "species": name, "alpha": alpha, "lucky": p["lucky"],
                "element": element_of(p["species"], self.src.elements),
                "els": palpals.elements_of(p["species"], self.src.elements),
                "partner": part["name"], "partner_tags": part["tags"],
                "level": p["level"], "gender": p["gender"],
                "iv": [p["hp_iv"], p["atk_iv"], p["def_iv"]],
                "stars": max(0, p["rank"] - 1), "where": loc[0],
                "score": score}

    SORTS = ("best", "attack", "defense", "tank", "fighter", "work", "level",
             "IVs", "name", "where")
    FILTERS = ("all", "team", "Palbox", "bases", "lucky + alpha")

    def pals(self, pid, sort, flt, at_level, text="", page=0,
             per_page=PER_PAGE, skills=False):
        """One page of a player's Pals: {pals, total, page, pages}."""
        saves = self._saves()
        game = self._game()
        bases_raw = saves.base_camps if saves else []
        mine = []
        for p in self._player_pals(pid):
            loc = palpals.location(p, saves.containers, bases_raw)
            if flt == "team" and loc[0] != "team" or \
                    flt == "Palbox" and loc[0] != "box" or \
                    flt == "bases" and not loc[0].startswith("base") or \
                    flt == "lucky + alpha" and not (
                        p["lucky"] or p["species"][:5].upper() == "BOSS_"):
                continue
            if text and not palpals.matches(game, p, text, self.src.pal_names,
                                            self.src.elements, skills):
                continue
            mine.append((p, loc))
        scores = {}
        if sort in palteam.SORT_GOALS:
            ranking = self.ranking(pid, sort, at_level)
            top = ranking[0][1]["score"] if ranking else 1.0
            order = {id(q): i for i, (q, _) in enumerate(ranking)}
            scores = {id(q): round(100.0 * ev["score"] / top)
                      for q, ev in ranking}
            mine.sort(key=lambda t: order.get(id(t[0]), 10**6))
        elif sort == "best":
            mine.sort(key=lambda t: palpals.score(t[0], game), reverse=True)
        elif sort == "level":
            mine.sort(key=lambda t: -t[0]["level"])
        elif sort == "IVs":
            mine.sort(key=lambda t: -sum((t[0]["hp_iv"], t[0]["atk_iv"],
                                          t[0]["def_iv"])))
        elif sort == "name":
            mine.sort(key=lambda t: pal_display(t[0]["species"],
                                                self.src.pal_names)[1])
        else:
            mine.sort(key=lambda t: t[1][0])
        pages = max(1, -(-len(mine) // per_page))
        page = min(max(0, page), pages - 1)
        return {"pals": [self._row(p, loc, scores.get(id(p)))
                         for p, loc in mine[page * per_page:
                                            (page + 1) * per_page]],
                "total": len(mine), "page": page, "pages": pages,
                "per_page": per_page}

    def pal(self, pid, pal_id, sort, at_level):
        saves = self._saves()
        game = self._game()
        mine = self._player_pals(pid)
        p = next((q for q in mine if q.get("id") == pal_id), None)
        if p is None:
            return {"error": "no such pal"}
        names, els = self.src.pal_names, self.src.elements
        info = palpals.species_info(game, p["species"])
        loc = palpals.location(p, saves.containers, saves.base_camps)
        key = palpals.species_key(game, p["species"]) or ""
        pair = els.get(key) or (element_of(p["species"], els), "None")
        skills = game.get("skills") or {}
        out = self._row(p, loc)
        out.update({
            "elements": [palpals.ELEMENT_NAMES.get(e, e) for e in pair
                         if e and e != "None"],
            "nickname": p["nickname"],
            "dex": info.get("zukan") if (info.get("zukan") or -1) >= 0
            else None,
            "hp": p["hp"], "trust": p["friendship"], "place": loc[1],
            "souls": p["soul"], "caught": p["caught"],
            "summon_note": palteam.summon_note(p["species"]),
            "passives": [dict(palpals.passive(game, q),
                effects=palpals.effect_texts(game, q),
                desc=palpals.passive_description(game, q)) for q in
                p["passives"]],
            "bonus": {k: v for k, v in palpals.bonuses(game, p).items() if v},
            "skills": [skills.get(q) or {"name": q, "element": "Normal",
                                         "power": 0, "cooldown": 0}
                       for q in p["skills"]],
            "also": [skills.get(q, {}).get("name", q) for q in p["learned"]
                     if q not in p["skills"]],
            "partner_skill": dict({k: v for k, v in palpals.partner(
                game, p["species"], p["rank"], names).items()
                if k in ("name", "text", "tags")},
                # the whole 0-4 star scale, this Pal's own number marked
                scaled=palpals.partner_scaled(game, p["species"], names,
                                              mark=p["rank"], rich=True)),
            "work": sorted([[palpals.WORK_NAMES.get(k, k), v] for k, v in
                            (info.get("work") or {}).items()],
                           key=lambda t: -t[1]),
            "status": [x for x in (
                p["sick"] and "sick: %s" % (self.src.conditions or {}).get(
                    p["sick"], p["sick"]),
                p["hunger"] and "starving",
                p["sanity"] is not None and p["sanity"] < 50 and
                "sanity %d" % p["sanity"]) if x],
        })
        if sort in palteam.SORT_GOALS:
            ranking = self.ranking(pid, sort, at_level)
            place = next((i for i, (q, _) in enumerate(ranking) if q is p),
                         None)
            if place is not None:
                ev = ranking[place][1]
                out["role"] = {"goal": palteam.GOAL_LABEL[sort],
                               "place": place + 1, "of": len(ranking),
                               "why": ev["why"], "good": ev["good"],
                               "bad": ev["bad"]}
        if self._breeder is None and game:
            self._breeder = palpals.Breeder(game)
        if self._breeder:
            out["breeding"] = [
                {"child": pal_display(c, names)[1],
                 "element": element_of(c, els),
                 "mate": mate["nickname"] or pal_display(mate["species"],
                                                         names)[1],
                 "level": mate["level"],
                 "iv": [mate["hp_iv"], mate["atk_iv"], mate["def_iv"]]}
                for c, mate in self._breeder.options(p, mine)]
        return out

    def _pool(self):
        """Passives worth offering in the planner, cached per save load."""
        saves = self._saves()
        key = saves.loaded_at if saves else 0
        if getattr(self, "_pool_key", None) != key:
            self._pool_cache = palteam.passive_pool(
                self._game(), saves.pals if saves else [])
            self._pool_key = key
        return self._pool_cache

    def _passive_row(self, pid, pool):
        game = self._game()
        ps = palpals.passive(game, pid)
        return {"id": pid, "name": ps["name"], "rank": ps["rank"],
                "effects": palpals.effect_texts(game, pid),
                "desc": palpals.passive_description(game, pid),
                "src": pool.get(pid) or ["this Pal"]}

    WEB_TABS = {"overview", "pals", "palpedia", "bases", "crashes",
                "battles"}
    SEEN_FOR = 75              # seconds a page counts as open after a ping

    def presence(self, user, tab, focused):
        """Note that `user` has the page open on `tab`, and list everyone
        who does: [{name, tab, away, for}] (away: the page is open but not
        in front). Kept in memory only: it is about right now."""
        now = time.time()
        with self.lock:
            me = self.seen.get(user["key"]) or {"since": now}
            self.seen[user["key"]] = {
                "name": user["name"], "since": me["since"], "at": now,
                "tab": tab if tab in self.WEB_TABS else None,
                "away": not focused}
            for k in [k for k, v in self.seen.items()
                      if now - v["at"] > self.SEEN_FOR]:
                del self.seen[k]
            out = [{"name": v["name"], "tab": v["tab"], "away": v["away"],
                    "for": round(now - v["since"]), "me": k == user["key"]}
                   for k, v in self.seen.items()]
        out.sort(key=lambda v: (v["away"], v["name"].lower()))
        return {"web": out}

    def _game_items(self):
        """{item id: {name, desc, type, kind, rarity, price, weight}} for
        the items partner skills and passives mention."""
        return self._game().get("items") or {}

    def battle(self, size=None):
        """A battle between two players' Pals for the page's background."""
        saves = self._saves()
        players = {p["pid"]: p["name"] for p in
                   self.overview().get("players") or [] if p.get("pid")}
        out = palbattle.battle(self._game(), saves.pals if saves else [],
                               players, self.src.elements, self.src.pal_names,
                               containers=saves.containers if saves else {},
                               size=size)
        return out or {"error": "needs two players with Pals"}

    # -- duels: a battle you fight yourself ------------------------------------

    duels = {}                   # user key -> palduel.Duel
    duel_lock = threading.Lock()

    def _duel(self, user):
        with self.duel_lock:
            now = time.time()
            for k in [k for k, d in self.duels.items()
                      if now - d.touched > palduel.IDLE]:
                del self.duels[k]
            return self.duels.get(user["key"])

    def duel_choices(self, user):
        """Your Pals to pick a team from."""
        me = self.me(user).get("pid")
        return {"pals": palduel.choices(self._game(), self._player_pals(me),
                                        self.src.elements,
                                        self.src.pal_names) if me else [],
                "linked": bool(me)}

    def duel_start(self, user, pal_ids):
        me = self.me(user).get("pid")
        if not me:
            return 400, {"error": "your user is not linked to a player"}
        saves = self._saves()
        duel, err = palduel.start(
            self._game(), saves.pals if saves else [], self._player_names(),
            self.src.elements, saves.containers if saves else {}, me,
            [str(i) for i in pal_ids][:3], self.src.pal_names)
        if not duel:
            return 400, {"error": err}
        with self.duel_lock:
            self.duels[user["key"]] = duel
        return 200, duel.view(full=True)

    def duel_act(self, user, skill, target):
        duel = self._duel(user)
        if not duel:
            return 404, {"error": "no duel - start a new one"}
        with self.duel_lock:
            answer, err = duel.act(str(skill or ""), target)
        if err:
            return 409, {"error": err}
        if answer["over"]:
            with self.duel_lock:
                self.duels.pop(user["key"], None)
        return 200, answer

    def duel_quit(self, user):
        with self.duel_lock:
            self.duels.pop(user["key"], None)
        return 200, {"ok": True}

    # -- boss planner --------------------------------------------------------

    BOSS_SHOW = 30               # of the player's Pals, best first

    def _bosses(self):
        return palboss.boss_list(self._game(), self.src.pal_names)

    def _player_names(self):
        return {p["pid"]: p["name"] for p in
                self.overview().get("players") or [] if p.get("pid")}

    def bosses(self, pid):
        """Every boss, with who has beaten it and whether `pid` has."""
        saves = self._saves()
        beaten = palboss.beaten_by(saves.containers if saves else {},
                                   self._player_names())
        mine = set(palboss.beaten_by(
            {pid: ((saves.containers if saves else {}).get(pid) or {})},
            {pid: "me"}))
        return {"bosses": [dict(b, beaten=sorted(beaten.get(b["id"], []),
                                                 key=str.lower),
                                mine=b["id"] in mine)
                           for b in self._bosses()],
                "level": self._owner_level(pid) or 1,
                "hp_times": list(palboss.HP_TIMES)}

    def boss_plan(self, pid, bid, level=None, hp_times=1, hard=False):
        boss = next((b for b in self._bosses() if b["id"] == bid), None)
        if not boss:
            return {"error": "no such boss"}
        if not level:
            level = boss["level"] or self._owner_level(pid) or 1
        level = max(1, min(80, level))
        if hp_times not in palboss.HP_TIMES:
            hp_times = 1
        saves = self._saves()
        game = self._game()
        out = palboss.plan(game, boss, level, self._player_pals(pid),
                           self.src.elements, self.src.pal_names, hp_times,
                           hard=hard)
        beaten = palboss.beaten_by(saves.containers if saves else {},
                                   self._player_names())
        card = out["boss"]
        f = card.pop("fighter")
        card.update(hp=f["hp"], atk=f["atk"], defense=f["def"],
                    skills=[{"name": s["name"], "element": s["element"],
                             "power": s["power"]} for s in f["skills"]],
                    beaten=sorted(beaten.get(bid, []), key=str.lower))
        rows = []
        for r in out["rows"][:self.BOSS_SHOW]:
            p = r.pop("pal")
            me = r.pop("fighter")
            loc = palpals.location(p, saves.containers, saves.base_camps)
            rows.append(dict(self._row(p, loc), **r,
                             hp=me["hp"], skills=[s["name"] for s in
                                                  me["skills"]],
                             team=p.get("id") in out["team"]["ids"]))
        return {"boss": card, "rows": rows, "count": len(out["rows"]),
                "team_share": out["team"]["share"]}

    # -- chat ----------------------------------------------------------------

    chat_limit = palchat.Limiter()

    def chat(self, after=None):
        """The chat between the game and the page: messages after id
        `after`, or the newest 100."""
        rows = self.db.chat(after=after, limit=200 if after is not None
                            else 100)
        return {"messages": rows, "last": rows[-1]["id"] if rows else after,
                "max_len": palchat.MAX_LEN}

    def chat_send(self, user, text):
        """Send a message from the page to the game (REST announce) and keep
        it: (status, answer)."""
        text = palchat.clean(text)
        if not text:
            return 400, {"error": "type a message"}
        if self.no_login:
            return 403, {"error": "not from the test page"}
        name = user["name"]
        if not self.chat_limit.allow(user["key"]):
            return 429, {"error": "slow down - at most %d messages a minute"
                         % palchat.PER_MINUTE}
        rest = self.src.rest
        if not rest:
            return 503, {"error": "the server's REST API is not reachable"}
        ok, err = palchat.announce(rest.host, rest.port,
                                   self.src.env.get("ADMIN_PASSWORD", ""),
                                   palchat.for_game(name, text))
        if not ok:
            log("chat: sending for %s failed: %s" % (name, err))
            return 502, {"error": "the game did not take it - is the server "
                         "up?"}
        mid = self.db.add_chat(time.time(), "web", name, text)
        return 200, {"ok": True, "id": mid}

    # -- quests --------------------------------------------------------------

    def quests(self, me):
        """The quest list, with what each player has done and taken on."""
        saves = self._saves()
        players = self._player_names()
        out = palquests.entries(self._game(),
                                saves.containers if saves else {}, players)
        out["names"] = {pid: players[pid] for pid in out["players"]}
        out["me"] = me
        return out

    # -- traitpedia ----------------------------------------------------------

    _traits_cache = None

    def traitpedia(self, me):
        """Every trait: tier, effects, where to get it, what implanting it
        costs, and who on the server has it. Cached per save load."""
        saves = self._saves()
        key = (saves.loaded_at if saves else 0, id(self._game()))
        if not self._traits_cache or self._traits_cache[0] != key:
            players = self._player_names()
            self._traits_cache = (key, paltraits.entries(
                self._game(), saves.pals if saves else [], players,
                self.src.pal_names), players)
        _key, rows, players = self._traits_cache
        mine = {}
        for p in self._player_pals(me) if me else []:
            for pid in p.get("passives") or []:
                mine[pid] = mine.get(pid, 0) + 1
        return {"traits": [dict(t, mine=mine.get(t["id"], 0)) for t in rows],
                "players": sorted(players.values(), key=str.lower)}

    # -- Palpedia ------------------------------------------------------------

    PEDIA_SORTS = [["paldeck", "Paldeck number"], ["name", "name"],
                   ["rarity", "rarity"], ["combi", "breeding power"]] + \
        [[g, label] for g, label, _ in palteam.GOALS]
    PEDIA_SHOW = [["all", "all Pals"], ["found", "found on the server"],
                  ["missing", "not found yet"], ["mine", "caught by me"],
                  ["not_mine", "not caught by me yet"]]
    PEDIA_KIND = [["any", "any element or job"]] + \
        [["el_" + e, palpals.ELEMENT_NAMES[e]] for e in palteam.ELEMENTS] + \
        [["work_" + k, "base: " + v] for k, v in palpals.WORK_NAMES.items()]

    def _pedia(self):
        """The cached tables: entries, id map, recipes, goal ranks (per game
        data) and who found what (per save)."""
        game = self._game()
        saves = self._saves()
        key = (id(game), saves.loaded_at if saves else 0)
        c = getattr(self, "_pedia_cache", None)
        if c is None or c["game"] != id(game):
            es = palpedia.entries(game, self.src.pal_names)
            c = {"game": id(game), "entries": es,
                 "of": palpedia.entry_of(game, es),
                 "recipes": palpedia.recipes(game),
                 "ranks": palpedia.goal_ranks(game, self.src.elements),
                 "saves": None}
            c["by_id"] = {e["id"]: e for e in es}
            self._pedia_cache = c
        if c["saves"] != key:
            c["found"] = palpedia.found(saves, c["of"])
            c["saves"] = key
        return c

    def _pedia_row(self, e, c, me):
        f = c["found"].get(e["id"])
        mine = (f or {}).get("players", {}).get(me) or {}
        return {"id": e["id"], "no": "%03d%s" % (e["no"], e["suffix"]),
                "name": e["name"],
                "els": palpals.elements_of(e["id"], self.src.elements),
                "rarity": e["rarity"],
                "work": [[palpals.WORK_NAMES.get(k, k), n]
                         for k, n in sorted(e["work"].items(),
                                            key=lambda t: -t[1])],
                "found": bool(f), "caught": bool(mine.get("caught") or
                                                 mine.get("deck")),
                "owned": mine.get("owned", 0)}

    def _pedia_ref(self, c, ref, spoilers):
        """The Pal others are compared with, if it may be shown."""
        e = c["by_id"].get(ref or "")
        if not e or not spoilers and e["id"] not in c["found"]:
            return None
        return e

    def palpedia(self, me, sort, show, kind, text, spoilers, page=0,
                 per_page=PER_PAGE, skills=False, ref=None):
        """One page of the Palpedia list. With `ref`, every row also says
        how it does against that Pal (for the sort's goal, or as an
        all-round fighter when the sort is not a goal)."""
        game, c = self._game(), self._pedia()
        sort = sort if sort in dict(self.PEDIA_SORTS) else "paldeck"
        show = show if show in dict(self.PEDIA_SHOW) else "all"
        kind = kind if kind in dict(self.PEDIA_KIND) else "any"
        els = self.src.elements
        rows, hidden = [], 0
        plain = sort == "paldeck" and kind == "any" and not text and \
            show in ("all", "missing")
        for e in c["entries"]:
            f = c["found"].get(e["id"])
            mine = (f or {}).get("players", {}).get(me) or {}
            caught = bool(mine.get("caught") or mine.get("deck"))
            if show == "found" and not f or show == "missing" and f or \
                    show == "mine" and not caught or \
                    show == "not_mine" and caught:
                continue
            if not f and not spoilers:
                hidden += 1
                if plain:               # a numbered gap, like the game's
                    rows.append((e, {"no": "%03d%s" % (e["no"], e["suffix"]),
                                     "hidden": True}))
                continue
            if kind.startswith("el_") and kind[3:] not in \
                    palpals.elements_of(e["id"], els):
                continue
            if kind.startswith("work_") and kind[5:] not in e["work"]:
                continue
            if text and not palpedia.matches(game, e, text, els, skills):
                continue
            rows.append((e, self._pedia_row(e, c, me)))
        ranks = c["ranks"].get(sort)
        if ranks:
            for e, r in rows:
                if not r.get("hidden"):
                    place, pct, _raw = ranks.get(e["id"], (None, 0, 0))
                    r["place"], r["score"] = place, pct
            rows.sort(key=lambda t: t[1].get("place") or 10 ** 6)
        elif sort == "name":
            rows.sort(key=lambda t: t[0]["name"].lower())
        elif sort == "rarity":
            rows.sort(key=lambda t: (-(t[0]["rarity"] or 0), t[0]["no"]))
        elif sort == "combi":
            rows.sort(key=lambda t: (t[0]["combi"] or 10 ** 6))
        pages = max(1, -(-len(rows) // per_page))
        page = min(max(0, page), pages - 1)
        shown = rows[page * per_page:(page + 1) * per_page]
        re_ = self._pedia_ref(c, ref, spoilers)
        vs_goal = sort if ranks else "fighter"
        if re_:
            table = c["ranks"].get(vs_goal) or {}
            base = (table.get(re_["id"]) or (0, 0, 0))[2]
            for e, r in shown:
                if not r.get("hidden"):
                    mine = (table.get(e["id"]) or (0, 0, 0))[2]
                    r["vs"] = mine / base - 1 if base else None
        return {"rows": [r for _, r in shown],
                "ref": {"id": re_["id"], "name": re_["name"],
                        "els": palpals.elements_of(re_["id"],
                                                   self.src.elements)}
                if re_ else None,
                "vs_goal": palteam.GOAL_LABEL.get(vs_goal, vs_goal),
                "total": len(rows), "page": page, "pages": pages,
                "per_page": per_page, "hidden": hidden,
                "found": len(c["found"]), "all": len(c["entries"]),
                "sorts": self.PEDIA_SORTS, "shows": self.PEDIA_SHOW,
                "kinds": self.PEDIA_KIND, "sort": sort, "show": show,
                "kind": kind, "ranked": bool(ranks)}

    def _firsts(self, c):
        return palpedia.first_finds(self.db.finds(),
                                    self.db.meta_get("finds_since") or 0)

    def pedia_race(self, me, spoilers):
        """The Paldeck race: who has found the most Pals, who found each
        first, who alone has one, and the latest finds."""
        c = self._pedia()
        players = {p["pid"]: p["name"] for p in
                   self.overview().get("players") or [] if p.get("pid")}
        firsts = self._firsts(c)
        since = self.db.meta_get("finds_since") or 0
        counts = palpedia.race_counts(c["found"], firsts)
        board = {pid: dict({"found": 0, "firsts": 0, "unique": 0,
                            "caught": 0}, **counts.get(pid, {}),
                           pid=pid, name=name, latest=None)
                 for pid, name in players.items()}
        for b in board.values():
            b["only"] = b.pop("unique")
        latest = []
        for f in self.db.finds():
            if f["pid"] not in board:
                continue
            if f["first_ts"] <= since + 3600:   # before paltop kept track:
                continue                        # no sure date
            e = c["by_id"].get(f["species"])
            if not e:
                continue
            first = firsts.get(f["species"])
            latest.append({"ts": f["first_ts"], "who": board[f["pid"]]["name"],
                           "pid": f["pid"], "id": e["id"], "name": e["name"],
                           "els": palpals.elements_of(e["id"],
                                                      self.src.elements),
                           "first": bool(first and f["pid"] in first[1])})
            b = board[f["pid"]]
            if not b["latest"] or f["first_ts"] > b["latest"]["ts"]:
                b["latest"] = {"ts": f["first_ts"], "name": e["name"],
                               "id": e["id"]}
        latest.sort(key=lambda x: -x["ts"])
        rows = sorted(board.values(), key=lambda b: (-b["found"], -b["firsts"],
                                                     b["name"].lower()))
        return {"board": rows, "all": len(c["entries"]),
                "server": len(c["found"]), "latest": latest[:25],
                "since": since, "me": me}

    def achievements(self, me):
        """Every player's achievements (the tier reached, the number behind
        it and the next threshold, and when each tier was first seen), a
        points table, the latest unlocks and the server's firsts. Worked out
        at most every 30 seconds (it reads everyone's Pals)."""
        saves = self._saves()
        key = (saves.loaded_at if saves else 0, int(time.time() // 30))
        hit = getattr(self, "_ach_cache", None)
        if not hit or hit[0] != key:
            hit = self._ach_cache = (key, self._achievements())
        return dict(hit[1], me=me)

    def _achievements(self):
        c = self._pedia()
        saves = self._saves()
        ov = self.overview().get("players") or []
        players = {p["pid"]: p for p in ov if p.get("pid")}
        counts = palpedia.race_counts(c["found"], self._firsts(c))
        extra = {pid: dict(level=p.get("level") or 0,
                           hours=(p.get("playtime") or 0) / 3600.0,
                           firsts=counts.get(pid, {}).get("firsts", 0),
                           unique=counts.get(pid, {}).get("unique", 0))
                 for pid, p in players.items()}
        since = self.db.meta_get("achievements_since") or 0
        when = {}
        for r in self.db.achievement_log():
            when[(r["pid"], r["key"], r["tier"])] = r["ts"]
        board, feed = [], []
        titles = {a[0]: (a[1], a[2]) for a in palachieve.ACHIEVEMENTS}
        for pid, rows in palachieve.compute(self._game(), saves,
                                            extra).items():
            if pid not in players:
                continue
            for r in rows:
                ts = when.get((pid, r["key"], r["tier"]))
                r["when"] = ts if ts and ts > since + 3600 else None
            board.append({"pid": pid, "name": players[pid]["name"],
                          "points": palachieve.points(rows),
                          "gold": sum(1 for r in rows if r["tier"] == 3),
                          "rows": rows})
        for (pid, key, tier), ts in when.items():
            if ts > since + 3600 and pid in players and key in titles:
                feed.append({"ts": ts, "who": players[pid]["name"],
                             "title": titles[key][0], "icon": titles[key][1],
                             "tier": palachieve.TIER_NAMES[tier - 1]})
        feed.sort(key=lambda f: -f["ts"])
        board.sort(key=lambda b: (-b["points"], -b["gold"], b["name"].lower()))
        by_uid = {u: p.get("name") for u, p in self.db.known_players().items()}
        firsts = [{"level": lv, "who": by_uid.get(uid) or "?", "ts": ts}
                  for lv, (uid, ts) in sorted(self.db.level_firsts(
                      palachieve.FIRST_LEVELS).items())]
        return {"board": board, "feed": feed[:30], "firsts": firsts,
                "since": since, "max": 3 * len(palachieve.ACHIEVEMENTS)}

    def palpedia_entry(self, me, eid, spoilers, ref=None):
        game, c = self._game(), self._pedia()
        e = c["by_id"].get(eid)
        if not e:
            return {"error": "no such Pal"}
        f = c["found"].get(eid)
        if not f and not spoilers:
            return {"hidden": True, "no": "%03d%s" % (e["no"], e["suffix"])}
        names, els = self.src.pal_names, self.src.elements
        players = {p["pid"]: p["name"] for p in
                   self.overview().get("players") or [] if p.get("pid")}
        part = palpals.partner(game, eid, 1, names)
        tops = {k: max(x[k] for x in c["entries"])
                for k in ("hp", "atk", "def", "craft")}

        def parent(sid):
            pe = c["by_id"].get(sid) or c["by_id"].get(
                c["of"].get(sid.lower(), ""))
            if not pe:
                return {"id": None, "name": _pal_name(sid, names)}
            ok = spoilers or pe["id"] in c["found"]
            return {"id": pe["id"] if ok else None,
                    "name": pe["name"] if ok else "???",
                    "els": palpals.elements_of(pe["id"], els) if ok else [],
                    "found": pe["id"] in c["found"]}
        pairs = c["recipes"].get(eid) or []
        mine_owned = {c["of"].get(p["species"].lower())
                      for p in self._player_pals(me)} if me else set()
        order = sorted(pairs, key=lambda t: (
            not t[2],                                    # special first
            -((t[0] in c["found"]) + (t[1] in c["found"])),
            -((t[0] in mine_owned) + (t[1] in mine_owned)), t[0] == t[1]))
        ranks = []
        for goal, label, _ in palteam.GOALS:
            if goal in ("breed", "synergy"):     # the same for every species
                continue                         # / the same as fighter
            place, pct, _raw = (c["ranks"].get(goal) or {}).get(
                eid, (None, 0, 0))
            if place and place <= 10:
                ranks.append([label, place, pct])
        ranks.sort(key=lambda t: t[1])
        row = self._pedia_row(e, c, me)
        re_ = self._pedia_ref(c, ref, spoilers)
        compare = None
        if re_ and re_["id"] != eid:
            pct = lambda a, b: a / b - 1 if a and b else None
            goals = []
            for goal, label, _ in palteam.GOALS:
                if goal in ("breed", "synergy"):
                    continue
                t = c["ranks"].get(goal) or {}
                a, b = t.get(eid, (None, 0, 0)), t.get(re_["id"], (None, 0, 0))
                # under 2% of the best is the filler score of a Pal that
                # cannot do it (no skill for the status, no such work)
                can_a, can_b = a[1] > 2, b[1] > 2
                if not can_a and not can_b:
                    continue
                goals.append([label, a[1], b[1],
                              pct(a[2], b[2]) if can_a and can_b else None,
                              a[0], b[0]])
            goals.sort(key=lambda g: -(g[3] if g[3] is not None else 99))

            def skill(sid):          # its partner skill at 1 and 5 stars
                one = palpals.partner(game, sid, 1, names)
                return {"name": one["name"],
                        "text": palpals.partner_scaled(game, sid, names,
                                                       rich=True),
                        "tags": one["tags"]}
            compare = {
                "partner": [skill(eid), skill(re_["id"])],
                "ref": {"id": re_["id"], "name": re_["name"],
                        "els": palpals.elements_of(re_["id"], els)},
                "stats": [[label, e[k], re_[k], pct(e[k], re_[k])]
                          for label, k in (("HP", "hp"), ("attack", "atk"),
                                           ("defense", "def"),
                                           ("work speed", "craft"))],
                "work": [[palpals.WORK_NAMES.get(k, k), e["work"].get(k, 0),
                          re_["work"].get(k, 0)]
                         for k in palpals.WORK_NAMES
                         if e["work"].get(k) or re_["work"].get(k)],
                "goals": goals}
        first = self._firsts(c).get(eid)
        players_ = {p["pid"]: p["name"] for p in
                    self.overview().get("players") or [] if p.get("pid")}
        return dict(row, **{
            "first": {"ts": first[0], "known": first[2],
                      "who": [players_.get(x, "?") for x in first[1]]}
            if first else None,
            "compare": compare,
            "stats": {k: e[k] for k in ("hp", "atk", "def", "craft")},
            "tops": tops, "male": e["male"], "combi": e["combi"],
            "partner": {"name": part["name"],
                        "text": palpals.partner_scaled(game, eid, names,
                                                       rich=True)},
            "skills": palpedia.learn_rows(game, eid),
            "recipes": [{"a": parent(a), "b": parent(b), "special": sp}
                        for a, b, sp in order[:16]],
            "recipe_count": len(pairs),
            "ranks": ranks[:8], "rank_level": palpedia.RANK_LEVEL,
            "server": [{"name": players.get(pid, "?"), "caught": r["caught"],
                        "deck": r["deck"], "owned": r["owned"]}
                       for pid, r in sorted(
                           ((f or {}).get("players") or {}).items(),
                           key=lambda t: -t[1]["caught"])],
            "owned_total": (f or {}).get("owned", 0)})

    def breeding(self, pid):
        """The breeding helper for one player: who to carry and keep at the
        base for alpha eggs, extra eggs, egg production and incubation, what
        is missing and how to get it, and which parents breed faster."""
        game, names = self._game(), self.src.pal_names
        saves = self._saves()
        mine = self._player_pals(pid)
        if self._breeder is None and game:
            self._breeder = palpals.Breeder(game)
        setup = palteam.breeding_setup(game, mine, self._breeder)
        owners = {p["pid"]: p["name"] for p in
                  (self.overview().get("players") or []) if p.get("pid")}
        loc = lambda p: palpals.location(p, saves.containers,
                                         saves.base_camps)
        disp = lambda sp: pal_display(sp, names)[1]
        mine_kinds = {palpals.base_species(p["species"]) for p in mine}
        roles = []
        for r in setup["roles"]:
            missing = []
            for sp in r["missing"]:
                others = {}
                for q in (saves.pals if saves else []):
                    if palpals.base_species(q["species"]) == sp and \
                            q.get("owner") and q.get("owner") != pid:
                        who = owners.get(q.get("owner"), "another player")
                        others[who] = others.get(who, 0) + 1
                missing.append({
                    "name": disp(sp),
                    "pairs": [[dict(self._row(a, loc(a)), gender=a["gender"]),
                               dict(self._row(b, loc(b)), gender=b["gender"])]
                              for s2, a, b in r["breed"] if s2 == sp],
                    "recipes": [[[disp(a), a in mine_kinds],
                                 [disp(b), b in mine_kinds]]
                                for a, b in r["recipes"].get(sp, [])],
                    "others": [[k, v] for k, v in sorted(
                        others.items(), key=lambda t: -t[1])]})
            roles.append({
                "label": r["label"], "what": r["what"], "where": r["where"],
                "species": [disp(sp) for sp in r["species"]],
                "have": [dict(self._row(p, loc(p)), now=v, max=vmax)
                         for v, vmax, p in r["have"][:5]],
                "missing": missing})
        return {"roles": roles, "parents": [dict(
            self._row(p, loc(p)), passives=[palpals.passive(game, x)["name"]
                                            for x in p["passives"]])
            for p in setup["parents"][:20]]}

    def _synergy_json(self, team, goal, ranking, locked):
        game, els, names = self._game(), self.src.elements, self.src.pal_names
        o = palteam.synergy_overview(game, team, els, goal, ranking, names,
                                     locked=set(locked))
        saves = self._saves()
        loc = lambda p: palpals.location(p, saves.containers,
                                         saves.base_camps)
        pair = lambda t: {"a": self._row(t[0], loc(t[0])),
                          "b": self._row(t[1], loc(t[1])),
                          "gain": t[2], "why": t[3]}
        return {"gain": o["gain"], "links": o["links"], "unused": o["unused"],
                "vs_fighter": o.get("vs_fighter"),
                "pairs": [pair(t) for t in o.get("pairs") or []],
                "best_pairs": [pair(t) for t in o.get("best_pairs") or []],
                "swaps": [{"slot": sw["slot"], "gain": sw["gain"],
                           "why": sw["why"],
                           "out": sw["out"]["nickname"] or pal_display(
                               sw["out"]["species"], names)[1],
                           "in": self._row(sw["in"], palpals.location(
                               sw["in"], saves.containers, saves.base_camps))}
                          for sw in o["swaps"]]}

    def _needs(self, pid, team):
        """Passives the team is counted with that its Pals do not have yet:
        [{name, for: [pal names], get: [where]}]."""
        game, names = self._game(), self.src.pal_names
        mine = self._player_pals(pid)
        want = {}
        for p in team:
            if not p or not p.get("planned"):
                continue
            for x in p["passives"]:
                if x not in (p.get("real_passives") or []):
                    want.setdefault(x, []).append(
                        p["nickname"] or pal_display(p["species"], names)[1])
        return [{"name": palpals.passive(game, x)["name"], "for": who,
                 "get": palteam.passive_sources(game, x, mine, "", names)}
                for x, who in sorted(want.items(), key=lambda t: -len(t[1]))]

    def plan(self, pid, pal_id, goal, at_level, planned, slot=None, text=""):
        """The passive planner for one Pal: its real passives, a planned set
        to try out, the best set for the goal, and where to get them."""
        if goal not in palteam.GOAL_LABEL:
            goal = "fighter"
        game, els, names = self._game(), self.src.elements, self.src.pal_names
        mine = self._player_pals(pid)
        p = next((q for q in mine if q.get("id") == pal_id), None)
        if p is None:
            return {"error": "no such pal"}
        pool = dict(self._pool())
        for x in p["passives"]:             # what it has can always be kept,
            pool.setdefault(x, ["this Pal"])  # Lucky included
        lvl = self._owner_level(pid) if at_level else None
        lvl = max(p["level"], lvl) if lvl else None
        planned = [x for x in planned if x in pool or x in p["passives"]]
        planned = (planned + [None] * palteam.MAX_PASSIVES)[
            :palteam.MAX_PASSIVES]
        real = p["passives"]
        ev_real = palteam.score_with(game, p, real, goal, els, lvl)
        ev_plan = palteam.score_with(game, p, planned, goal, els, lvl)
        best = palteam.best_passives(game, p, goal, els, pool, lvl)
        ev_best = palteam.score_with(game, p, best, goal, els, lvl)

        def stats(ev):
            st = ev["st"]
            return {"hp": round(st["hp"]), "atk": round(st["atk"]),
                    "def": round(st["def"]), "craft": round(st["craft"])}
        wanted = [x for x in set(planned + best) if x and x not in real]
        out = {
            "goals": [[g, label] for g, label, _ in palteam.GOALS],
            "goal": goal, "level": lvl or p["level"],
            "pal": self._row(p, palpals.location(
                p, self._saves().containers, self._saves().base_camps)),
            "real": [self._passive_row(x, pool) for x in real],
            "planned": [self._passive_row(x, pool) if x else None
                        for x in planned],
            "best": [self._passive_row(x, pool) for x in best],
            "score": {"real": ev_real["score"], "planned": ev_plan["score"],
                      "best": ev_best["score"]},
            "why": {"real": ev_real["why"], "planned": ev_plan["why"],
                    "best": ev_best["why"]},
            "stats": {"real": stats(ev_real), "planned": stats(ev_plan),
                      "best": stats(ev_best)},
            "adds": palteam.contributions(game, p, goal, els, planned, lvl),
            "sources": {x: palteam.passive_sources(game, x, mine, p["species"],
                                                   names) for x in wanted},
            "cannot": ev_best["score"] <= 0,
        }
        if slot is not None and 0 <= slot < palteam.MAX_PASSIVES:
            low = (text or "").lower()
            match = (lambda x: low in palpals.passive(game, x)["name"].lower()
                     or low in palpals.passive_description(game, x).lower()) \
                if low else None
            base = ev_plan["score"] or 1.0
            out["slot"] = slot
            out["options"] = [dict(self._passive_row(x, pool),
                                   score=sc, delta=(sc - base) / base)
                              for x, sc in palteam.slot_options(
                                  game, p, goal, els, pool, planned, slot,
                                  lvl, match)]
        return out

    def _mode_ranking(self, pid, goal, at_level, mode, plans):
        """The ranking for the team builder with the passives the mode asks
        for: as they are, with the viewer's plans, or with the best set."""
        if mode not in ("plans", "best"):
            return self.ranking(pid, goal, at_level)
        saves = self._saves()
        game, els = self._game(), self.src.elements
        lvl = self._owner_level(pid) if at_level else None
        level_for = (lambda p: max(p["level"], lvl)) if lvl else None
        key = (pid, goal, at_level, mode, saves.loaded_at if saves else 0,
               tuple(sorted((k, tuple(v)) for k, v in (plans or {}).items()))
               if mode == "plans" else None)
        with self.lock:
            hit = self._rank.get(key)
        if hit is None:
            pals = palteam.what_if(game, self._player_pals(pid), goal, els,
                                   mode, plans, self._pool(), level_for)
            hit = palteam.ranked(game, pals, goal, els, level_for)
            with self.lock:
                self._rank[key] = hit
        return hit

    @staticmethod
    def _choose_order(ranking, csort):
        """The ranking re-sorted for the choose list (best first)."""
        key = {
            "hp": lambda t: t[1]["st"]["hp"],
            "atk": lambda t: t[1]["st"]["atk"],
            "def": lambda t: t[1]["st"]["def"],
            "tank": lambda t: t[1]["st"]["hp"] * t[1]["st"]["def"],
            "craft": lambda t: t[1]["st"]["craft"],
            "level": lambda t: t[0]["level"],
            "iv": lambda t: t[0]["hp_iv"] + t[0]["atk_iv"] + t[0]["def_iv"],
            "stars": lambda t: t[0].get("rank") or 1,
        }.get(csort)
        if not key:
            return ranking
        return sorted(ranking, key=lambda t: (-key(t), -t[1]["score"]))

    @staticmethod
    def _wish_order(game, ranking, els, team, wishes):
        """The ranking with the Pals that meet the most of the wishes the
        team does not meet yet first (then the ones it does), best first."""
        have = {w for w in wishes for p in team
                if p and palteam.wish_met(game, p, els, w)}
        open_ = [w for w in wishes if w not in have]

        def key(t):
            met = [w for w in wishes if palteam.wish_met(game, t[0], els, w)]
            return (-sum(1 for w in met if w in open_), -len(met),
                    -t[1]["score"])
        return sorted(ranking, key=key)

    COMPARE_GOALS = ("fighter", "attack", "defense", "tank", "work")

    def compare(self, pid, a, b, goal, at_level):
        """Two of a player's Pals side by side: stats, scores for the goal
        and the main roles, passives, skills, partner skill, work and the
        elements that hit them hard. `diff` is b against a, as a share."""
        game, els, names = self._game(), self.src.elements, self.src.pal_names
        saves = self._saves()
        mine = {p.get("id"): p for p in self._player_pals(pid)}
        pals = [mine.get(a), mine.get(b)]
        if not all(pals):
            return {"error": "no such pal"}
        lvl = self._owner_level(pid) if at_level else None
        goals = [goal] if goal in palteam.GOAL_LABEL else []
        goals += [g for g in self.COMPARE_GOALS if g not in goals]
        skills = game.get("skills") or {}
        out = []
        for p in pals:
            info = palpals.species_info(game, p["species"])
            loc = palpals.location(p, saves.containers, saves.base_camps)
            st = palteam.stats(game, p, lvl)
            my_el = palteam.elements(p, els)
            out.append(dict(
                self._row(p, loc),
                stats={"level": st["level"], "hp": round(st["hp"]),
                       "atk": round(st["atk"]), "def": round(st["def"]),
                       "craft": round(st["craft"])},
                scores={g: palteam.evaluate(game, p, g, els, lvl)["score"]
                        for g in goals},
                why=palteam.evaluate(game, p, goals[0], els, lvl)["why"]
                if goals else [],
                passives=[dict(palpals.passive(game, q),
                               effects=palpals.effect_texts(game, q),
                               desc=palpals.passive_description(game, q))
                          for q in p["passives"]],
                skills=[skills.get(q) or {"name": q, "element": "Normal",
                                          "power": 0, "cooldown": 0}
                        for q in p["skills"]],
                partner_skill=dict({k: v for k, v in palpals.partner(
                    game, p["species"], p["rank"], names).items()
                    if k in ("name", "text", "tags")},
                    scaled=palpals.partner_scaled(game, p["species"], names,
                                                  mark=p["rank"], rich=True)),
                work=sorted([[palpals.WORK_NAMES.get(k, k), v] for k, v in
                             (info.get("work") or {}).items()],
                            key=lambda t: -t[1]),
                weak=[palpals.ELEMENT_NAMES.get(e, e) for e in palteam.ELEMENTS
                      if palteam.multiplier(e, my_el) > 1],
                resists=[palpals.ELEMENT_NAMES.get(e, e)
                         for e in palteam.ELEMENTS
                         if palteam.multiplier(e, my_el) < 1],
                summon_note=palteam.summon_note(p["species"])))
        return {"pals": out, "goal": goals[0] if goals else None,
                "goal_labels": {g: palteam.GOAL_LABEL[g] for g in goals},
                "level": lvl}

    CHOOSE_SORTS = [["score", "score for the goal"],
                    ["wishes", "meets the most wishes"], ["hp", "HP"],
                    ["atk", "attack"], ["def", "defense"],
                    ["tank", "HP x defense"], ["craft", "work speed"],
                    ["level", "level"], ["iv", "IVs (sum)"],
                    ["stars", "condenser stars"]]
    CHOOSE_LIMIT = 40

    @staticmethod
    def _stat_json(ev):
        st = ev["st"]
        return {"hp": round(st["hp"]), "atk": round(st["atk"]),
                "def": round(st["def"]), "craft": round(st["craft"]),
                "raw": ev["score"]}

    def team(self, pid, goal, at_level, locks, choose=None,
             include_hard=False, text="", mode="real", plans=None,
             skills=False, fill=True, csort="score", wishes=()):
        if goal not in palteam.GOAL_LABEL:
            goal = palteam.GOALS[0][0]
        game, els = self._game(), self.src.elements
        ranking, left_out = palteam.usable(
            self._mode_ranking(pid, goal, at_level, mode, plans), include_hard)
        by_id = {p.get("id"): p for p, _ in ranking}
        locked = {i: by_id[pal] for i, pal in locks.items() if pal in by_id}
        wishes = [w for w in wishes if w in palteam.WISHES]
        unmet = []
        if fill and wishes:
            team, unmet = palteam.build_wishes(ranking, goal, els, wishes,
                                               locked=locked, game=game)
        elif fill:
            team = palteam.build(ranking, goal, els, locked=locked, game=game)
        else:                     # only what the user picked; the rest empty
            team = [locked.get(i) for i in range(palteam.TEAM_SIZE)]
        # what paltop would put in the empty slots for the wishes and the goal
        suggested = None
        if wishes and not fill and None in team:
            sug, unmet = palteam.build_wishes(ranking, goal, els, wishes,
                                              locked=locked, game=game)
            suggested = {
                "slots": [None if p is None or i in locked else {
                    "id": p.get("id"),
                    "name": p["nickname"] or pal_display(
                        p["species"], self.src.pal_names)[1],
                    "element": element_of(p["species"], els),
                    "level": p["level"],
                    "wishes": [w for w in wishes
                               if palteam.wish_met(game, p, els, w)]}
                    for i, p in enumerate(sug)],
                "report": palteam.wish_report(game, sug, els, wishes)}
        synergy = palteam.team_synergy(game, team, els, goal,
                                       self.src.pal_names) \
            if not goal.startswith("work") else [[] for _ in team]
        evs = {id(p): ev for p, ev in ranking}
        top = ranking[0][1]["score"] if ranking else 1.0
        saves = self._saves()
        slots = []
        for i, p in enumerate(team):
            if p is None:
                slots.append(None)
                continue
            ev = evs[id(p)]
            loc = palpals.location(p, saves.containers, saves.base_camps)
            hint = None
            low = min(p["hp_iv"], p["atk_iv"], p["def_iv"])
            if low >= 90 and ev["bad"]:
                hint = "great IVs held back by its traits - breed it with a " \
                       "Pal that has good ones"
            elif ev["good"] and low < 50:
                hint = "good traits but weak IVs - breed it with a high-IV " \
                       "Pal of the same species"
            hint = palteam.drain_hint(game, p, team, els, goal,
                                      self.src.pal_names) or hint
            slots.append(dict(self._row(p, loc, round(100 * ev["score"] /
                                                      top)),
                              why=ev["why"], good=ev["good"], bad=ev["bad"],
                              traits=[palpals.passive(game, q)["name"]
                                      for q in p["passives"]],
                              hint=hint, manual=i in locked,
                              synergy=synergy[i],
                              passives=[palpals.passive(game, q)["name"]
                                        for q in p["passives"]],
                              changed=[palpals.passive(game, q)["name"]
                                       for q in p["passives"]
                                       if q not in (p.get("real_passives")
                                                    or p["passives"])],
                              planned=bool(p.get("planned")),
                              stats=self._stat_json(ev),
                              wishes=[w for w in wishes
                                      if palteam.wish_met(game, p, els, w)]))
        weak = palteam.weaknesses(team, els)
        members = [p for p in team if p]
        lvl = self._owner_level(pid)
        owned = {pal_display(p["species"], self.src.pal_names)[1]
                 for p, _ in ranking}
        ideal = [pal_display(sp, self.src.pal_names)[1] for sp in
                 palteam.best_species(game, goal, els, level=lvl or 60,
                                      include_hard=include_hard)]
        return {
            "goals": [[g, label] for g, label, _ in palteam.GOALS],
            "goal": goal, "level": lvl, "slots": slots,
            "elements": sorted({palpals.ELEMENT_NAMES.get(e, e)
                                for p in members
                                for e in palteam.elements(p, els)}),
            "weak": [[palpals.ELEMENT_NAMES.get(e, e), n, len(members)]
                     for e, n in sorted(weak.items(), key=lambda t: -t[1])
                     if n >= 2] if not goal.startswith("work") else [],
            "ideal": [[n, n in owned] for n in ideal],
            "synergy": self._synergy_json(team, goal, ranking, locked),
            "status": palteam.status_info(game, team, palteam.status_of(goal),
                                          self.src.pal_names)
            if palteam.status_of(goal) else None,
            "locked": sorted(locked),
            "mode": mode, "modes": [[m, palteam.MODE_LABEL[m]]
                                    for m in palteam.PASSIVE_MODES],
            "needs": self._needs(pid, team),
            "left_out": left_out, "include_hard": include_hard,
            "choose": choose, "fill": fill, "csort": csort,
            "wishes": palteam.wish_report(game, team, els, wishes),
            "wish_unmet": [[w, palteam.WISHES[w][0]] for w in unmet],
            "wish_options": [[k, label, group] for k, (label, group)
                             in palteam.WISHES.items()],
            "suggested": suggested,
            "recommend": [dict(
                self._row(r["pal"], palpals.location(
                    r["pal"], saves.containers, saves.base_camps),
                    round(100 * r["ev"]["score"] / top)),
                share=r["share"], reasons=r["reasons"], wishes=r["wishes"],
                stats=self._stat_json(r["ev"]))
                for r in palteam.recommend(game, ranking, team, els, goal,
                                           wishes, self.src.pal_names)],
            "choose_sorts": self.CHOOSE_SORTS,
            "choices": [dict(
                self._row(c["pal"], palpals.location(
                    c["pal"], saves.containers, saves.base_camps),
                    round(100 * c["ev"]["score"] / top)),
                why=c["ev"]["why"], good=c["ev"]["good"], bad=c["ev"]["bad"],
                in_slot=c["in_slot"], same_species=c["same_species"],
                gains=[palpals.ELEMENT_NAMES.get(e, e) for e in c["gains"]],
                loses=[palpals.ELEMENT_NAMES.get(e, e) for e in c["loses"]],
                weak=[[palpals.ELEMENT_NAMES.get(e, e), n]
                      for e, n in c["weak"]],
                stats=self._stat_json(c["ev"]),
                covers=[w for w in wishes
                        if palteam.wish_met(game, c["pal"], els, w)])
                for c in palteam.candidates(
                    self._wish_order(game, ranking, els, team, wishes)
                    if csort == "wishes" else
                    self._choose_order(ranking, csort), goal, els, team,
                    choose, limit=self.CHOOSE_LIMIT,
                    match=(lambda p: palpals.matches(
                        game, p, text, self.src.pal_names, els, skills))
                    if text else None)]
            if choose is not None and 0 <= choose < len(team) else [],
        }


class Handler(BaseHTTPRequestHandler):
    site: Site = None
    server_version = "paltop"
    sys_version = ""

    def log_message(self, fmt, *args):          # quiet; logins are logged
        pass

    def _addr(self):
        return self.client_address[0]

    def _token(self):
        c = SimpleCookie(self.headers.get("Cookie") or "")
        return c[COOKIE].value if COOKIE in c else None

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in HEADERS.items():
            self.send_header(k, v)
        if self.site.secure:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _cookie(self, token, max_age):
        parts = ["%s=%s" % (COOKIE, token), "Path=/", "HttpOnly",
                 "SameSite=Strict", "Max-Age=%d" % max_age]
        if self.site.secure:
            parts.append("Secure")
        return {"Set-Cookie": "; ".join(parts)}

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path in STATIC:
            fname, ctype = STATIC[path]
            try:
                with open(os.path.join(WEB_DIR, fname), "rb") as fh:
                    return self._send(200, fh.read(), ctype)
            except OSError:
                return self._send(404, {"error": "missing"})
        if not path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        user = self.site.user(self._token())
        if not user:
            return self._send(401, {"error": "login"})
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        at_level = q.get("level") == "1"
        try:
            parts = path.strip("/").split("/")
            if path == "/api/me":
                return self._send(200, self.site.me(user))
            if path == "/api/presence":         # who is on the page
                return self._send(200, self.site.presence(
                    user, q.get("tab", ""), q.get("focus") == "1"))
            if path == "/api/items":            # for the item tooltips
                return self._send(200, self.site._game_items())
            if path == "/api/battle":           # ?size=1, 2 or 3 to pick
                size = q.get("size")
                return self._send(200, self.site.battle(
                    int(size) if size in ("1", "2", "3") else None))
            if path == "/api/achievements":
                return self._send(200, self.site.achievements(
                    self.site.me(user).get("pid")))
            if path == "/api/duel/pals":
                return self._send(200, self.site.duel_choices(user))
            if path == "/api/chat":
                after = q.get("after", "")
                return self._send(200, self.site.chat(
                    int(after) if after.isdigit() else None))
            if path == "/api/quests":
                return self._send(200, self.site.quests(
                    self.site.me(user).get("pid")))
            if path == "/api/traitpedia":
                return self._send(200, self.site.traitpedia(
                    self.site.me(user).get("pid")))
            if path == "/api/palpedia/race":
                return self._send(200, self.site.pedia_race(
                    self.site.me(user).get("pid"), q.get("spoilers") == "1"))
            if path == "/api/palpedia":
                page = q.get("page", "0")
                return self._send(200, self.site.palpedia(
                    self.site.me(user).get("pid"), q.get("sort", ""),
                    q.get("show", ""), q.get("kind", ""), q.get("q", "")[:40],
                    q.get("spoilers") == "1",
                    int(page) if page.isdigit() else 0,
                    skills=q.get("skills") == "1", ref=q.get("ref")))
            if len(parts) == 3 and parts[1] == "palpedia":
                return self._send(200, self.site.palpedia_entry(
                    self.site.me(user).get("pid"), parts[2],
                    q.get("spoilers") == "1", ref=q.get("ref")))
            if path == "/api/overview":
                return self._send(200, self.site.overview())
            if len(parts) == 4 and parts[1] == "players" and \
                    parts[3] == "pals":
                page = q.get("page", "0")
                return self._send(200, dict(
                    self.site.pals(parts[2], q.get("sort", "best"),
                                   q.get("filter", "all"), at_level,
                                   q.get("q", "")[:40],
                                   int(page) if page.isdigit() else 0,
                                   skills=q.get("skills") == "1"),
                    sorts=Site.SORTS, filters=Site.FILTERS))
            if len(parts) == 6 and parts[1] == "players" and \
                    parts[3] == "pals" and parts[5] == "plan":
                slot = q.get("slot")
                return self._send(200, self.site.plan(
                    parts[2], parts[4], q.get("goal", ""), at_level,
                    [x for x in (q.get("p") or "").split(",")][:4],
                    int(slot) if slot and slot.isdigit() else None,
                    q.get("q", "")[:40]))
            if len(parts) == 5 and parts[1] == "players" and \
                    parts[3] == "pals":
                return self._send(200, self.site.pal(
                    parts[2], parts[4], q.get("sort", "best"), at_level))
            if len(parts) == 4 and parts[1] == "players" and \
                    parts[3] == "bosses":
                return self._send(200, self.site.bosses(parts[2]))
            if len(parts) == 5 and parts[1] == "players" and \
                    parts[3] == "bosses":
                lv, hp = q.get("lv", ""), q.get("hp", "1")
                return self._send(200, self.site.boss_plan(
                    parts[2], parts[4], int(lv) if lv.isdigit() else None,
                    int(hp) if hp.isdigit() else 1, q.get("hard") == "1"))
            if len(parts) == 4 and parts[1] == "players" and \
                    parts[3] == "breeding":
                return self._send(200, self.site.breeding(parts[2]))
            if len(parts) == 4 and parts[1] == "players" and \
                    parts[3] == "team":
                locks = {}
                for item in (q.get("lock") or "").split(","):
                    if ":" in item:
                        slot, pal = item.split(":", 1)
                        if slot.isdigit():
                            locks[int(slot)] = pal
                choose = q.get("choose")
                plans = {}
                for item in (q.get("plans") or "").split(";")[:60]:
                    if ":" in item:
                        pal, ids = item.split(":", 1)
                        plans[pal] = [x for x in ids.split(".") if x][:4]
                return self._send(200, self.site.team(
                    parts[2], q.get("goal", ""), at_level, locks,
                    int(choose) if choose and choose.isdigit() else None,
                    q.get("hard") == "1", q.get("q", "")[:40],
                    q.get("mode", "real"), plans,
                    skills=q.get("skills") == "1",
                    fill=q.get("fill") == "1",
                    wishes=[w for w in (q.get("wish") or "").split(",")
                            if w][:20],
                    csort=q.get("csort", "score")[:10]))
            if len(parts) == 4 and parts[1] == "players" and \
                    parts[3] == "compare":
                return self._send(200, self.site.compare(
                    parts[2], q.get("a", ""), q.get("b", ""),
                    q.get("goal", ""), at_level))
        except Exception as exc:
            log("error on %s: %s: %s" % (path, type(exc).__name__, exc))
            return self._send(500, {"error": "server error"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        n = min(int(self.headers.get("Content-Length") or 0), 4096)
        raw = self.rfile.read(n) if n else b""
        if path in ("/api/login", "/api/register", "/api/register/start"):
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    body = {}
            except ValueError:
                body = {}
            field = lambda k: str(body.get(k) or "")[:200]
            name = " ".join(field("name").split())
            if self.site.limited(self._addr()):
                return self._send(429, {"error": "too many attempts - wait "
                                        "10 minutes"})
            if path == "/api/register/start":
                ticket = self.site.register_start(self._addr(),
                                                  field("server_password"))
                if not ticket:
                    return self._send(403, {"error": "wrong server password"})
                return self._send(200, {"ticket": ticket,
                                        "minutes": REGISTER_MINUTES})
            if not name:
                return self._send(400, {"error": "type your in-game name"})
            player = None
            if path == "/api/login":
                token, err = self.site.login(self._addr(), name,
                                             field("password")), \
                    "wrong name or password"
            else:
                token, player, err = self.site.register(
                    self._addr(), field("ticket"), name, field("password"))
            if not token:
                return self._send(403, {"error": err})
            return self._send(200, {"ok": True, "player": player},
                              extra=self._cookie(token,
                                                 SESSION_DAYS * 86400))
        if path in ("/api/duel", "/api/duel/act", "/api/duel/quit"):
            user = self.site.user(self._token())
            if not user:
                return self._send(401, {"error": "login"})
            if "application/json" not in (self.headers.get("Content-Type")
                                          or ""):
                return self._send(415, {"error": "JSON only"})
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            body = body if isinstance(body, dict) else {}
            if path == "/api/duel":
                ids = body.get("pals") if isinstance(body.get("pals"), list) \
                    else []
                code, answer = self.site.duel_start(user, ids)
            elif path == "/api/duel/act":
                target = body.get("target")
                code, answer = self.site.duel_act(
                    user, body.get("skill"),
                    target if isinstance(target, int) else None)
            else:
                code, answer = self.site.duel_quit(user)
            return self._send(code, answer)
        if path == "/api/chat":
            user = self.site.user(self._token())
            if not user:
                return self._send(401, {"error": "login"})
            # JSON only: a form on another site cannot send that
            if "application/json" not in (self.headers.get("Content-Type")
                                          or ""):
                return self._send(415, {"error": "JSON only"})
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            code, answer = self.site.chat_send(
                user, (body.get("text") if isinstance(body, dict) else "")
                or "")
            return self._send(code, answer)
        if path == "/api/logout":
            self.site.logout(self._token())
            return self._send(200, {"ok": True}, extra=self._cookie("", 0))
        return self._send(404, {"error": "not found"})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """One line instead of a traceback: most of these are browsers that
        have not yet accepted the self-signed certificate."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError)):
            key = (client_address[0], type(exc).__name__)
            now = time.time()
            if now - _quiet.get(key, 0) > 600:  # at most every 10 minutes
                _quiet[key] = now
                log("%s from %s: %s" % (type(exc).__name__, client_address[0],
                                        getattr(exc, "reason", None) or exc))
            return
        super().handle_error(request, client_address)


_quiet = {}


def ensure_cert():
    """A self-signed certificate for HTTPS, made once, valid 10 years."""
    cert = os.path.join(TLS_DIR, "cert.pem")
    key = os.path.join(TLS_DIR, "key.pem")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    os.makedirs(TLS_DIR, mode=0o700, exist_ok=True)
    host = socket.gethostname()
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-days", "3650", "-subj", "/CN=paltop",
         "-addext", "subjectAltName=DNS:%s,DNS:localhost,IP:127.0.0.1" % host,
         "-keyout", key, "-out", cert],
        check=True, capture_output=True, timeout=60)
    os.chmod(key, 0o600)
    return cert, key


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--http", action="store_true",
                    help="plain HTTP instead of HTTPS (only for testing)")
    ap.add_argument("-c", "--container", default="palworld-server")
    ap.add_argument("--test-no-login", action="store_true",
                    help=argparse.SUPPRESS)
    ap.add_argument("--list-users", action="store_true",
                    help="list the web page's users and exit")
    ap.add_argument("--remove-user", metavar="NAME",
                    help="remove a user (and log out their browsers), so the "
                         "name can be registered again")
    args = ap.parse_args(argv)
    if args.list_users or args.remove_user:
        from palstore import Store
        db = Store(DB)
        if args.remove_user:
            ok = db.remove_web_user(args.remove_user)
            print("removed" if ok else "no user called %r" % args.remove_user)
            return 0 if ok else 1
        fmt = lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) \
            if t else "never"
        for u in db.web_users():
            print("%-20s made %s   last login %s   %s" % (
                u["name"], fmt(u["created"]), fmt(u["last_login"]),
                "player %s" % u["player"] if u["player"] else "not linked"))
        return 0
    site = Site(args.container, secure=not args.http)
    if args.test_no_login:
        # only for a short look at the page from a browser on the LAN; never
        # on the public port, never on all interfaces
        if args.bind in ("0.0.0.0", "::") or args.port == PORT:
            print("--test-no-login needs a specific --bind and another port",
                  file=sys.stderr)
            return 1
        site.no_login = True
        log("TEST MODE: no login required")
    if not site.password():
        print("paltop web: the server has no SERVER_PASSWORD - refusing to "
              "start an open site", file=sys.stderr)
        return 1
    Handler.site = site
    httpd = Server((args.bind, args.port), Handler)
    httpd.daemon_threads = True
    if not args.http:
        cert, key = ensure_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        # the handshake happens on first read, in the connection's own
        # thread, so a slow or broken client cannot stall the others
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True,
                                       do_handshake_on_connect=False)
    log("paltop web on %s://%s:%d" % ("http" if args.http else "https",
                                      args.bind, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
