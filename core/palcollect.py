#!/usr/bin/env python3
"""
palcollect.py - the background collector, with no user interface.

Samples the server every 30 seconds (PalSource.sample(), the same one the
terminal uses) and records the history in the database: sessions, positions,
levels, metrics, backups and server status. Every cycle it also writes
state/web.json, the part of the snapshot the web page may show, and all the
time it follows the server's log for the in-game chat (palchat).

Both the terminal and the web page use what it records, so it belongs to the
core: it runs as the systemd user service paltop-collector (install.sh sets
it up). Only one collector runs at a time.

    python3 palcollect.py [-c CONTAINER] [-H HOST] [-i SECONDS]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import palchat
import palcrash
import palsource
from palpals import pal_display

INTERVAL = 30.0            # seconds between samples
AFK_AFTER = 300            # seconds in the same spot before a player is AFK


class Idle:
    """Seconds a player has stood in the same spot, from the positions the
    collector records each minute. Cached for 30 s per player, since the
    terminal rebuilds the roster every frame."""

    def __init__(self, db):
        self.db = db
        self._cache = {}

    def idle_for(self, uid, x, y):
        if x is None or y is None:
            return None
        hit = self._cache.get(uid)
        if hit and time.monotonic() - hit[0] < 30:
            return hit[1]
        since, _sure = self.db.last_moved(uid, x, y)
        idle = max(0.0, time.time() - since) if since else None
        self._cache[uid] = (time.monotonic(), idle)
        return idle


ROSTER_SORTS = {
    "level": lambda r: -r["level"],
    "name": lambda r: r["name"].lower(),
    "ping": lambda r: (r["ping"] is None, r["ping"] or 0),
    "playtime": lambda r: -r["playtime"],
    "seen": lambda r: -(r["last_seen"].timestamp()
                        if r["last_seen"] else 0),
}


def roster(snap, db, idle, sort="level", show_all=True):
    """Merge live REST players, log-derived sessions and the level store."""
    s = snap
    online = {}
    for p in s.get("players") or []:
        uid = p.get("userId") or p.get("playerId")
        online[uid] = p
    sessions = s.get("sessions") or {}
    store = s.get("store") or {}
    save_ids = (s.get("world") or {}).get("save_ids") or set()

    rows = []
    for uid in set(online) | set(sessions) | set(store):
        live = online.get(uid)
        sess = sessions.get(uid, {})
        mem = store.get(uid, {})
        name = (live or {}).get("name") or sess.get("name") \
            or mem.get("name") or uid
        account = (live or {}).get("accountName") or mem.get("account")
        pid = next((v for v in ((live or {}).get("playerId"), sess.get("pid"),
                                mem.get("pid")) if v and v != "None"), None)
        if not pid and name:      # joining: the REST API has no id for it yet
            pid = next((k for k, sp in (s.get("save_players") or {}).items()
                        if (sp.get("name") or "").lower() == name.lower()),
                       None)
        saved = (s.get("save_players") or {}).get(pid) if pid else None
        level = ((live or {}).get("level") or (saved or {}).get("level")
                 or mem.get("level") or 0)
        if saved and not name:
            name = saved["name"]
        last = sess.get("last_seen")
        if last is None and mem.get("last_seen"):
            last = datetime.fromtimestamp(mem["last_seen"], timezone.utc)
        rows.append({
            "uid": uid, "name": name, "account": account, "pid": pid,
            "level": int(level or 0),
            "online": live is not None,
            "ping": (live or {}).get("ping"),
            "ip": (live or {}).get("iP") or sess.get("ip") or mem.get("ip"),
            "x": (live or {}).get("location_x", mem.get("x")),
            "y": (live or {}).get("location_y", mem.get("y")),
            "peak": mem.get("peak_level") or 0,
            "pals": (s.get("save_pal_counts") or {}).get(pid, 0),
            "from_save": bool(saved),
            "gained": db.levels_gained(uid, 7),
            "playtime": sess.get("playtime", 0.0),
            "joins": sess.get("joins", 0),
            "since": sess.get("current_since"),
            "idle": idle.idle_for(uid, live.get("location_x"),
                                  live.get("location_y"))
            if live else None,
            "last_seen": last,
            "saved": pid in save_ids if pid else False,
            "stale": last is None,
        })

    # players who exist in the save but never appeared in the log window
    # (the log only reaches back to container creation)
    known_pids = {r["pid"] for r in rows if r["pid"]}
    for pid, sp in (s.get("save_players") or {}).items():
        if pid in known_pids:
            continue
        rows.append({
            "uid": "save:" + pid, "name": sp["name"], "account": None,
            "pid": pid, "level": sp["level"], "online": False,
            "ping": None, "ip": None, "x": None, "y": None,
            "peak": sp["level"], "gained": 0,
            "pals": (s.get("save_pal_counts") or {}).get(pid, 0),
            "from_save": True, "playtime": 0.0, "joins": 0, "since": None,
            "last_seen": None, "saved": True, "stale": True,
        })

    key = ROSTER_SORTS[sort]
    rows.sort(key=lambda r: (not r["online"], key(r)))
    if not show_all:
        rows = [r for r in rows if r["online"]]
    return rows


def web_snapshot(src, snap, rows):
    """What the web page may show, built from the current snapshot.

    An allow-list: every field is copied by name, so nothing new leaks onto
    the page just because the snapshot grew. No IP addresses, Steam ids,
    account names, admin commands or anything from the admin API."""
    s = snap
    m = s.get("metrics") or {}
    info = s.get("info") or {}
    settings = s.get("settings") or {}
    now = s.get("now") or datetime.now(timezone.utc)
    rs = s.get("rest_state")
    status = {"ok": "running", "timeout": "NOT RESPONDING",
              "refused": "DOWN", "error": "REST ERROR"}.get(rs, "unknown")
    if rs == "refused" and s.get("health") == "starting":
        status = "STARTING"
    saves = src.saves
    guild_of = {m["pid"]: g["name"] for g in (saves.guilds if saves else [])
                for m in g["members"]}
    players = []
    for r in rows:
        if r["uid"].startswith("save:") and not r.get("pid"):
            continue
        where = None
        if r["online"] and r.get("x") is not None:
            mx, my = palcrash.map_coords(r["x"], r["y"])
            where = {"map": [round(mx), round(my)]}
        players.append({
            "name": r["name"], "pid": r.get("pid"), "level": r["level"],
            "online": r["online"], "pals": r["pals"],
            "online_since": r["since"].timestamp() if r.get("since") else None,
            "idle": round(r["idle"]) if r.get("idle") is not None else None,
            "afk": bool(r["online"] and (r.get("idle") or 0) >= AFK_AFTER),
            "playtime": round(r.get("playtime") or 0),
            "last_seen": r["last_seen"].timestamp() if r.get("last_seen")
            else None, "where": where, "guild": guild_of.get(r.get("pid"))})
    bases = []
    cond = s.get("conditions") or {}
    names = s.get("pal_names")
    for i, b in enumerate(s.get("base_camps") or []):
        trouble = []
        for key, sev, what in (("dying", 2, None), ("sick", 2, "sick"),
                               ("starving", 2, "starving"),
                               ("insane", 1, "sanity"),
                               ("slacking", 0, "slacking off")):
            for sp, val in b.get(key) or []:
                text = {"dying": str(val).lower(),
                        "sick": "sick: %s" % cond.get(val, val),
                        "insane": "sanity %s" % val}.get(key, what)
                trouble.append({"sev": sev, "pal": pal_display(sp, names)[1],
                                "what": text})
        bases.append({
            "label": "Base %d" % (i + 1),
            "map": [round(v) for v in palcrash.map_coords(b["x"], b["y"])],
            "structures": b["structures"], "workers": b["workers"],
            "cap": b["worker_cap"],
            "trouble": sorted(trouble, key=lambda t: -t["sev"])})
    incidents = []
    for inc in (s.get("incidents") or [])[:30]:
        incidents.append({
            "start": inc["start"], "end": inc["end"],
            "kind": palcrash.KIND_LABEL.get(inc["kind"], inc["kind"]),
            "planned": inc["planned"],
            "down": (inc["end"] - inc["down_from"]) if inc["end"] else None,
            "people": [p["name"] for p in inc["people"]],
            "verdict": inc["verdict"],
            "notes": {p["name"]: p["notes"] for p in inc["people"]},
            "patterns": inc.get("patterns") or []})
    backups = s.get("backups") or []
    bc, rc = s.get("backup_cron") or {}, s.get("restart_cron") or {}

    def ts(dt):
        return dt.timestamp() if dt else None
    return {
        "t": now.timestamp(),
        "server": {
            "name": info.get("servername") or "",
            "version": info.get("version") or "",
            "status": status, "ok": rs == "ok",
            "day": m.get("days"), "camps": m.get("basecampnum"),
            "uptime": m.get("uptime"),
            "fps": m.get("serverfps"), "fps_avg": m.get("serverfpsaverage"),
            "frametime": m.get("serverframetime"),
            "players": m.get("currentplayernum"),
            "max": m.get("maxplayernum"),
            "difficulty": settings.get("Difficulty"),
            "last_backup": backups[0]["mtime"] if backups else None,
            "next_backup": ts(bc.get("next")) if bc.get("active") else None,
            "next_restart": ts(rc.get("next")) if rc.get("active") else None,
        },
        "fps_hist": src.db.series("fps", days=1, buckets=96, missing=None),
        "players_hist": src.db.series("players", days=1, buckets=96,
                                      missing=None),
        "players": players,
        "alerts": [{"level": a["level"], "text": a["text"]}
                   for a in (s.get("alerts") or [])
                   if "docker" not in a["text"] and "admin" not in
                   a["text"].lower()][:8],
        "bases": bases,
        "incidents": incidents,
    }


def write_web_snapshot(src, snap, rows, path):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(web_snapshot(src, snap, rows), fh, default=str)
    os.replace(tmp, path)


def status(db):
    """(running?, heartbeat age in s, info) from the collector's heartbeat."""
    info = db.meta_get("collector")
    if not info:
        return None, None, None
    age = time.time() - float(info.get("heartbeat") or 0)
    return age < 3 * float(info.get("interval") or INTERVAL), age, info


def collect(src, interval=INTERVAL):
    """Keep sampling and writing history, until SIGTERM or ^C."""
    import fcntl
    import signal
    lock_path = os.path.join(palsource.STATE_DIR, "collector.lock")
    os.makedirs(palsource.STATE_DIR, exist_ok=True)
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("paltop collector: already running (%s is locked)" % lock_path,
              file=sys.stderr)
        return 1
    stop = {"now": False}

    def on_signal(signum, _frame):
        stop["now"] = True
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    def log(msg):
        print("%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg),
              flush=True)

    db = src.db
    idle = Idle(db)
    # the in-game chat, from the server's log, for the web page's chat
    tz = timezone.utc
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(src.env.get("TZ") or "UTC")
    except Exception:
        pass
    chat = palchat.Follower(src.container, db, tz, log)
    chat.start()
    web_path = os.path.join(palsource.STATE_DIR, "web.json")
    log("collector started, every %.0fs, db %s" % (interval, db.path))
    last_state = last_err = None
    cycles = 0
    while not stop["now"]:
        t0 = time.monotonic()
        try:
            snap = src.sample()
            cycles += 1
            try:                              # the web page reads this
                write_web_snapshot(src, snap, roster(snap, db, idle),
                                   web_path)
            except Exception as exc:
                log("web snapshot failed: %s: %s" % (type(exc).__name__, exc))
            state = snap.get("rest_state")
            if state != last_state:
                log("server state: %s -> %s  (%s)" % (
                    last_state, state,
                    snap.get("rest_error") or "%s online" % len(
                        snap.get("players") or [])))
                last_state = state
            last_err = None
            db.meta_set("collector", {
                "pid": os.getpid(), "heartbeat": time.time(),
                "interval": interval, "cycles": cycles, "state": state,
                "took": round(time.monotonic() - t0, 2)})
        except Exception as exc:              # never die on one bad cycle
            err = "%s: %s" % (type(exc).__name__, exc)
            if err != last_err:
                log("sample failed: %s" % err)
                last_err = err
        left = interval - (time.monotonic() - t0)
        while left > 0 and not stop["now"]:
            time.sleep(min(1.0, left))
            left -= 1.0
    chat.close()
    log("collector stopped after %d cycles" % cycles)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="paltop's background collector: records the Palworld "
                    "server's history for the terminal and the web page")
    ap.add_argument("-c", "--container", default=os.environ.get(
        "PALTOP_CONTAINER", "palworld-server"), help="docker container name")
    ap.add_argument("-H", "--host", default=os.environ.get(
        "PALTOP_REST_HOST", "127.0.0.1"), help="REST API host")
    ap.add_argument("-i", "--interval", type=float, default=INTERVAL,
                    help="seconds between samples (default 30)")
    args = ap.parse_args(argv)
    src = palsource.PalSource(container=args.container, rest_host=args.host,
                              use_net=True,
                              db_path=os.environ.get("PALTOP_DB"))
    for e in src.err:
        print("paltop collector: %s" % e, file=sys.stderr)
    return collect(src, max(5.0, args.interval))


if __name__ == "__main__":
    sys.exit(main())
