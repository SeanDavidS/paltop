#!/usr/bin/env python3
"""
palstore.py - persistent history for paltop, kept in one SQLite database.

`docker logs` only reaches back to when the container was *created*, so every
player session, playtime total and join/leave event would be lost the moment
the container is recreated. This module keeps that history on disk in
state/paltop.db, which can also be queried directly:

    sqlite3 -readonly ~/paltop/state/paltop.db \
        "SELECT datetime(ts,'unixepoch'), kind, name FROM events ORDER BY ts DESC LIMIT 20"

Tables (all timestamps are unix seconds, UTC):

    events      joins, leaves, admin commands          kept EVENTS_MONTHS
    levels      when each player first reached a level kept (tiny)
    status_log  server state changes (ok/hung/down),    kept EVENTS_MONTHS
                container starts, host boots, autoheal
    crashes     the game's own crash reports            kept (tiny; the game
                                                        deletes its copies
                                                        after 2 days)
    samples     server metrics, once a minute           kept METRICS_DAYS
    positions   each online player's position, 1/min    kept METRICS_DAYS
    players     each player's last known state          one row per player
    backups     backup archives seen                    last MAX_BACKUPS
    meta        small key/value settings (JSON values)
    web_users   the web page's users: in-game name, a   kept until removed
                salted scrypt password hash, and the
                in-game player it is linked to
    web_sessions  logged-in browsers (token hashes)     until they expire
    achievements  when each player reached each tier     kept (small)
    paldeck_finds when each player first had each Pal:   kept (small)
                  from its Pals' caught time, or the
                  first time it showed in their Paldeck

The size is bounded: retention is applied every PRUNE_EVERY seconds, and if the
file is still over MAX_DB_BYTES the oldest day of positions, then samples, is
deleted until it fits. Freed pages go back to the filesystem
(auto_vacuum=INCREMENTAL), so the file shrinks as well as stops growing.

Several paltop processes may use the database at once (the background
collector and any number of UIs): it runs in WAL mode, and every write is an
INSERT OR IGNORE on a natural key, so a record written twice only counts once.

Everything written here is derived from data paltop already reads; the store
never touches the Palworld server.
"""

from __future__ import annotations

import functools
import glob
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

SAMPLE_EVERY = 60              # seconds between persisted metric samples
POSITION_EVERY = 60            # seconds between persisted positions per player
METRICS_DAYS = 60              # keep samples and positions this many days
EVENTS_MONTHS = 24             # keep events and status changes this many months
MAX_DB_BYTES = 50 * 2**20      # hard cap for the database file
MAX_BACKUPS = 500              # backup archives remembered
PRUNE_EVERY = 3600             # seconds between retention passes

METRIC_COLUMNS = {             # name used by series() -> column in samples
    "players": "players", "fps": "fps", "frametime": "frametime",
    "cpu": "cpu", "mem": "mem", "camps": "camps", "host_cpu": "host_cpu",
    "host_mem": "host_mem",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS events (
    ts     INTEGER NOT NULL,
    kind   TEXT    NOT NULL,
    uid    TEXT    NOT NULL DEFAULT '',
    name   TEXT    NOT NULL DEFAULT '',
    pid    TEXT,
    ip     TEXT,
    detail TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (ts, kind, uid, detail)
);
CREATE INDEX IF NOT EXISTS events_ts  ON events (ts);
CREATE INDEX IF NOT EXISTS events_uid ON events (uid, ts);
CREATE TABLE IF NOT EXISTS players (
    uid        TEXT PRIMARY KEY,
    name       TEXT,
    account    TEXT,
    pid        TEXT,
    ip         TEXT,
    level      INTEGER DEFAULT 0,
    peak_level INTEGER DEFAULT 0,
    first_seen INTEGER,
    last_seen  INTEGER,
    x          REAL,
    y          REAL
);
CREATE TABLE IF NOT EXISTS levels (
    uid   TEXT    NOT NULL,
    level INTEGER NOT NULL,
    ts    INTEGER NOT NULL,
    PRIMARY KEY (uid, level)
);
CREATE TABLE IF NOT EXISTS samples (
    ts        INTEGER PRIMARY KEY,
    players   INTEGER,
    fps       REAL,
    frametime REAL,
    cpu       REAL,
    mem       INTEGER,
    days      INTEGER,
    camps     INTEGER,
    host_cpu  REAL,
    host_mem  REAL
);
CREATE TABLE IF NOT EXISTS backups (
    name  TEXT PRIMARY KEY,
    mtime INTEGER,
    size  INTEGER
);
CREATE TABLE IF NOT EXISTS positions (
    ts  INTEGER NOT NULL,
    uid TEXT    NOT NULL,
    x   REAL,
    y   REAL,
    PRIMARY KEY (ts, uid)
);
CREATE INDEX IF NOT EXISTS positions_uid ON positions (uid, ts);
CREATE TABLE IF NOT EXISTS status_log (
    ts     INTEGER NOT NULL,
    state  TEXT    NOT NULL,
    detail TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (ts, state)
);
CREATE TABLE IF NOT EXISTS crashes (
    guid    TEXT PRIMARY KEY,
    ts      INTEGER NOT NULL,
    type    TEXT,
    top     TEXT,
    frames  TEXT,
    hash    TEXT,
    uptime  INTEGER,
    clients INTEGER,
    release TEXT,
    message TEXT
);
CREATE TABLE IF NOT EXISTS web_users (
    key        TEXT PRIMARY KEY,           -- the name, casefolded: unique
    name       TEXT NOT NULL,              -- as it is written in the game
    pw         TEXT NOT NULL,              -- scrypt$n$r$p$salt$hash
    created    INTEGER NOT NULL,
    last_login INTEGER,
    player     TEXT                        -- the linked in-game player id
);
CREATE TABLE IF NOT EXISTS web_sessions (
    token   TEXT PRIMARY KEY,              -- sha256 of the cookie value
    user    TEXT NOT NULL,                 -- web_users.key
    expires INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS web_sessions_user ON web_sessions (user);
CREATE TABLE IF NOT EXISTS achievements (
    pid  TEXT NOT NULL,                    -- the player
    key  TEXT NOT NULL,                    -- palachieve.ACHIEVEMENTS
    tier INTEGER NOT NULL,                 -- 1 bronze, 2 silver, 3 gold
    ts   INTEGER NOT NULL,                 -- when paltop first saw it
    PRIMARY KEY (pid, key, tier)
);
CREATE TABLE IF NOT EXISTS paldeck_finds (
    pid      TEXT NOT NULL,                -- the player
    species  TEXT NOT NULL,                -- the Palpedia entry id
    first_ts INTEGER NOT NULL,             -- when, as far as we know
    how      TEXT NOT NULL,                -- "owned": a Pal's caught time;
                                           -- "seen": first in the Paldeck
    PRIMARY KEY (pid, species)
);
CREATE TABLE IF NOT EXISTS chat (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     INTEGER NOT NULL,               -- when it was written
    source TEXT NOT NULL,                  -- "game" or "web"
    name   TEXT NOT NULL,                  -- who wrote it
    text   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS chat_once ON chat (ts, source, name, text);
"""
CHAT_KEEP = 5000               # messages kept

# columns added after a table was first made: (table, column, definition)
MIGRATIONS = [
    ("web_users", "player", "TEXT"),
]
LATE_SCHEMA = """
CREATE UNIQUE INDEX IF NOT EXISTS web_users_player ON web_users (player)
    WHERE player IS NOT NULL;              -- one user per in-game player
"""


def _ts(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _dt(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


class Store:
    """History store backed by the SQLite file at `path`."""

    def __init__(self, path):
        self._lock = threading.RLock()
        self.path = path
        self.ok = False
        self.error = None
        self.db = None
        self._last_sample_ts = 0
        self._last_pos = {}
        self._next_prune = 0.0
        self.pruned_at = None
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            fresh = not os.path.exists(path)
            self.db = sqlite3.connect(path, timeout=10,
                                      check_same_thread=False,
                                      isolation_level=None)
            self.db.row_factory = sqlite3.Row
            if fresh:                           # must precede the first table
                self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
            for table, col, kind in MIGRATIONS:
                have = {r[1] for r in self._q("PRAGMA table_info(%s)" % table)}
                if col not in have:
                    try:
                        self.db.execute("ALTER TABLE %s ADD COLUMN %s %s"
                                        % (table, col, kind))
                    except sqlite3.OperationalError:
                        pass                # another process just added it
            self.db.executescript(LATE_SCHEMA)
            self.ok = True
            self._import_text_history()
        except (OSError, sqlite3.Error) as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)

    def _q(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def _write(self, fn):
        """Run fn() in one transaction; a failure is remembered, not raised.
        Returns True on success."""
        if not self.ok:
            return False
        try:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                fn()
                self.db.execute("COMMIT")
                return True
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        except sqlite3.Error as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
            return False

    # -- writes ----------------------------------------------------------

    def scrub_secrets(self, mask):
        """Rewrite stored command details through `mask` (e.g. hide the
        AdminPassword argument), then vacuum so the old text is not left in
        free pages. Returns how many rows changed."""
        if not self.ok:
            return 0
        rows = self._q("SELECT rowid, detail FROM events WHERE kind='cmd'")
        bad = [(r[0], r[1], mask(r[1])) for r in rows if mask(r[1]) != r[1]]
        if not bad:
            return 0

        def go():
            for rowid, _old, new in bad:
                self.db.execute("UPDATE OR IGNORE events SET detail=? WHERE"
                                " rowid=?", (new, rowid))
                # a masked twin already existed: drop the unmasked row
                self.db.execute("DELETE FROM events WHERE rowid=? AND"
                                " detail<>?", (rowid, new))
        if not self._write(go):
            return 0
        try:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.execute("VACUUM")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as exc:
            self.error = "vacuum after scrub: %s" % exc
        return len(bad)

    def ingest_events(self, events):
        """Merge a batch of parsed log events. Idempotent."""
        if not events:
            return 0
        n = [0]

        def go():
            for e in events:
                n[0] += self.db.execute(
                    "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                    (_ts(e["ts"]), e["kind"], e.get("uid") or "",
                     e.get("name") or "", e.get("pid"), e.get("ip"),
                     e.get("detail") or "")).rowcount
        return n[0] if self._write(go) else 0

    def track_presence(self, online, now):
        """Joins and leaves from who the REST API says is online, for when
        the server's log cannot be read (a damaged Docker log file stops
        `docker logs` at the damage). `online` maps uid to (name, player id).
        The last known set lives in meta, read and written in one
        transaction, so several paltop processes record each change once.
        Returns the events added."""
        if not self.ok:
            return 0
        ts = _ts(now)
        n = [0]

        def go():
            row = self.db.execute("SELECT value FROM meta WHERE key = ?",
                                  ("presence",)).fetchone()
            before = json.loads(row[0]) if row else {}
            for uid, (name, pid) in online.items():
                if uid not in before:
                    n[0] += self.db.execute(
                        "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                        (ts, "join", uid, name or "", pid, None,
                         "seen online")).rowcount
            for uid, (name, pid) in before.items():
                if uid not in online:
                    n[0] += self.db.execute(
                        "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                        (ts, "left", uid, name or "", pid, None,
                         "no longer online")).rowcount
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                            ("presence", json.dumps(
                                {u: list(v) for u, v in online.items()})))
        with self._lock:
            return n[0] if self._write(go) else 0

    def observe_players(self, players, now):
        """Record who is online right now, their level and where they are."""
        if not players:
            return
        ts = _ts(now)

        def go():
            for p in players:
                uid = p.get("userId") or p.get("playerId")
                if not uid:
                    continue
                lvl = int(p.get("level") or 0)
                x, y = p.get("location_x"), p.get("location_y")
                self.db.execute(
                    "INSERT INTO players (uid, name, account, pid, ip, level,"
                    " peak_level, first_seen, last_seen, x, y)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(uid) DO UPDATE SET"
                    " name=coalesce(excluded.name, name),"
                    " account=coalesce(excluded.account, account),"
                    " pid=coalesce(excluded.pid, pid),"
                    " ip=coalesce(excluded.ip, ip),"
                    " level=max(level, excluded.level),"
                    " peak_level=max(peak_level, excluded.level),"
                    " last_seen=excluded.last_seen,"
                    " x=coalesce(excluded.x, x), y=coalesce(excluded.y, y)",
                    (uid, p.get("name") or None, p.get("accountName") or None,
                     p.get("playerId") or None, p.get("iP") or None, lvl, lvl,
                     ts, ts,
                     round(x) if x is not None else None,
                     round(y) if y is not None else None))
                if lvl > 0:
                    self.db.execute(
                        "INSERT OR IGNORE INTO levels VALUES (?,?,?)",
                        (uid, lvl, ts))
        self._write(go)

    def record_sample(self, now, metrics, container, host_cpu, host_mem_pct):
        """Persist one metrics row, at most once per SAMPLE_EVERY seconds."""
        if not metrics:
            return
        ts = (_ts(now) // SAMPLE_EVERY) * SAMPLE_EVERY
        if ts == self._last_sample_ts:
            return
        row = (ts, int(metrics.get("currentplayernum") or 0),
               round(float(metrics.get("serverfps") or 0), 1),
               round(float(metrics.get("serverframetime") or 0), 2),
               round(float(container.get("cpu_pct") or 0), 1),
               int(container.get("mem") or 0),
               int(metrics.get("days") or 0),
               int(metrics.get("basecampnum") or 0),
               round(float(host_cpu or 0), 1),
               round(float(host_mem_pct or 0), 1))
        if self._write(lambda: self.db.execute(
                "INSERT OR IGNORE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?)",
                row)):
            self._last_sample_ts = ts

    def record_positions(self, now, players, every=POSITION_EVERY):
        """Where each online player is, at most once per `every` seconds."""
        if not players:
            return
        ts = (int(now.timestamp()) // every) * every
        rows = []
        for p in players:
            uid = p.get("userId") or p.get("playerId")
            if not uid or p.get("location_x") is None:
                continue
            if self._last_pos.get(uid) == ts:
                continue
            self._last_pos[uid] = ts
            rows.append((ts, uid, round(p["location_x"]),
                         round(p["location_y"])))
        if rows:
            self._write(lambda: self.db.executemany(
                "INSERT OR IGNORE INTO positions VALUES (?,?,?,?)", rows))

    def record_status(self, ts, state, detail=""):
        self._write(lambda: self.db.execute(
            "INSERT OR IGNORE INTO status_log VALUES (?,?,?)",
            (int(ts), state, detail or "")))

    def record_crashes(self, reports):
        """Keep the game's crash reports; returns how many were new."""
        if not reports:
            return 0
        n = [0]

        def go():
            for r in reports:
                n[0] += self.db.execute(
                    "INSERT OR IGNORE INTO crashes VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (r["guid"], int(r["ts"]), r.get("type"), r.get("top"),
                     json.dumps(r.get("frames") or []), r.get("hash"),
                     r.get("uptime"), r.get("clients"), r.get("release"),
                     r.get("message"))).rowcount
        return n[0] if self._write(go) else 0

    def record_backups(self, backups):
        if not backups:
            return

        def go():
            self.db.executemany(
                "INSERT OR REPLACE INTO backups VALUES (?,?,?)",
                [(b["name"], int(b["mtime"]), int(b["size"]))
                 for b in backups])
            self.db.execute(
                "DELETE FROM backups WHERE name NOT IN (SELECT name FROM"
                " backups ORDER BY mtime DESC LIMIT ?)", (MAX_BACKUPS,))
        self._write(go)

    def meta_get(self, key, default=None):
        if not self.ok:
            return default
        rows = self._q("SELECT value FROM meta WHERE key=?", (key,))
        try:
            return json.loads(rows[0][0]) if rows else default
        except ValueError:
            return default

    def meta_set(self, key, value):
        self._write(lambda: self.db.execute(
            "INSERT OR REPLACE INTO meta VALUES (?,?)",
            (key, json.dumps(value))))

    def prune(self, force=False):
        """Apply the retention rules and the size cap (at most hourly)."""
        if not self.ok:
            return
        if not force and time.monotonic() < self._next_prune:
            return
        self._next_prune = time.monotonic() + PRUNE_EVERY
        now = int(time.time())
        self.pruned_at = now
        metrics_cut = now - METRICS_DAYS * 86400
        events_cut = now - EVENTS_MONTHS * 31 * 86400

        def retention():
            for table, cut in (("samples", metrics_cut),
                               ("positions", metrics_cut),
                               ("events", events_cut),
                               ("status_log", events_cut)):
                self.db.execute("DELETE FROM %s WHERE ts < ?" % table, (cut,))
        self._write(retention)
        self._vacuum()
        # still too big: drop the oldest day of positions, then of samples,
        # until it fits. The last 24 hours and the events are never cut here.
        for table in ("positions", "samples"):
            while self.db_bytes() > MAX_DB_BYTES:
                oldest = self._q("SELECT min(ts) FROM %s" % table)[0][0]
                if oldest is None or oldest >= now - 86400:
                    break
                self._write(lambda: self.db.execute(
                    "DELETE FROM %s WHERE ts < ?" % table, (oldest + 86400,)))
                self._vacuum()
        if self.db_bytes() > MAX_DB_BYTES:
            self.error = "history database over %d MB" % (MAX_DB_BYTES // 2**20)

    def _vacuum(self):
        try:
            self.db.execute("PRAGMA incremental_vacuum").fetchall()
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass

    def db_bytes(self):
        """Bytes of live data pages (what the size cap measures)."""
        if not self.ok:
            return 0
        n, size, free = self._q(
            "SELECT page_count, page_size, freelist_count FROM"
            " pragma_page_count(), pragma_page_size(),"
            " pragma_freelist_count()")[0]
        return (n - free) * size

    # -- reads -----------------------------------------------------------

    def events(self, since_days=None, kinds=None, limit=20000):
        if not self.ok:
            return []
        sql, args = "SELECT * FROM events WHERE 1", []
        if since_days:
            sql += " AND ts >= ?"
            args.append(int(time.time() - since_days * 86400))
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args.extend(kinds)
        sql += " ORDER BY ts LIMIT ?"
        args.append(limit)
        return [{"ts": _dt(r["ts"]), "kind": r["kind"], "uid": r["uid"],
                 "name": r["name"], "pid": r["pid"], "ip": r["ip"],
                 "detail": r["detail"]} for r in self._q(sql, args)]

    def known_players(self):
        if not self.ok:
            return {}
        return {r["uid"]: {k: r[k] for k in r.keys() if r[k] is not None}
                for r in self._q("SELECT * FROM players")}

    def levels_gained(self, uid, days=7):
        """How many levels this player gained in the window."""
        if not self.ok:
            return 0
        return self._q("SELECT count(*) FROM levels WHERE uid=? AND ts >= ?",
                       (uid, int(time.time() - days * 86400)))[0][0]

    def level_series(self, uid, points=40):
        if not self.ok:
            return []
        rows = self._q("SELECT level FROM levels WHERE uid=?"
                       " ORDER BY ts DESC LIMIT ?", (uid, points))
        return [r[0] for r in reversed(rows)]

    def series(self, column, days=1, buckets=120, missing=0.0):
        """Bucketed average of a metric, oldest first; `missing` fills the
        buckets with no samples (None leaves a gap for a chart)."""
        col = METRIC_COLUMNS.get(column)
        if not self.ok or not col:
            return []
        now = int(time.time())
        start = now - int(days * 86400)
        width = max(1, (now - start) // buckets)
        got = {r[0]: r[1] for r in self._q(
            "SELECT (ts / ?) * ?, avg(%s) FROM samples WHERE ts >= ?"
            " GROUP BY 1" % col, (width, width, start))}
        out = []
        for i in range(buckets):
            b = ((start + i * width) // width) * width
            out.append(float(got[b] or 0) if b in got else missing)
        return out

    def backup_series(self, limit=60):
        if not self.ok:
            return []
        rows = self._q("SELECT * FROM backups ORDER BY mtime DESC LIMIT ?",
                       (limit,))
        return [dict(r) for r in reversed(rows)]

    def status_history(self, limit=200):
        if not self.ok:
            return []
        rows = self._q("SELECT * FROM status_log ORDER BY ts DESC LIMIT ?",
                       (limit,))
        return [{"ts": _dt(r["ts"]), "state": r["state"],
                 "detail": r["detail"]} for r in reversed(rows)]

    def last_status(self, states=None):
        """The newest status_log row (optionally among `states`), or None."""
        if not self.ok:
            return None
        sql, args = "SELECT * FROM status_log", []
        if states:
            sql += " WHERE state IN (%s)" % ",".join("?" * len(states))
            args = list(states)
        rows = self._q(sql + " ORDER BY ts DESC LIMIT 1", args)
        return dict(rows[0]) if rows else None

    def status_rows(self, since=0):
        """Raw status_log rows since a unix time, oldest first."""
        if not self.ok:
            return []
        return [dict(r) for r in self._q(
            "SELECT * FROM status_log WHERE ts >= ? ORDER BY ts", (since,))]

    def crashes(self):
        if not self.ok:
            return []
        out = []
        for r in self._q("SELECT * FROM crashes ORDER BY ts"):
            d = dict(r)
            try:
                d["frames"] = json.loads(d["frames"] or "[]")
            except ValueError:
                d["frames"] = []
            out.append(d)
        return out

    def window(self, start, end):
        """Everything recorded between two unix times, for an investigation:
        positions, samples, events and level-ups."""
        if not self.ok:
            return {}
        a = (int(start), int(end))
        return {
            "positions": [dict(r) for r in self._q(
                "SELECT * FROM positions WHERE ts BETWEEN ? AND ?"
                " ORDER BY ts", a)],
            "samples": [dict(r) for r in self._q(
                "SELECT * FROM samples WHERE ts BETWEEN ? AND ?"
                " ORDER BY ts", a)],
            "events": [dict(r) for r in self._q(
                "SELECT * FROM events WHERE ts BETWEEN ? AND ?"
                " ORDER BY ts", a)],
            "levels": [dict(r) for r in self._q(
                "SELECT * FROM levels WHERE ts BETWEEN ? AND ?"
                " ORDER BY ts", a)],
        }

    def last_alive_before(self, ts):
        """The newest metrics sample before a time (None if none)."""
        if not self.ok:
            return None
        return self._q("SELECT max(ts) FROM samples WHERE ts < ?",
                       (int(ts),))[0][0]

    def last_moved(self, uid, x, y, window=3600, eps=500):
        """When a player got to where they are now: (unix time, sure).

        Walks the recorded positions (one a minute) back from the newest and
        returns the first one at the current spot - within `eps` world units,
        5 m by default, so turning around or small steps do not count. `sure`
        is False when they have been there for the whole window."""
        if not self.ok:
            return None, False
        rows = self._q("SELECT ts, x, y FROM positions WHERE uid=? AND ts >= ?"
                       " ORDER BY ts DESC", (uid, int(time.time() - window)))
        first = None
        for ts, px, py in rows:
            if (px - x) ** 2 + (py - y) ** 2 > eps * eps:
                return first, True
            first = ts
        return first, False

    def last_join(self, uid, before):
        """(ts, kind, name) of the player's last join/leave before a time."""
        if not self.ok:
            return None
        rows = self._q("SELECT ts, kind, name FROM events WHERE uid=? AND"
                       " ts <= ? AND kind IN ('join', 'leave') ORDER BY ts"
                       " DESC LIMIT 1", (uid, int(before)))
        return tuple(rows[0]) if rows else None

    def table_stats(self):
        """Per table: rows, and the oldest and newest timestamp."""
        if not self.ok:
            return []
        out = []
        for t, col in (("samples", "ts"), ("positions", "ts"),
                       ("events", "ts"), ("status_log", "ts"),
                       ("levels", "ts"), ("crashes", "ts"),
                       ("players", "last_seen"), ("backups", "mtime"),
                       ("web_users", "created"), ("web_sessions", "expires"),
                       ("paldeck_finds", "first_ts"), ("achievements", "ts"),
                       ("meta", None)):
            if col:
                n, lo, hi = self._q("SELECT count(*), min(%s), max(%s)"
                                    " FROM %s" % (col, col, t))[0]
            else:
                n, lo, hi = self._q("SELECT count(*) FROM %s" % t)[0][0], \
                    None, None
            out.append({"table": t, "rows": n, "oldest": _dt(lo),
                        "newest": _dt(hi)})
        return out

    def activity_matrix(self, sessions, weeks=8):
        """7x24 grid of seconds played, bucketed by local weekday and hour.

        Sessions are supplied by the caller (folded from the event log) so the
        grid reflects exactly what the player list shows.
        """
        grid = [[0.0] * 24 for _ in range(7)]
        horizon = datetime.now(timezone.utc) - timedelta(weeks=weeks)
        for rec in sessions.values():
            spans = list(rec.get("sessions") or [])
            if rec.get("current_since"):
                spans.append((rec["current_since"], datetime.now(timezone.utc)))
            for start, end in spans:
                if end <= horizon:
                    continue
                start = max(start, horizon)
                cur = start.astimezone()
                stop = end.astimezone()
                while cur < stop:
                    nxt = min(stop, (cur + timedelta(hours=1)).replace(
                        minute=0, second=0, microsecond=0))
                    grid[cur.weekday()][cur.hour] += (nxt - cur).total_seconds()
                    cur = nxt
        return grid

    @staticmethod
    def _file_bytes(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0

    def disk_usage(self):
        """Bytes the database takes on disk (file plus its WAL)."""
        total = 0
        for p in (self.path, self.path + "-wal"):
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        return total

    def stats(self):
        if not self.ok:
            return {}
        out = {t: self._q("SELECT count(*) FROM %s" % t)[0][0]
               for t in ("events", "samples", "players", "levels",
                         "backups", "positions", "status_log", "crashes")}
        first, last = self._q("SELECT min(ts), max(ts) FROM events")[0]
        oldest = self._q("SELECT min(ts) FROM samples")[0][0]
        out.update(first_event=_dt(first), last_event=_dt(last),
                   oldest_sample=_dt(oldest), bytes=self.disk_usage(),
                   data_bytes=self.db_bytes(), cap=MAX_DB_BYTES,
                   metrics_days=METRICS_DAYS, events_months=EVENTS_MONTHS,
                   file_bytes=self._file_bytes(self.path),
                   wal_bytes=self._file_bytes(self.path + "-wal"),
                   pruned_at=_dt(self.pruned_at), path=self.path)
        return out

    # -- the Paldeck race -------------------------------------------------

    def record_finds(self, rows):
        """Merge (player id, species, unix time, how) rows, keeping the
        earliest time for each player and species. Returns rows added."""
        if not rows or not self.ok:
            return 0
        n = [0]

        def go():
            for pid, sp, ts, how in rows:
                cur = self.db.execute(
                    "INSERT INTO paldeck_finds VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(pid, species) DO UPDATE SET"
                    " first_ts = excluded.first_ts, how = excluded.how"
                    " WHERE excluded.first_ts < paldeck_finds.first_ts",
                    (pid, sp, int(ts), how))
                n[0] += cur.rowcount
        with self._lock:
            self._write(go)
        return n[0]

    def record_achievements(self, rows):
        """(player id, key, tier, unix time) rows; a tier keeps the time it
        was first seen. Returns the rows that are new."""
        if not rows or not self.ok:
            return 0
        n = [0]

        def go():
            for pid, key, tier, ts in rows:
                n[0] += self.db.execute(
                    "INSERT OR IGNORE INTO achievements VALUES (?, ?, ?, ?)",
                    (pid, key, int(tier), int(ts))).rowcount
        with self._lock:
            self._write(go)
        return n[0]

    # -- chat (palchat) ------------------------------------------------------

    def add_chat(self, ts, source, name, text):
        """Keep a chat message; the id, or None when it is already kept
        (the log is read again after a reconnect)."""
        if not self.ok:
            return None
        with self._lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO chat (ts, source, name, text) "
                "VALUES (?, ?, ?, ?)", (int(ts), source, name, text))
            new = cur.lastrowid if cur.rowcount else None
            if new and new % 500 == 0:
                self.db.execute("DELETE FROM chat WHERE id <= ?",
                                (new - CHAT_KEEP,))
            self.db.commit()
            return new

    def chat(self, after=None, limit=100):
        """[{id, ts, source, name, text}] oldest first: those after id
        `after`, or the newest `limit`."""
        if not self.ok:
            return []
        with self._lock:
            if after is not None:
                rows = self.db.execute(
                    "SELECT id, ts, source, name, text FROM chat "
                    "WHERE id > ? ORDER BY id LIMIT ?", (after, limit))
            else:
                rows = self.db.execute(
                    "SELECT * FROM (SELECT id, ts, source, name, text FROM "
                    "chat ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))
            return [dict(zip(("id", "ts", "source", "name", "text"), r))
                    for r in rows.fetchall()]

    def achievement_log(self):
        """[{pid, key, tier, ts}]"""
        if not self.ok:
            return []
        with self._lock:
            return [dict(r) for r in self._q("SELECT * FROM achievements")]

    def level_firsts(self, levels):
        """{level: (uid, when)} for the first player to reach each level,
        for the levels reached since paltop started keeping track."""
        if not self.ok:
            return {}
        out = {}
        with self._lock:
            # levels players already had when paltop first saw them all
            # carry that moment: nobody can be said to be first to those
            start = (self._q("SELECT min(ts) FROM levels")[0][0] or 0) + 3600
            for lv in levels:
                rows = self._q("SELECT uid, min(ts) AS ts FROM levels"
                               " WHERE level >= ? GROUP BY uid ORDER BY ts"
                               " LIMIT 1", (lv,))
                if rows and rows[0]["ts"] and rows[0]["ts"] > start:
                    out[lv] = (rows[0]["uid"], rows[0]["ts"])
        return out

    def finds(self):
        """[{pid, species, first_ts, how}]"""
        if not self.ok:
            return []
        with self._lock:
            return [dict(r) for r in self._q("SELECT * FROM paldeck_finds")]

    # -- web page users ----------------------------------------------------

    @staticmethod
    def user_key(name):
        return " ".join((name or "").split()).casefold()

    def web_user(self, name):
        """The user row for `name` (any case), or None."""
        if not self.ok:
            return None
        with self._lock:
            rows = self._q("SELECT * FROM web_users WHERE key = ?",
                           (self.user_key(name),))
        return dict(rows[0]) if rows else None

    def web_users(self):
        if not self.ok:
            return []
        with self._lock:
            return [dict(r) for r in self._q(
                "SELECT key, name, created, last_login, player FROM web_users"
                " ORDER BY key")]

    def add_web_user(self, name, pw_hash, player=None):
        """False when the name (any case) or the player is taken."""
        if not self.ok:
            return False
        name = " ".join(name.split())
        with self._lock:
            try:
                self.db.execute(
                    "INSERT INTO web_users (key, name, pw, created, player)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (self.user_key(name), name, pw_hash, int(time.time()),
                     player))
                return True
            except sqlite3.IntegrityError:
                return False

    def link_web_user(self, name, player):
        """Link the user to an in-game player id. False when another user
        already has that player."""
        with self._lock:
            try:
                self.db.execute("UPDATE web_users SET player = ? WHERE key = ?",
                                (player, self.user_key(name)))
                return True
            except sqlite3.IntegrityError:
                return False

    def set_web_password(self, name, pw_hash):
        with self._lock:
            self.db.execute("UPDATE web_users SET pw = ? WHERE key = ?",
                            (pw_hash, self.user_key(name)))

    def remove_web_user(self, name):
        """Removes the user and logs out all their browsers."""
        key = self.user_key(name)
        with self._lock:
            if not self._q("SELECT 1 FROM web_users WHERE key = ?", (key,)):
                return False
            return self._write(lambda: (
                self.db.execute("DELETE FROM web_sessions WHERE user = ?",
                                (key,)),
                self.db.execute("DELETE FROM web_users WHERE key = ?",
                                (key,))))

    def add_web_session(self, token_hash, name, expires):
        key = self.user_key(name)
        now = int(time.time())
        with self._lock:
            self._write(lambda: (
                self.db.execute("DELETE FROM web_sessions WHERE expires < ?",
                                (now,)),
                self.db.execute("INSERT OR REPLACE INTO web_sessions"
                                " VALUES (?, ?, ?)",
                                (token_hash, key, int(expires))),
                self.db.execute("UPDATE web_users SET last_login = ?"
                                " WHERE key = ?", (now, key))))

    def web_session(self, token_hash):
        """The user logged in with this session, or None."""
        if not self.ok:
            return None
        with self._lock:
            rows = self._q(
                "SELECT u.key, u.name, u.player"
                " FROM web_sessions s JOIN web_users u"
                " ON u.key = s.user WHERE s.token = ? AND s.expires > ?",
                (token_hash, int(time.time())))
        return dict(rows[0]) if rows else None

    def remove_web_session(self, token_hash):
        with self._lock:
            self.db.execute("DELETE FROM web_sessions WHERE token = ?",
                            (token_hash,))

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
            self.ok = False

    # -- one-off import of the short-lived text-file history -------------

    def _import_text_history(self):
        """Pull state/history/*.jsonl and players/backups/meta.json (the text
        format used briefly on 25.09.2026) into the database, then move them
        into state/history.migrated-<date>."""
        base = os.path.dirname(self.path) or "."
        hist = os.path.join(base, "history")
        if not os.path.isdir(hist):
            return
        recs = []
        for p in sorted(glob.glob(os.path.join(hist, "*.jsonl"))):
            with open(p) as fh:
                for line in fh:
                    try:
                        recs.append(json.loads(line))
                    except ValueError:
                        pass

        def load(name, default):
            try:
                with open(os.path.join(base, name)) as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                return default
        players = load("players.json", {})
        backups = load("backups.json", [])
        meta = load("meta.json", {})

        def go():
            x = self.db.execute
            for r in recs:
                k, t = r.get("k"), r.get("t")
                if k == "e":
                    x("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                      (t, r["kind"], r.get("u") or "", r.get("n") or "",
                       r.get("pid"), r.get("ip"), r.get("d") or ""))
                elif k == "l":
                    x("INSERT INTO levels VALUES (?,?,?) ON CONFLICT(uid, level)"
                      " DO UPDATE SET ts=min(ts, excluded.ts)",
                      (r["u"], r["lv"], t))
                elif k == "st":
                    x("INSERT OR IGNORE INTO status_log VALUES (?,?,?)",
                      (t, r["state"], r.get("d") or ""))
                elif k == "s":
                    x("INSERT OR IGNORE INTO samples VALUES"
                      " (?,?,?,?,?,?,?,?,?,?)",
                      (t, r.get("pl"), r.get("fps"), r.get("ft"),
                       r.get("cpu"), r.get("mem"), r.get("days"),
                       r.get("camps"), r.get("hcpu"), r.get("hmem")))
                elif k == "p":
                    x("INSERT OR IGNORE INTO positions VALUES (?,?,?,?)",
                      (t, r["u"], r.get("x"), r.get("y")))
            cols = ("uid", "name", "account", "pid", "ip", "level",
                    "peak_level", "first_seen", "last_seen", "x", "y")
            for uid, p in players.items():
                p = dict(p, uid=uid)
                x("INSERT OR REPLACE INTO players VALUES (%s)"
                  % ",".join("?" * len(cols)), [p.get(c) for c in cols])
            for b in backups:
                x("INSERT OR REPLACE INTO backups VALUES (?,?,?)",
                  (b["name"], b["mtime"], b["size"]))
            for key, val in meta.items():
                x("INSERT OR REPLACE INTO meta VALUES (?,?)",
                  (key, json.dumps(val)))
        if not self._write(go):
            return                              # keep the files, retry later
        dest = "%s.migrated-%s" % (hist, time.strftime("%Y%m%d"))
        os.replace(hist, dest)
        for name in ("players.json", "backups.json", "meta.json"):
            p = os.path.join(base, name)
            if os.path.exists(p):
                os.replace(p, os.path.join(dest, name))
            for extra in (p + ".lock", p + ".tmp"):
                if os.path.exists(extra):
                    os.remove(extra)


_FALLBACK = {"ingest_events": 0, "events": [], "known_players": {},
             "levels_gained": 0,
             "level_series": [], "series": [], "backup_series": [],
             "status_history": [], "stats": {}, "disk_usage": 0,
             "record_crashes": 0, "status_rows": [], "crashes": [],
             "window": {}, "table_stats": [], "last_moved": (None, False),
             "db_bytes": 0}


def _locked(fn):
    fallback = _FALLBACK.get(fn.__name__)

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            try:
                return fn(self, *args, **kwargs)
            except sqlite3.Error as exc:
                self.error = "%s: %s" % (type(exc).__name__, exc)
                if fn.__name__ == "meta_get":
                    return args[1] if len(args) > 1 else kwargs.get("default")
                return fallback
    return wrapper


# paltop reads history on the UI thread while a worker thread polls, over one
# shared connection
for _name in ("ingest_events", "observe_players", "record_sample",
              "record_positions", "record_status", "record_backups",
              "meta_get", "meta_set", "prune", "events", "known_players",
              "levels_gained", "level_series",
              "series", "backup_series", "status_history", "stats",
              "disk_usage", "db_bytes", "close", "record_crashes",
              "last_status", "status_rows", "crashes", "window", "last_join",
              "last_alive_before", "scrub_secrets", "last_moved",
              "table_stats"):
    setattr(Store, _name, _locked(getattr(Store, _name)))
