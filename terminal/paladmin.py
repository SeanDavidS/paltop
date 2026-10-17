#!/usr/bin/env python3
"""
paladmin.py - the ONLY part of paltop that changes anything.

Everything else in paltop is read-only by construction: palsource issues GET
requests against an allow-list, palsave and palpak open files 'rb'. This module
is deliberately separate so that boundary stays obvious - if you want paltop to
be incapable of touching the server, delete this file and the admin menu stops
working while the rest carries on.

Actions are graded:

    SAFE       save, backup, announce   - no player is interrupted
    DISRUPTIVE kick, ban                - affects one player
    SEVERE     restart, stop, restore   - ends everyone's session

The UI requires a confirmation for anything that is not SAFE, and every action
attempted is appended to state/admin.log with its outcome.
"""

from __future__ import annotations

import base64
import glob
import gzip
import json
import os
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import palpaths

LOGFILE = os.environ.get("PALTOP_ADMIN_LOG") or \
    os.path.join(palpaths.STATE_DIR, "admin.log")

SAFE, DISRUPTIVE, SEVERE = 0, 1, 2


class Admin:
    """Write-side companion to palsource.Rest."""

    def __init__(self, host, port, password, container, user="admin",
                 timeout=10.0):
        self.host, self.port, self.container = host, port, container
        self.timeout = timeout
        token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
        self._auth = "Basic " + token

    # -- plumbing --------------------------------------------------------

    def _post(self, path, payload=None):
        url = "http://%s:%s%s" % (self.host, self.port, path)
        body = json.dumps(payload or {}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", self._auth)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")[:200]

    def _log(self, action, detail, ok, note):
        line = "%s\t%s\t%s\t%s\t%s\n" % (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            action, detail, "ok" if ok else "FAILED", note.replace("\t", " "))
        try:
            os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
            with open(LOGFILE, "a") as fh:
                fh.write(line)
        except OSError:
            pass

    def _run(self, action, detail, fn):
        try:
            note = fn()
            self._log(action, detail, True, str(note))
            return True, str(note)
        except urllib.error.HTTPError as exc:
            note = "HTTP %s %s" % (exc.code, exc.reason)
        except Exception as exc:
            note = "%s: %s" % (type(exc).__name__, exc)
        self._log(action, detail, False, note)
        return False, note

    # -- actions ---------------------------------------------------------

    def save(self):
        """Force a world save. The server does this on its own schedule too."""
        return self._run("save", "", lambda: self._post("/v1/api/save")[1]
                         or "saved")

    def announce(self, message):
        """Broadcast a message to everyone online."""
        message = (message or "").strip()
        if not message:
            return False, "empty message"
        return self._run("announce", message,
                         lambda: self._post("/v1/api/announce",
                                            {"message": message})[1] or "sent")

    def backup(self):
        """Run the container's own backup script."""
        def go():
            p = subprocess.run(
                ["docker", "exec", self.container, "bash",
                 "/usr/local/bin/backup"],
                capture_output=True, text=True, timeout=300)
            if p.returncode != 0:
                raise RuntimeError((p.stderr or p.stdout or "").strip()[:160])
            return (p.stdout or "").strip().splitlines()[-1:] or ["done"]
        return self._run("backup", "", go)

    def kick(self, userid, name="", message="Kicked by an admin"):
        return self._run("kick", "%s (%s)" % (name, userid),
                         lambda: self._post("/v1/api/kick",
                                            {"userid": userid,
                                             "message": message})[1] or "kicked")

    def ban(self, userid, name="", message="Banned by an admin"):
        return self._run("ban", "%s (%s)" % (name, userid),
                         lambda: self._post("/v1/api/ban",
                                            {"userid": userid,
                                             "message": message})[1] or "banned")

    def unban(self, userid, name=""):
        return self._run("unban", "%s (%s)" % (name, userid),
                         lambda: self._post("/v1/api/unban",
                                            {"userid": userid})[1]
                         or "unbanned")

    def shutdown(self, seconds=30, message="Server restarting"):
        """Ask the server to stop after a countdown.

        The container runs with `restart: unless-stopped`, so Docker starts it
        again straight after - which is what makes this a restart rather than
        a shutdown. Players get `seconds` of warning.
        """
        return self._run(
            "restart", "%ds: %s" % (seconds, message),
            lambda: self._post("/v1/api/shutdown",
                               {"waittime": int(seconds),
                                "message": message})[1] or "shutting down")

    # -- menu definition -------------------------------------------------

    def menu(self):
        """(key, label, severity, needs) for the UI to render."""
        return [
            ("s", "Save world now", SAFE, None),
            ("b", "Create a backup", SAFE, None),
            ("a", "Announce a message", SAFE, "text"),
            ("k", "Kick a player", DISRUPTIVE, "player"),
            ("r", "Restart (30s warning)", SEVERE, None),
            ("R", "Restore a backup...", SEVERE, "backup"),
            ("p", "Players: bans, history", DISRUPTIVE, "players"),
        ]


def read_log(limit=200):
    """Recent admin actions, newest last."""
    try:
        with open(LOGFILE) as fh:
            rows = [l.rstrip("\n").split("\t") for l in fh][-limit:]
    except OSError:
        return []
    out = []
    for r in rows:
        if len(r) >= 5:
            out.append({"ts": r[0], "action": r[1], "detail": r[2],
                        "ok": r[3] == "ok", "note": r[4]})
    return out


# ---------------------------------------------------------------------------
# Restoring a backup
# ---------------------------------------------------------------------------

SNAPSHOT_PREFIX = "paltop-before-restore-"
AUTOHEAL = "palworld-autoheal"


def list_backups(data_dir):
    """Every backup archive, newest first: the image's own and paltop's
    before-restore snapshots."""
    out = []
    for path in glob.glob(os.path.join(data_dir, "backups", "*.tar.gz")):
        try:
            st = os.stat(path)
        except OSError:
            continue
        out.append({"path": path, "name": os.path.basename(path),
                    "mtime": st.st_mtime, "size": st.st_size,
                    "snapshot": os.path.basename(path).startswith(
                        SNAPSHOT_PREFIX)})
    out.sort(key=lambda b: -b["mtime"])
    return out


_inspected = {}


def inspect_backup(path):
    """What is in a backup: {ok, error, world, day, players, pals, files}.

    Reads the archive without unpacking it, checks that it is a complete
    gzip/tar with a world in it, and parses its Level.sav. Cached per file."""
    try:
        st = os.stat(path)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    key = (path, st.st_mtime, st.st_size)
    if key in _inspected:
        return _inspected[key]
    info = {"ok": False, "error": None, "world": None, "day": None,
            "players": [], "pals": 0, "files": 0}
    try:
        with tarfile.open(path, "r:gz") as tf:
            members = tf.getmembers()             # reads to the end: a torn
            info["files"] = len(members)          # archive fails right here
            levels = [m for m in members if m.name.startswith(
                "Saved/SaveGames/") and m.name.endswith("/Level.sav")]
            bad = [m.name for m in members if m.name.startswith("/")
                   or ".." in m.name.split("/") or m.issym() or m.islnk()]
            if bad:
                raise ValueError("unsafe paths in archive: %s" % bad[0])
            if not levels:
                raise ValueError("no Saved/SaveGames/.../Level.sav in it")
            info["world"] = levels[0].name.split("/")[3]
            raw = tf.extractfile(levels[0]).read()
        import palsave
        info.update(palsave.world_summary(palsave.decompress_bytes(raw)))
        info["ok"] = True
    except Exception as exc:
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
    _inspected[key] = info
    return info


def _docker(*args, timeout=120):
    p = subprocess.run(["docker"] + list(args), capture_output=True,
                       text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError("docker %s: %s" % (
            " ".join(args), (p.stderr or p.stdout).strip()[:160]))
    return p.stdout.strip()


class Restore(threading.Thread):
    """Put a backup in place of the current world.

    Steps, each shown in the UI as it runs:

      1  check the archive (complete, has a world, same world id)
      2  save the world, if the server answers
      3  stop autoheal, so it cannot restart the server half-way
      4  stop the server container (the game saves and exits cleanly)
      5  snapshot the current world to backups/paltop-before-restore-*.tar.gz
         and verify it - this is what makes the restore undoable
      6  unpack the backup to a temporary folder and check it
      7  swap Saved/SaveGames and Saved/Config; the game's own rolling
         backup folders are kept. Any error here puts the old files back.
      8  start the server and autoheal again - always, even after an error

    Files are swapped while the container is stopped, so the game can never
    see a half-restored world. Everything is logged to state/admin.log.
    """

    STEPS = ("check the backup", "save the world", "pause autoheal",
             "stop the server", "snapshot the current world",
             "unpack the backup", "swap the world files",
             "start the server again")

    def __init__(self, admin, data_dir, backup_path, docker=_docker,
                 allow_other_world=False):
        # not a daemon: if paltop is closed half-way, the restore still
        # finishes and starts the server again before the process exits
        super().__init__(daemon=False)
        self.admin = admin
        self.data_dir = data_dir
        self.backup = backup_path
        self.docker = docker
        self.allow_other_world = allow_other_world
        self.step = 0
        self.log = []                  # (step, ok, note)
        self.done = False
        self.ok = None
        self.snapshot = None

    def _note(self, ok, note):
        self.log.append((self.step, ok, note))

    def run(self):
        saved = os.path.join(self.data_dir, "Pal", "Saved")
        stopped_server = stopped_heal = False
        tmp = None
        try:
            # 1 check
            self.step = 0
            info = inspect_backup(self.backup)
            if not info["ok"]:
                raise RuntimeError("backup is not usable: %s" % info["error"])
            current = [d for d in glob.glob(os.path.join(
                saved, "SaveGames", "*", "*", "Level.sav"))]
            here = current[0].split(os.sep)[-2] if current else None
            if here and info["world"] != here and not self.allow_other_world:
                raise RuntimeError("backup is of world %s, the server runs %s"
                                   % (info["world"][:8], here[:8]))
            self._note(True, "day %s, %d players, %d pals" % (
                info["day"], len(info["players"]), info["pals"]))

            # 2 save
            self.step = 1
            try:
                self.admin._post("/v1/api/save")
                self._note(True, "saved")
            except Exception as exc:
                self._note(True, "server did not answer (%s) - going on"
                           % type(exc).__name__)

            # 3 autoheal
            self.step = 2
            try:
                state = self.docker("inspect", "-f", "{{.State.Running}}",
                                    AUTOHEAL)
                if state == "true":
                    self.docker("stop", AUTOHEAL)
                    stopped_heal = True
                self._note(True, "paused" if stopped_heal else "not running")
            except RuntimeError as exc:
                self._note(True, "no autoheal (%s)" % exc)

            # 4 stop the server
            self.step = 3
            stopped_server = True       # from here on, always start it again
            self.docker("stop", "-t", "90", self.admin.container, timeout=150)
            self._note(True, "stopped")

            # 5 snapshot the current world, and check it
            self.step = 4
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            base = os.path.join(self.data_dir, "backups",
                                SNAPSHOT_PREFIX + stamp)
            self.snapshot, n = base + ".tar.gz", 1
            while os.path.exists(self.snapshot):   # never overwrite a backup
                n += 1
                self.snapshot = "%s-%d.tar.gz" % (base, n)
            if os.path.realpath(self.snapshot) == os.path.realpath(
                    self.backup):
                raise RuntimeError("snapshot would overwrite the backup")
            with tarfile.open(self.snapshot, "x:gz") as tf:
                tf.add(saved, arcname="Saved",
                       filter=lambda ti: None if ti.name.split("/")[-1]
                       in ("backup", "Crashes") else ti)
            snap = inspect_backup(self.snapshot)
            if not snap["ok"]:
                raise RuntimeError("snapshot failed its check: %s"
                                   % snap["error"])
            self._note(True, os.path.basename(self.snapshot))

            # 6 unpack
            self.step = 5
            tmp = os.path.join(self.data_dir, "backups", ".paltop-restore-" +
                               os.path.basename(self.snapshot)[:-7])
            os.makedirs(tmp)
            with tarfile.open(self.backup, "r:gz") as tf:
                tf.extractall(tmp, filter="data")
            new = os.path.join(tmp, "Saved")
            if not glob.glob(os.path.join(new, "SaveGames", "*", "*",
                                          "Level.sav")):
                raise RuntimeError("unpacked backup has no Level.sav")
            self._note(True, "%d files" % info["files"])

            # 7 swap, with rollback
            self.step = 6
            self._swap(saved, new, os.path.join(tmp, "old"))
            self._note(True, "world from %s is in place"
                       % os.path.basename(self.backup))
            self.ok = True
        except Exception as exc:
            self._note(False, "%s: %s" % (type(exc).__name__, exc))
            self.ok = False
        finally:
            # 8 always bring things back up
            self.step = 7
            for name, was in ((self.admin.container, stopped_server),
                              (AUTOHEAL, stopped_heal)):
                if not was:
                    continue
                try:
                    if self.docker("inspect", "-f", "{{.State.Running}}",
                                   name) == "true":
                        self._note(True, "%s is running" % name)
                        continue
                    self.docker("start", name)
                    self._note(True, "%s started" % name)
                except Exception as exc:
                    self.ok = False
                    self._note(False, "could not start %s: %s - run: docker "
                               "start %s" % (name, exc, name))
            if tmp and os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
            self.admin._log("restore", os.path.basename(self.backup),
                            bool(self.ok), "; ".join(n for _, _, n in
                                                     self.log)[-400:])
            self.done = True

    @staticmethod
    def _swap(saved, new, old):
        """Replace SaveGames and Config under `saved` with those in `new`.
        The game's own `backup` folders move across into the new tree. On any
        error, what was moved is put back."""
        os.makedirs(old)
        moved = []
        try:
            for part in ("SaveGames", "Config"):
                cur = os.path.join(saved, part)
                if os.path.isdir(cur):
                    os.rename(cur, os.path.join(old, part))
                    moved.append(part)
            # keep the game's rolling backups of each world
            for b in glob.glob(os.path.join(old, "SaveGames", "*", "*",
                                            "backup")):
                rel = os.path.relpath(os.path.dirname(b), old)
                dest = os.path.join(new, rel)
                if os.path.isdir(dest) and not os.path.exists(
                        os.path.join(dest, "backup")):
                    shutil.copytree(b, os.path.join(dest, "backup"))
            for part in ("SaveGames", "Config"):
                src = os.path.join(new, part)
                if os.path.isdir(src):
                    os.rename(src, os.path.join(saved, part))
        except Exception:
            for part in ("SaveGames", "Config"):
                cur = os.path.join(saved, part)
                if part in moved:
                    if os.path.isdir(cur):
                        shutil.rmtree(cur)
                    os.rename(os.path.join(old, part), cur)
            raise


# ---------------------------------------------------------------------------
# Player administration: ban list and history
# ---------------------------------------------------------------------------

BANLIST = os.path.join("Pal", "Saved", "SaveGames", "banlist.txt")


def read_banlist(data_dir):
    """User ids in the server's own ban list (one per line), in file order.
    The server writes it when someone is banned; no file means no bans."""
    if not data_dir:
        return []
    try:
        with open(os.path.join(data_dir, BANLIST)) as fh:
            lines = [l.strip() for l in fh]
    except OSError:
        return []
    return [l for l in lines if l and not l.startswith("#")]


def _log_line(action, detail, ok, note):
    line = "%s\t%s\t%s\t%s\t%s\n" % (
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        action, detail, "ok" if ok else "FAILED", str(note).replace("\t", " "))
    try:
        os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
        with open(LOGFILE, "a") as fh:
            fh.write(line)
    except OSError:
        pass


MODERATION = ("kick", "ban", "unban")


def history(command_events=(), limit=500):
    """Kicks, bans and unbans, newest first: paltop's own
    admin log plus admin commands typed in the game (/KickPlayer,
    /BanPlayer, /UnBanPlayer) from the server log."""
    out = []
    for r in read_log(limit=5000):
        if r["action"] not in MODERATION:
            continue
        try:
            ts = datetime.fromisoformat(r["ts"])
        except ValueError:
            continue
        out.append({"ts": ts, "action": r["action"], "detail": r["detail"],
                    "ok": r["ok"], "by": "paltop", "note": r["note"]})
    for e in command_events or []:
        word = e["detail"].split(" ", 1)[0].strip("/").lower()
        act = {"kickplayer": "kick", "banplayer": "ban",
               "unbanplayer": "unban"}.get(word)
        if act:
            out.append({"ts": e["ts"], "action": act,
                        "detail": e["detail"].split(" ", 1)[1:] and
                        e["detail"].split(" ", 1)[1] or "",
                        "ok": True, "by": "%s in game" % e["name"],
                        "note": ""})
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out[:limit]
