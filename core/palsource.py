#!/usr/bin/env python3
"""
palsource.py - read-only data layer for paltop.

SAFETY CONTRACT
    Everything in this module is read-only with respect to the Palworld server:

      * REST API .... HTTP GET only, and only against the four informational
                      endpoints listed in Rest.SAFE. The mutating endpoints
                      (/announce, /kick, /ban, /save, /shutdown, /stop) are
                      never constructed, and Rest.get() refuses any path that
                      is not on the allow-list.
      * Docker ...... `docker inspect`, `docker logs`, and `docker exec cat`
                      of a config file. No start/stop/restart/kill/exec-write.
      * Filesystem .. stat/read of the save + backup directories, /proc, /sys.

    Nothing here can stop, restart, save or otherwise disturb the server.
"""

from __future__ import annotations

import base64
import glob
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import palpaths
import palcrash
import palpak
import palsave
from palstore import Store

STATE_DIR = palpaths.STATE_DIR
CLK_TCK = os.sysconf("SC_CLK_TCK")
NCPU = os.cpu_count() or 1
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def run(args, timeout=8):
    """Run a command, return stdout ('' on any failure). Never raises."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.stdout
    except Exception:
        return ""


def read(path, default=""):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return default


# --------------------------------------------------------------------------
# cron
# --------------------------------------------------------------------------

class Cron:
    """Minimal 5-field cron expression - enough for the schedules the
    palworld-server-docker image installs via supercronic."""

    FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))

    def __init__(self, expr):
        self.expr = expr.strip().strip('"').strip("'")
        parts = self.expr.split()
        if len(parts) != 5:
            raise ValueError("not a 5-field cron expression: %r" % expr)
        self.sets = [self._field(p, lo, hi)
                     for p, (lo, hi) in zip(parts, self.FIELDS)]

    @staticmethod
    def _field(spec, lo, hi):
        out = set()
        for chunk in spec.split(","):
            step = 1
            if "/" in chunk:
                chunk, raw = chunk.split("/", 1)
                step = max(1, int(raw))
            if chunk in ("*", ""):
                a, b = lo, hi
            elif "-" in chunk:
                a, b = (int(x) for x in chunk.split("-", 1))
            else:
                a = b = int(chunk)
                if step != 1:          # "5/10" means 5,15,25... up to hi
                    b = hi
            out.update(range(a, b + 1, step))
        return out

    def matches(self, dt):
        dom_all = self.sets[2] == set(range(1, 32))
        dow_all = self.sets[4] == set(range(0, 7))
        dom_ok = dt.day in self.sets[2]
        dow_ok = ((dt.weekday() + 1) % 7) in self.sets[4]
        # cron: when both day fields are restricted, either one matching wins
        day_ok = (dom_ok or dow_ok) if not dom_all and not dow_all \
            else (dom_ok and dow_ok)
        return (dt.minute in self.sets[0] and dt.hour in self.sets[1]
                and dt.month in self.sets[3] and day_ok)

    def next_after(self, dt):
        cur = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60):
            if self.matches(cur):
                return cur
            cur += timedelta(minutes=1)
        return None


# --------------------------------------------------------------------------
# REST client
# --------------------------------------------------------------------------

class Rest:
    """Palworld REST API client, restricted to informational GETs."""

    SAFE = ("/v1/api/info", "/v1/api/players",
            "/v1/api/metrics", "/v1/api/settings")

    def __init__(self, host, port, user, password, timeout=2.5):
        self.host, self.port, self.timeout = host, port, timeout
        token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
        self._auth = "Basic " + token
        self.last_error = None
        self.last_latency = None
        # "ok" | "timeout" (accepts the connection, never answers - a hung
        # server) | "refused" (nothing listening - down or starting) | "error"
        self.state = "unknown"
        self.last_ok = None             # wall-clock time of the last answer
        self.failing_since = None       # wall-clock time the current outage began
        self._cycle_timed_out = False

    def begin_cycle(self):
        """Called once per poll. After one timeout in a cycle, the remaining
        calls are skipped, so a hung server costs one timeout per refresh
        rather than one per endpoint."""
        self._cycle_timed_out = False

    def _failed(self, state, exc):
        self.state = state
        self.last_error = "%s: %s" % (type(exc).__name__, exc)
        if self.failing_since is None:
            self.failing_since = time.time()

    def get(self, path):
        if path not in self.SAFE:
            raise ValueError("refusing non read-only endpoint: %r" % path)
        url = "http://%s:%s%s" % (self.host, self.port, path)
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", self._auth)
        req.add_header("Accept", "application/json")
        if self._cycle_timed_out:
            return None
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
            self.last_latency = (time.monotonic() - t0) * 1000.0
            self.last_error = None
            self.state = "ok"
            self.last_ok = time.time()
            self.failing_since = None
            return json.loads(body)
        except (socket.timeout, TimeoutError) as exc:
            self._cycle_timed_out = True
            self._failed("timeout", exc)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                self._cycle_timed_out = True
                self._failed("timeout", exc)
            elif isinstance(reason, (ConnectionRefusedError, ConnectionResetError)):
                self._failed("refused", exc)
            else:
                self._failed("error", exc)
        except (ConnectionRefusedError, ConnectionResetError) as exc:
            self._failed("refused", exc)
        except Exception as exc:                       # auth / parse / other
            self._failed("error", exc)
        return None


# --------------------------------------------------------------------------
# host sampling
# --------------------------------------------------------------------------

class CpuSampler:
    """Per-core utilisation from /proc/stat, differenced between samples."""

    def __init__(self):
        self.prev = {}

    def sample(self):
        cur, pct = {}, {}
        for line in read("/proc/stat").splitlines():
            if not line.startswith("cpu"):
                break
            f = line.split()
            vals = [int(x) for x in f[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            cur[f[0]] = (sum(vals), idle)
        for name, (total, idle) in cur.items():
            p_total, p_idle = self.prev.get(name, (None, None))
            if p_total is None or total <= p_total:
                pct[name] = 0.0
            else:
                dt, di = total - p_total, idle - p_idle
                pct[name] = max(0.0, min(100.0, 100.0 * (dt - di) / dt))
        self.prev = cur
        return pct


class RateSampler:
    """Turns a monotonically increasing counter into a per-second rate."""

    def __init__(self):
        self.prev_val = None
        self.prev_t = None

    def sample(self, value):
        now = time.monotonic()
        rate = 0.0
        if self.prev_val is not None and value >= self.prev_val:
            dt = now - self.prev_t
            if dt > 0:
                rate = (value - self.prev_val) / dt
        self.prev_val, self.prev_t = value, now
        return rate


def meminfo():
    out = {}
    for line in read("/proc/meminfo").splitlines():
        k, _, rest = line.partition(":")
        try:
            out[k] = int(rest.split()[0]) * 1024
        except (IndexError, ValueError):
            pass
    return out


def core_temps():
    """CPU package + per-core temperatures, in Celsius."""
    out = {}
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if read(hw + "/name").strip() != "coretemp":
            continue
        for inp in sorted(glob.glob(hw + "/temp*_input")):
            label = read(inp.replace("_input", "_label")).strip() \
                or os.path.basename(inp)
            try:
                out[label] = int(read(inp, "0")) / 1000.0
            except ValueError:
                pass
    if not out:                                        # fall back to acpitz
        for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
            t = read(zone + "/type").strip()
            try:
                out[t] = int(read(zone + "/temp", "0")) / 1000.0
            except ValueError:
                pass
    return out


def cpu_model():
    for line in read("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            name = line.split(":", 1)[1].strip()
            for junk in ("(R)", "(TM)", "CPU ", "Processor"):
                name = name.replace(junk, "")
            return re.sub(r"\s+@.*$", "", name).strip()
    return "cpu"


def cpu_freq_ghz():
    vals = [float(l.split(":")[1]) for l in read("/proc/cpuinfo").splitlines()
            if l.startswith("cpu MHz")]
    return (sum(vals) / len(vals) / 1000.0) if vals else None


def loadavg():
    try:
        return tuple(float(x) for x in read("/proc/loadavg").split()[:3])
    except (ValueError, IndexError):
        return (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------
# log history
# --------------------------------------------------------------------------

RE_JOIN = re.compile(
    r"\[(?P<ts>[\d\-]+ [\d:]+)\] \[LOG\] (?P<name>.+?) joined the server\. "
    r"\(User id: (?P<uid>[^,]+), Player id: (?P<pid>\w+)\)")
RE_LEFT = re.compile(
    r"\[(?P<ts>[\d\-]+ [\d:]+)\] \[LOG\] (?P<name>.+?) left the server\. "
    r"\(User id: (?P<uid>[^)]+)\)")
RE_CMD = re.compile(
    r"\[(?P<ts>[\d\-]+ [\d:]+)\] \[LOG\] (?P<name>.+?) executed the "
    r"command\. (?P<detail>.*?)\s*$")
RE_CONN = re.compile(
    r"\[(?P<ts>[\d\-]+ [\d:]+)\] \[LOG\] (?P<acct>.+?) "
    r"(?P<ip>\d+\.\d+\.\d+\.\d+) connected the server\. "
    r"\(User id: (?P<uid>[^)]+)\)")


# in-game commands whose arguments are secrets: the server logs them in full,
# paltop keeps only the command name
SECRET_COMMANDS = ("adminpassword",)


def mask_command(detail):
    """'AdminPassword hunter2' -> 'AdminPassword ****'."""
    word = detail.split(" ", 1)[0]
    if word.strip("/'\"").lower() in SECRET_COMMANDS and " " in detail:
        return word + " ****"
    return detail


def parse_log_events(container, hours=336, limit=6000, log_tz=timezone.utc):
    """Player join/leave events from `docker logs`, newest last.

    The server writes log timestamps in the container's timezone (UTC for
    this image); they are returned here as aware UTC datetimes.
    """
    cmd = ("docker logs --since %dh %s 2>&1 | "
           "grep -aE 'the server\\.|executed the command' | tail -n %d"
           % (hours, shlex.quote(container), limit))
    raw = run(["bash", "-c", cmd], timeout=30)
    events = []
    for line in raw.splitlines():
        line = ANSI.sub("", line)
        for kind, rx in (("join", RE_JOIN), ("left", RE_LEFT),
                         ("conn", RE_CONN), ("cmd", RE_CMD)):
            m = rx.search(line)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S") \
                    .replace(tzinfo=log_tz)
            except ValueError:
                break
            g = m.groupdict()
            events.append({
                "kind": kind, "ts": ts, "uid": (g.get("uid") or "").strip(),
                "name": (g.get("name") or g.get("acct") or "").strip(),
                "pid": g.get("pid"), "ip": g.get("ip"),
                "detail": mask_command((g.get("detail") or "").strip()),
            })
            break
    events.sort(key=lambda e: e["ts"])
    return events


def sessions_from_events(events, now):
    """Fold join/leave events into per-user session statistics."""
    per = {}
    for ev in events:
        if ev["kind"] == "cmd":
            continue
        uid = ev["uid"]
        if not uid:
            continue
        rec = per.setdefault(uid, {
            "uid": uid, "name": ev["name"], "pid": None, "ip": None,
            "sessions": [], "open": None, "joins": 0,
        })
        if ev["kind"] == "conn":
            rec["ip"] = ev["ip"]
            continue
        rec["name"] = ev["name"] or rec["name"]
        if ev["kind"] == "join":
            rec["pid"] = ev["pid"] or rec["pid"]
            rec["joins"] += 1
            if rec["open"] is None:
                rec["open"] = ev["ts"]
        elif ev["kind"] == "left" and rec["open"] is not None:
            rec["sessions"].append((rec["open"], ev["ts"]))
            rec["open"] = None
    for rec in per.values():
        closed = sum((b - a).total_seconds() for a, b in rec["sessions"])
        live = (now - rec["open"]).total_seconds() if rec["open"] else 0.0
        rec["playtime"] = closed + max(0.0, live)
        rec["current_since"] = rec["open"]
        ends = [b for _, b in rec["sessions"]]
        rec["last_seen"] = rec["open"] or (max(ends) if ends else None)
    return per


# --------------------------------------------------------------------------
# the source
# --------------------------------------------------------------------------

class PalSource:
    def __init__(self, container="palworld-server", rest_host="127.0.0.1",
                 use_net=True, db_path=None):
        self.container = container
        self.rest_host = rest_host
        self.use_net = use_net
        self.cpu = CpuSampler()
        self.net_rx = RateSampler()
        self.net_tx = RateSampler()
        self.cg_cpu = RateSampler()
        self.db = Store(db_path or os.path.join(STATE_DIR, "paltop.db"))
        self.db.scrub_secrets(mask_command)     # older rows kept them in full
        self._build = None
        self._build_at = 0.0
        self.saves = None
        self.elements = {}
        self.pal_names = {}
        self.conditions = {}
        self.gamedata = {}
        self._finds_at = None
        # the cell size is read from the game files, not fitted
        self._integrity = {}
        self.rest = None
        self.env = {}
        self.err = []
        self._slow_at = 0.0
        self._slow = {}
        self._last_metrics = None
        self._last_state = None
        self._last_online = ""
        self._crash_guids = set()
        self._heal_at = 0.0
        self._heals = []
        self.discover()

    # -- discovery ---------------------------------------------------------

    def discover(self):
        self.err = []
        raw = run(["docker", "inspect", self.container], timeout=10)
        try:
            d = json.loads(raw)[0]
        except (ValueError, IndexError):
            self.err.append("cannot inspect container %r" % self.container)
            self.inspect = {}
            self.env = {}
            self.pid = 0
            self.cgroup = None
            self.data_dir = None
            self.rest = None
            return
        self.inspect = d
        env = {}
        for item in d.get("Config", {}).get("Env", []):
            k, _, v = item.partition("=")
            env[k] = v.strip().strip('"').strip("'")
        self.env = env
        self.pid = int(d.get("State", {}).get("Pid") or 0)

        cg = read("/proc/%d/cgroup" % self.pid).strip().split("::")[-1] \
            if self.pid else ""
        self.cgroup = ("/sys/fs/cgroup" + cg) if cg.startswith("/") else None

        self.data_dir = None
        for m in d.get("Mounts", []):
            if m.get("Destination") == "/palworld":
                self.data_dir = m.get("Source")
        palpaths.set_data_dir(self.data_dir)
        self.save_dir = None
        if self.data_dir:
            hits = glob.glob(os.path.join(
                self.data_dir, "Pal/Saved/SaveGames/*/*/Level.sav"))
            if hits:
                self.save_dir = os.path.dirname(hits[0])

        if self.save_dir:
            self.saves = palsave.SaveCache(
                os.path.join(self.save_dir, "Level.sav"))
        try:                                   # species -> element, from the pak
            self.elements = palpak.read_elements(
                cache=os.path.join(STATE_DIR, "elements.json"))
            self.pal_names = palpak.read_pal_names(
                cache=os.path.join(STATE_DIR, "palnames.json"))
            self.conditions = palpak.read_conditions(
                cache=os.path.join(STATE_DIR, "conditions.json"))
            self.gamedata = palpak.read_game_data(
                cache=os.path.join(STATE_DIR, "gamedata.json"))
        except Exception as exc:
            self.err.append("element table: %s" % exc)

        port = env.get("REST_API_PORT", "8212")
        pw = env.get("ADMIN_PASSWORD", "")
        if env.get("REST_API_ENABLED", "").lower() == "true":
            self.rest = Rest(self.rest_host, port, "admin", pw)
        else:
            self.err.append("REST_API_ENABLED is not true - no live data")
            self.rest = None

    # -- slow-changing facts ----------------------------------------------

    def slow(self, now):
        """Refreshed every ~20s: backups, logs, container state, settings."""
        out = {}

        raw = run(["docker", "inspect", self.container], timeout=10)
        try:
            d = json.loads(raw)[0]
            state = d.get("State", {})
            out["status"] = state.get("Status", "?")
            out["health"] = (state.get("Health") or {}).get("Status")
            out["restart_count"] = d.get("RestartCount", 0)
            out["started_at"] = _iso(state.get("StartedAt"))
            out["image"] = d.get("Config", {}).get("Image", "")
            if self.pid != int(state.get("Pid") or 0):
                self.discover()
        except (ValueError, IndexError):
            out["status"] = "unreachable"

        # crontab actually installed in the container (supercronic)
        out["crontab"] = []
        cron_raw = run(["docker", "exec", self.container,
                        "cat", "/home/steam/server/crontab"], timeout=8)
        for line in cron_raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split(None, 5)
            if len(fields) >= 6:
                out["crontab"].append((" ".join(fields[:5]), fields[5]))

        installed = {job for expr, job in out["crontab"]}
        out["backup_cron"] = _cron_info(
            self.env.get("BACKUP_CRON_EXPRESSION"), now,
            active=any("backup" in j for j in installed))
        # the image's variables are AUTO_REBOOT_*; AUTO_RESTART_* is not read
        # by it, but was what this compose file originally used, so still
        # recognise it in order to flag that it does nothing
        expr = (self.env.get("AUTO_REBOOT_CRON_EXPRESSION")
                or self.env.get("AUTO_RESTART_CRON_EXPRESSION"))
        out["restart_cron"] = _cron_info(
            expr, now,
            active=any("reboot" in j or "restart" in j for j in installed))
        out["restart_cron"]["warn_minutes"] = 30 if any(
            "auto_reboot" in j for j in installed) else None
        # scripts/auto_reboot.sh only acts when the local hour matches, so of
        # the cron's fire times, the next *real* restart is the first one
        # that lands on that hour in AUTO_REBOOT_TZ
        rc = out["restart_cron"]
        gate = self.env.get("AUTO_REBOOT_LOCAL_HOUR")
        if gate and rc.get("valid"):
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(self.env.get("AUTO_REBOOT_TZ")
                              or self.env.get("TZ") or "UTC")
                cron, t, hit = Cron(rc["expr"]), now, None
                for _ in range(96):
                    t = cron.next_after(t)
                    if t is None:
                        break
                    if t.astimezone(tz).hour == int(gate):
                        hit = t
                        break
                rc["next"] = hit
                rc["local_hour"] = int(gate)
                rc["tz"] = str(tz)
            except Exception:
                pass

        out["backups"] = self._backups()
        out["manual_backups"] = self._manual_backups()
        out["world"] = self._world()

        # merge this log window into the database, then read the full history
        # back out of it - `docker logs` only reaches back to container
        # creation, the database remembers everything paltop has ever seen.
        fresh = parse_log_events(self.container)
        out["ingested"] = self.db.ingest_events(fresh)
        self.db.record_backups(out["backups"])
        self.db.prune()
        stored = self.db.events()
        out["events"] = stored or fresh
        out["sessions"] = sessions_from_events(out["events"], now)
        out["failed_joins"] = failed_joins(out["events"])
        out["commands"] = [e for e in out["events"] if e["kind"] == "cmd"]
        self._track_restarts(out)
        out["db"] = self.db.stats()
        out["db_tables"] = self.db.table_stats()

        if self.rest:
            # keep showing the last good values while the server is down, so
            # the screen still says which server and world this is
            out["info"] = (self.rest.get("/v1/api/info")
                           or (self._slow or {}).get("info") or {})
            out["settings"] = (self.rest.get("/v1/api/settings")
                               or (self._slow or {}).get("settings") or {})
        if self.data_dir:
            out["settings_diff"] = settings_diff(
                out.get("settings"),
                parse_ini_options(os.path.join(
                    self.data_dir, "DefaultPalWorldSettings.ini")))

        # newest archive: does it actually decompress, and did it shrink?
        if out["backups"]:
            newest = out["backups"][0]
            if newest["name"] not in self._integrity:
                ok, msg = check_gzip(newest["path"])
                self._integrity = {newest["name"]: (ok, msg)}
            out["backup_ok"] = self._integrity.get(newest["name"], (None, []))
            if len(out["backups"]) > 1:
                prev = out["backups"][1]["size"]
                out["backup_shrink"] = (
                    (prev - newest["size"]) / prev if prev else 0.0)

        # base camps come from Level.sav, which only changes when the server
        # writes a save, so this is a no-op most of the time
        if self.saves:
            self.saves.refresh()
            out["base_camps"] = self.saves.base_camps
            out["save_players"] = {p["uid"]: p for p in self.saves.players
                                   if p.get("uid")}
            # summarise per owner once here, so drawing a frame stays cheap
            by_owner = {}
            for p in self.saves.pals:
                # unowned pals (base workers with no recorded owner) group
                # under "" so these stay string-keyed and JSON-sortable
                by_owner.setdefault(p["owner"] or "", []).append(p)
            out["save_pal_counts"] = {k: len(v) for k, v in by_owner.items()}
            out["save_top_pals"] = {
                k: sorted(v, key=lambda q: (-q["level"], q["species"]))[:60]
                for k, v in by_owner.items()}
            out["save_pal_total"] = len(self.saves.pals)
            # the Paldeck race: when each player first had each Pal
            if self.saves.loaded_at and \
                    self.saves.loaded_at != self._finds_at and self.gamedata:
                self._finds_at = self.saves.loaded_at
                try:
                    import palpedia
                    es = palpedia.entries(self.gamedata)
                    self.db.record_finds(palpedia.find_rows(
                        self.gamedata, self.saves,
                        palpedia.entry_of(self.gamedata, es), time.time()))
                    if not self.db.meta_get("finds_since"):
                        self.db.meta_set("finds_since", int(time.time()))
                    self._record_achievements(out, es)
                except Exception as exc:        # never take the sample down
                    self.err.append("paldeck finds: %s" % exc)
            out["save_error"] = self.saves.error
            out["save_time"] = self.saves.loaded_at
        out["incidents"] = palcrash.build(
            self.db, out.get("base_camps"), self.env,
            self._heals, palcrash.admin_restarts(
                os.path.join(STATE_DIR, "admin.log")))
        out["elements"] = self.elements
        out["pal_names"] = self.pal_names
        out["conditions"] = self.conditions

        out["build"] = installed_build(self.data_dir)
        if self.use_net and time.monotonic() - self._build_at > 21600:
            self._build = latest_build()
            self._build_at = time.monotonic()
        out["build_latest"] = self._build
        if self.data_dir:
            try:
                out["disk"] = shutil.disk_usage(self.data_dir)
            except OSError:
                pass
        return out

    def _track_restarts(self, out):
        """Keep what the crash log needs: the game's crash reports (the game
        deletes them after two days), container starts, host boots and the
        restarts autoheal made."""
        reports = [r for r in palcrash.read_reports(self.data_dir)
                   if r["guid"] not in self._crash_guids]
        if reports:
            self.db.record_crashes(reports)
            self._crash_guids.update(r["guid"] for r in reports)
        started = out.get("started_at")
        if started:
            ts = int(started.timestamp())
            if self.db.meta_get("container_started") != ts:
                self.db.record_status(ts, "container_start",
                                      "restart count %s"
                                      % out.get("restart_count", 0))
                self.db.meta_set("container_started", ts)
        boot = _boot_time()
        if boot and abs((self.db.meta_get("host_boot") or 0) - boot) > 5:
            self.db.record_status(boot, "host_boot", "")
            self.db.meta_set("host_boot", boot)
        if time.monotonic() - self._heal_at > 300:
            self._heal_at = time.monotonic()
            logs = run(["docker", "logs", "-t", "--since", "72h",
                        "palworld-autoheal"], timeout=10)
            self._heals = palcrash.autoheal_restarts(logs)
            for ts in self._heals:
                self.db.record_status(ts, "autoheal", self.container)

    def _record_achievements(self, out, entries):
        """Note each achievement tier the first time the save shows it."""
        import palachieve
        import palpedia
        game, saves = self.gamedata, self.saves
        of = palpedia.entry_of(game, entries)
        firsts = palpedia.first_finds(self.db.finds(),
                                      self.db.meta_get("finds_since") or 0)
        counts = palpedia.race_counts(palpedia.found(saves, of), firsts)
        hours = {}
        for rec in (out.get("sessions") or {}).values():
            if rec.get("pid"):
                hours[rec["pid"]] = hours.get(rec["pid"], 0) + \
                    (rec.get("playtime") or 0) / 3600.0
        extra = {}
        for p in saves.players:
            pid = p.get("uid")
            extra[pid] = dict(level=p.get("level") or 0,
                              hours=hours.get(pid, 0),
                              firsts=counts.get(pid, {}).get("firsts", 0),
                              unique=counts.get(pid, {}).get("unique", 0))
        now = time.time()
        rows = []
        for pid, achs in palachieve.compute(game, saves, extra).items():
            for a in achs:
                for tier in range(1, a["tier"] + 1):
                    rows.append((pid, a["key"], tier, now))
        self.db.record_achievements(rows)
        if not self.db.meta_get("achievements_since"):
            self.db.meta_set("achievements_since", int(now))

    def _backups(self):
        """Automatic backups written by the container's backup cron."""
        if not self.data_dir:
            return []
        files = []
        for p in glob.glob(os.path.join(self.data_dir, "backups", "*.tar.gz")):
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append({"path": p, "name": os.path.basename(p),
                          "mtime": st.st_mtime, "size": st.st_size})
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return files

    def _manual_backups(self):
        """Ad-hoc archives sitting next to the compose file."""
        if not self.data_dir:
            return []
        files = []
        for p in glob.glob(os.path.join(
                os.path.dirname(self.data_dir), "*.tar.gz")):
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append({"path": p, "name": os.path.basename(p),
                          "mtime": st.st_mtime, "size": st.st_size})
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return files

    def _world(self):
        w = {"level_mtime": None, "level_size": 0, "players_saved": 0,
             "save_bytes": 0}
        if not self.save_dir:
            return w
        try:
            st = os.stat(os.path.join(self.save_dir, "Level.sav"))
            w["level_mtime"], w["level_size"] = st.st_mtime, st.st_size
        except OSError:
            pass
        saves = glob.glob(os.path.join(self.save_dir, "Players", "*.sav"))
        w["players_saved"] = len(saves)
        w["save_ids"] = {os.path.basename(p)[:-4] for p in saves}
        total = 0
        for root, _, names in os.walk(self.save_dir):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(root, n))
                except OSError:
                    pass
        w["save_bytes"] = total
        return w

    # -- fast sample -------------------------------------------------------

    def sample(self, slow_every=20.0):
        now = datetime.now(timezone.utc)
        snap = {"now": now, "err": list(self.err),
                "container_name": self.container}

        if self.rest:
            self.rest.begin_cycle()
        if time.monotonic() - self._slow_at > slow_every or not self._slow:
            self._slow = self.slow(now)
            self._slow_at = time.monotonic()
        snap.update(self._slow)

        # --- live server data (GET only) ---
        players, metrics = [], {}
        if self.rest:
            pl = self.rest.get("/v1/api/players")
            if isinstance(pl, dict):
                players = pl.get("players") or []
            metrics = self.rest.get("/v1/api/metrics") or {}
            snap["rest_error"] = self.rest.last_error
            snap["rest_latency"] = self.rest.last_latency
            snap["rest_state"] = self.rest.state
            snap["rest_last_ok"] = self.rest.last_ok
            snap["rest_down_for"] = (time.time() - self.rest.failing_since
                                     if self.rest.failing_since else None)
            # a change of server state is worth keeping forever
            # recorded once across every paltop process: only when it
            # differs from the newest state already in the database
            state = self.rest.state
            if players:
                self._last_online = ",".join(
                    sorted(p.get("name", "?") for p in players))
            if state != self._last_state and state != "unknown":
                last = self.db.last_status(("ok",) + tuple(palcrash.OUTAGE))
                if not last or last["state"] != state:
                    detail = self._last_online
                    if self.rest.last_error and state != "ok":
                        detail = "%s | online: %s" % (self.rest.last_error,
                                                      detail or "-")
                    self.db.record_status(time.time(), state, detail)
                self._last_state = state
            if not metrics and self._last_metrics:
                metrics = dict(self._last_metrics)      # identity, not live
                snap["metrics_stale"] = True
            elif metrics:
                self._last_metrics = metrics
        snap["players"] = players
        snap["metrics"] = metrics
        # joins and leaves from the REST API: the server's log can be
        # unreadable (see Store.track_presence). A good answer tells who is
        # on; a server down for a while has nobody on since it went down.
        if self.rest:
            down = self.rest.failing_since
            if isinstance(pl, dict):
                self.db.track_presence(
                    {p.get("userId") or p.get("playerId"):
                     (p.get("name"), p.get("playerId")) for p in players
                     if p.get("userId") or p.get("playerId")}, now)
            elif down and time.time() - down > 90:
                self.db.track_presence(
                    {}, datetime.fromtimestamp(down, timezone.utc))
        self.db.observe_players(players, now)
        self.db.record_positions(now, players)
        snap["store"] = self.db.known_players()

        # --- host ---
        pct = self.cpu.sample()
        snap["cpu_total"] = pct.get("cpu", 0.0)
        snap["cpu_cores"] = [pct.get("cpu%d" % i, 0.0) for i in range(NCPU)]
        snap["load"] = loadavg()
        snap["temps"] = core_temps()
        snap["freq"] = cpu_freq_ghz()
        mi = meminfo()
        snap["mem"] = {
            "total": mi.get("MemTotal", 0),
            "available": mi.get("MemAvailable", 0),
            "cached": mi.get("Cached", 0) + mi.get("SReclaimable", 0),
            "free": mi.get("MemFree", 0),
            "swap_total": mi.get("SwapTotal", 0),
            "swap_free": mi.get("SwapFree", 0),
        }
        snap["mem"]["used"] = snap["mem"]["total"] - snap["mem"]["available"]

        # --- container / game process ---
        snap["container"] = self._container_stats()
        snap["net"] = self._net_stats()
        snap["host_uptime"] = _host_uptime()
        self.db.record_sample(
            now, metrics, snap["container"], snap["cpu_total"],
            100.0 * snap["mem"]["used"] / max(1, snap["mem"]["total"]))
        snap["alerts"] = compute_alerts(snap)
        return snap

    def _container_stats(self):
        out = {"cpu_pct": 0.0, "mem": 0, "pid": self.pid, "proc_uptime": None}
        if self.cgroup:
            for line in read(self.cgroup + "/cpu.stat").splitlines():
                if line.startswith("usage_usec"):
                    usec = int(line.split()[1])
                    # rate of cpu-microseconds per wall second -> % of one core
                    out["cpu_pct"] = self.cg_cpu.sample(usec) / 10000.0
            try:
                out["mem"] = int(read(self.cgroup + "/memory.current", "0"))
            except ValueError:
                pass
        # the game process itself (PalServer-Linux-Shipping)
        game = run(["bash", "-c",
                    "ps -eo pid,etimes,rss,comm --no-headers | "
                    "grep -i 'PalServer-Linux' | head -1"], timeout=6)
        f = game.split()
        if len(f) >= 3:
            out["game_pid"] = int(f[0])
            out["proc_uptime"] = int(f[1])
            out["game_rss"] = int(f[2]) * 1024
        return out

    def _net_stats(self):
        """Traffic on the container's own eth0, read through its netns."""
        out = {"rx": 0.0, "tx": 0.0, "rx_total": 0, "tx_total": 0}
        if not self.pid:
            return out
        for line in read("/proc/%d/net/dev" % self.pid).splitlines():
            name, _, rest = line.partition(":")
            if name.strip() != "eth0":
                continue
            f = rest.split()
            if len(f) >= 9:
                out["rx_total"], out["tx_total"] = int(f[0]), int(f[8])
                out["rx"] = self.net_rx.sample(out["rx_total"])
                out["tx"] = self.net_tx.sample(out["tx_total"])
        return out


def failed_joins(events, window=180):
    """Connections that never completed a join.

    The server logs `connected the server` when a client reaches it and
    `joined the server` once it is actually in the world. A connect with no
    join shortly after means a rejected password, a failed load or a crash -
    exactly the "my friend cannot get in" case.
    """
    joins = {}
    for ev in events:
        if ev["kind"] == "join" and ev["uid"]:
            joins.setdefault(ev["uid"], []).append(ev["ts"])
    out = []
    for ev in events:
        if ev["kind"] != "conn":
            continue
        landed = any(0 <= (j - ev["ts"]).total_seconds() <= window
                     for j in joins.get(ev["uid"], []))
        if not landed:
            out.append(ev)
    return out


def _split_top(body):
    """Split a comma separated list, ignoring commas inside quotes."""
    parts, cur, quoted = [], "", False
    for ch in body:
        if ch == '"':
            quoted = not quoted
            cur += ch
        elif ch == "," and not quoted:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


def parse_ini_options(path):
    """Pull OptionSettings=(...) out of a PalWorldSettings-style ini."""
    m = re.search(r"OptionSettings=\((.*)\)", read(path), re.S)
    if not m:
        return {}
    out = {}
    for part in _split_top(m.group(1)):
        k, sep, v = part.partition("=")
        if sep:
            out[k.strip()] = v.strip().strip('"')
    return out


def _norm_setting(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v).strip().strip('"')
    try:
        return ("%.6f" % float(s)).rstrip("0").rstrip(".") or "0"
    except ValueError:
        return s.lower()


def settings_diff(live, defaults):
    """Only the settings that differ from the shipped defaults."""
    out = []
    for k, v in (live or {}).items():
        if isinstance(v, (dict, list)):
            continue
        d = defaults.get(k)
        if d is None:
            continue
        if _norm_setting(v) != _norm_setting(d):
            out.append({"key": k, "value": v, "default": d})
    out.sort(key=lambda r: r["key"])
    return out


def check_gzip(path, timeout=25):
    """Verify a backup archive really decompresses. Read-only."""
    try:
        p = subprocess.run(["gzip", "-t", path], capture_output=True,
                           text=True, timeout=timeout)
        return (p.returncode == 0,
                (p.stderr or "").strip().splitlines()[:1] or [""])
    except Exception as exc:
        return (None, [str(exc)[:80]])


def installed_build(data_dir):
    """Build id of the installed server, from the Steam app manifest."""
    if not data_dir:
        return None
    m = re.search(r'"buildid"\s+"(\d+)"',
                  read(os.path.join(data_dir, "steamapps",
                                    "appmanifest_2394010.acf")))
    return m.group(1) if m else None


def latest_build(timeout=6.0):
    """Current public build id for the Palworld dedicated server (app 2394010).

    Sends only the app id to a public Steam metadata mirror - no server,
    account or player information leaves the machine. Disable with --no-net.
    """
    try:
        req = urllib.request.Request(
            "https://api.steamcmd.net/v1/info/2394010",
            headers={"Accept": "application/json", "User-Agent": "paltop"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        branch = data["data"]["2394010"]["depots"]["branches"]["public"]
        return {"buildid": str(branch.get("buildid")),
                "updated": int(branch.get("timeupdated") or 0)}
    except Exception:
        return None


def compute_alerts(snap):
    """Threshold checks over the current snapshot, worst first."""
    out = []
    now = snap.get("now") or datetime.now(timezone.utc)

    def add(level, text):
        out.append({"level": level, "text": text})

    if snap.get("status") not in (None, "running"):
        add("crit", "container is %s" % snap.get("status"))
    if snap.get("health") not in (None, "healthy"):
        add("crit", "container health: %s" % snap.get("health"))
    rs = snap.get("rest_state")
    down = snap.get("rest_down_for")
    since = (" for %dm %02ds" % divmod(int(down), 60)) if down else ""
    game_alive = bool((snap.get("container") or {}).get("game_pid"))
    if rs == "timeout" and game_alive:
        add("crit", "SERVER HUNG%s: game process is alive but not answering - "
            "restart it: docker restart %s" % (since, snap.get("container_name")
                                              or "palworld-server"))
    elif rs == "timeout":
        add("crit", "server not answering%s (REST times out)" % since)
    elif rs == "refused" and snap.get("health") == "starting":
        add("warn", "server starting up%s" % since)
    elif rs == "refused":
        add("crit", "server down%s: nothing listening on the REST port" % since)
    elif rs == "error":
        add("crit", "REST API error: %s" % (snap.get("rest_error") or "?"))

    for inc in snap.get("incidents") or []:
        if inc["planned"]:
            continue
        if time.time() - inc["start"] < 24 * 3600:
            add("warn", "%s at %s: %s - press x for the crash log" % (
                palcrash.KIND_LABEL.get(inc["kind"], inc["kind"]).lower(),
                datetime.fromtimestamp(inc["start"]).strftime("%H:%M"),
                ", ".join(p["name"] for p in inc["people"])
                or "nobody online"))
        break

    backups = snap.get("backups") or []
    if not backups:
        add("crit", "no backups found")
    else:
        age = (now - datetime.fromtimestamp(
            backups[0]["mtime"], tz=timezone.utc)).total_seconds()
        if age > 72 * 3600:
            add("crit", "newest backup is %.0fh old" % (age / 3600))
        elif age > 36 * 3600:
            add("warn", "newest backup is %.0fh old" % (age / 3600))
        ok = (snap.get("backup_ok") or (None, []))[0]
        if ok is False:
            add("crit", "newest backup fails gzip integrity check")
        shrink = snap.get("backup_shrink") or 0.0
        if shrink > 0.30:
            add("warn", "newest backup is %.0f%% smaller than the previous one"
                % (shrink * 100))
    if not (snap.get("backup_cron") or {}).get("active"):
        add("warn", "no backup job installed in the container")
    rc = snap.get("restart_cron") or {}
    if rc.get("expr") and not rc.get("active"):
        add("warn", "auto-restart configured but not installed")

    disk = snap.get("disk")
    if disk:
        used = 100.0 * disk.used / max(1, disk.total)
        if used > 95:
            add("crit", "disk %.0f%% full" % used)
        elif used > 85:
            add("warn", "disk %.0f%% full" % used)

    m = snap.get("metrics") or {}
    fps = float(m.get("serverfpsaverage") or 0)
    if m and fps:
        if fps < 20:
            add("crit", "server averaging %.0f fps" % fps)
        elif fps < 30:
            add("warn", "server averaging %.0f fps" % fps)

    mem = snap.get("mem") or {}
    if mem.get("total"):
        mp = 100.0 * mem["used"] / mem["total"]
        if mp > 92:
            add("warn", "host memory %.0f%% used" % mp)

    have, want = snap.get("build"), snap.get("build_latest")
    if have and want and want.get("buildid") and have != want["buildid"]:
        add("warn", "server update available (build %s -> %s)"
            % (have, want["buildid"]))

    # base camp worker health, from Level.sav
    bases = snap.get("base_camps") or []
    dying = sum(len(b.get("dying") or ()) for b in bases)
    sick = sum(len(b.get("sick") or ()) for b in bases)
    starving = sum(len(b.get("starving") or ()) for b in bases)
    unhappy = sum(len(b.get("insane") or ()) for b in bases)
    hurt = [b for b in bases if b.get("sick") or b.get("dying")
            or b.get("starving")]
    if dying:
        add("crit", "%d base worker%s dying" % (dying, "" if dying == 1 else "s"))
    if starving:
        add("warn", "%d base worker%s starving"
            % (starving, "" if starving == 1 else "s"))
    if sick:
        add("warn", "%d sick base worker%s across %d base%s" % (
            sick, "" if sick == 1 else "s", len(hurt),
            "" if len(hurt) == 1 else "s"))
    if unhappy:
        add("warn", "%d base worker%s with low sanity"
            % (unhappy, "" if unhappy == 1 else "s"))

    cur, mx = m.get("currentplayernum"), m.get("maxplayernum")
    if cur and mx and cur >= mx:
        add("warn", "server is full (%s/%s)" % (cur, mx))

    rank = {"crit": 0, "warn": 1, "info": 2}
    out.sort(key=lambda a: rank.get(a["level"], 3))
    return out


def _cron_info(expr, now, active):
    """Describe a cron schedule, and whether it is actually installed."""
    info = {"expr": (expr or "").strip(), "active": active, "next": None,
            "valid": False}
    if not info["expr"]:
        return info
    try:
        info["next"] = Cron(info["expr"]).next_after(now)
        info["valid"] = True
    except (ValueError, TypeError):
        pass
    return info


def _iso(text):
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    text = re.sub(r"\.(\d{6})\d+", r".\1", text)       # trim ns -> us
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _boot_time():
    """When the host last booted, as a unix time."""
    for line in read("/proc/stat").splitlines():
        if line.startswith("btime "):
            try:
                return int(line.split()[1])
            except ValueError:
                return None
    return None


def _host_uptime():
    try:
        return float(read("/proc/uptime", "0").split()[0])
    except (ValueError, IndexError):
        return 0.0


if __name__ == "__main__":                             # smoke test
    import pprint
    src = PalSource()
    src.sample()
    time.sleep(1)
    snap = src.sample()
    snap["events"] = "<%d events>" % len(snap.get("events", []))
    pprint.pprint(snap, width=100)
