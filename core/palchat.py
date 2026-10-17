#!/usr/bin/env python3
"""
palchat.py - the chat between the game and the web page.

From the game: the server writes every chat message to its log,

    [2026-09-30 18:29:43] [CHAT] <Alice> anyone up for a boss fight?

and the collector follows the log (`docker logs -f`) and keeps each message
in the database (table chat).

To the game: the web page sends a message with the REST API's announce, so it
shows in the game as a server message: "[web] Name: text". This is the only
thing the web page ever sends to the server, and it is limited: logged-in
users only, one line of at most MAX_LEN characters, at most PER_MINUTE a
minute per user and GLOBAL_PER_MINUTE for everyone.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone

CHAT_LINE = re.compile(
    r"^\[(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)Z?\] \[CHAT\] <(.+?)> ?(.*)$")
WEB_PREFIX = "[web] "
MAX_LEN = 200
PER_MINUTE = 5
GLOBAL_PER_MINUTE = 20
TAIL = 300                 # log lines read again when (re)connecting


def parse(line, tz=timezone.utc):
    """(unix time, name, text) for a chat line of the server log, or None.
    The log's times are the container's clock (UTC unless TZ says so)."""
    m = CHAT_LINE.match(line.strip())
    if not m:
        return None
    when = datetime.strptime(m.group(1).replace("T", " "),
                             "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
    return int(when.timestamp()), m.group(2), m.group(3).strip()


def clean(text):
    """One line of printable text, at most MAX_LEN characters."""
    text = "".join(c for c in str(text or "")
                   if unicodedata.category(c)[0] != "C" or c == " ")
    return " ".join(text.split())[:MAX_LEN]


def for_game(name, text):
    return "%s%s: %s" % (WEB_PREFIX, name, text)


def announce(host, port, password, message, timeout=5.0):
    """Send a message to everyone in the game: (ok, error)."""
    token = base64.b64encode(("admin:%s" % password).encode()).decode()
    req = urllib.request.Request(
        "http://%s:%s/v1/api/announce" % (host, port),
        data=json.dumps({"message": message}).encode(), method="POST",
        headers={"Authorization": "Basic " + token,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300, None
    except urllib.error.HTTPError as exc:
        return False, "the server said %s" % exc.code
    except (OSError, ValueError) as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)


class Limiter:
    """At most `per_minute` sends a minute for each key, and `total` for
    all keys together."""

    def __init__(self, per_minute=PER_MINUTE, total=GLOBAL_PER_MINUTE):
        self.per, self.total = per_minute, total
        self.sent = {}
        self.all = []
        self.lock = threading.Lock()

    def allow(self, key, now=None):
        now = now or time.time()
        with self.lock:
            self.all = [t for t in self.all if now - t < 60]
            mine = [t for t in self.sent.get(key, []) if now - t < 60]
            if len(mine) >= self.per or len(self.all) >= self.total:
                self.sent[key] = mine
                return False
            mine.append(now)
            self.sent[key] = mine
            self.all.append(now)
            return True


class Follower(threading.Thread):
    """Follows the server's log and keeps its chat lines in the database.
    Reconnects when the log stream ends (a container restart); the last
    TAIL lines are read again then, and the database drops the doubles."""

    def __init__(self, container, db, tz=timezone.utc, log=print):
        super().__init__(name="chat", daemon=True)
        self.container, self.db, self.tz, self.log = container, db, tz, log
        self.stop = threading.Event()
        self.proc = None

    def run(self):
        while not self.stop.is_set():
            try:
                self.proc = subprocess.Popen(
                    ["docker", "logs", "-f", "--tail", str(TAIL),
                     self.container], stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, errors="replace")
                for line in self.proc.stdout:
                    if self.stop.is_set():
                        break
                    if "[CHAT]" not in line:
                        continue
                    hit = parse(line, self.tz)
                    if hit and not hit[2].startswith(WEB_PREFIX):
                        self.db.add_chat(hit[0], "game", hit[1], hit[2])
            except Exception as exc:          # never take the collector down
                self.log("chat: %s: %s" % (type(exc).__name__, exc))
            finally:
                if self.proc and self.proc.poll() is None:
                    self.proc.terminate()
            self.stop.wait(10)

    def close(self):
        self.stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
