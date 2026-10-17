#!/usr/bin/env python3
"""
palcrash.py - crash log with an automatic investigation.

Every time the server stops answering, paltop's status log records it (the
background collector checks every 30 s). This module turns those records,
together with the game's own crash reports, into incidents, and for each one
works out what it can:

    what happened   hang (game thread froze), crash, host reboot, scheduled
                    or admin restart, or an outage with no known cause
    when            last sign of life, when the REST API stopped answering,
                    when the server was back, and whether autoheal did it
    who and where   the players online, where each was (in-game map
                    coordinates), how far they had moved, whether
                    they had just fast-travelled, joined or levelled up
    patterns        across all incidents: the same freeze point in the game
                    code, the same player, the same place

The game writes crash reports to Pal/Saved/Crashes and deletes them after two
days (CrashConfigPurgeDays=2), so read_reports() is run by the collector and
the results are kept in the database.

Nothing here writes to the server; it only reads files and paltop's database.
"""

from __future__ import annotations

import glob
import math
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# the game binary's frames in the crash report's call stack
FRAME_RX = re.compile(r"(\S+)!UnknownFunction\((0x[0-9a-fA-F]+)\)")
RELEASE_RX = re.compile(r'"release":\s*"([^"]+)"')

# in-game map coordinates from world units: the same transform the game's own
# map uses (world Y runs east, world X north; 1 map unit = 462.96 world units)
MAP_SCALE = 462.962962963
MAP_OFFSET_X = 123467.1611767
MAP_OFFSET_Y = 157664.55791065

LOOKBACK = 15 * 60            # seconds of history examined before an incident
FAST_TRAVEL_SPEED = 5000      # world units (cm) per second: faster = teleport
SAME_PLACE = 30000            # 300 m: "the same place" across incidents
REPORT_SLACK = 300            # a crash report this close to an outage belongs to it
SHARED_FRAMES = 3             # game frames in common to call two stacks alike

# freeze points already explained, by the call path they share. A stack
# matches when it has at least SHARED_FRAMES of these frames.
KNOWN_CAUSES = [
    {"frames": ["0x775e20b", "0x80f46eb", "0x82b7575", "0x88dbced",
                "0x889d424"],
     "cause": "Panthalus (Legendary Ocean King) boss fight in the Eternal "
              "Sea - hangs the game thread on build 1.0.5 (seen twice "
              "23.09.2026)"},
]

OUTAGE = {"timeout", "refused", "error"}

KIND_LABEL = {
    "hang": "HANG", "crash": "CRASH", "outage": "OUTAGE",
    "host_reboot": "HOST REBOOT", "scheduled": "SCHEDULED RESTART",
    "admin": "ADMIN RESTART",
}
PLANNED = {"host_reboot", "scheduled", "admin"}


# -- the game's crash reports ---------------------------------------------

def read_reports(data_dir):
    """Parse Pal/Saved/Crashes/*/CrashContext.runtime-xml."""
    out = []
    if not data_dir:
        return out
    for path in glob.glob(os.path.join(
            data_dir, "Pal/Saved/Crashes/*/CrashContext.runtime-xml")):
        try:
            root = ET.parse(path).getroot()
            ts = os.path.getmtime(path)
        except (OSError, ET.ParseError):
            continue

        def field(tag):
            el = root.find(".//" + tag)
            return (el.text or "").strip() if el is not None else ""
        msg = field("ErrorMessage")
        game = [off.lower() for mod, off in FRAME_RX.findall(msg)
                if mod.startswith("PalServer")]
        rel = RELEASE_RX.search(field("__sentry") or "")
        try:
            uptime = int(float(field("SecondsSinceStart") or 0))
            clients = int(field("NumClients") or 0)
        except ValueError:
            uptime, clients = None, None
        out.append({
            "guid": os.path.basename(os.path.dirname(path)),
            "ts": int(ts),
            "type": field("CrashType") or "Crash",
            "top": game[0] if game else None,
            "frames": game[:24],
            "hash": field("PCallStackHash")[:16],
            "uptime": uptime,
            "clients": clients,
            "release": rel.group(1) if rel else None,
            "message": msg.splitlines()[0][:200] if msg else "",
        })
    return out


def autoheal_restarts(logs):
    """Unix times autoheal restarted the server, from `docker logs -t`."""
    out = []
    for line in logs.splitlines():
        if "unhealthy" not in line or "Restarting" not in line:
            continue
        stamp = line.split(" ", 1)[0]
        try:                            # 2026-09-25T12:47:48.512167238Z
            dt = datetime.fromisoformat(stamp[:19] + "+00:00")
        except ValueError:
            continue
        out.append(int(dt.timestamp()))
    return out


def admin_restarts(path):
    """Unix times of restarts/shutdowns issued from paltop's admin menu."""
    out = []
    try:
        with open(path) as fh:
            lines = fh.read().splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] in ("restart", "shutdown", "stop"):
            try:
                out.append(int(datetime.fromisoformat(parts[0]).timestamp()))
            except ValueError:
                pass
    return out


# -- helpers ----------------------------------------------------------------

def map_coords(x, y):
    """World units -> the coordinates the in-game map shows."""
    return ((y - MAP_OFFSET_Y) / MAP_SCALE, (x + MAP_OFFSET_X) / MAP_SCALE)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _dt(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


def known_cause(frames):
    got = set(frames or [])
    for k in KNOWN_CAUSES:
        if len(got & set(k["frames"])) >= SHARED_FRAMES:
            return k["cause"]
    return None


def same_stack(a, b):
    return len(set(a or []) & set(b or [])) >= SHARED_FRAMES


# -- incidents ------------------------------------------------------------

def _outages(rows, now):
    """Fold the status log into (start, end, first_state, states) periods
    when the REST API was not answering. end is None while it still is."""
    periods, cur = [], None
    for r in rows:
        st = r["state"]
        if st in OUTAGE:
            if cur is None:
                cur = {"start": r["ts"], "end": None, "first": st,
                       "states": [st], "detail": r["detail"]}
            elif st not in cur["states"]:
                cur["states"].append(st)
        elif st == "ok" and cur is not None:
            cur["end"] = r["ts"]
            periods.append(cur)
            cur = None
    if cur is not None:
        periods.append(cur)
    return periods


def build(store, bases=None, env=None, heals=(),
          admin=(), now=None):
    """All incidents, newest first, each with its investigation."""
    now = now or int(datetime.now(timezone.utc).timestamp())
    env = env or {}
    rows = store.status_rows()
    reports = store.crashes()
    boots = [r["ts"] for r in rows if r["state"] == "host_boot"]
    starts = [r["ts"] for r in rows if r["state"] == "container_start"]
    heals = sorted(set(heals) | {r["ts"] for r in rows
                                 if r["state"] == "autoheal"})

    incidents = []
    used = set()
    for p in _outages(rows, now):
        end = p["end"] or now
        inc = {"start": p["start"], "end": p["end"], "states": p["states"],
               "detail": p["detail"], "report": None}
        for rep in reports:
            if rep["guid"] in used:
                continue
            if p["start"] - REPORT_SLACK <= rep["ts"] <= end + REPORT_SLACK:
                inc["report"] = rep
                used.add(rep["guid"])
                break
        incidents.append(inc)
    # reports with no recorded outage (before the collector existed)
    for rep in reports:
        if rep["guid"] not in used:
            incidents.append({"start": rep["ts"], "end": None,
                              "states": [], "detail": "", "report": rep,
                              "unobserved": True})

    for inc in incidents:
        _classify(inc, boots, starts, heals, admin, env)
        if inc["kind"] == "host_reboot" and inc["boot"]:
            # nothing was watching while the machine was down: the outage
            # began before the boot, at the last sign of life
            alive = store.last_alive_before(inc["boot"])
            inc["start"] = min(inc["start"], inc["boot"])
            if alive and inc["boot"] - alive < 12 * 3600:
                inc["start"] = alive + 30
        _investigate(inc, store, bases or [])
        # "down" counts from the last sign of life, which for a hang is
        # before the REST API stopped answering
        inc["down_from"] = min(inc["start"], (inc["last_alive"] or
                                              inc["start"]) + 30)
    incidents.sort(key=lambda i: i["start"])
    _patterns(incidents)
    for inc in incidents:
        inc["verdict"] = _verdict(inc)
    incidents.reverse()
    return incidents


def _classify(inc, boots, starts, heals, admin, env):
    s, e = inc["start"], inc["end"] or inc["start"] + 3600
    rep = inc["report"]
    local_hour = env.get("AUTO_REBOOT_LOCAL_HOUR")
    if rep:
        inc["kind"] = "hang" if rep["type"].lower() == "hang" else "crash"
    elif any(s - 60 <= b <= e + 60 for b in boots):
        inc["kind"] = "host_reboot"
    elif any(s - 180 <= a <= s + 30 for a in admin):
        inc["kind"] = "admin"
    elif local_hour and _is_scheduled(s, local_hour,
                                      env.get("AUTO_REBOOT_TZ")):
        inc["kind"] = "scheduled"
    elif "timeout" in inc["states"]:
        inc["kind"] = "hang"            # accepted connections, never answered
    elif "unobserved" in inc:
        inc["kind"] = "crash"
    else:
        inc["kind"] = "outage"
    inc["planned"] = inc["kind"] in PLANNED
    inc["healed"] = next((h for h in heals if s <= h <= e + 60), None)
    inc["restarted"] = next((c for c in starts if s <= c <= e + 60), None)
    inc["boot"] = next((b for b in boots if s - 60 <= b <= e + 60), None)


def _is_scheduled(ts, hour, tz):
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromtimestamp(ts, tz=ZoneInfo(tz) if tz else None)
    except Exception:
        dt = datetime.fromtimestamp(ts)
    try:
        return dt.hour == int(hour) and dt.minute < 10
    except ValueError:
        return False


def _investigate(inc, store, bases):
    """Who was online just before, where, and what they were doing."""
    freeze = inc["start"]
    win = store.window(freeze - LOOKBACK, freeze + 60) or {}
    samples = [r for r in win.get("samples", []) if r["ts"] <= freeze]
    positions = [r for r in win.get("positions", []) if r["ts"] <= freeze + 30]
    # the last sign of life: the last metrics or position before the outage
    alive = [r["ts"] for r in samples] + [r["ts"] for r in positions]
    inc["last_alive"] = max(alive) if alive else None
    rep = inc["report"]
    if rep and inc.get("unobserved") and alive:
        inc["start"] = min(inc["start"], max(alive) + 60)
    if samples:
        fps = [r["fps"] for r in samples if r["fps"]]
        inc["fps_min"] = min(fps) if fps else None
        inc["mem_before"] = samples[-1]["mem"]
        inc["players_before"] = samples[-1]["players"]

    by_uid = {}
    for r in positions:
        by_uid.setdefault(r["uid"], []).append(r)
    names = {uid: p.get("name") or uid
             for uid, p in (store.known_players() or {}).items()}
    for ev in win.get("events", []):
        if ev["name"]:
            names.setdefault(ev["uid"], ev["name"])

    # online = seen moving in the window, or joined without leaving
    online = set(by_uid)
    for uid in names:
        last = store.last_join(uid, freeze)
        if last and last[1] == "join" and freeze - last[0] < 12 * 3600:
            online.add(uid)

    people = []
    for uid in sorted(online, key=lambda u: (names.get(u) or u).lower()):
        trail = by_uid.get(uid, [])
        last = store.last_join(uid, freeze)
        who = {"uid": uid, "notes": [],
               "name": names.get(uid) or (last[2] if last else None) or uid}
        if last and last[1] == "join":
            who["joined"] = last[0]
            if freeze - last[0] < 10 * 60:
                who["notes"].append("joined %d min before"
                                    % max(1, (freeze - last[0]) // 60))
        if trail:
            end = trail[-1]
            who["pos"] = (end["x"], end["y"])
            who["pos_age"] = freeze - end["ts"]
            who["map"] = map_coords(end["x"], end["y"])
            moved, jump = 0.0, None
            for a, b in zip(trail, trail[1:]):
                d = _dist((a["x"], a["y"]), (b["x"], b["y"]))
                moved += d
                dt = max(1, b["ts"] - a["ts"])
                if d / dt > FAST_TRAVEL_SPEED:
                    jump = (b["ts"], d)
            who["moved"] = moved
            if jump:
                who["jump"] = jump
                who["notes"].append("fast-travelled %.1f km %d min before"
                                    % (jump[1] / 100000.0,
                                       max(0, (freeze - jump[0]) // 60)))
            base = min(((_dist((b["x"], b["y"]), who["pos"]), i)
                        for i, b in enumerate(bases)), default=None)
            if base and base[0] < 15000:
                who["base"] = base[1] + 1
                who["notes"].append("at base %d" % who["base"])
            elif len(trail) > 1 and moved < 2000:
                who["notes"].append("standing still")
        ups = [lv for lv in win.get("levels", []) if lv["uid"] == uid
               and lv["ts"] <= freeze]
        if ups:
            who["notes"].append("levelled up to %d" % ups[-1]["level"])
        people.append(who)
    inc["people"] = people


def _patterns(incidents):
    """Look across incidents: same freeze point, same player, same place."""
    bad = [i for i in incidents if not i["planned"]]
    for inc in bad:
        found = []
        rep = inc["report"]
        if rep:
            cause = known_cause(rep["frames"])
            if cause:
                inc["known"] = cause
            twins = [o for o in bad if o is not inc and o["report"]
                     and same_stack(rep["frames"], o["report"]["frames"])]
            if twins:
                found.append("same freeze point in the game code as %d other "
                             "incident%s (%s)" % (
                                 len(twins), "s" * (len(twins) > 1),
                                 ", ".join(_short(o["start"])
                                           for o in twins[:3])))
        for who in inc["people"]:
            others = [o for o in bad if o is not inc
                      and any(p["uid"] == who["uid"] for p in o["people"])]
            if others and len(bad) > 1:
                found.append("%s was online in %d of %d unplanned incidents"
                             % (who["name"], len(others) + 1, len(bad)))
            if "pos" not in who:
                continue
            near = [o for o in others for p in o["people"]
                    if p["uid"] == who["uid"] and "pos" in p
                    and _dist(p["pos"], who["pos"]) < SAME_PLACE]
            if near:
                found.append("%s was within %d m of the same place %s"
                             % (who["name"], SAME_PLACE // 100,
                                "as on " + ", ".join(
                                    _short(o["start"]) for o in near[:3])))
        inc["patterns"] = found


def _short(ts):
    return datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")


def _verdict(inc):
    kind = inc["kind"]
    if kind == "host_reboot":
        return "the machine itself was restarted"
    if kind == "scheduled":
        return "the nightly scheduled restart"
    if kind == "admin":
        return "restarted from paltop's admin menu"
    if inc.get("known"):
        return "known: " + inc["known"]
    hints = []
    for who in inc["people"]:
        if "jump" in who:
            hints.append("%s had just fast-travelled - check what is at "
                         "%s" % (who["name"], fmt_map(who.get("map"))))
    if not inc["people"]:
        hints.append("nobody was online - a server-side problem, not a "
                     "player action")
    for p in inc.get("patterns", []):
        if "same place" in p:
            hints.insert(0, "likely location-triggered: " + p)
    if hints:
        return hints[0]
    if inc["people"]:
        return "look at what %s was doing at %s" % (
            " and ".join(w["name"] for w in inc["people"]),
            _short(inc["start"]))
    return "not enough data yet"


def fmt_map(m):
    if not m:
        return "?"
    return "%d, %d" % (round(m[0]), round(m[1]))


def summary(inc):
    """One line: kind, who, verdict."""
    who = ", ".join(p["name"] for p in inc["people"]) or "nobody online"
    return "%s  %s  -  %s" % (KIND_LABEL.get(inc["kind"], inc["kind"]),
                              who, inc["verdict"])
