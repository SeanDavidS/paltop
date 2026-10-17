#!/usr/bin/env python3
"""
paltop - a btop-style terminal dashboard for a Palworld dedicated server.

Read-only by construction: all data comes from palsource, which only ever
issues informational REST GETs and reads docker metadata, /proc and the save
directory. paltop cannot restart, stop or save the server.

Keys:  q quit   +/- interval   p sort players   a all/online   s settings
       e events   r refresh    h help
"""

from __future__ import annotations

import argparse
import json
import curses
import locale
import os
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

def _find_core():
    """core/ (the shared modules): PALTOP_CORE, or next to this part, as in
    the repository and in an install (<paltop>/core beside <paltop>/terminal)."""
    here = os.path.dirname(os.path.realpath(__file__))
    for d in (os.environ.get("PALTOP_CORE"), os.path.join(here, "..", "core")):
        if d and os.path.isfile(os.path.join(d, "palsource.py")):
            return os.path.abspath(d)
    sys.exit("paltop: cannot find core/ (set PALTOP_CORE to where it is)")


sys.path.insert(0, _find_core())
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import paladmin  # noqa: E402
import palcollect  # noqa: E402
import palcrash  # noqa: E402
import palpals  # noqa: E402
import palteam  # noqa: E402
import palsource  # noqa: E402
from palsource import NCPU, PalSource, cpu_model  # noqa: E402

LOCAL_TZ = datetime.now().astimezone().tzinfo
LEVEL_CAP = 60                      # vanilla Palworld cap, grown if exceeded

# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

def fmt_bytes(n, digits=2):
    n = float(n or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024.0 or unit == "TiB":
            if unit == "B":
                return "%d B" % n
            return "%.*f %s" % (digits, n, unit)
        n /= 1024.0


def fmt_rate(bps):
    return fmt_bytes(bps, 1) + "/s"


def fmt_dur(sec, compact=False):
    """1d 22:55  /  3h 12m  /  45s"""
    if sec is None:
        return "-"
    sec = int(max(0, sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if compact:
        if d:
            return "%dd %dh" % (d, h)
        if h:
            return "%dh %dm" % (h, m)
        if m:
            return "%dm" % m
        return "%ds" % s
    if d:
        return "%dd %02d:%02d" % (d, h, m)
    return "%02d:%02d:%02d" % (h, m, s)


def to_local(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def fmt_clock(dt, fmt="%H:%M"):
    dt = to_local(dt)
    return dt.strftime(fmt) if dt else "-"


def _dtu(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


def fmt_stamp(dt):
    """Local wall-clock, with the day only when it is not today."""
    dt = to_local(dt)
    if not dt:
        return "-"
    today = datetime.now(LOCAL_TZ).date()
    if dt.date() == today:
        return dt.strftime("%H:%M:%S")
    if (today - dt.date()).days < 7:
        return dt.strftime("%a %H:%M")
    return dt.strftime("%m-%d %H:%M")


def fmt_ago(dt, now):
    if dt is None:
        return "never"
    return fmt_dur((now - dt).total_seconds(), compact=True) + " ago"


def fmt_in(dt, now):
    if dt is None:
        return "-"
    delta = (dt - now).total_seconds()
    return ("in " + fmt_dur(delta, compact=True)) if delta > 0 else "due"


def fmt_setting(v):
    """Palworld returns rates as float32 widened to double - 0.7 arrives as
    0.699999988079071. Show what the admin actually typed."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return (("%.4f" % v).rstrip("0").rstrip(".")) or "0"
    if v is None or v == "":
        return "-"
    return str(v)


def trunc(s, n):
    s = str(s)
    return s if len(s) <= n else (s[:max(0, n - 1)] + "…")


# ---------------------------------------------------------------------------
# theme
# ---------------------------------------------------------------------------

# btop's built-in "Default" theme, taken verbatim from its theme definition.
BTOP = {
    "main_fg":     "#cccccc",
    "title":       "#eeeeee",
    "hi_fg":       "#b54040",
    "inactive_fg": "#404040",
    "graph_text":  "#606060",
    "meter_bg":    "#404040",
    "proc_misc":   "#0de756",
    "cpu_box":     "#3d7b46",
    "mem_box":     "#8a882e",
    "net_box":     "#423ba5",
    "proc_box":    "#923535",
    "div_line":    "#30302f",
}

# start / mid / end stops for each of btop's gradients
RAMPS = {
    "temp":      ("#4897d4", "#5474e8", "#ff40b6"),
    "cpu":       ("#50f095", "#f2e266", "#fa1e1e"),
    "free":      ("#223014", "#b5e685", "#dcff85"),
    "cached":    ("#0b1a29", "#74e6fc", "#26c5ff"),
    "available": ("#292107", "#ffd77a", "#ffb814"),
    "used":      ("#3b1f1c", "#d9626d", "#ff4769"),
    "download":  ("#231a63", "#4f43a3", "#b0a9de"),
    "upload":    ("#510554", "#7d4180", "#dcafde"),
    "process":   ("#80d0a3", "#dcd179", "#d45454"),
}
GRAD_STEPS = 24

# semantic names used by the panels, resolved against the theme above
SEMANTIC = {
    "border":     BTOP["div_line"],
    "rule":       BTOP["div_line"],
    "title":      BTOP["title"],
    "hotkey":     BTOP["hi_fg"],
    "label":      BTOP["main_fg"],
    "value":      BTOP["title"],
    "dim":        BTOP["graph_text"],
    "meter_bg":   BTOP["meter_bg"],
    "inactive":   BTOP["inactive_fg"],
    "offline":    BTOP["inactive_fg"],
    "online":     BTOP["proc_misc"],
    "good":       "#50f095",
    "warn":       "#f2e266",
    "crit":       "#fa1e1e",
    "accent":     "#74e6fc",
    "accent2":    "#dcafde",
    "cpu_box":    BTOP["cpu_box"],
    "mem_box":    BTOP["mem_box"],
    "net_box":    BTOP["net_box"],
    "proc_box":   BTOP["proc_box"],
}

FALLBACK = {
    "border": curses.COLOR_BLUE, "rule": curses.COLOR_BLUE,
    "title": curses.COLOR_WHITE, "hotkey": curses.COLOR_RED,
    "label": curses.COLOR_CYAN, "value": curses.COLOR_WHITE,
    "dim": curses.COLOR_BLUE, "meter_bg": curses.COLOR_BLUE,
    "inactive": curses.COLOR_BLUE, "offline": curses.COLOR_BLUE,
    "online": curses.COLOR_GREEN, "good": curses.COLOR_GREEN,
    "warn": curses.COLOR_YELLOW, "crit": curses.COLOR_RED,
    "accent": curses.COLOR_CYAN, "accent2": curses.COLOR_MAGENTA,
    "cpu_box": curses.COLOR_GREEN, "mem_box": curses.COLOR_YELLOW,
    "net_box": curses.COLOR_BLUE, "proc_box": curses.COLOR_RED,
}

CUBE = (0, 95, 135, 175, 215, 255)


def _hex(s):
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def rgb_to_xterm(r, g, b):
    """Nearest xterm-256 entry: the 6x6x6 colour cube or the 24-step grey ramp.

    btop paints with 24-bit colour; curses can only address the 256-colour
    palette, so each theme colour is snapped to its closest palette entry.
    Redefining palette slots with init_color() would be exact but can leave
    the user's terminal recoloured after exit, so it is deliberately avoided.
    """
    idx = [min(range(6), key=lambda i: abs(CUBE[i] - v)) for v in (r, g, b)]
    cube = (CUBE[idx[0]], CUBE[idx[1]], CUBE[idx[2]])
    gi = max(0, min(23, int(round((((r + g + b) / 3.0) - 8) / 10.0))))
    grey = 8 + 10 * gi
    dist = lambda c: sum((a - o) ** 2 for a, o in zip((r, g, b), c))
    if dist(cube) <= dist((grey, grey, grey)):
        return 16 + 36 * idx[0] + 6 * idx[1] + idx[2]
    return 232 + gi


def build_ramp(start, mid, end, steps=GRAD_STEPS):
    """btop-style three-stop gradient: start -> mid over the first half of the
    range, mid -> end over the second."""
    a, b, c = _hex(start), _hex(mid), _hex(end)
    out = []
    for i in range(steps):
        t = i / float(steps - 1)
        lo, hi, u = (a, b, t * 2.0) if t <= 0.5 else (b, c, (t - 0.5) * 2.0)
        out.append(rgb_to_xterm(
            *[int(round(lo[k] + (hi[k] - lo[k]) * u)) for k in range(3)]))
    return out


class Theme:
    """btop's palette, mapped onto curses colour pairs.

    Degrades to the 8 ANSI colours on a basic terminal and to plain
    attributes with no colour at all.
    """

    def __init__(self):
        self.color = curses.has_colors()
        self.rich = self.color and curses.COLORS >= 256
        if self.color:
            try:
                curses.use_default_colors()
            except curses.error:
                pass
        self._pairs = {}
        self._next = 1
        self._max = max(1, min(getattr(curses, "COLOR_PAIRS", 256) - 1, 4096))
        self._ramps = {}

    def _pair(self, fg):
        if not self.color or fg is None:
            return 0
        got = self._pairs.get(fg)
        if got is not None:
            return got
        if self._next > self._max:
            return 0
        idx = self._next
        try:
            curses.init_pair(idx, fg, -1)
        except curses.error:
            return 0
        self._next += 1
        self._pairs[fg] = idx
        return idx

    def c(self, name, bold=False):
        attr = curses.A_BOLD if bold else 0
        if not self.color:
            return attr
        if self.rich and name in SEMANTIC:
            return curses.color_pair(
                self._pair(rgb_to_xterm(*_hex(SEMANTIC[name])))) | attr
        return curses.color_pair(
            self._pair(FALLBACK.get(name, curses.COLOR_WHITE))) | attr

    def grad(self, name, frac):
        """Colour from one of btop's gradients; frac in 0..1."""
        if not self.color:
            return 0
        frac = 0.0 if frac != frac else max(0.0, min(1.0, frac))
        if not self.rich:
            basic = (curses.COLOR_GREEN if frac < 0.6 else
                     curses.COLOR_YELLOW if frac < 0.85 else curses.COLOR_RED)
            return curses.color_pair(self._pair(basic))
        ramp = self._ramps.get(name)
        if ramp is None:
            ramp = build_ramp(*RAMPS.get(name, RAMPS["cpu"]))
            self._ramps[name] = ramp
        return curses.color_pair(
            self._pair(ramp[int(frac * (len(ramp) - 1))]))


# ---------------------------------------------------------------------------
# drawing primitives
# ---------------------------------------------------------------------------

BRAILLE_L = (0x01, 0x02, 0x04, 0x40)
BRAILLE_R = (0x08, 0x10, 0x20, 0x80)
METER_FULL = "█"
METER_EMPTY = "·"


class Canvas:
    """Thin, clip-safe wrapper around a curses window."""

    def __init__(self, win):
        self.win = win
        self.h, self.w = win.getmaxyx()

    def text(self, y, x, s, attr=0, maxw=None):
        if y < 0 or y >= self.h or x >= self.w:
            return 0
        if maxw is not None:
            s = s[:max(0, maxw)]
        if x < 0:
            s = s[-x:]
            x = 0
        room = self.w - x
        if y == self.h - 1:
            room -= 1                      # bottom-right cell aborts curses
        s = s[:max(0, room)]
        if not s:
            return 0
        try:
            self.win.addstr(y, x, s, attr)
        except curses.error:
            pass
        return len(s)

    def kv(self, y, x, w, label, value, th, vattr=None, lw=None):
        """A label on the left, a value flushed right."""
        lw = lw or len(label)
        self.text(y, x, label.ljust(lw), th.c("label"), maxw=w)
        val = trunc(value, max(0, w - lw - 1))
        self.text(y, x + w - len(val), val, vattr if vattr is not None
                  else th.c("value"), maxw=len(val))


def draw_box(cv, y, x, h, w, title, th, hotkey=0, right=None,
             color="cpu_box"):
    """btop-style rounded box: the border carries the box's own colour, the
    title is drawn in the title colour with its hotkey letter highlighted."""
    if h < 2 or w < 4:
        return (y, x, 0, 0)
    bd = th.c(color)
    cv.text(y, x, "╭" + "─" * (w - 2) + "╮", bd)
    for row in range(y + 1, y + h - 1):
        cv.text(row, x, "│", bd)
        cv.text(row, x + w - 1, "│", bd)
    cv.text(y + h - 1, x, "╰" + "─" * (w - 2) + "╯", bd)

    if title:
        tx = x + 2
        cv.text(y, tx, "┤", bd)
        tx += 1
        for i, ch in enumerate(title):
            cv.text(y, tx + i, ch,
                    th.c("hotkey", bold=True) if i == hotkey
                    else th.c("title", bold=True))
        cv.text(y, tx + len(title), "├", bd)
    if right:
        rx = x + w - 2 - len(right) - 2
        if rx > x + 2 + len(title or "") + 3:
            cv.text(y, rx, "┤", bd)
            cv.text(y, rx + 1, right, th.c("label"))
            cv.text(y, rx + 1 + len(right), "├", bd)
    return (y + 1, x + 2, h - 2, w - 4)     # inner rect


def draw_meter(cv, y, x, w, frac, th, grad="cpu"):
    """Gradient block meter, coloured per cell like btop."""
    if w <= 0:
        return
    frac = max(0.0, min(1.0, frac or 0.0))
    filled = int(round(frac * w))
    for i in range(w):
        if i < filled:
            cv.text(y, x + i, METER_FULL, th.grad(grad, i / max(1, w - 1)))
        else:
            cv.text(y, x + i, METER_EMPTY, th.c("meter_bg"))


def draw_graph(cv, y, x, h, w, series, th, grad="cpu", vmax=None, vmin=0.0):
    """Braille history graph: 2 samples per column, 4 levels per row.

    `vmin` lifts the baseline off zero, so a series that only ever moves
    between 55 and 60 fps still shows its dips instead of a solid block.
    """
    if h <= 0 or w <= 0:
        return
    need = w * 2
    data = list(series)[-need:]
    data = [0.0] * (need - len(data)) + data
    top = vmax if vmax else (max(data) or 1.0)
    span = (top - vmin) or 1.0
    total = h * 4
    levels = [int(round(max(0.0, min(1.0, (v - vmin) / span)) * total))
              for v in data]
    for r in range(h):
        out = []
        for c in range(w):
            bits = 0
            for dr in range(4):
                threshold = total - (r * 4 + dr)
                if levels[2 * c] >= threshold:
                    bits |= BRAILLE_L[dr]
                if levels[2 * c + 1] >= threshold:
                    bits |= BRAILLE_R[dr]
            out.append(chr(0x2800 + bits))
        cv.text(y + r, x, "".join(out), th.grad(grad, (h - r) / max(1, h)))


def pct_attr(th, pct, warn=70, crit=90):
    """Threshold colouring for values whose scale is not a plain percentage
    (ping, disk). Anchored so `warn` lands mid-ramp and `crit` near the end."""
    if pct >= crit:
        return th.grad("cpu", 1.0)
    if pct >= warn:
        return th.grad("cpu", 0.5 + 0.35 * (pct - warn) / max(1, crit - warn))
    return th.grad("cpu", 0.5 * pct / max(1, warn))


class RowList:
    """Rows with a drop-rank, so a panel degrades by shedding its least
    important lines instead of truncating whatever happens to be last."""

    def __init__(self):
        self.rows = []

    def add(self, fn, rank=0):
        self.rows.append([rank, fn])

    def rule(self, cv, ix, iw, th, rank=1):
        self.add(lambda r: cv.text(r, ix, "\u2500" * iw, th.c("dim")), rank)

    def render(self, iy, ih):
        rows = list(self.rows)
        while len(rows) > ih:
            ranks = [r for r, _ in rows if r > 0]
            if not ranks:
                break
            worst = max(ranks)
            for i in range(len(rows) - 1, -1, -1):
                if rows[i][0] == worst:
                    del rows[i]
                    break
        for i, (_, fn) in enumerate(rows[:ih]):
            fn(iy + i)
        return len(rows)


def split_heights(total, wants, mins):
    """Share a column between stacked panels by how much each one wants."""
    if total >= sum(wants):
        out = list(wants)
        out[0] += total - sum(wants)          # spare room -> first panel
        return out
    if total <= sum(mins):
        out, over = list(mins), sum(mins) - total
        for i in range(len(out) - 1, -1, -1):
            take = min(over, out[i])
            out[i] -= take
            over -= take
            if over <= 0:
                break
        return out
    out = list(mins)
    spare = total - sum(mins)
    extra = [w - m for w, m in zip(wants, mins)]
    denom = sum(extra) or 1
    for i in range(len(out)):
        out[i] += spare * extra[i] // denom
    out[-1] += total - sum(out)
    return out


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------

class Poller(threading.Thread):
    """Fetches snapshots off the UI thread.

    A hung game server accepts connections and then never answers, so every
    request waits out its full timeout. Doing that on the UI thread froze the
    screen (and left it blank at start-up); here it only delays the next
    snapshot while the UI keeps drawing and taking keys.
    """

    def __init__(self, app):
        super().__init__(daemon=True, name="paltop-poller")
        self.app = app
        self._lock = threading.Lock()
        self._latest = None
        self._wake = threading.Event()
        self._halt = threading.Event()
        self.busy_since = None          # monotonic start of the running sample
        self.error = None

    def run(self):
        while not self._halt.is_set():
            if not self.app.paused:
                self.busy_since = time.monotonic()
                try:
                    snap = self.app.src.sample()
                    self.error = None
                except Exception as exc:          # keep polling regardless
                    snap = None
                    self.error = "%s: %s" % (type(exc).__name__, exc)
                self.busy_since = None
                if snap is not None:
                    with self._lock:
                        self._latest = snap
            self._wake.wait(self.app.interval)
            self._wake.clear()

    def take(self):
        with self._lock:
            snap, self._latest = self._latest, None
        return snap

    def refresh(self):
        self._wake.set()

    def stop(self):
        self._halt.set()
        self._wake.set()


SORTS = ("level", "name", "ping", "playtime", "seen")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# ncurses exposes ctrl+arrow through extended terminfo capabilities, but the
# key codes it hands out are assigned at runtime, so they must be resolved by
# name rather than hardcoded.
CTRL_ARROWS = {b"kUP5": (0, 1), b"kDN5": (0, -1),
               b"kLFT5": (-1, 0), b"kRIT5": (1, 0)}


def resolve_ctrl_arrows():
    """Map this terminal's ctrl+arrow key codes to (dx, dy) pan directions.

    Returns {} on a terminal that does not report them, in which case the
    caller should keep the plain arrows usable as a fallback.
    """
    found = {}
    try:
        start = max(getattr(curses, "KEY_MAX", 511), 511) + 1
    except Exception:
        start = 512
    for code in range(start, start + 1024):
        try:
            name = curses.keyname(code)
        except Exception:
            continue
        if name in CTRL_ARROWS:
            found[code] = CTRL_ARROWS[name]
    return found


# Element colours, keyed by the game's own EPalElementType names.
ELEMENT_COLORS = {
    "Fire": "#ff6a3d", "Water": "#4da6ff", "Ice": "#9fe8ff",
    "Leaf": "#6fd44f", "Electricity": "#ffd84a", "Earth": "#c79a5c",
    "Dark": "#a87ce0", "Dragon": "#e070c8", "Normal": "#d0d0d0",
}
PALS_PER_PLAYER = 24       # how many of each player's best Pals to list
AFK_AFTER = palcollect.AFK_AFTER   # seconds in the same spot before AFK
IV_HIGH = "#7fd4ff"        # 80+
IV_PERFECT = "#ffb454"     # 100
for _e, _c in ELEMENT_COLORS.items():
    SEMANTIC["el_" + _e.lower()] = _c
SEMANTIC["iv_high"] = IV_HIGH
SEMANTIC["iv_perfect"] = IV_PERFECT
FALLBACK.update({"el_" + e.lower(): curses.COLOR_WHITE for e in ELEMENT_COLORS})
FALLBACK["el_fire"] = curses.COLOR_RED
FALLBACK["el_water"] = curses.COLOR_BLUE
FALLBACK["el_ice"] = curses.COLOR_CYAN
FALLBACK["el_leaf"] = curses.COLOR_GREEN
FALLBACK["el_electricity"] = curses.COLOR_YELLOW
FALLBACK["el_earth"] = curses.COLOR_YELLOW
FALLBACK["el_dark"] = curses.COLOR_MAGENTA
FALLBACK["el_dragon"] = curses.COLOR_MAGENTA
FALLBACK["iv_high"] = curses.COLOR_CYAN
FALLBACK["iv_perfect"] = curses.COLOR_YELLOW

# kept here too: the pages have always imported them from paltop
from palpals import VARIANT_SUFFIXES, element_of, pal_display  # noqa: E402,F401


PLANS_FILE = os.path.join(palsource.STATE_DIR, "plans.json")
# a partner skill number with its condenser scale: 50/53/56/[60]/65%
_SCALE = re.compile(r"([+-]?[\d.?\[\]]+(?:/[\d.?\[\]]+){2,}%?)")


def load_plans():
    """Planned passives from earlier paltop sessions: {pal id: [ids]}."""
    try:
        with open(PLANS_FILE) as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def save_plans(plans):
    try:
        tmp = PLANS_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(plans, fh)
        os.replace(tmp, PLANS_FILE)
    except OSError:
        pass


def wrap_text(text, width):
    """Split text into lines of at most `width` characters, on spaces."""
    out, cur = [], ""
    for word in (text or "").split():
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        out.append(cur)
    return out


def draw_elements(cv, th, y, x, sid, els):
    """A species' one or two elements as coloured letters (Dr F = Dragon
    Fire); returns the width used."""
    w = 0
    for e in palpals.elements_of(sid, els):
        letter = palpals.ELEMENT_LETTERS.get(e, "?")
        cv.text(y, x + w, letter, th.c("el_" + e.lower(), bold=True))
        w += len(letter)
    return w


def iv_attr(th, v):
    if v >= 100:
        return th.c("iv_perfect", bold=True)
    if v >= 80:
        return th.c("iv_high")
    return th.c("label")


def pal_name(species):
    """Tidy Palworld's internal creature ids for display.

    These are the game's asset names, not its localised display names (those
    live in the pak's L10N tables). A BOSS_ prefix marks an alpha/boss
    variant, shown here as a leading alpha sign.
    """
    alpha = species.startswith("BOSS_")
    if alpha:
        species = species[5:]
    return ("\u03b1 " if alpha else "") + species


def db_size_note(st):
    """'db 272 KiB/50 MiB': history database size against its cap."""
    if not st:
        return "db -"
    n = st.get("bytes", 0)
    return "db %s/%s" % (fmt_bytes(n, 0 if n < 2**20 else 1),
                         fmt_bytes(st.get("cap", 0), 0))


def build_note(s):
    """' - update available' when Steam has a newer dedicated-server build."""
    have, want = s.get("build"), s.get("build_latest")
    if have and want and want.get("buildid"):
        if have != want["buildid"]:
            return "   update available (%s)" % want["buildid"]
        return "   up to date"
    return ""


class App:
    def __init__(self, container, interval, rest_host, use_net=True,
                 allow_admin=True):
        self.src = PalSource(container=container, rest_host=rest_host,
                             use_net=use_net,
                             db_path=os.environ.get("PALTOP_DB"))
        self.admin_api = None
        self._idle = palcollect.Idle(self.src.db)
        if allow_admin and self.src.rest:
            self.admin_api = paladmin.Admin(
                rest_host, self.src.env.get("REST_API_PORT", "8212"),
                self.src.env.get("ADMIN_PASSWORD", ""), container)
        self.interval = interval
        self.snap = {}
        self.hist = {k: deque(maxlen=400) for k in
                     ("cpu", "fps", "players", "rx", "tx", "mem")}
        self.sort = "level"
        self.show_all = True
        self.page = "main"
        self.settings_all = False
        self.pal_cursor = 0            # selected player in the pals overview
        self.pal_player = None         # pid when looking at one player's pals
        self.pal_idx = 0               # selected Pal in that list
        self.pal_sort = 0
        self.pal_filter = 0
        self._pal_order = []           # pids in the order the overview draws
        self._pal_view = []            # the list the player view last drew
        self._breeder = None
        self.pal_team = False          # team builder instead of the list
        self.pal_goal = 0
        self.pal_slot = 0
        self.pal_locked = {}           # slot -> Pal the user chose or locked
        self.pal_at_level = False      # True: score as if levelled to the owner
        self.pal_choosing = False      # the candidate list is open
        self.pal_include_hard = False  # offer hard-to-summon Pals too
        self.pal_search = ""           # / in a player's pals
        self.pal_csearch = ""          # / in the candidate list
        self.pal_typing = None         # "list" or "choose" while typing
        self.planner = None            # the passive planner, when open
        self.team_mode = "real"        # passives the team builder counts
        self.breed_view = False        # the breeding helper, when open
        self.plans = load_plans()      # pal id -> planned passive ids
        self.pal_cand = 0
        self._rank_cache = {}
        self.overlay = None
        self.scroll = 0
        self.paused = False
        self.ctrl_arrows = {}
        self.collector = (None, None, None)
        self.admin = None            # None, or the admin-menu state machine
        self.last_poll = 0.0
        self.frames = 0

    # -- data ------------------------------------------------------------

    def poll(self):
        """Synchronous refresh, for --dump / --json and tests."""
        self.ingest(self.src.sample())

    def refresh(self):
        """Ask for fresh data now, without blocking the UI."""
        if getattr(self, "poller", None):
            self.poller.refresh()
        else:
            self.poll()

    def ingest(self, snap):
        self.collector = palcollect.status(self.src.db)
        running, age, _ = self.collector
        if running is False:
            snap = dict(snap)
            snap["alerts"] = [{"level": "warn", "text":
                               "background collector stopped %s ago - "
                               "history is not being recorded"
                               % fmt_dur(age, compact=True)}] + list(
                                   snap.get("alerts") or [])
        self.snap = snap
        s = self.snap
        self.hist["cpu"].append(s.get("cpu_total", 0.0))
        self.hist["mem"].append(
            100.0 * s["mem"]["used"] / max(1, s["mem"]["total"]))
        m = s.get("metrics") or {}
        self.hist["fps"].append(float(m.get("serverfps") or 0))
        self.hist["players"].append(float(m.get("currentplayernum") or 0))
        self.hist["rx"].append(s.get("net", {}).get("rx", 0.0))
        self.hist["tx"].append(s.get("net", {}).get("tx", 0.0))
        self.last_poll = time.monotonic()

    def idle_for(self, uid, x, y):
        return self._idle.idle_for(uid, x, y)

    def roster(self):
        """Merge live REST players, log-derived sessions and the level store
        (palcollect.roster, which the web page's snapshot uses too)."""
        return palcollect.roster(self.snap, self.src.db, self._idle,
                                 self.sort, self.show_all)

    # -- panels ----------------------------------------------------------

    def panel_server(self, cv, y, x, h, w, th):
        s = self.snap
        info = s.get("info") or {}
        m = s.get("metrics") or {}
        settings = s.get("settings") or {}
        now = s.get("now") or datetime.now(timezone.utc)
        iy, ix, ih, iw = draw_box(
            cv, y, x, h, w, "server", th, 0,
            right="%s  %dms" % (fmt_clock(now, "%H:%M:%S"),
                                int(self.interval * 1000)),
            color="cpu_box")
        if ih <= 0:
            return

        status = s.get("status", "?")
        health = s.get("health")
        ok = status == "running" and health in (None, "healthy")
        badge = status + ((" / " + health) if health else "")
        # the container's own healthcheck only asks whether the process
        # exists, so a hung server still reads "healthy" - trust the REST API
        rs = s.get("rest_state")
        down = s.get("rest_down_for")
        took = (" %dm%02ds" % divmod(int(down), 60)) if down else ""
        if rs == "timeout":
            badge, ok = "NOT RESPONDING%s" % took, False
        elif rs == "refused":
            badge = ("STARTING%s" if health == "starting" else "DOWN%s") % took
            ok = False
        elif rs == "error":
            badge, ok = "REST ERROR", False

        # left block: identity
        col = min(iw, max(30, iw - 34))
        rows = [
            (info.get("servername") or self.src.env.get("SERVER_NAME", "?"),
             th.c("value", bold=True)),
            ("%s   %s   %s   %s" % (
                info.get("version", "?"),
                settings.get("Difficulty") or self.src.env.get("DIFFICULTY", "?"),
                "crossplay" if str(self.src.env.get("CROSSPLAY", "")).lower()
                == "true" else "steam-only", db_size_note(s.get("db"))),
             th.c("label")),
            ("world %s   day %s   %s base camps" % (
                (info.get("worldguid") or "?")[:8],
                m.get("days", "?"), m.get("basecampnum", "?")), th.c("label")),
            ("%s   up %s   restarts %s%s" % (
                badge, fmt_dur(m.get("uptime") or 0),
                s.get("restart_count", 0), build_note(s)),
             th.c("good") if ok else th.c("crit")),
        ]
        for i, (text, attr) in enumerate(rows[:ih]):
            cv.text(iy + i, ix, trunc(text, col), attr)

        # right block: gauges + fps graph
        gx = ix + col + 2
        gw = iw - col - 2
        if gw < 18:
            return
        cur = int(m.get("currentplayernum") or 0)
        mx = int(m.get("maxplayernum") or 1)
        bar = max(6, gw - 16)
        cv.text(iy, gx, "players", th.c("label"))
        draw_meter(cv, iy, gx + 8, bar, cur / max(1, mx), th, "free")
        cv.text(iy, gx + 9 + bar, "%d/%d" % (cur, mx),
                th.c("value", bold=True))

        fps = float(m.get("serverfps") or 0)
        cv.text(iy + 1, gx, "fps", th.c("label"))
        draw_meter(cv, iy + 1, gx + 8, bar, fps / 60.0, th, "free")
        cv.text(iy + 1, gx + 9 + bar, "%.0f" % fps, th.grad("free", fps / 60.0))
        if ih >= 4:
            cv.text(iy + 3, gx, "%.2f ms  avg %.1f fps" % (
                float(m.get("serverframetime") or 0),
                float(m.get("serverfpsaverage") or 0)), th.c("label"),
                maxw=gw)
        if ih >= 3 and gw > 24:
            draw_graph(cv, iy + 2, gx, 1, gw - 2, self.hist["fps"], th,
                       "free", vmax=60.0)

    def panel_host(self, cv, y, x, h, w, th):
        s = self.snap
        iy, ix, ih, iw = draw_box(cv, y, x, h, w, "host", th, 0,
                                  right=cpu_model(), color="cpu_box")
        if ih <= 0:
            return
        rl = RowList()
        cores = s.get("cpu_cores") or []
        temps = s.get("temps") or {}
        bar = max(6, iw - 18)

        def cpu_row(label, pct, temp):
            def _draw(row):
                cv.text(row, ix, label.ljust(5), th.c("label"))
                draw_meter(cv, row, ix + 5, bar, pct / 100.0, th, "cpu")
                cv.text(row, ix + 6 + bar, "%3.0f%%" % pct,
                        th.grad("cpu", pct / 100.0))
                if temp is not None and iw > bar + 16:
                    cv.text(row, ix + 11 + bar, "%3.0f\u00b0C" % temp,
                            th.grad("temp", (temp - 25.0) / 60.0))
            return _draw

        rl.add(cpu_row("CPU", s.get("cpu_total", 0.0),
                       temps.get("Package id 0")), 0)
        for i, pct in enumerate(cores):
            rl.add(cpu_row("C%d" % i, pct, temps.get("Core %d" % i)), 2)

        ld = s.get("load", (0, 0, 0))
        freq = s.get("freq")
        rl.add(lambda r: cv.text(
            r, ix, "load %.2f %.2f %.2f   %s  up %s" % (
                ld[0], ld[1], ld[2], ("%.1f GHz" % freq) if freq else "",
                fmt_dur(s.get("host_uptime", 0), compact=True)),
            th.c("label"), maxw=iw), 3)

        mem = s.get("mem", {})
        mtot = max(1, mem.get("total", 1))
        mpct = 100.0 * mem.get("used", 0) / mtot
        rl.add(lambda r: (
            cv.text(r, ix, "MEM".ljust(5), th.c("label")),
            draw_meter(cv, r, ix + 5, bar, mpct / 100.0, th, "used"),
            cv.text(r, ix + 6 + bar, "%3.0f%%" % mpct,
                    th.grad("used", mpct / 100.0))), 0)
        rl.add(lambda r: cv.kv(r, ix, iw, "  host used", "%s / %s" % (
            fmt_bytes(mem.get("used", 0)), fmt_bytes(mtot)), th), 3)

        c = s.get("container", {})
        cpu_pct = c.get("cpu_pct", 0.0)
        rl.rule(cv, ix, iw, th, 4)
        rl.add(lambda r: cv.kv(
            r, ix, iw, "palworld cpu", "%.0f%%  (%.0f%% of %d cores)" % (
                cpu_pct, cpu_pct / max(1, NCPU), NCPU), th,
            th.grad("cpu", cpu_pct / (100.0 * max(1, NCPU)))), 0)
        rl.add(lambda r: cv.kv(
            r, ix, iw, "palworld mem", "%s  %.0f%%" % (
                fmt_bytes(c.get("mem", 0)),
                100.0 * c.get("mem", 0) / mtot), th), 0)
        rl.add(lambda r: cv.kv(
            r, ix, iw, "game process", "pid %s  %s" % (
                c.get("game_pid", "-"), fmt_bytes(c.get("game_rss", 0))),
            th), 5)

        net = s.get("net", {})
        half = max(14, iw // 2)
        rl.rule(cv, ix, iw, th, 6)
        rl.add(lambda r: (
            cv.text(r, ix, "net", th.c("label")),
            cv.text(r, ix + 5, "\u25bc %s" % fmt_rate(net.get("rx", 0)),
                    th.grad("download", 0.9)),
            cv.text(r, ix + 5 + half, "\u25b2 %s" % fmt_rate(
                net.get("tx", 0)), th.grad("upload", 0.9),
                maxw=iw - 5 - half)), 1)
        rl.add(lambda r: (
            cv.text(r, ix + 5, "\u03a3 %s" % fmt_bytes(
                net.get("rx_total", 0)), th.c("label")),
            cv.text(r, ix + 5 + half, "\u03a3 %s" % fmt_bytes(
                net.get("tx_total", 0)), th.c("label"),
                maxw=iw - 5 - half)), 7)

        used = rl.render(iy, ih)
        spare = ih - used
        if spare >= 1:                         # cpu history fills the slack
            draw_graph(cv, iy + used, ix, min(3, spare), iw,
                       self.hist["cpu"], th, "cpu", vmax=100.0)

    def panel_players(self, cv, y, x, h, w, th):
        s = self.snap
        now = s.get("now") or datetime.now(timezone.utc)
        rows = self.roster()
        online = sum(1 for r in rows if r["online"])
        iy, ix, ih, iw = draw_box(
            cv, y, x, h, w, "players", th, 0,
            right="sort:%s  %s  %d/%d" % (
                self.sort, "all" if self.show_all else "online",
                online, len(rows)), color="proc_box")
        if ih <= 1:
            return

        cap = max(LEVEL_CAP, max([r["level"] for r in rows] or [0]))
        wide = iw >= 62
        # column layout
        c_name = max(10, min(18, iw - (44 if wide else 30)))
        bar_w = 8 if wide else 5
        cols = ix + c_name + 7
        hdr = th.c("label", bold=True)
        cv.text(iy, ix + 2, "PLAYER", hdr)
        cv.text(iy, ix + c_name + 3, "LVL", hdr)
        cv.text(iy, cols + bar_w + 4, "PING", hdr)
        cv.text(iy, cols + bar_w + 11, "PLAYED", hdr)
        if wide:
            cv.text(iy, cols + bar_w + 18, "STATUS", hdr)

        body = ih - 1
        total = len(rows)
        self.scroll = max(0, min(self.scroll, max(0, total - body)))
        view = rows[self.scroll:self.scroll + body]

        for i, r in enumerate(view):
            row = iy + 1 + i
            on = r["online"]
            mark = "●" if on else "○"
            cv.text(row, ix, mark, th.c("online") if on else th.c("offline"))
            label = r["name"]
            if r["account"] and r["account"] != r["name"] and wide:
                label = "%s (%s)" % (r["name"], trunc(r["account"], 10))
            cv.text(row, ix + 2, trunc(label, c_name),
                    th.c("value", bold=True) if on else th.c("offline"))

            lvl = r["level"]
            cv.text(row, ix + c_name + 3, ("%3d" % lvl) if lvl else " --",
                    th.grad("free", lvl / max(1, cap)) if on
                    else th.c("offline"))
            draw_meter(cv, row, cols, bar_w, lvl / max(1, cap), th,
                       "free" if on else "cached")

            ping = r["ping"]
            cv.text(row, cols + bar_w + 2,
                    ("%4.0fms" % ping) if ping is not None else "   --",
                    pct_attr(th, ping or 0, 90, 150) if ping is not None
                    else th.c("offline"))
            cv.text(row, cols + bar_w + 11,
                    ("%5.1fh" % (r["playtime"] / 3600.0)).rjust(6),
                    th.c("label"))
            if wide:
                if on and (r.get("idle") or 0) >= AFK_AFTER:
                    txt = "AFK %s" % fmt_dur(r["idle"], compact=True)
                    attr = th.c("warn")
                elif on and r["since"]:
                    txt = "online %s" % fmt_dur(
                        (now - r["since"]).total_seconds(), compact=True)
                    attr = th.c("good")
                elif on:
                    txt, attr = "online", th.c("good")
                else:
                    txt, attr = fmt_ago(r["last_seen"], now), th.c("offline")
                cv.text(row, cols + bar_w + 18, trunc(txt, max(
                    0, iw - (cols + bar_w + 18 - ix))), attr)

        if total > body:
            cv.text(iy, ix + iw - 12, "↑↓ %d/%d" % (
                self.scroll + 1, total), th.c("dim"))

    def panel_ops(self, cv, y, x, h, w, th):
        s = self.snap
        now = s.get("now") or datetime.now(timezone.utc)
        iy, ix, ih, iw = draw_box(cv, y, x, h, w, "ops", th, 0,
                                  color="mem_box")
        if ih <= 0:
            return
        rl = RowList()
        lw = min(15, max(10, iw // 3))

        def put(label, value, attr=None, rank=0):
            rl.add(lambda r: cv.kv(r, ix, iw, label, value, th, attr, lw=lw),
                   rank)

        started = s.get("started_at")
        put("last restart", "%s  %s" % (fmt_stamp(started),
                                        fmt_ago(started, now)))
        rc = s.get("restart_cron") or {}
        if rc.get("active"):
            put("next restart", "%s  %s" % (fmt_stamp(rc.get("next")),
                                            fmt_in(rc.get("next"), now)))
            if rc.get("warn_minutes"):
                put("  if online", "+%d min countdown" % rc["warn_minutes"],
                    th.c("dim"), 3)
        elif rc.get("expr"):
            put("next restart", "not scheduled", th.c("warn"))
            put("  cron", "%s not installed" % rc["expr"], th.c("dim"), 3)
        else:
            put("next restart", "no auto-restart", th.c("dim"))

        rl.rule(cv, ix, iw, th, 5)
        backups = s.get("backups") or []
        bc = s.get("backup_cron") or {}
        if backups:
            newest = backups[0]
            bdt = datetime.fromtimestamp(newest["mtime"], tz=timezone.utc)
            age = (now - bdt).total_seconds()
            put("last backup", "%s  %s" % (fmt_stamp(bdt), fmt_ago(bdt, now)),
                th.c("good") if age < 36 * 3600 else th.c("warn"))
            ok_flag = (s.get("backup_ok") or (None, []))[0]
            shrink = s.get("backup_shrink") or 0.0
            if ok_flag is False:
                put("  integrity", "FAILED gzip check", th.c("crit"))
            elif shrink > 0.30:
                put("  size", "%s  %.0f%% smaller!" % (
                    fmt_bytes(newest["size"]), shrink * 100), th.c("warn"))
            else:
                put("  size", "%s%s" % (
                    fmt_bytes(newest["size"]),
                    "  verified" if ok_flag else ""), None, 4)
        else:
            put("last backup", "none found", th.c("crit"))
        if bc.get("active"):
            put("next backup", "%s  %s" % (fmt_stamp(bc.get("next")),
                                           fmt_in(bc.get("next"), now)))
        else:
            put("next backup", "not scheduled", th.c("warn"))
        if backups:
            oldest = datetime.fromtimestamp(backups[-1]["mtime"],
                                            tz=timezone.utc)
            put("archive", "%d files  %s" % (
                len(backups), fmt_bytes(sum(b["size"] for b in backups))),
                None, 1)
            put("  since", fmt_stamp(oldest), None, 6)
        man = s.get("manual_backups") or []
        if man:
            put("manual copies", "%d files  %s" % (
                len(man), fmt_bytes(sum(b["size"] for b in man))), None, 2)

        rl.rule(cv, ix, iw, th, 5)
        world = s.get("world") or {}
        if world.get("level_mtime"):
            wdt = datetime.fromtimestamp(world["level_mtime"], tz=timezone.utc)
            put("last world save", "%s  %s" % (fmt_stamp(wdt),
                                               fmt_ago(wdt, now)))
            put("  Level.sav", fmt_bytes(world.get("level_size", 0)), None, 4)
        put("save data", "%s  %d saves" % (
            fmt_bytes(world.get("save_bytes", 0)),
            world.get("players_saved", 0)), None, 1)
        disk = s.get("disk")
        if disk:
            used_pct = 100.0 * disk.used / max(1, disk.total)
            put("disk", "%s free  %.0f%% used" % (
                fmt_bytes(disk.free), used_pct),
                pct_attr(th, used_pct, 85, 95), 1)
        rl.render(iy, ih)

    def panel_events(self, cv, y, x, h, w, th):
        s = self.snap
        now = s.get("now") or datetime.now(timezone.utc)
        events = [e for e in (s.get("events") or []) if e["kind"] != "conn"]
        iy, ix, ih, iw = draw_box(cv, y, x, h, w, "events", th, 0,
                                  right="%d in 14d" % len(events),
                                  color="net_box")
        if ih <= 0:
            return
        for i, ev in enumerate(reversed(events[-ih:])):
            row = iy + ih - 1 - i
            if row < iy:
                break
            joined = ev["kind"] == "join"
            # "10-06 19:41" is the longest stamp: 11 characters
            cv.text(row, ix, fmt_stamp(ev["ts"]).rjust(11), th.c("dim"))
            cv.text(row, ix + 12, "→" if joined else "←",
                    th.c("good") if joined else th.c("crit"))
            cv.text(row, ix + 14, trunc(ev["name"], 22),
                    th.c("value", bold=True))
            cv.text(row, ix + 37, "joined" if joined else "left",
                    th.c("good") if joined else th.c("offline"), maxw=8)
            if iw > 54:
                cv.text(row, ix + 46, fmt_ago(ev["ts"], now), th.c("dim"),
                        maxw=iw - 46)

    def panel_footer(self, cv, y, w, th):
        s = self.snap
        keys = [("q", "quit"), ("t", "trends"), ("p", "sort"),
                ("a", "all"), ("s", "settings"), ("e", "events"),
                ("c", "cmds"), ("l", "pals"), ("b", "bases"),
                ("x", "crashes"), ("d", "db"), ("A", "admin"),
                ("h", "help")]
        x = 1
        for key, desc in keys:
            if x + len(key) + len(desc) + 3 > w:
                break
            cv.text(y, x, key, th.c("hotkey", bold=True))
            x += len(key)
            cv.text(y, x, " " + desc + "  ", th.c("label"))
            x += len(desc) + 3
        note = []
        rs = s.get("rest_state")
        if rs == "timeout":
            note.append("REST not responding")
        elif rs in ("refused", "error"):
            note.append("REST down")
        elif s.get("rest_latency"):
            note.append("api %.0fms" % s["rest_latency"])
        running, age, _ = self.collector
        if running is True:
            note.append("collector ok")
        elif running is False:
            note.append("COLLECTOR STOPPED")
        busy = getattr(getattr(self, "poller", None), "busy_since", None)
        if busy and time.monotonic() - busy > 1.5:
            note.append("waiting on server %.0fs" % (time.monotonic() - busy))
        if self.paused:
            note.append("PAUSED")
        note.append("read-only")
        attr = th.c("crit") if rs not in (None, "ok", "unknown") else th.c("dim")
        while note:
            txt = "  ".join(note)
            if w - len(txt) - 1 >= x:
                cv.text(y, w - len(txt) - 1, txt, attr)
                return
            note.pop()

    def panel_alerts(self, cv, y, x, h, w, th):
        alerts = self.snap.get("alerts") or []
        iy, ix, ih, iw = draw_box(
            cv, y, x, h, w, "alerts", th, 0,
            right="%d" % len(alerts) if alerts else None,
            color="proc_box" if any(a["level"] == "crit" for a in alerts)
            else "mem_box")
        for i, a in enumerate(alerts[:max(0, ih)]):
            attr = th.c("crit") if a["level"] == "crit" else (
                th.c("warn") if a["level"] == "warn" else th.c("label"))
            mark = "\u25b2" if a["level"] == "crit" else "\u25b3"
            cv.text(iy + i, ix, mark, attr)
            cv.text(iy + i, ix + 2, trunc(a["text"], iw - 2), attr)
        if len(alerts) > ih > 0:
            cv.text(iy + ih - 1, ix + iw - 12, "+%d more" % (len(alerts) - ih),
                    th.c("dim"))

    # -- trends page ------------------------------------------------------

    def page_trends(self, cv, th, top):
        H, W = cv.h - 1, cv.w
        avail = H - top
        hm_h = min(11, max(5, avail // 2))
        self.panel_heatmap(cv, top, 0, hm_h, W, th)
        rest = avail - hm_h
        if rest < 5:
            return
        gh = rest // 2 if rest >= 12 else rest
        half = W // 2
        self.panel_series(cv, top + hm_h, 0, gh, half, th, "players",
                          "players 24h", "free", None)
        self.panel_series(cv, top + hm_h, half, gh, W - half, th, "fps",
                          "server fps 24h", "free", 60.0, floor=True)
        if rest - gh >= 5:
            self.panel_growth(cv, top + hm_h + gh, 0, rest - gh, W, th)

    def panel_heatmap(self, cv, y, x, h, w, th):
        grid = self.src.db.activity_matrix(self.snap.get("sessions") or {})
        peak = max((max(r) for r in grid), default=0.0) or 1.0
        total = sum(sum(r) for r in grid)
        iy, ix, ih, iw = draw_box(
            cv, y, x, h, w, "activity", th, 0,
            right="player-hours by local time, last 8 weeks  \u03a3 %.0fh"
            % (total / 3600.0), color="mem_box")
        if ih < 3:
            return
        cell = max(1, min(4, (iw - 5) // 24))
        x0 = ix + 4
        hdr = "".join(("%-*d" % (cell * 2, hour)) if hour % 2 == 0 else ""
                      for hour in range(24))
        cv.text(iy, x0, hdr[:max(0, iw - 4)], th.c("label"))
        for d in range(min(7, ih - 1)):
            cv.text(iy + 1 + d, ix, DAYS[d], th.c("label"))
            for hour in range(24):
                v = grid[d][hour] / peak
                cx = x0 + hour * cell
                if cx + cell > ix + iw:
                    break
                ch = "\u2588" * cell if v > 0.02 else "\u00b7" * cell
                cv.text(iy + 1 + d, cx, ch,
                        th.grad("cached", v) if v > 0.02 else th.c("meter_bg"))

    def panel_series(self, cv, y, x, h, w, th, column, title, grad, vmax,
                     floor=False):
        data = self.src.db.series(column, days=1, buckets=max(8, (w - 6) * 2))
        iy, ix, ih, iw = draw_box(
            cv, y, x, h, w, title, th, 0, color="cpu_box",
            right=("now %.0f" % data[-1]) if data else "no history yet")
        if ih <= 0:
            return
        if not any(data):
            cv.text(iy, ix, "collecting - history builds while paltop runs",
                    th.c("dim"), maxw=iw)
            return
        live = [v for v in data if v] or [0.0]
        top = vmax or (max(data) or 1.0)
        base = max(0.0, min(live) - (top - min(live)) * 0.15) if floor else 0.0
        draw_graph(cv, iy, ix, ih - 1, iw, data, th, grad, vmax=top, vmin=base)
        cv.text(iy + ih - 1, ix, "24h ago", th.c("dim"))
        cv.text(iy + ih - 1, ix + iw - 22,
                "min %.0f   peak %.0f" % (min(live), max(data)), th.c("dim"))

    def panel_growth(self, cv, y, x, h, w, th):
        rows = self.src.db.backup_series()
        iy, ix, ih, iw = draw_box(cv, y, x, h, w, "world growth", th, 0,
                                  color="mem_box",
                                  right="%d backups" % len(rows))
        if ih <= 0 or not rows:
            return
        sizes = [r["size"] for r in rows]
        if ih >= 2:
            draw_graph(cv, iy, ix, ih - 1, iw, sizes, th, "available",
                       vmax=max(sizes))
        first, last = rows[0], rows[-1]
        span = (last["mtime"] - first["mtime"]) / 86400.0 or 1.0
        cv.text(iy + ih - 1, ix, "%s \u2192 %s over %.0f days  (+%s/day)" % (
            fmt_bytes(first["size"]), fmt_bytes(last["size"]), span,
            fmt_bytes((last["size"] - first["size"]) / span)),
            th.c("dim"), maxw=iw)

    # -- overlays --------------------------------------------------------

    def overlay_settings(self, cv, th):
        diff = self.snap.get("settings_diff")
        if self.settings_all or diff is None:
            items = [(k, fmt_setting(v))
                     for k, v in (self.snap.get("settings") or {}).items()
                     if not isinstance(v, (dict, list))]
            self._overlay_table(cv, th, "world settings - all",
                                items, "a: only changed")
        else:
            items = [(r["key"], "%s      was %s" % (
                fmt_setting(r["value"]), fmt_setting(r["default"])))
                for r in diff]
            if not items:
                items = [("(nothing)", "every setting matches the defaults")]
            self._overlay_table(cv, th, "world settings - changed from default",
                                items, "a: show all")

    def overlay_pals(self, cv, th):
        """Each player's best Pals in a single column under their name.

        A player and their Pals form one block. Blocks stack down a column,
        and a block that will not fit before the bottom starts a new column
        instead of being split. If that needs more columns than the width
        allows, fewer Pals are shown per player until it fits.

        Species are coloured by element (from the game's own data table via
        palpak.py) and IVs are flagged at 80+ and again at a perfect 100.
        """
        snap = self.snap
        top = snap.get("save_top_pals") or {}
        counts = snap.get("save_pal_counts") or {}
        els = snap.get("elements") or {}

        y = 5 if cv.h > 24 else 0
        for row in range(y, cv.h - 1):              # clear what is behind
            cv.text(row, 0, " " * cv.w, th.c("value"))
        iy, ix, ih, iw = draw_box(
            cv, y, 0, cv.h - 1 - y, cv.w, "pals", th, 0,
            right="%d pals   esc to close" % (snap.get("save_pal_total") or 0),
            color="proc_box")
        if ih <= 0:
            return

        if self.pal_player:
            return self.overlay_pal_player(cv, th)
        players = [r for r in self.roster()
                   if r.get("pid") and r["pid"] in counts]
        players.sort(key=lambda r: -r["level"])
        self._pal_order = [r["pid"] for r in players]
        self.pal_cursor = max(0, min(self.pal_cursor, len(players) - 1))
        cv.text(y, 10, " \u2191\u2193 pick a player, enter to see all "
                "their pals ", th.c("dim"))
        if not players:
            cv.text(iy, ix, snap.get("save_error") or "no save data yet",
                    th.c("dim"), maxw=iw)
            return

        max_cols = max(1, iw // 40)
        cols = [[]]
        for cap in (PALS_PER_PLAYER, 20, 16, 12, 10, 8, 6, 4, 3, 2, 1):
            cols, used = [[]], 0
            for r in players:
                n = min(len(top.get(r["pid"], [])), cap)
                height = n + 2                      # name + pals + blank line
                if used and used + height > ih:
                    cols.append([])
                    used = 0
                cols[-1].append((r, n))
                used += height
            if len(cols) <= max_cols:
                break
        colw = iw // len(cols)

        for ci, col in enumerate(cols[:max_cols]):
            cx = ix + ci * colw
            ry = iy
            for r, n in col:
                if ry >= iy + ih:
                    break
                sel = (self._pal_order.index(r["pid"]) == self.pal_cursor)
                if sel:
                    cv.text(ry, cx, "\u25b6", th.c("hotkey", bold=True))
                cv.text(ry, cx + 2, trunc(r["name"], 16),
                        th.c("hotkey", bold=True) if sel else
                        th.c("value", bold=True) if r["online"]
                        else th.c("label"))
                nx = cx + 2 + min(17, len(r["name"]) + 1)
                cv.text(ry, nx, "lv%3d" % r["level"],
                        th.c("online") if r["online"] else th.c("dim"))
                cv.text(ry, nx + 7, "%d pals" % counts.get(r["pid"], 0),
                        th.c("dim"))
                ry += 1
                for q in top.get(r["pid"], [])[:n]:
                    if ry >= iy + ih:
                        break
                    alpha, name = pal_display(q["species"],
                                              snap.get("pal_names"))
                    if alpha:
                        cv.text(ry, cx, "\u03b1", th.c("warn", bold=True))
                    cv.text(ry, cx + 2, trunc(name, 18),
                            th.c("el_" + element_of(q["species"], els).lower()))
                    cv.text(ry, cx + 21, "lv%3d" % q["level"], th.c("label"))
                    vx = cx + 27
                    for j, iv in enumerate((q["hp_iv"], q["atk_iv"],
                                            q["def_iv"])):
                        t = "%d" % iv
                        cv.text(ry, vx, t, iv_attr(th, iv))
                        vx += len(t)
                        if j < 2:
                            cv.text(ry, vx, "/", th.c("dim"))
                            vx += 1
                    ry += 1
                ry += 1                             # blank line between blocks

    PAL_SORTS = ("best", "attack", "defense", "tank", "fighter", "work",
                 "level", "IVs", "name", "where")
    PAL_FILTERS = ("all", "team", "Palbox", "bases", "lucky + alpha")

    def pal_list(self, pid):
        """This player's Pals from the save, filtered and sorted as chosen,
        each paired with (short, long) location."""
        saves = self.src.saves
        game = self.src.gamedata or {}
        if not saves:
            return []
        bases = self.snap.get("base_camps") or []
        mine = []
        for p in saves.pals:
            if (p.get("owner") or "") != pid:
                continue
            loc = palpals.location(p, saves.containers, bases)
            want = self.PAL_FILTERS[self.pal_filter]
            if want == "team" and loc[0] != "team":
                continue
            if want == "Palbox" and loc[0] != "box":
                continue
            if want == "bases" and not loc[0].startswith("base"):
                continue
            if want == "lucky + alpha" and not (
                    p["lucky"] or p["species"][:5].upper() == "BOSS_"):
                continue
            if self.pal_search and not palpals.matches(
                    game, p, self.pal_search, self.snap.get("pal_names"),
                    self.snap.get("elements") or {}):
                continue
            mine.append((p, loc))
        names = self.snap.get("pal_names")
        how = self.PAL_SORTS[self.pal_sort]
        if how in palteam.SORT_GOALS:
            order = {id(q): i for i, (q, _) in
                     enumerate(self.pal_ranking(pid, how))}
            mine.sort(key=lambda t: order.get(id(t[0]), 10**6))
        elif how == "best":
            mine.sort(key=lambda t: palpals.score(t[0], game), reverse=True)
        elif how == "level":
            mine.sort(key=lambda t: (-t[0]["level"], t[0]["species"]))
        elif how == "IVs":
            mine.sort(key=lambda t: -(t[0]["hp_iv"] + t[0]["atk_iv"]
                                      + t[0]["def_iv"]))
        elif how == "name":
            mine.sort(key=lambda t: pal_display(t[0]["species"], names)[1])
        else:
            order = {"team": 0, "box": 2, "other": 3}
            mine.sort(key=lambda t: (order.get(t[1][0], 1), t[1][0],
                                     t[0].get("slot") or 0))
        return mine

    def owner_level(self, pid):
        sp = (self.snap.get("save_players") or {}).get(pid) or {}
        return sp.get("level")

    def pal_ranking(self, pid, goal):
        """[(pal, evaluation)] for one player's Pals, best first. Cached per
        save load, since ranking 800 Pals every frame would be wasted work."""
        saves = self.src.saves
        key = (pid, goal, self.pal_at_level, saves.loaded_at if saves else 0)
        hit = self._rank_cache.get(key)
        if hit is None:
            if len(self._rank_cache) > 64:
                self._rank_cache.clear()
            mine = [p for p in (saves.pals if saves else [])
                    if (p.get("owner") or "") == pid]
            lvl = self.owner_level(pid) if self.pal_at_level else None
            hit = palteam.ranked(
                self.src.gamedata or {}, mine, goal,
                self.snap.get("elements") or {},
                (lambda p: max(p["level"], lvl)) if lvl else None)
            self._rank_cache[key] = hit
        return hit

    def pals_key(self, ch):
        """Keys inside the pals overlay. True when the key was used here."""
        enter = ch in (10, 13, curses.KEY_ENTER)
        if not self.pal_player:
            n = len(self._pal_order)
            if ch in (curses.KEY_DOWN, curses.KEY_RIGHT) and n:
                self.pal_cursor = (self.pal_cursor + 1) % n
            elif ch in (curses.KEY_UP, curses.KEY_LEFT) and n:
                self.pal_cursor = (self.pal_cursor - 1) % n
            elif enter and n:
                self.pal_player = self._pal_order[self.pal_cursor]
                self.pal_idx = 0
            else:
                return False
            return True
        if self.planner:
            return self.planner_key(ch)
        if self.breed_view:
            if ch in (27, ord("g")):
                self.breed_view = False
            return True
        if self.pal_typing:
            return self._search_key(ch)
        if self.pal_team:
            if self.pal_choosing and ch == ord("/"):
                self.pal_typing = "choose"
                return True
            return self.team_key(ch)
        total = len(self._pal_view)
        if ch == ord("g"):
            self.breed_view = True
            return True
        if ch == ord("e") and self._pal_view:
            self.open_planner(self._pal_view[
                min(self.pal_idx, len(self._pal_view) - 1)][0])
            return True
        if ch == ord("/"):
            self.pal_typing = "list"
            return True
        if ch == ord("t"):
            self.pal_team = True
            self.pal_slot = 0
            self.pal_locked = {}
            self.pal_choosing = False
            return True
        if ch == ord("n"):
            self.pal_at_level = not self.pal_at_level
            return True
        if ch == 27:
            self.pal_player = None
        elif ch == curses.KEY_DOWN:
            self.pal_idx += 1
        elif ch == curses.KEY_UP:
            self.pal_idx -= 1
        elif ch in (curses.KEY_NPAGE, ord("j")):
            self.pal_idx += 10
        elif ch in (curses.KEY_PPAGE, ord("k")):
            self.pal_idx -= 10
        elif ch == curses.KEY_HOME:
            self.pal_idx = 0
        elif ch == curses.KEY_END:
            self.pal_idx = total - 1
        elif ch == ord("o"):
            self.pal_sort = (self.pal_sort + 1) % len(self.PAL_SORTS)
            self.pal_idx = 0
        elif ch == ord("f"):
            self.pal_filter = (self.pal_filter + 1) % len(self.PAL_FILTERS)
            self.pal_idx = 0
        else:
            return False
        self.pal_idx = max(0, min(self.pal_idx, max(0, total - 1)))
        return True

    def team_key(self, ch):
        n = len(palteam.GOALS)
        if self.pal_choosing:
            return self._choose_key(ch)
        if ch in (27, ord("t")):
            self.pal_team = False
        elif ch in (curses.KEY_RIGHT, curses.KEY_LEFT):
            step = 1 if ch == curses.KEY_RIGHT else -1
            self.pal_goal = (self.pal_goal + step) % n
            self.pal_locked = {}
        elif ch == curses.KEY_DOWN:
            self.pal_slot = min(palteam.TEAM_SIZE - 1, self.pal_slot + 1)
        elif ch == curses.KEY_UP:
            self.pal_slot = max(0, self.pal_slot - 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            self.pal_choosing, self.pal_cand = True, 0
        elif ch == ord("l"):                          # lock / unlock the slot
            if self.pal_slot in self.pal_locked:
                del self.pal_locked[self.pal_slot]
            else:
                _goal, _ranking, team = self._team()
                if team[self.pal_slot] is not None:
                    self.pal_locked[self.pal_slot] = team[self.pal_slot]
        elif ch == ord("r"):
            self.pal_locked = {}
        elif ch == ord("p"):                  # which passives count
            modes = palteam.PASSIVE_MODES
            self.team_mode = modes[(modes.index(self.team_mode) + 1)
                                   % len(modes)]
            self.pal_locked = {}
        elif ch == ord("e"):                  # plan this slot's passives
            goal, ranking, team = self._team()
            p = team[self.pal_slot] if self.pal_slot < len(team) else None
            if p is not None:
                real = next((q for q in (self.src.saves.pals if
                                          self.src.saves else [])
                             if palteam.pal_key(q) == palteam.pal_key(p)), p)
                self.open_planner(real)
                self.planner["goal"] = self.PLAN_GOALS.index(goal)
        elif ch == ord("u") and getattr(self, "_team_swaps", None):
            w = self._team_swaps[0]           # the team the gain was for
            goal, ranking, team = self._team()
            self.pal_locked = {i: q for i, q in enumerate(team)
                               if q is not None}
            self.pal_locked[w["slot"]] = w["in"]
        elif ch == ord("i"):                  # include hard-to-summon Pals
            self.pal_include_hard = not self.pal_include_hard
            self.pal_locked = {}
        elif ch == ord("n"):
            self.pal_at_level = not self.pal_at_level
            self.pal_locked = {}
        elif ch == 9 and self._pal_order:             # Tab: next player
            i = self._pal_order.index(self.pal_player) \
                if self.pal_player in self._pal_order else -1
            self.pal_player = self._pal_order[(i + 1) % len(self._pal_order)]
            self.pal_locked = {}
        else:
            return False
        return True

    def _search_key(self, ch):
        """Typing a search: letters add, Backspace removes, Enter keeps it,
        Esc clears it."""
        attr = "pal_search" if self.pal_typing == "list" else "pal_csearch"
        text = getattr(self, attr)
        if ch == 27:
            text, self.pal_typing = "", None
        elif ch in (10, 13, curses.KEY_ENTER):
            self.pal_typing = None
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            text = text[:-1]
        elif 32 <= ch < 127 and len(text) < 30:
            text += chr(ch)
        setattr(self, attr, text)
        self.pal_idx = self.pal_cand = 0
        return True

    def _match(self):
        text = self.pal_csearch
        if not text:
            return None
        game = self.src.gamedata or {}
        names = self.snap.get("pal_names")
        els = self.snap.get("elements") or {}
        return lambda p: palpals.matches(game, p, text, names, els)

    def _choose_key(self, ch):
        """Keys while the candidate list for a slot is open."""
        if ch == 27:
            self.pal_choosing = False
            self.pal_csearch = ""
        elif ch == curses.KEY_DOWN:
            self.pal_cand += 1
        elif ch == curses.KEY_UP:
            self.pal_cand = max(0, self.pal_cand - 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            goal, ranking, team = self._team()
            cands = palteam.candidates(ranking, goal,
                                       self.snap.get("elements") or {},
                                       team, self.pal_slot,
                                       match=self._match())
            if cands:
                pick = cands[min(self.pal_cand, len(cands) - 1)]["pal"]
                # taking a Pal from another slot frees that slot for refill
                self.pal_locked = {i: q for i, q in self.pal_locked.items()
                                   if q is not pick}
                self.pal_locked[self.pal_slot] = pick
            self.pal_choosing = False
        return True

    def _mode_ranking(self, goal):
        """The ranking with the passives the team mode asks for."""
        if self.team_mode == "real":
            return self.pal_ranking(self.pal_player, goal)
        saves = self.src.saves
        plans = self.plans if self.team_mode == "plans" else None
        key = ("mode", self.pal_player, goal, self.pal_at_level,
               self.team_mode, saves.loaded_at if saves else 0,
               tuple(sorted((k, tuple(v)) for k, v in (plans or {}).items())))
        hit = self._rank_cache.get(key)
        if hit is None:
            game = self.src.gamedata or {}
            els = self.snap.get("elements") or {}
            mine = [p for p in (saves.pals if saves else [])
                    if (p.get("owner") or "") == self.pal_player]
            lvl = self.owner_level(self.pal_player) if self.pal_at_level \
                else None
            level_for = (lambda p: max(p["level"], lvl)) if lvl else None
            pool = self._plan_pool({"passives": []})
            pals = palteam.what_if(game, mine, goal, els, self.team_mode,
                                   plans, pool, level_for)
            hit = palteam.ranked(game, pals, goal, els, level_for)
            self._rank_cache[key] = hit
        return hit

    def _team(self):
        goal = palteam.GOALS[self.pal_goal][0]
        ranking, self._team_left_out = palteam.usable(
            self._mode_ranking(goal), self.pal_include_hard)
        # locks hold Pals by identity; find them again in this ranking
        by_key = {palteam.pal_key(p): p for p, _ in ranking}
        locked = {i: by_key.get(palteam.pal_key(q), q)
                  for i, q in self.pal_locked.items()}
        team = palteam.build(ranking, goal, self.snap.get("elements") or {},
                             locked=locked, game=self.src.gamedata or {})
        return goal, ranking, team

    def _draw_choices(self, cv, th, goal, ranking, team, y, iy, ix, ih,
                      iw):
        """The candidates for the selected slot and what each would do to
        the team."""
        els = self.snap.get("elements") or {}
        names = self.snap.get("pal_names")
        cands = palteam.candidates(ranking, goal, els, team, self.pal_slot,
                                   match=self._match())
        self.pal_cand = max(0, min(self.pal_cand, len(cands) - 1))
        top = ranking[0][1]["score"] if ranking else 1
        cur = team[self.pal_slot]
        cv.text(y, ix, "CHOOSE SLOT %d%s   %s" % (
            self.pal_slot + 1, "  (now %s)" % (
                cur["nickname"] or pal_display(cur["species"], names)[1])
            if cur else "",
            ("search: %s%s" % (self.pal_csearch, "_" if
                               self.pal_typing == "choose" else ""))
            if self.pal_csearch or self.pal_typing == "choose"
            else "/ to search"), th.c("label", bold=True), maxw=iw)
        if not cands:
            cv.text(y + 1, ix + 2, "no candidates match" if self.pal_csearch
                    else "no other candidates", th.c("dim"))
        y += 1
        body = iy + ih - y
        first = max(0, min(self.pal_cand - body // 2, len(cands) - body))
        for n, c in enumerate(cands[first:first + body]):
            k = first + n
            p, ev = c["pal"], c["ev"]
            row = y + n
            sel = k == self.pal_cand
            if sel:
                cv.text(row, ix, "\u25b6", th.c("hotkey", bold=True))
            _, name = pal_display(p["species"], names)
            cv.text(row, ix + 2, trunc(p["nickname"] or name, 18),
                    th.c("el_" + element_of(p["species"], els).lower(),
                         bold=sel))
            cv.text(row, ix + 21, "%3d" % p["level"], th.c("label"))
            vx = ix + 26
            for j, iv in enumerate((p["hp_iv"], p["atk_iv"], p["def_iv"])):
                t = "%d" % iv
                cv.text(row, vx, t, iv_attr(th, iv))
                vx += len(t)
                if j < 2:
                    cv.text(row, vx, "/", th.c("dim"))
                    vx += 1
            cv.text(row, ix + 38, "%5.0f" % (100.0 * ev["score"] / top),
                    th.c("value", bold=True))
            x = ix + 45
            bits = []
            if c["in_slot"] is not None:
                bits.append(("in slot %d, moves here" % (c["in_slot"] + 1),
                             "warn"))
            if c["same_species"] is not None:
                bits.append(("same species as slot %d"
                             % (c["same_species"] + 1), "warn"))
            if c["gains"]:
                bits.append(("+" + ",".join(palpals.ELEMENT_NAMES.get(e, e)
                                            for e in c["gains"]), "good"))
            if c["loses"]:
                bits.append(("-" + ",".join(palpals.ELEMENT_NAMES.get(e, e)
                                            for e in c["loses"]), "crit"))
            if not goal.startswith("work") and c["weak"]:
                bits.append(("weak: " + ", ".join(
                    "%d to %s" % (n2, palpals.ELEMENT_NAMES.get(e, e))
                    for e, n2 in c["weak"]), "label"))
            if not bits:
                bits.append(("; ".join(ev["why"]), "label"))
            for text, attr in bits:
                if x >= ix + iw:
                    break
                cv.text(row, x, text, th.c(attr), maxw=ix + iw - x)
                x += len(text) + 3

    def overlay_team(self, cv, th, pname):
        """Team builder: the best team of five for a goal from this player's
        Pals, why each was picked, and the team's weak spots."""
        game = self.src.gamedata or {}
        els = self.snap.get("elements") or {}
        names = self.snap.get("pal_names")
        goal, ranking, team = self._team()
        evs = {id(p): ev for p, ev in ranking}
        lvl = self.owner_level(self.pal_player)
        mode = ("as if levelled to %d" % lvl if self.pal_at_level and lvl
                else "at their current level")
        iy, ix, ih, iw = self._full_box(
            cv, th, "team builder: %s" % pname,
            ("\u2191\u2193 pick   enter choose   esc cancel"
             if self.pal_choosing else
             "\u2190\u2192 goal   \u2191\u2193 slot   enter choose   l lock"
             "   e plan   p passives   r reset   i hard   n level   esc back"),
            "proc_box")
        if ih <= 0:
            return
        cv.text(iy, ix, "goal", th.c("label"))
        cv.text(iy, ix + 8, "\u25c0 %s \u25b6" % palteam.GOAL_LABEL[goal],
                th.c("hotkey", bold=True))
        cv.text(iy + 1, ix, "scored", th.c("label"))
        cv.text(iy + 1, ix + 8, "%s, from %d pals   passives: %s%s   "
                "(p to change)" % (
                    mode, len(ranking), palteam.MODE_LABEL[self.team_mode],
                    "  (%d planned)" % len(self.plans) if self.plans else ""),
                th.c("value"), maxw=iw - 8)
        hdr = th.c("label", bold=True)
        for cx, t in ((4, "PAL"), (23, "LV"), (28, "HP/ATK/DEF"),
                      (41, "SCORE"), (49, "WHY")):
            cv.text(iy + 3, ix + cx, t, hdr)
        top = ranking[0][1]["score"] if ranking else 1
        for i, p in enumerate(team):
            row = iy + 4 + i
            sel = i == self.pal_slot
            if sel:
                cv.text(row, ix, "\u25b6", th.c("hotkey", bold=True))
            cv.text(row, ix + 2, "%d" % (i + 1), th.c("dim"))
            if p is None:
                cv.text(row, ix + 4, "(no more pals for this goal)",
                        th.c("dim"))
                continue
            ev = evs.get(id(p)) or palteam.evaluate(game, p, goal, els)
            _, name = pal_display(p["species"], names)
            cv.text(row, ix + 4, trunc(p["nickname"] or name, 18),
                    th.c("el_" + element_of(p["species"], els).lower(),
                         bold=sel))
            cv.text(row, ix + 23, "%3d" % p["level"], th.c("label"))
            vx = ix + 28
            for j, iv in enumerate((p["hp_iv"], p["atk_iv"], p["def_iv"])):
                t = "%d" % iv
                cv.text(row, vx, t, iv_attr(th, iv))
                vx += len(t)
                if j < 2:
                    cv.text(row, vx, "/", th.c("dim"))
                    vx += 1
            cv.text(row, ix + 41, "%5.0f" % (100.0 * ev["score"] / top),
                    th.c("value", bold=True))
            if i in self.pal_locked:
                cv.text(row, ix + 47, "*", th.c("warn"))
            cv.text(row, ix + 49, "; ".join(ev["why"]), th.c("label"),
                    maxw=iw - 49)

        line = iy + 5 + palteam.TEAM_SIZE
        members = [p for p in team if p]
        weak = palteam.weaknesses(team, els)
        bad = [(el, n) for el, n in sorted(weak.items(), key=lambda t: -t[1])
               if n >= 2]
        have = sorted({e for p in members for e in palteam.elements(p, els)})
        cv.text(line, ix, "team", th.c("label"))
        cv.text(line, ix + 8, "elements: %s" % ", ".join(
            palpals.ELEMENT_NAMES.get(e, e) for e in have), th.c("value"),
            maxw=iw - 8)
        if not goal.startswith("work"):
            cv.text(line + 1, ix + 8, ("weak spot: " + ", ".join(
                "%d of %d weak to %s" % (n, len(members),
                                         palpals.ELEMENT_NAMES.get(el, el))
                for el, n in bad)) if bad else "no element hits more than "
                "one of them for double damage",
                th.c("warn") if bad else th.c("good"), maxw=iw - 8)
        best = palteam.best_species(game, goal, els, level=lvl or 60,
                                    include_hard=self.pal_include_hard)
        # by display name: a caught Panthalus is KingWhale_otomo, not KingWhale
        owned = {pal_display(p["species"], names)[1] for p, _ in ranking}
        cv.text(line + 2, ix, "ideal", th.c("label"))
        cv.text(line + 2, ix + 8, "strongest species for this goal: " +
                ", ".join("%s%s" % (pal_display(sp, names)[1],
                                    " (owned)" if pal_display(sp, names)[1]
                                    in owned else "")
                          for sp in best), th.c("value"), maxw=iw - 8)

        y = line + 3
        status = palteam.status_of(goal)
        if status:                  # what it does, and how the team uses it
            si = palteam.status_info(game, members, status, names)
            bits = ["applied by " + ", ".join(si["appliers"])
                    if si["appliers"] else "nobody here applies it"]
            if si["combo"]:
                bits.append("%s hits from %s" % (si["combo"], ", ".join(
                    si["finishers"])) if si["finishers"]
                    else "no %s skills to cash in on it" % si["combo"])
            if si["booster"]:
                bits.append("%s: +%g%% damage to them" % si["booster"])
            cv.text(y, ix, si["name"][:7], th.c("label"))
            cv.text(y, ix + 8, "; ".join(bits), th.c("good")
                    if si["appliers"] else th.c("warn"), maxw=iw - 8)
            y += 1
        left = getattr(self, "_team_left_out", [])
        if left or self.pal_include_hard:
            cv.text(y, ix, "left out", th.c("label"))
            cv.text(y, ix + 10, ("; ".join(left) + "   (i to include)")
                    if left else "hard-to-summon Pals included   (i to "
                    "leave them out)", th.c("warn") if left else th.c("dim"),
                    maxw=iw - 10)
            y += 1
        y += 1
        # the team's synergy, all in one place
        if not palteam.is_work(goal) and not self.pal_choosing:
            o = palteam.synergy_overview(game, team, els, goal, ranking,
                                         names)
            self._team_swaps = o["swaps"]
            cv.text(y, ix, "synergy", th.c("label"))
            cv.text(y, ix + 10, "partner skills make this team %+.0f%% "
                    "stronger" % (100 * o["gain"]) if o["gain"] > 0.001
                    else "no partner skills that help each other",
                    th.c("good") if o["gain"] > 0.001 else th.c("dim"),
                    maxw=iw - 10)
            y += 1
            for l in o["links"][:4]:
                cv.text(y, ix + 10, "%s -> %s: %s" % (l["from"], l["to"],
                                                     l["effect"]),
                        th.c("el_ice"), maxw=iw - 10)
                y += 1
            if o.get("vs_fighter") is not None and o["vs_fighter"] > 0.005:
                cv.text(y, ix + 10, "built for buffs: the strongest fighter "
                        "team is %+.0f%% stronger in total" % (
                            100 * o["vs_fighter"]), th.c("dim"),
                        maxw=iw - 10)
                y += 1
            for u in o["unused"][:2]:
                cv.text(y, ix + 10, "! " + u, th.c("warn"), maxw=iw - 10)
                y += 1
            nm = lambda p: p.get("nickname") or pal_display(p["species"],
                                                            names)[1]
            for a, b, g2, why in (o.get("pairs") or [])[:2]:
                cv.text(y, ix + 10, "pair %s + %s %+.0f%%: %s" % (
                    nm(a), nm(b), 100 * g2, "; ".join(why[:1])),
                    th.c("el_ice"), maxw=iw - 10)
                y += 1
            for a, b, g2, why in (o.get("best_pairs") or [])[:1]:
                cv.text(y, ix + 10, "also good together: %s lv %d + %s lv %d "
                        "%+.0f%%" % (nm(a), a["level"], nm(b), b["level"],
                                     100 * g2), th.c("dim"), maxw=iw - 10)
                y += 1
            if o["swaps"]:
                w = o["swaps"][0]
                cv.text(y, ix + 10, "u: swap slot %d %s -> %s for %+.0f%% "
                        "(%s)" % (w["slot"] + 1, w["out"].get("nickname") or
                                  pal_display(w["out"]["species"], names)[1],
                                  w["in"].get("nickname") or pal_display(
                                      w["in"]["species"], names)[1],
                                  100 * w["gain"], "; ".join(w["why"][:1])),
                        th.c("hotkey"), maxw=iw - 10)
                y += 1
            y += 1
        if self.pal_choosing:
            return self._draw_choices(cv, th, goal, ranking, team, y, iy,
                                      ix, ih, iw)
        # the selected slot: traits, and what could replace it
        p = team[self.pal_slot] if self.pal_slot < len(team) else None
        if p is None or y >= iy + ih:
            if y < iy + ih:
                cv.text(y, ix, "* = chosen or locked; stays put while the "
                        "other slots are refilled", th.c("dim"), maxw=iw)
            return
        ev = evs.get(id(p)) or palteam.evaluate(game, p, goal, els)
        _, name = pal_display(p["species"], names)
        cv.text(y, ix, "SLOT %d  %s" % (self.pal_slot + 1,
                                         p["nickname"] or name),
                th.c("label", bold=True))
        y += 1
        others = [q for q in team if q is not None and q is not p]
        syn = palteam.team_synergy(game, others + [p], els, goal,
                                   names)[-1]
        rows = [("synergy" if k == 0 else "", note, th.c("el_ice"))
                for k, note in enumerate(syn)]
        if p.get("planned"):
            real_ids = p.get("real_passives") or []
            rows.append(("counted with", ", ".join(
                palpals.passive(game, q)["name"] + ("" if q in real_ids
                                                    else "*")
                for q in p["passives"]) + "   (* = it does not have it yet; "
                "e to plan)", th.c("warn")))
        rows += [("good traits", ", ".join(ev["good"]) or "none for this goal",
                 th.c("good") if ev["good"] else th.c("dim")),
                ("bad traits", ", ".join(ev["bad"]) or "none",
                 th.c("crit") if ev["bad"] else th.c("dim")),
                ("all traits", ", ".join(palpals.passive(game, q)["name"]
                                         for q in p["passives"]) or "none",
                 th.c("label"))]
        stars = max(0, p["rank"] - 1)
        if stars or any(p["soul"].values()):
            rows.append(("not counted", "condenser %s, souls HP+%d ATK+%d "
                         "DEF+%d" % ("\u2605" * stars or "-",
                                     p["soul"]["HP"], p["soul"]["Attack"],
                                     p["soul"]["Defence"]), th.c("dim")))
        if min(p["hp_iv"], p["atk_iv"], p["def_iv"]) >= 90 and ev["bad"]:
            rows.append(("hint", "great IVs held back by its traits - breed "
                         "it with a Pal that has good ones", th.c("warn")))
        elif ev["good"] and min(p["hp_iv"], p["atk_iv"], p["def_iv"]) < 50:
            rows.append(("hint", "good traits but weak IVs - breed it with a "
                         "high-IV Pal of the same species", th.c("warn")))
        rows.append(("", "enter: choose another Pal for this slot   l: "
                     "%s   * = stays put" % (
                         "unlock" if self.pal_slot in self.pal_locked
                         else "lock it"), th.c("dim")))
        for label, text, attr in rows:
            if y >= iy + ih:
                break
            cv.text(y, ix, label, th.c("label"))
            cv.text(y, ix + 13, text, attr, maxw=iw - 13)
            y += 1

    # -- passive planner ---------------------------------------------------

    PLAN_GOALS = [g for g, _, _ in palteam.GOALS]

    def _plan_pool(self, pal):
        saves = self.src.saves
        key = saves.loaded_at if saves else 0
        if getattr(self, "_plan_pool_key", None) != key:
            self._plan_pool_cache = palteam.passive_pool(
                self.src.gamedata or {}, saves.pals if saves else [])
            self._plan_pool_key = key
        pool = dict(self._plan_pool_cache)
        for x in pal["passives"]:
            pool.setdefault(x, ["this Pal"])
        return pool

    def open_planner(self, pal):
        how = self.PAL_SORTS[self.pal_sort]
        goal = how if how in palteam.GOAL_LABEL else "fighter"
        saved = self.plans.get(pal.get("id"))
        self.planner = {"pal": pal, "goal": self.PLAN_GOALS.index(goal),
                        "p": (list(saved if saved is not None
                                   else pal["passives"]) + [None] * 4)[:4],
                        "slot": 0, "choosing": False, "cand": 0, "q": "",
                        "typing": False}

    def _plan_level(self, pal):
        lvl = self.owner_level(self.pal_player) if self.pal_at_level else None
        return max(pal["level"], lvl) if lvl else None

    def _plan_options(self, st):
        game = self.src.gamedata or {}
        pal = st["pal"]
        pool = self._plan_pool(pal)
        goal = self.PLAN_GOALS[st["goal"]]
        low = st["q"].lower()
        match = (lambda x: low in palpals.passive(game, x)["name"].lower()
                 or low in palpals.passive_description(game, x).lower()) \
            if low else None
        return palteam.slot_options(game, pal, goal,
                                    self.snap.get("elements") or {}, pool,
                                    st["p"], st["slot"],
                                    self._plan_level(pal), match)

    def _remember_plan(self, st):
        pid = st["pal"].get("id")
        if not pid:
            return
        if sorted(x for x in st["p"] if x) == sorted(st["pal"]["passives"]):
            self.plans.pop(pid, None)
        else:
            self.plans[pid] = [x for x in st["p"] if x]
        save_plans(self.plans)

    def planner_key(self, ch):
        st = self.planner
        try:
            return self._planner_key(ch)
        finally:
            if self.planner is not None:
                self._remember_plan(self.planner)
            elif st is not None:
                self._remember_plan(st)

    def _planner_key(self, ch):
        st = self.planner
        n = len(self.PLAN_GOALS)
        if st["typing"]:
            if ch == 27:
                st["q"], st["typing"] = "", False
            elif ch in (10, 13, curses.KEY_ENTER):
                st["typing"] = False
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                st["q"] = st["q"][:-1]
            elif 32 <= ch < 127 and len(st["q"]) < 30:
                st["q"] += chr(ch)
            st["cand"] = 0
            return True
        if st["choosing"]:
            if ch == 27:
                st["choosing"], st["q"] = False, ""
            elif ch == ord("/"):
                st["typing"] = True
            elif ch == curses.KEY_DOWN:
                st["cand"] += 1
            elif ch == curses.KEY_UP:
                st["cand"] = max(0, st["cand"] - 1)
            elif ch in (10, 13, curses.KEY_ENTER):
                opts = self._plan_options(st)
                if opts:
                    st["p"][st["slot"]] = opts[min(st["cand"],
                                                   len(opts) - 1)][0]
                st["choosing"], st["q"] = False, ""
            return True
        if ch == 27:
            self.planner = None
        elif ch in (curses.KEY_RIGHT, curses.KEY_LEFT):
            st["goal"] = (st["goal"] + (1 if ch == curses.KEY_RIGHT
                                        else -1)) % n
        elif ch == curses.KEY_DOWN:
            st["slot"] = min(3, st["slot"] + 1)
        elif ch == curses.KEY_UP:
            st["slot"] = max(0, st["slot"] - 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            st["choosing"], st["cand"], st["q"] = True, 0, ""
        elif ch in (ord("x"), curses.KEY_DC):
            st["p"][st["slot"]] = None
        elif ch == ord("b"):
            game = self.src.gamedata or {}
            best = palteam.best_passives(
                game, st["pal"], self.PLAN_GOALS[st["goal"]],
                self.snap.get("elements") or {}, self._plan_pool(st["pal"]),
                self._plan_level(st["pal"]))
            st["p"] = (best + [None] * 4)[:4]
        elif ch == ord("r"):
            st["p"] = (list(st["pal"]["passives"]) + [None] * 4)[:4]
        elif ch == ord("n"):
            self.pal_at_level = not self.pal_at_level
        return True

    def overlay_planner(self, cv, th):
        """Try passive skills on one Pal for a goal, see the best set of four
        and where to get each."""
        st = self.planner
        game = self.src.gamedata or {}
        els = self.snap.get("elements") or {}
        names = self.snap.get("pal_names")
        pal = st["pal"]
        goal = self.PLAN_GOALS[st["goal"]]
        lvl = self._plan_level(pal)
        pool = self._plan_pool(pal)
        alpha, name = pal_display(pal["species"], names)
        iy, ix, ih, iw = self._full_box(
            cv, th, "passive planner: %s" % (pal["nickname"] or name),
            ("↑↓ pick   enter use   / search   esc cancel"
             if st["choosing"] else
             "←→ goal   ↑↓ slot   enter change   x clear"
             "   b best set   r its own   n level   esc back"), "proc_box")
        if ih <= 0:
            return
        real = palteam.score_with(game, pal, pal["passives"], goal, els, lvl)
        plan = palteam.score_with(game, pal, st["p"], goal, els, lvl)
        best_ids = palteam.best_passives(game, pal, goal, els, pool, lvl)
        best = palteam.score_with(game, pal, best_ids, goal, els, lvl)
        cv.text(iy, ix, "goal", th.c("label"))
        cv.text(iy, ix + 8, "◀ %s ▶" % palteam.GOAL_LABEL[goal],
                th.c("hotkey", bold=True))
        w = draw_elements(cv, th, iy, ix + 50, pal["species"], els)
        cv.text(iy, ix + 51 + w, "%s lv %d%s" % (
            name, pal["level"], "   (scored at lv %d)" % lvl if lvl else ""),
            th.c("value"), maxw=iw - 51 - w)
        hdr = th.c("label", bold=True)
        for cx, t in ((0, ""), (18, "SCORE"), (32, "HP"), (40, "ATK"),
                      (48, "DEF"), (56, "WORK")):
            cv.text(iy + 2, ix + cx, t, hdr)
        for k, (label, ev) in enumerate((("its own", real), ("planned", plan),
                                         ("best possible", best))):
            row = iy + 3 + k
            s = ev["st"]
            cv.text(row, ix, label, th.c("value", bold=True) if k == 1
                    else th.c("label"))
            rel = "" if k == 0 or not real["score"] else " %+.0f%%" % (
                100.0 * (ev["score"] / real["score"] - 1))
            cv.text(row, ix + 18, "%6.0f" % ev["score"], th.c("value"))
            cv.text(row, ix + 24, rel, th.c("good") if ev["score"] >=
                    real["score"] else th.c("crit"))
            for cx, v in ((32, s["hp"]), (40, s["atk"]), (48, s["def"]),
                          (56, s["craft"])):
                cv.text(row, ix + cx, "%6.0f" % v, th.c("label"))
        if best["score"] <= 0:
            cv.text(iy + 6, ix, "this Pal cannot do this job at all - no "
                    "passive changes that", th.c("crit"), maxw=iw)

        adds = palteam.contributions(game, pal, goal, els, st["p"], lvl)
        y = iy + 8
        cv.text(y, ix, "PASSIVES", hdr)
        for i, pid in enumerate(st["p"]):
            row = y + 1 + i
            sel = i == st["slot"]
            if sel:
                cv.text(row, ix, "▶", th.c("hotkey", bold=True))
            cv.text(row, ix + 2, "%d" % (i + 1), th.c("dim"))
            if not pid:
                cv.text(row, ix + 4, "(empty)", th.c("dim"))
                continue
            ps = palpals.passive(game, pid)
            rank = ps["rank"]
            attr = (th.c("iv_perfect", bold=True) if rank >= 4 else
                    th.c("iv_high") if rank == 3 else
                    th.c("crit") if rank < 0 else th.c("value"))
            cv.text(row, ix + 4, trunc(ps["name"], 22), attr)
            a = adds[i]
            cv.text(row, ix + 28, "%+5.0f%%" % (100 * a) if a is not None
                    else "", th.c("good") if a and a > 0 else
                    th.c("crit") if a and a < 0 else th.c("dim"))
            cv.text(row, ix + 36, ", ".join(palpals.effect_texts(game, pid)),
                    th.c("label"), maxw=iw - 36)
        y += 6
        if st["choosing"]:
            opts = self._plan_options(st)
            st["cand"] = max(0, min(st["cand"], len(opts) - 1))
            base = plan["score"] or 1.0
            cv.text(y, ix, "SLOT %d: PICK A PASSIVE   %s" % (
                st["slot"] + 1, ("search: %s%s" % (
                    st["q"], "_" if st["typing"] else ""))
                if st["q"] or st["typing"] else "/ to search"), hdr, maxw=iw)
            body = iy + ih - y - 1
            first = max(0, min(st["cand"] - body // 2, len(opts) - body))
            for n, (pid, sc) in enumerate(opts[first:first + body]):
                k = first + n
                row = y + 1 + n
                ps = palpals.passive(game, pid)
                if k == st["cand"]:
                    cv.text(row, ix, "▶", th.c("hotkey", bold=True))
                cv.text(row, ix + 2, trunc(ps["name"], 22), th.c(
                    "iv_perfect" if ps["rank"] >= 4 else "iv_high"
                    if ps["rank"] == 3 else "crit" if ps["rank"] < 0
                    else "value"))
                d = (sc - base) / base
                cv.text(row, ix + 26, "%+5.0f%%" % (100 * d),
                        th.c("good") if d > 0.001 else th.c("crit")
                        if d < -0.001 else th.c("dim"))
                cv.text(row, ix + 34, "/".join(pool.get(pid) or []),
                        th.c("dim"))
                cv.text(row, ix + 58, ", ".join(
                    palpals.effect_texts(game, pid)), th.c("label"),
                    maxw=iw - 58)
            if not opts:
                cv.text(y + 1, ix + 2, "no passives match", th.c("dim"))
            return
        cv.text(y, ix, "BEST SET", hdr)
        x = ix + 10
        for pid in best_ids:
            nm = palpals.passive(game, pid)["name"]
            cv.text(y, x, nm, th.c("iv_perfect" if palpals.passive(
                game, pid)["rank"] >= 4 else "value"), maxw=ix + iw - x)
            x += len(nm) + 3
        if not best_ids:
            cv.text(y, x, "nothing helps for this goal", th.c("dim"))
        y += 2
        cv.text(y, ix, "WHERE TO GET THEM", hdr)
        mine = [q for q in self.src.saves.pals
                if (q.get("owner") or "") == (pal.get("owner") or "")] \
            if self.src.saves else []
        for pid in [x for x in dict.fromkeys(list(st["p"]) + best_ids)
                    if x and x not in pal["passives"]]:
            y += 1
            if y >= iy + ih:
                break
            cv.text(y, ix, trunc(palpals.passive(game, pid)["name"], 22),
                    th.c("value"))
            cv.text(y, ix + 24, "  ·  ".join(palteam.passive_sources(
                game, pid, mine, pal["species"], names)) or "-",
                th.c("label"), maxw=iw - 24)

    def overlay_breeding(self, cv, th, pname):
        """Which of the player's Pals to use for breeding: alpha eggs, extra
        eggs, egg production, incubation - and parents that breed faster."""
        game = self.src.gamedata or {}
        els = self.snap.get("elements") or {}
        names = self.snap.get("pal_names")
        saves = self.src.saves
        mine = [p for p in (saves.pals if saves else [])
                if (p.get("owner") or "") == self.pal_player]
        key = ("breed", self.pal_player, saves.loaded_at if saves else 0)
        setup = self._rank_cache.get(key)
        if setup is None:
            if self._breeder is None and game:
                self._breeder = palpals.Breeder(game)
            setup = palteam.breeding_setup(game, mine, self._breeder)
            self._rank_cache[key] = setup
        iy, ix, ih, iw = self._full_box(
            cv, th, "breeding helper: %s" % pname, "esc back", "proc_box")
        if ih <= 0:
            return
        owners = {r.get("pid"): r["name"] for r in self.roster()}
        disp = lambda sp: pal_display(sp, names)[1]
        kinds = {palpals.base_species(p["species"]) for p in mine}
        y = iy
        cv.text(y, ix, "partner skills that help breeding do not stack: only "
                "the best of each counts", th.c("dim"), maxw=iw)
        y += 2

        def put(x, text, attr, row=None):
            if (row if row is not None else y) < iy + ih:
                cv.text(row if row is not None else y, ix + x, text, attr,
                        maxw=max(0, iw - x))
        for r in setup["roles"]:
            if y >= iy + ih:
                break
            put(0, r["label"].upper(), th.c("label", bold=True))
            put(len(r["label"]) + 2, "%s - %s" % (r["what"], r["where"]),
                th.c("dim"))
            y += 1
            for k, (v, vmax, p) in enumerate(r["have"][:3]):
                put(2, "use" if k == 0 else "spare",
                    th.c("good") if k == 0 else th.c("dim"))
                nm = p["nickname"] or disp(p["species"])
                put(9, trunc(nm, 18), th.c("el_" + element_of(
                    p["species"], els).lower(), bold=k == 0))
                put(28, "lv %2d" % p["level"], th.c("label"))
                put(35, "%g%%" % v, th.c("value", bold=True) if k == 0
                    else th.c("label"))
                put(42, "%g%% with 4 condenser stars" % vmax if v < vmax
                    else "maxed", th.c("dim"))
                y += 1
            if not r["have"]:
                put(2, "none of %s's Pals can do this" % pname, th.c("warn"))
                y += 1
            elif len(r["have"]) > 1 and r["have"][0][0] < r["have"][0][1]:
                put(2, "tip: condense the spares into the one you use",
                    th.c("el_ice"))
                y += 1
            for sp in r["missing"]:
                bits = []
                pairs = [(a, b) for s2, a, b in r["breed"] if s2 == sp]
                if pairs:
                    a, b = pairs[0]
                    bits.append("breed %s + %s" % (
                        a["nickname"] or disp(a["species"]),
                        b["nickname"] or disp(b["species"])))
                for a, b in r["recipes"].get(sp, []):
                    bits.append("special pair %s%s + %s%s" % (
                        disp(a), "" if a in kinds else " (not yours)",
                        disp(b), "" if b in kinds else " (not yours)"))
                others = {}
                for q in (saves.pals if saves else []):
                    if palpals.base_species(q["species"]) == sp and \
                            q.get("owner") and q["owner"] != self.pal_player:
                        who = owners.get(q["owner"], "another player")
                        others[who] = others.get(who, 0) + 1
                if others:
                    bits.append("on the server: " + ", ".join(
                        "%s has %d" % t for t in sorted(
                            others.items(), key=lambda t: -t[1])))
                label = ("better: " if r["have"] else "get: ") + disp(sp)
                put(2, label, th.c("warn"))
                put(max(24, len(label) + 4), "   ".join(bits) or "catch one",
                    th.c("label"))
                y += 1
            y += 1
        if y < iy + ih:
            put(0, "PARENTS THAT BREED FASTER", th.c("label", bold=True))
            put(27, "Philanthropist doubles breeding speed at the farm",
                th.c("dim"))
            y += 1
            for p in setup["parents"][:max(0, iy + ih - y)]:
                put(2, trunc(p["nickname"] or disp(p["species"]), 18),
                    th.c("el_" + element_of(p["species"], els).lower()))
                put(21, "lv %2d" % p["level"], th.c("label"))
                put(28, ", ".join(palpals.passive(game, x)["name"]
                                  for x in p["passives"]), th.c("label"))
                y += 1
            if not setup["parents"]:
                put(2, "none yet - Philanthropist can be implanted at the "
                    "surgery table", th.c("dim"))

    def overlay_pal_player(self, cv, th):
        """One player's Pals: a list on the left, everything known about the
        selected one on the right (or below, on a narrow screen)."""
        pid = self.pal_player
        who = next((r for r in self.roster() if r.get("pid") == pid), None)
        pname = (who or {}).get("name") or pid[:8]
        if self.planner:
            return self.overlay_planner(cv, th)
        if self.breed_view:
            return self.overlay_breeding(cv, th, pname)
        if self.pal_team:
            return self.overlay_team(cv, th, pname)
        rows = self.pal_list(pid)
        self._pal_view = rows
        self.pal_idx = max(0, min(self.pal_idx, max(0, len(rows) - 1)))
        iy, ix, ih, iw = self._full_box(
            cv, th, "%s's pals" % pname,
            "%s%d shown   o sort: %s   f show: %s   n %s   e plan passives"
            "   t team builder   g breeding"
            "   esc back" % (
                ("search: %s%s   " % (self.pal_search, "_" if
                                      self.pal_typing == "list" else ""))
                if self.pal_search or self.pal_typing == "list"
                else "/ search   ",
                len(rows), self.PAL_SORTS[self.pal_sort],
                self.PAL_FILTERS[self.pal_filter],
                "at your level" if self.pal_at_level else "current level"),
            "proc_box")
        if ih <= 0:
            return
        if not rows:
            cv.text(iy, ix, "no pals here" if self.src.saves else
                    "no save data yet", th.c("dim"), maxw=iw)
            return
        side = iw >= 110
        lw = 58 if side else iw
        lh = ih if side else max(4, ih // 3)
        names = self.snap.get("pal_names")
        els = self.snap.get("elements") or {}

        how = self.PAL_SORTS[self.pal_sort]
        role = how if how in palteam.SORT_GOALS else None
        role_ev, top = {}, 1.0
        if role:
            ranking = self.pal_ranking(pid, role)
            role_ev = {id(q): ev for q, ev in ranking}
            top = ranking[0][1]["score"] if ranking else 1.0
        hdr = th.c("label", bold=True)
        for cx, t in ((4, "PAL"), (20, "EL"), (25, "LV"), (33, "HP/ATK/DEF"),
                      (45, "RANK"), (51, "SCORE" if role else "WHERE")):
            cv.text(iy, ix + cx, t, hdr)
        body = lh - 1
        first = max(0, min(self.pal_idx - body // 2, len(rows) - body))
        for n, (p, loc) in enumerate(rows[first:first + body]):
            i = first + n
            row = iy + 1 + n
            sel = i == self.pal_idx
            alpha, name = pal_display(p["species"], names)
            if p["nickname"]:
                name = p["nickname"]
            if sel:
                cv.text(row, ix, "▶", th.c("hotkey", bold=True))
            if p["lucky"]:
                cv.text(row, ix + 2, "✦", th.c("iv_perfect", bold=True))
            elif alpha:
                cv.text(row, ix + 2, "α", th.c("warn", bold=True))
            cv.text(row, ix + 4, trunc(name, 15),
                    th.c("el_" + element_of(p["species"], els).lower(),
                         bold=sel))
            draw_elements(cv, th, row, ix + 20, p["species"], els)
            cv.text(row, ix + 25, "%3d" % p["level"], th.c("label"))
            g = {"Male": "♂", "Female": "♀"}.get(p["gender"], " ")
            cv.text(row, ix + 29, g, th.c("iv_high") if g == "♂"
                    else th.c("el_fire"))
            vx = ix + 33
            for j, iv in enumerate((p["hp_iv"], p["atk_iv"], p["def_iv"])):
                t = "%d" % iv
                cv.text(row, vx, t, iv_attr(th, iv))
                vx += len(t)
                if j < 2:
                    cv.text(row, vx, "/", th.c("dim"))
                    vx += 1
            stars = max(0, p["rank"] - 1)
            if stars:
                cv.text(row, ix + 45, "★" * stars, th.c("warn"))
            if role:
                ev = role_ev.get(id(p))
                cv.text(row, ix + 51, "%5.0f" % (100.0 * ev["score"] / top)
                        if ev else "    -", th.c("value", bold=sel))
            else:
                cv.text(row, ix + 51, loc[0], th.c("dim"), maxw=lw - 51)

        p, loc = rows[self.pal_idx]
        if side:
            self._pal_detail(cv, th, p, loc, iy, ix + lw + 2, ih,
                             iw - lw - 2)
        else:
            self._pal_detail(cv, th, p, loc, iy + lh + 1, ix, ih - lh - 1, iw)

    def _pal_detail(self, cv, th, p, loc, y, x, h, w):
        game = self.src.gamedata or {}
        names = self.snap.get("pal_names") or {}
        els = self.snap.get("elements") or {}
        info = palpals.species_info(game, p["species"])
        alpha, name = pal_display(p["species"], names)
        el = element_of(p["species"], els)
        lines = []                       # (x offset, text, attr) runs per line

        def line(*runs):
            lines.append(runs)
        title = [(0, name, th.c("el_" + el.lower(), bold=True))]
        off = len(name) + 1
        if p["nickname"]:
            title.append((off, "“%s”" % p["nickname"],
                          th.c("value")))
            off += len(p["nickname"]) + 3
        if p["lucky"]:
            title.append((off, "LUCKY", th.c("iv_perfect", bold=True)))
            off += 6
        if alpha:
            title.append((off, "ALPHA", th.c("warn", bold=True)))
        line(*title)
        pair = els.get(palpals.species_key(game, p["species"]) or "") or \
            (el, "None")
        elem = " / ".join(palpals.ELEMENT_NAMES.get(e, e) for e in pair
                          if e and e != "None")
        dex = info.get("zukan")
        line((0, "%s   lv %d   %s   %s" % (
            elem, p["level"], p["gender"] or "-",
            "Paldeck #%03d%s" % (dex, info.get("suffix") or "")
            if dex is not None and dex >= 0 else ""), th.c("label")))
        line((0, "HP %s   %s%s" % (
            "%.0f" % p["hp"] if p["hp"] is not None else "?",
            "trust %d   " % p["friendship"] if p["friendship"] else "",
            loc[1]), th.c("label")))
        line()
        bar = max(8, min(24, w - 16))
        for label, iv in (("HP", p["hp_iv"]), ("ATK", p["atk_iv"]),
                          ("DEF", p["def_iv"])):
            line((0, label, th.c("label")),
                 (5, ("meter", iv / 100.0, bar), None),
                 (6 + bar, "%3d" % iv, iv_attr(th, iv)))
        stars = max(0, p["rank"] - 1)
        soul = p["soul"]
        line((0, "condenser", th.c("label")),
             (10, "★" * stars + "☆" * (4 - stars), th.c("warn")),
             (16, "souls  HP+%d ATK+%d DEF+%d WORK+%d" % (
                 soul["HP"], soul["Attack"], soul["Defence"],
                 soul["CraftSpeed"]), th.c("label")))
        bon = palpals.bonuses(game, p)
        text = "   ".join("%s %+.0f%%" % (k, v) for k, v in bon.items() if v)
        line((0, "passives", th.c("label")),
             (10, text or "no stat bonus",
              th.c("good") if text else th.c("dim")))
        how = self.PAL_SORTS[self.pal_sort]
        if how in palteam.SORT_GOALS and self.pal_player:
            ranking = self.pal_ranking(self.pal_player, how)
            place = next((i for i, (q, _) in enumerate(ranking) if q is p),
                         None)
            if place is not None:
                ev = ranking[place][1]
                line()
                line((0, "AS %s" % palteam.GOAL_LABEL[how].upper(),
                      th.c("label", bold=True)),
                     (22, "#%d of %d   %s" % (place + 1, len(ranking),
                                             "; ".join(ev["why"])),
                      th.c("value")))
                if ev["good"]:
                    line((0, "helps", th.c("label")),
                         (10, ", ".join(ev["good"]), th.c("good")))
                if ev["bad"]:
                    line((0, "hurts", th.c("label")),
                         (10, ", ".join(ev["bad"]), th.c("crit")))
        line()
        line((0, "PASSIVES", th.c("label", bold=True)))
        for pid in p["passives"] or []:
            ps = palpals.passive(game, pid)
            rank = ps["rank"]
            attr = (th.c("iv_perfect", bold=True) if rank >= 4 else
                    th.c("iv_high") if rank == 3 else
                    th.c("crit") if rank < 0 else th.c("value"))
            eff = ", ".join(palpals.effect_texts(game, pid))
            line((0, trunc(ps["name"], 21), attr), (22, eff, th.c("label")))
        if not p["passives"]:
            line((0, "none", th.c("dim")))
        line((0, "SKILLS", th.c("label", bold=True)))
        skills = game.get("skills") or {}
        for sid in p["skills"]:
            sk = skills.get(sid) or {"name": sid, "element": "Normal",
                                     "power": 0, "cooldown": 0}
            line((0, trunc(sk["name"], 21),
                  th.c("el_" + sk["element"].lower())),
                 (22, "power %d   cooldown %gs   %s" % (
                     sk["power"], sk["cooldown"],
                     palpals.ELEMENT_NAMES.get(sk["element"], sk["element"])),
                  th.c("label")))
        extra = [skills.get(s, {}).get("name", s) for s in p["learned"]
                 if s not in p["skills"]]
        if extra:
            line((0, "also knows " + ", ".join(extra), th.c("dim")))
        part = palpals.partner(game, p["species"], p["rank"], names)
        work = ", ".join("%s %d" % (palpals.WORK_NAMES.get(k, k), v)
                         for k, v in sorted((info.get("work") or {}).items(),
                                            key=lambda kv: -kv[1]))
        line((0, "partner", th.c("label")),
             (10, part["name"] or "-", th.c("value", bold=True)),
             (11 + len(part["name"] or "-"),
              " ".join("[%s]" % t for t in part["tags"] if t != "base"),
              th.c("dim")))
        # the whole 0-4 star scale, with this Pal's own number in [ ]
        scaled = palpals.partner_scaled(game, p["species"], names,
                                        mark=p["rank"])
        marked = "\x01" in scaled
        scaled = scaled.replace("\x01", "[").replace("\x02", "]")
        for chunk in wrap_text(scaled or part["text"], max(20, w - 13)):
            segs, at = [], 10                 # the scales in green
            for i, bit in enumerate(_SCALE.split(chunk)):
                if bit:
                    segs.append((at, bit, th.c("good") if i % 2
                                 else th.c("label")))
                    at += len(bit)
            line(*segs)
        if marked:
            line((10, "numbers for 0/1/2/3/4 condenser stars; [this Pal's]",
                  th.c("dim")))
        line((0, "work", th.c("label")), (10, work or "-", th.c("value")))
        trouble = []
        if p["sick"]:
            cond = self.snap.get("conditions") or {}
            trouble.append("sick: %s" % cond.get(p["sick"], p["sick"]))
        if p["hunger"]:
            trouble.append("starving")
        if p["sanity"] is not None and p["sanity"] < 50:
            trouble.append("sanity %d" % p["sanity"])
        if trouble:
            line((0, "status", th.c("label")),
                 (10, ", ".join(trouble), th.c("crit")))
        note = palteam.summon_note(p["species"])
        if note:
            line((0, "note", th.c("label")), (10, note, th.c("warn")))
        if p["caught"]:
            line((0, "caught", th.c("label")),
                 (10, fmt_stamp(_dtu(p["caught"])), th.c("value")))

        # breeding: what this Pal gives with the owner's other Pals
        if self._breeder is None and game:
            self._breeder = palpals.Breeder(game)
        if self._breeder is not None:
            others = [q for q in self.src.saves.pals
                      if (q.get("owner") or "") == (p.get("owner") or "")]
            opts = self._breeder.options(p, others)
            line()
            line((0, "BREEDING  with the owner's other pals",
                  th.c("label", bold=True)))
            for child, mate in opts:
                _, cname = pal_display(child, names)
                _, mname = pal_display(mate["species"], names)
                line((0, trunc(cname, 21),
                      th.c("el_" + element_of(child, els).lower(),
                           bold=True)),
                     (22, "with %s lv %d  %d/%d/%d" % (
                         mate["nickname"] or mname, mate["level"],
                         mate["hp_iv"], mate["atk_iv"], mate["def_iv"]),
                      th.c("label")))
            if not opts:
                line((0, "no partner of the opposite gender", th.c("dim")))

        for n, runs in enumerate(lines[:h]):
            for off, text, attr in runs:
                if isinstance(text, tuple):
                    _, frac, bw = text
                    draw_meter(cv, y + n, x + off, bw, frac, th, "free")
                else:
                    cv.text(y + n, x + off, text, attr, maxw=max(0, w - off))

    def overlay_bases(self, cv, th):
        """How each base camp is doing: what is built, who works it, and any
        worker problems - sickness, hunger, low sanity, slacking off."""
        snap = self.snap
        bases = snap.get("base_camps") or []
        y = 5 if cv.h > 24 else 0
        for row in range(y, cv.h - 1):
            cv.text(row, 0, " " * cv.w, th.c("value"))
        iy, ix, ih, iw = draw_box(
            cv, y, 0, cv.h - 1 - y, cv.w, "bases", th, 0,
            right="%d base camps   esc to close" % len(bases),
            color="mem_box")
        if ih <= 0:
            return
        if not bases:
            cv.text(iy, ix, snap.get("save_error") or "no save data yet",
                    th.c("dim"), maxw=iw)
            return

        hdr = th.c("label", bold=True)
        for cx, t in ((0, "BASE"), (9, "GUILD"), (20, "WHERE"), (41, "BUILT"),
                      (49, "WORKERS"), (59, "STATUS")):
            cv.text(iy, ix + cx, t, hdr)

        line = 1
        for i, b in enumerate(bases):
            if line >= ih:
                break
            row = iy + line
            label = b["name"] if b["name"] and not b["name"].startswith(
                "\u65b0\u898f\u751f\u6210") else "Base %d" % (i + 1)
            cv.text(row, ix, trunc(label, 8), th.c("value", bold=True))
            cv.text(row, ix + 9, b["guild"][:8], th.c("dim"))
            cv.text(row, ix + 20, palcrash.fmt_map(
                palcrash.map_coords(b["x"], b["y"])), th.c("label"), maxw=20)
            cv.text(row, ix + 41, "%5d" % b["structures"], th.c("value"))
            cap = b["worker_cap"]
            cv.text(row, ix + 49, "%2d/%-2d" % (b["workers"], cap),
                    th.c("good") if cap and b["workers"] >= cap
                    else th.c("warn"))
            bits, worst = [], 0
            if b["dying"]:
                bits.append(("%d dying" % len(b["dying"]), 2))
                worst = 2
            if b["sick"]:
                bits.append(("%d sick" % len(b["sick"]), 2))
                worst = 2
            if b["starving"]:
                bits.append(("%d starving" % len(b["starving"]), 2))
                worst = 2
            if b["insane"]:
                bits.append(("%d unhappy" % len(b["insane"]), 1))
                worst = max(worst, 1)
            if b["slacking"]:
                bits.append(("%d slacking" % len(b["slacking"]), 1))
                worst = max(worst, 1)
            if not bits:
                cv.text(row, ix + 59, "ok", th.c("good"))
            else:
                px = ix + 59
                for txt, sev in bits:
                    attr = th.c("crit") if sev == 2 else th.c("warn")
                    cv.text(row, px, txt, attr)
                    px += len(txt)
                    cv.text(row, px, "  ", th.c("dim"))
                    px += 2
            line += 1

        # detail: who is actually in trouble
        cond = snap.get("conditions") or {}
        trouble = []
        for i, b in enumerate(bases):
            label = "Base %d" % (i + 1)
            for sp, what in b["dying"]:
                trouble.append((2, label, sp, what.lower()))
            for sp, what in b["sick"]:
                trouble.append((2, label, sp, "sick: %s" % cond.get(what, what)))
            for sp, what in b["starving"]:
                trouble.append((2, label, sp, "starving"))
            for sp, val in b["insane"]:
                trouble.append((1, label, sp, "sanity %d" % val))
            for sp, what in b["slacking"]:
                trouble.append((0, label, sp, "slacking off"))
        trouble.sort(key=lambda t: -t[0])
        if trouble and line + 1 < ih:
            line += 1
            cv.text(iy + line, ix, "WORKERS NEEDING ATTENTION", hdr)
            line += 1
            for sev, base, sp, what in trouble:
                if line >= ih:
                    break
                attr = th.c("crit") if sev == 2 else (
                    th.c("warn") if sev == 1 else th.c("dim"))
                cv.text(iy + line, ix, base, th.c("label"))
                _, name = pal_display(sp, snap.get("pal_names"))
                cv.text(iy + line, ix + 9, trunc(name, 18),
                        th.c("el_" + element_of(
                            sp, snap.get("elements") or {}).lower()))
                cv.text(iy + line, ix + 29, what, attr, maxw=iw - 29)
                line += 1
        elif not trouble and line < ih:
            cv.text(iy + line + 1, ix, "no worker problems",
                    th.c("good"), maxw=iw)

    def _full_box(self, cv, th, title, right, color):
        """A box over everything below the server panel, like the bases view."""
        y = 5 if cv.h > 24 else 0
        for row in range(y, cv.h - 1):
            cv.text(row, 0, " " * cv.w, th.c("value"))
        return draw_box(cv, y, 0, cv.h - 1 - y, cv.w, title, th, 0,
                        right=right, color=color)

    def overlay_crashes(self, cv, th):
        """Crash log: every time the server went down, and what paltop could
        work out about why. Up/down picks an incident."""
        incs = self.snap.get("incidents") or []
        bad = sum(1 for i in incs if not i["planned"])
        iy, ix, ih, iw = self._full_box(
            cv, th, "crash log",
            "%d crashes/hangs, %d planned   \u2191\u2193 select   esc" % (
                bad, len(incs) - bad), "cpu_box")
        if ih <= 0:
            return
        if not incs:
            cv.text(iy, ix, "no crashes, hangs or restarts recorded yet - the "
                    "background collector watches the server every 30 s",
                    th.c("good"), maxw=iw)
            return
        self.scroll = max(0, min(self.scroll, len(incs) - 1))
        hdr = th.c("label", bold=True)
        for cx, t in ((2, "WHEN"), (17, "WHAT"), (36, "DOWN"), (45, "WHO"),
                      (64, "LIKELY CAUSE")):
            cv.text(iy, ix + cx, t, hdr)
        list_h = max(3, min(len(incs), ih // 3))
        first = max(0, min(self.scroll - list_h // 2, len(incs) - list_h))
        for n, inc in enumerate(incs[first:first + list_h]):
            i = first + n
            row = iy + 1 + n
            sel = i == self.scroll
            sev = th.c("dim") if inc["planned"] else th.c("crit")
            cv.text(row, ix, "\u25b6" if sel else " ", th.c("value", bold=True))
            cv.text(row, ix + 2, fmt_stamp(_dtu(inc["start"])),
                    th.c("value", bold=sel))
            cv.text(row, ix + 17, palcrash.KIND_LABEL.get(inc["kind"],
                                                          inc["kind"]), sev)
            down = (inc["end"] - inc["down_from"]) if inc["end"] else None
            cv.text(row, ix + 36, fmt_dur(down, compact=True) if down
                    else ("?" if inc.get("unobserved") else "now"),
                    th.c("label"))
            cv.text(row, ix + 45, trunc(", ".join(
                p["name"] for p in inc["people"]) or "-", 18), th.c("value"))
            cv.text(row, ix + 64, inc["verdict"], th.c("label"),
                    maxw=iw - 64)

        # detail of the selected incident
        inc = incs[self.scroll]
        line = list_h + 2
        rows = []

        def add(label, text, attr=None):
            rows.append((label, text, attr or th.c("value")))
        rep = inc["report"]
        add("what", "%s%s" % (palcrash.KIND_LABEL.get(inc["kind"]),
                              "  (planned)" if inc["planned"] else ""),
            th.c("dim") if inc["planned"] else th.c("crit", bold=True))
        if inc.get("last_alive"):
            add("last sign of life", fmt_stamp(_dtu(inc["last_alive"])))
        if inc["kind"] == "host_reboot":
            pass                        # nothing was running to notice
        elif not inc.get("unobserved"):
            add("stopped answering", "%s   (%s)" % (
                fmt_stamp(_dtu(inc["start"])), ", ".join(inc["states"])))
        else:
            add("stopped answering", "not recorded - before the background "
                "collector was running", th.c("dim"))
        if rep:
            add("game crash report", "%s at %s, server up %s, %s client%s" % (
                rep["type"], fmt_stamp(_dtu(rep["ts"])),
                fmt_dur(rep["uptime"], compact=True), rep["clients"],
                "" if rep["clients"] == 1 else "s"))
            add("freeze point", "%s  (%s)" % (
                " \u2190 ".join(rep["frames"][:4]), rep["release"] or "?"),
                th.c("label"))
        if inc.get("boot"):
            add("host booted", fmt_stamp(_dtu(inc["boot"])))
        if inc.get("healed"):
            add("autoheal", "restarted it at %s" % fmt_stamp(
                _dtu(inc["healed"])), th.c("good"))
        if inc.get("restarted"):
            add("container started", fmt_stamp(_dtu(inc["restarted"])))
        if inc["end"]:
            add("back up", "%s   down %s" % (
                fmt_stamp(_dtu(inc["end"])),
                fmt_dur(inc["end"] - inc["down_from"], compact=True)),
                th.c("good"))
        if inc.get("fps_min") is not None:
            add("server before", "min %.0f fps, %s memory, %s online" % (
                inc["fps_min"], fmt_bytes(inc.get("mem_before") or 0, 1),
                inc.get("players_before", "?")), th.c("label"))
        for who in inc["people"]:
            where = "position not recorded"
            if who.get("map"):
                where = "map %s" % palcrash.fmt_map(who["map"])
            add(who["name"], "%s   %s" % (where, "; ".join(who["notes"])),
                th.c("value", bold=True))
        if not inc["people"]:
            add("players", "nobody was online", th.c("label"))
        for pat in inc.get("patterns") or []:
            add("pattern", pat, th.c("warn"))
        add("likely cause", inc["verdict"],
            th.c("dim") if inc["planned"] else th.c("warn", bold=True))

        lw = 19
        for label, text, attr in rows:
            if line >= ih:
                break
            cv.text(iy + line, ix, label, th.c("label"))
            cv.text(iy + line, ix + lw, text, attr, maxw=iw - lw)
            line += 1

    def overlay_database(self, cv, th):
        """The history database: size against its cap, what each table holds
        and how far back it goes, retention and the collector."""
        st = self.snap.get("db") or {}
        iy, ix, ih, iw = self._full_box(
            cv, th, "database", "%s   esc to close" % (
                st.get("path") or "state/paltop.db"), "mem_box")
        if ih <= 0:
            return
        if not st:
            cv.text(iy, ix, self.src.db.error or "database not available",
                    th.c("crit"), maxw=iw)
            return
        now = self.snap.get("now") or datetime.now(timezone.utc)
        cap = st.get("cap") or 1
        used = st.get("data_bytes") or 0
        frac = used / float(cap)
        bar = max(10, min(40, iw - 40))
        cv.text(iy, ix, "size", th.c("label"))
        draw_meter(cv, iy, ix + 19, bar, frac, th, "used")
        cv.text(iy, ix + 20 + bar, "%s of %s  (%.1f%%)" % (
            fmt_bytes(used, 1), fmt_bytes(cap, 0), frac * 100),
            th.grad("used", frac), maxw=iw - 20 - bar)
        rows = [
            ("on disk", "%s file + %s write-ahead log" % (
                fmt_bytes(st.get("file_bytes", 0), 1),
                fmt_bytes(st.get("wal_bytes", 0), 1))),
            ("kept", "metrics and positions %d days, events %d months; "
             "over the cap, the oldest positions go first" % (
                 st.get("metrics_days", 0), st.get("events_months", 0))),
            ("last clean-up", fmt_ago(st.get("pruned_at"), now)
             if st.get("pruned_at") else "not yet in this process"),
        ]
        ok, age, info = palcollect.status(self.src.db)
        if info:
            rows.append(("collector", "%s   last write %s ago, every %.0fs, "
                         "%s rounds, pid %s" % (
                             "running" if ok else "STOPPED",
                             fmt_dur(age, compact=True),
                             float(info.get("interval") or 0),
                             info.get("cycles", "?"), info.get("pid", "?"))))
        else:
            rows.append(("collector", "never ran"))
        for n, (k, v) in enumerate(rows):
            attr = th.c("crit") if "STOPPED" in v else th.c("value")
            cv.text(iy + 1 + n, ix, k, th.c("label"))
            cv.text(iy + 1 + n, ix + 19, v, attr, maxw=iw - 19)

        line = len(rows) + 2
        hdr = th.c("label", bold=True)
        if line < ih:
            for cx, t in ((0, "TABLE"), (13, "ROWS"), (22, "OLDEST"),
                          (38, "NEWEST"), (54, "HOLDS")):
                cv.text(iy + line, ix + cx, t, hdr)
        what = {"samples": "server metrics, 1/min",
                "positions": "player positions, 1/min",
                "events": "joins, leaves, admin commands",
                "status_log": "server up/down, restarts, boots",
                "levels": "when each level was reached",
                "crashes": "the game's crash reports",
                "players": "every player seen",
                "backups": "backup archives seen",
                "meta": "settings, collector heartbeat"}
        for t in self.snap.get("db_tables") or []:
            line += 1
            if line >= ih:
                break
            row = iy + line
            cv.text(row, ix, t["table"], th.c("value", bold=True))
            cv.text(row, ix + 13, "%7d" % t["rows"], th.c("value"))
            cv.text(row, ix + 22, fmt_stamp(t["oldest"]) if t["oldest"]
                    else "-", th.c("label"))
            cv.text(row, ix + 38, fmt_stamp(t["newest"]) if t["newest"]
                    else "-", th.c("label"))
            cv.text(row, ix + 54, what.get(t["table"], ""), th.c("dim"),
                    maxw=iw - 54)

    def overlay_admin(self, cv, th):
        """Hotkey menu for server actions. The only screen that changes
        anything; everything not marked SAFE needs a confirmation."""
        st = self.admin or {}
        if st.get("stage") in ("backup", "restore_confirm", "restoring"):
            return self.overlay_restore(cv, th, st)
        if st.get("stage") == "players":
            return self.overlay_moderation(cv, th, st)
        items = self.admin_api.menu() if self.admin_api else []
        rows = [r for r in self.roster() if r["online"]]
        body = []
        sev_name = {paladmin.SAFE: "safe",
                    paladmin.DISRUPTIVE: "disruptive",
                    paladmin.SEVERE: "SEVERE"}
        stage = st.get("stage", "menu")
        if stage == "menu":
            for key, label, sev, needs in items:
                body.append((key, label, sev))
        elif stage == "player":
            for i, r in enumerate(rows[:9]):
                body.append((str(i + 1), "%s  (lv %d)" % (r["name"], r["level"]),
                             paladmin.DISRUPTIVE))
            if not rows:
                body.append(("", "nobody is online", paladmin.SAFE))

        h = min(cv.h - 6, max(9, len(body) + 8))
        w = min(cv.w - 6, 72)
        y, x = (cv.h - h) // 2, (cv.w - w) // 2
        for row in range(y, y + h):
            cv.text(row, x, " " * w, th.c("value"))
        iy, ix, ih, iw = draw_box(cv, y, x, h, w, "admin", th, 0,
                                  right="esc to close", color="proc_box")
        if ih <= 0:
            return
        line = 0

        def put(text, attr=None):
            nonlocal line
            if line < ih:
                cv.text(iy + line, ix, text, attr or th.c("label"), maxw=iw)
                line += 1

        if not self.admin_api:
            put("admin actions are disabled (--no-admin)", th.c("dim"))
            return

        if stage == "menu":
            put("pick an action:", th.c("dim"))
            for key, label, sev in body:
                cv.text(iy + line, ix + 1, key, th.c("hotkey", bold=True))
                cv.text(iy + line, ix + 3, label, th.c("value"))
                tag = sev_name[sev]
                cv.text(iy + line, ix + iw - len(tag), tag,
                        th.c("crit") if sev == paladmin.SEVERE else (
                            th.c("warn") if sev else th.c("good")))
                line += 1
        elif stage == "text":
            put("message to announce:", th.c("dim"))
            put("")
            cv.text(iy + line, ix + 1, "> " + st.get("buf", "") + "_",
                    th.c("value", bold=True), maxw=iw - 2)
            line += 1
            put("")
            put("enter to send, esc to cancel", th.c("dim"))
        elif stage == "player":
            put("which player?", th.c("dim"))
            for key, label, sev in body:
                if key:
                    cv.text(iy + line, ix + 1, key, th.c("hotkey", bold=True))
                cv.text(iy + line, ix + 3, label, th.c("value"))
                line += 1
        elif stage == "confirm":
            put(st.get("prompt", "are you sure?"), th.c("crit", bold=True))
            put("")
            put("this affects players currently on the server", th.c("warn"))
            put("")
            put("press y to confirm, anything else to cancel",
                th.c("value", bold=True))

        res = st.get("result")
        if res and line + 2 <= ih:
            line = ih - 2
            ok, note = res
            cv.text(iy + line, ix, ("ok: " if ok else "failed: ") + note[:iw - 8],
                    th.c("good") if ok else th.c("crit"), maxw=iw)

    def overlay_restore(self, cv, th, st):
        """Pick a backup, see what is in it, confirm, and follow the restore."""
        data_dir = self.src.data_dir
        backups = paladmin.list_backups(data_dir) if data_dir else []
        stage = st["stage"]
        iy, ix, ih, iw = self._full_box(
            cv, th, "restore a backup",
            {"backup": "\u2191\u2193 pick   enter restore   esc back",
             "restore_confirm": "type restore and press enter   esc cancel",
             "restoring": "esc to close" if stage == "restoring" and
             st["job"].done else "working - do not close paltop"}[stage],
            "cpu_box")
        if ih <= 0:
            return
        if stage == "restoring":
            return self._draw_restoring(cv, th, st, iy, ix, ih, iw)
        if not backups:
            cv.text(iy, ix, "no backups found in %s/backups" % data_dir,
                    th.c("dim"), maxw=iw)
            return
        st["idx"] = max(0, min(st.get("idx", 0), len(backups) - 1))
        lw = min(56, iw // 2)
        hdr = th.c("label", bold=True)
        for cx, t in ((2, "WHEN"), (18, "SIZE"), (28, "BACKUP")):
            cv.text(iy, ix + cx, t, hdr)
        body = ih - 1
        first = max(0, min(st["idx"] - body // 2, len(backups) - body))
        for n, b in enumerate(backups[first:first + body]):
            i = first + n
            row = iy + 1 + n
            sel = i == st["idx"]
            if sel:
                cv.text(row, ix, "\u25b6", th.c("hotkey", bold=True))
            cv.text(row, ix + 2, fmt_stamp(_dtu(b["mtime"])),
                    th.c("value", bold=sel))
            cv.text(row, ix + 18, fmt_bytes(b["size"], 1), th.c("label"))
            cv.text(row, ix + 28, ("before restore " + b["name"][22:-7])
                    if b["snapshot"] else b["name"][14:-7],
                    th.c("warn") if b["snapshot"] else th.c("dim"),
                    maxw=lw - 28)

        # what is in the selected one
        b = backups[st["idx"]]
        info = paladmin.inspect_backup(b["path"])
        x, w = ix + lw + 2, iw - lw - 2
        y = iy
        now = self.snap.get("now") or datetime.now(timezone.utc)
        cur_day = (self.snap.get("metrics") or {}).get("days")

        def put(label, text, attr=None):
            nonlocal y
            if y < iy + ih:
                cv.text(y, x, label, th.c("label"))
                cv.text(y, x + 12, text, attr or th.c("value"),
                        maxw=max(0, w - 12))
                y += 1
        cv.text(y, x, b["name"], th.c("value", bold=True), maxw=w)
        y += 2
        put("made", "%s  (%s)" % (fmt_stamp(_dtu(b["mtime"])),
                                  fmt_ago(_dtu(b["mtime"]), now)))
        put("size", fmt_bytes(b["size"], 1))
        if not info["ok"]:
            put("check", "NOT USABLE: %s" % info["error"], th.c("crit"))
            return
        put("check", "complete archive, %d files, world %s" % (
            info["files"], info["world"][:8]), th.c("good"))
        put("game day", "%s%s" % (info["day"], "   (now %s, %+d days)" % (
            cur_day, info["day"] - int(cur_day))
            if cur_day and info["day"] is not None else ""))
        put("pals", "%d" % info["pals"])
        y += 1
        put("players", "")
        levels_now = {p["name"]: p["level"]
                      for p in (self.snap.get("save_players") or {}).values()}
        for name, lv in info["players"]:
            now_lv = levels_now.get(name)
            diff = "" if now_lv is None or now_lv == lv else \
                "   (now %d, %+d)" % (now_lv, lv - now_lv)
            put("", "%-16s lv %d%s" % (name, lv, diff),
                th.c("warn") if diff else th.c("value"))
        y += 1
        if stage == "backup":
            put("", "Enter to restore this backup.", th.c("dim"))
            return

        # confirm
        online = [r["name"] for r in self.roster() if r["online"]]
        y += 0
        put("RESTORE", "the world goes back to this backup;",
            th.c("crit", bold=True))
        put("", "everything since then is lost.", th.c("crit", bold=True))
        put("", "The current world is saved first as a new", th.c("warn"))
        put("", "backup, so this can be undone.", th.c("warn"))
        put("", "The server stops for a minute or two.", th.c("warn"))
        if online:
            put("online", "%s will be disconnected - announce it first "
                "(a in the admin menu)" % ", ".join(online), th.c("crit"))
        else:
            put("online", "nobody", th.c("good"))
        y += 1
        put("type", "> " + st.get("buf", "") + "_",
            th.c("value", bold=True))

    def _draw_restoring(self, cv, th, st, iy, ix, ih, iw):
        job = st["job"]
        cv.text(iy, ix, "restoring %s" % os.path.basename(job.backup),
                th.c("value", bold=True), maxw=iw)
        notes = {}
        for step, ok, note in job.log:
            notes.setdefault(step, []).append((ok, note))
        for i, name in enumerate(job.STEPS):
            row = iy + 2 + i
            if row >= iy + ih:
                break
            if i in notes:
                ok = all(o for o, _ in notes[i])
                mark, attr = ("\u2713", th.c("good")) if ok else \
                    ("\u2717", th.c("crit"))
            elif i == job.step and not job.done:
                mark, attr = "\u2026", th.c("warn")
            else:
                mark, attr = " ", th.c("dim")
            cv.text(row, ix, mark, attr)
            cv.text(row, ix + 2, name, attr if i in notes else th.c("label"))
            cv.text(row, ix + 30, "; ".join(n for _, n in notes.get(i, [])),
                    th.c("label"), maxw=iw - 30)
        row = iy + 3 + len(job.STEPS)
        if not job.done:
            cv.text(row, ix, "working...", th.c("warn"))
            return
        if job.ok:
            cv.text(row, ix, "Done. The server is starting with the restored "
                    "world - give it a minute or two.", th.c("good", bold=True),
                    maxw=iw)
        else:
            cv.text(row, ix, "The restore did not complete. The current world "
                    "was left as it was (or put back).", th.c("crit",
                                                              bold=True),
                    maxw=iw)
        if job.snapshot and os.path.exists(job.snapshot):
            cv.text(row + 1, ix, "undo: restore %s" % os.path.basename(
                job.snapshot), th.c("warn"), maxw=iw)
        cv.text(row + 3, ix, "esc to close", th.c("dim"))

    MOD_TABS = ("players", "bans", "history")

    def _moderation_rows(self):
        """Every known player with their ban state, online first."""
        bans = set(paladmin.read_banlist(self.src.data_dir))
        rows = []
        for r in self.roster():
            rows.append(dict(r, banned=r["uid"] in bans or
                             (r.get("pid") or "") in bans))
        rows.sort(key=lambda r: (not r["online"], r["last_seen"] is None,
                                 -(r["last_seen"].timestamp()
                                   if r["last_seen"] else 0)))
        return rows, bans

    def overlay_moderation(self, cv, th, st):
        """Player administration: who is banned, the ban list, and a history
        of kicks and bans."""
        tab = self.MOD_TABS[st.get("tab", 0)]
        keys = {"players": "\u2191\u2193 pick  k kick  b ban  u unban",
                "bans": "\u2191\u2193 pick  u unban",
                "history": "\u2191\u2193 scroll"}[tab]
        iy, ix, ih, iw = self._full_box(
            cv, th, "players", "%s   tab next   esc back" % keys, "proc_box")
        if ih <= 0:
            return
        x = ix
        for i, name in enumerate(self.MOD_TABS):
            label = " %s " % name
            cv.text(iy, x, label, th.c("hotkey", bold=True)
                    if name == tab else th.c("label"))
            x += len(label) + 2
        rows, bans = self._moderation_rows()
        now = self.snap.get("now") or datetime.now(timezone.utc)
        y0, body = iy + 2, ih - 4

        if tab == "players":
            items = rows
        elif tab == "bans":
            names = {r["uid"]: r for r in rows}
            names.update({r.get("pid"): r for r in rows if r.get("pid")})
            items = [names.get(b) or {"uid": b, "name": "(never seen)",
                                      "level": 0, "online": False,
                                      "last_seen": None, "banned": True}
                     for b in paladmin.read_banlist(self.src.data_dir)]
        else:
            items = paladmin.history(self.snap.get("commands") or [])
        st["idx"] = max(0, min(st.get("idx", 0), max(0, len(items) - 1)))
        first = max(0, min(st["idx"] - body // 2, len(items) - body))
        hdr = th.c("label", bold=True)

        if tab in ("players", "bans"):
            for cx, t in ((2, "PLAYER"), (21, "LV"), (26, "SEEN"),
                          (40, "STATUS"), (62, "ID")):
                cv.text(y0, ix + cx, t, hdr)
            if not items:
                cv.text(y0 + 1, ix + 2, "nobody is banned on this server "
                        "(its own list; the global list is BanListURL)"
                        if tab == "bans" else "no players known yet",
                        th.c("dim"), maxw=iw - 2)
            for n, r in enumerate(items[first:first + body]):
                i = first + n
                row = y0 + 1 + n
                sel = i == st["idx"]
                if sel:
                    cv.text(row, ix, "\u25b6", th.c("hotkey", bold=True))
                cv.text(row, ix + 2, trunc(r["name"], 18),
                        th.c("value", bold=True) if r["online"] or sel
                        else th.c("label"))
                if r.get("level"):
                    cv.text(row, ix + 21, "%3d" % r["level"], th.c("label"))
                seen = "online" if r["online"] else (
                    fmt_ago(r["last_seen"], now) if r.get("last_seen")
                    else "-")
                cv.text(row, ix + 26, seen, th.c("online") if r["online"]
                        else th.c("dim"))
                if r.get("banned"):
                    cv.text(row, ix + 40, "BANNED", th.c("crit", bold=True))
                cv.text(row, ix + 62, r["uid"], th.c("dim"),
                        maxw=iw - 62)
        else:
            by_x = max(60, iw - 22)
            for cx, t in ((2, "WHEN"), (17, "WHAT"), (33, "WHO"),
                          (by_x, "BY")):
                cv.text(y0, ix + cx, t, hdr)
            if not items:
                cv.text(y0 + 1, ix + 2, "no kicks or bans recorded yet", th.c("dim"), maxw=iw - 2)
            for n, h in enumerate(items[first:first + body]):
                row = y0 + 1 + n
                sel = first + n == st["idx"]
                if sel:
                    cv.text(row, ix, "\u25b6", th.c("hotkey", bold=True))
                cv.text(row, ix + 2, fmt_stamp(h["ts"]), th.c("label"))
                cv.text(row, ix + 17, h["action"] + ("" if h["ok"]
                                                     else " FAILED"),
                        th.c("crit") if not h["ok"] or h["action"] == "ban"
                        else th.c("warn") if "kick" in h["action"]
                        else th.c("value"))
                cv.text(row, ix + 33, trunc(h["detail"], by_x - 34),
                        th.c("value"))
                cv.text(row, ix + by_x, h["by"], th.c("dim"),
                        maxw=iw - by_x)

        # confirmation or result line at the bottom
        foot = iy + ih - 1
        if st.get("confirm"):
            cv.text(foot, ix, st["confirm"][0] + "   y to confirm, "
                    "anything else cancels", th.c("crit", bold=True),
                    maxw=iw)
        elif st.get("result"):
            ok, msg = st["result"]
            cv.text(foot, ix, ("ok: " if ok else "failed: ") + msg,
                    th.c("good") if ok else th.c("crit"), maxw=iw)

    def moderation_key(self, ch, st):
        """Keys in the players window. Everything that affects a player asks
        for y first."""
        api = self.admin_api
        if st.get("confirm"):
            label, fn = st.pop("confirm")
            if ch in (ord("y"), ord("Y")):
                try:
                    st["result"] = fn()
                except Exception as exc:
                    st["result"] = (False, "%s: %s" % (type(exc).__name__,
                                                       exc))
                self.src._slow_at = 0.0
            else:
                st["result"] = (False, "cancelled")
            return True
        tab = self.MOD_TABS[st.get("tab", 0)]
        if ch == 27:
            self.admin = {"stage": "menu"}
            return True
        if ch == 9:
            st["tab"] = (st.get("tab", 0) + 1) % len(self.MOD_TABS)
            st["idx"] = 0
            return True
        if ch == curses.KEY_DOWN:
            st["idx"] = st.get("idx", 0) + 1
            return True
        if ch == curses.KEY_UP:
            st["idx"] = max(0, st.get("idx", 0) - 1)
            return True
        if ch in (curses.KEY_NPAGE, curses.KEY_PPAGE):
            st["idx"] = max(0, st.get("idx", 0) + (
                10 if ch == curses.KEY_NPAGE else -10))
            return True
        rows, bans = self._moderation_rows()
        if tab == "bans":
            ids = paladmin.read_banlist(self.src.data_dir)
            names = {r["uid"]: r["name"] for r in rows}
            if ch == ord("u") and ids:
                uid = ids[min(st.get("idx", 0), len(ids) - 1)]
                name = names.get(uid, uid)
                st["confirm"] = ("Unban %s?" % name,
                                 lambda: api.unban(uid, name))
            return True
        if tab != "players" or not rows:
            return True
        r = rows[min(st.get("idx", 0), len(rows) - 1)]
        if ch in (ord("k"), ord("b"), ord("u")) and \
                r["uid"].startswith("save:"):
            st["result"] = (False, "%s has only been seen in the save file, "
                            "so their user id is unknown - it is learnt the "
                            "next time they join" % r["name"])
            return True
        if ch == ord("k"):
            if not r["online"]:
                st["result"] = (False, "%s is not online" % r["name"])
            else:
                st["confirm"] = ("Kick %s from the server?" % r["name"],
                                 lambda: api.kick(r["uid"], r["name"]))
        elif ch == ord("b"):
            st["confirm"] = ("BAN %s? They cannot join again until unbanned."
                             % r["name"], lambda: api.ban(r["uid"], r["name"]))
        elif ch == ord("u"):
            st["confirm"] = ("Unban %s?" % r["name"],
                             lambda: api.unban(r["uid"], r["name"]))
        return True

    def overlay_commands(self, cv, th):
        now = self.snap.get("now") or datetime.now(timezone.utc)
        items = [("%s  %s" % (fmt_stamp(e["ts"]), e["name"]),
                  "%s   %s" % (e["detail"], fmt_ago(e["ts"], now)))
                 for e in reversed(self.snap.get("commands") or [])]
        if not items:
            items = [("(none)", "no admin commands in the recorded history")]
        self._overlay_table(cv, th, "admin command audit", items)

    def overlay_help(self, cv, th):
        items = [
            ("q / Esc", "quit"),
            ("+ / -", "refresh interval (0.5s - 30s)"),
            ("t", "trends page: activity heatmap + history"),
            ("p", "cycle player sort: %s" % ", ".join(SORTS)),
            ("a", "all/online players (in settings: all/changed)"),
            ("c", "admin command audit"),
            ("l", "pals: each player's best; enter opens all their pals"),
            ("", "  in there: o sort (attack, defense, tank, work...),"),
            ("", "  f filter, n current level / at your level,"),
            ("", "  t team builder: best team of 5 for a goal"),
            ("", "  / search by name, passive, partner skill or element"),
            ("", "  e passive planner: try passives, best set for a goal"),
            ("", "  g breeding helper: alpha eggs, extra eggs, egg speed"),
            ("", "  in the team builder: p passives as they are / planned /"),
            ("", "  best, e plan the selected slot's passives,"),
            ("", "  u use the suggested swap for more synergy"),
            ("b", "bases: what is built and who works each base camp"),
            ("x", "crash log: every crash, hang and restart, investigated"),
            ("d", "database: size, tables, retention, collector"),
            ("A", "admin menu: save, backup, announce, kick, restart"),
            ("PgUp/PgDn", "scroll the player list"),
            ("s", "world settings overlay"),
            ("e", "event log overlay"),
            ("r", "force a full refresh"),
            ("space", "pause polling"),
            ("", ""),
            ("safety", "paltop only issues REST GETs and reads"),
            ("", "docker metadata, /proc and the save dir."),
            ("", "It cannot restart or stop the server."),
        ]
        self._overlay_table(cv, th, "help", items)

    def overlay_events(self, cv, th):
        now = self.snap.get("now") or datetime.now(timezone.utc)
        failed = {(e["ts"], e["uid"])
                  for e in (self.snap.get("failed_joins") or [])}
        items = []
        for ev in reversed(self.snap.get("events") or []):
            if ev["kind"] == "cmd":
                verb = "admin: %s" % ev["detail"]
            elif ev["kind"] == "conn":
                verb = "connected"
                if (ev["ts"], ev["uid"]) in failed:
                    verb += "  -- never joined"
            else:
                verb = "joined" if ev["kind"] == "join" else "left"
            items.append(("%s  %s" % (fmt_stamp(ev["ts"]), ev["name"]),
                          "%s   %s" % (verb, fmt_ago(ev["ts"], now))))
        self._overlay_table(cv, th, "event log", items)

    def _overlay_table(self, cv, th, title, items, hint=None):
        h = min(cv.h - 4, len(items) + 4)
        w = min(cv.w - 6, 76)
        y = (cv.h - h) // 2
        x = (cv.w - w) // 2
        for row in range(y, y + h):
            cv.text(row, x, " " * w, th.c("value"))
        iy, ix, ih, iw = draw_box(cv, y, x, h, w, title, th, 0,
                                  right=hint or "esc to close",
                                  color="proc_box")
        total = len(items)
        self.scroll = max(0, min(self.scroll, max(0, total - ih)))
        lw = min(max([len(str(k)) for k, _ in items] or [8]) + 2, iw * 2 // 3)
        for i, (k, v) in enumerate(items[self.scroll:self.scroll + ih]):
            cv.kv(iy + i, ix, iw, str(k), str(v), th, lw=lw)
        if total > ih:
            cv.text(y + h - 1, x + w - 16, " %d-%d/%d " % (
                self.scroll + 1, min(total, self.scroll + ih), total),
                th.c("dim"))

    # -- layout ----------------------------------------------------------

    def draw(self, stdscr, th):
        stdscr.erase()
        cv = Canvas(stdscr)
        H, W = cv.h, cv.w
        if H < 16 or W < 56:
            cv.text(0, 0, "terminal too small (need 56x16, have %dx%d)"
                    % (W, H), th.c("crit"))
            stdscr.noutrefresh()
            curses.doupdate()
            return

        foot = H - 1
        hs = min(6, foot)
        self.panel_server(cv, 0, 0, hs, W, th)
        top = hs

        alerts = self.snap.get("alerts") or []
        if alerts and not self.overlay and foot - top > 14:
            ah = min(len(alerts) + 2, 6)
            self.panel_alerts(cv, top, 0, ah, W, th)
            top += ah

        if self.overlay:
            {"settings": self.overlay_settings,
             "help": self.overlay_help,
             "events": self.overlay_events,
             "commands": self.overlay_commands,
             "pals": self.overlay_pals,
             "bases": self.overlay_bases,
             "crashes": self.overlay_crashes,
             "database": self.overlay_database,
             "admin": self.overlay_admin}[self.overlay](cv, th)
        elif self.page == "trends":
            self.page_trends(cv, th, top)
        else:
            self.page_main(cv, th, top, foot, W)

        self.panel_footer(cv, foot, W, th)
        stdscr.noutrefresh()
        curses.doupdate()

    def page_main(self, cv, th, top, foot, W):
        body = foot - top
        if W >= 104 and body >= 22:
            he = max(5, min(9, body // 6))
            mid = body - he
            lw = max(42, min(54, W * 38 // 100))
            hh, oh = split_heights(mid, (NCPU + 15, 17), (7, 8))
            self.panel_host(cv, top, 0, hh, lw, th)
            if oh >= 4:
                self.panel_ops(cv, top + hh, 0, oh, lw, th)
            self.panel_players(cv, top, lw, mid, W - lw, th)
            self.panel_events(cv, top + mid, 0, he, W, th)
        else:
            ph = max(5, body // 2)
            self.panel_players(cv, top, 0, ph, W, th)
            if body - ph >= 4:
                self.panel_ops(cv, top + ph, 0, body - ph, W, th)

    # -- admin actions ---------------------------------------------------

    def admin_exec(self, action, arg=None):
        api = self.admin_api
        if action == "s":
            res = api.save()
        elif action == "b":
            res = api.backup()
        elif action == "a":
            res = api.announce(arg)
        elif action == "k":
            res = api.kick(arg["uid"], arg["name"])
        elif action == "r":
            res = api.shutdown(30, "Server restarting in 30 seconds")
        else:
            res = (False, "unknown action")
        self.admin = {"stage": "menu", "result": res}
        self.src._slow_at = 0.0                 # refresh state after a change
        self.refresh()

    def admin_key(self, ch):
        """Drive the admin menu. Returns True if the key was consumed."""
        st = self.admin or {"stage": "menu"}
        stage = st.get("stage", "menu")
        if stage == "players":
            return self.moderation_key(ch, st)
        if stage == "restoring":
            if ch == 27 and st["job"].done:
                self.admin = {"stage": "menu"}
            return True                         # nothing else while it runs
        if stage == "backup":
            if ch == curses.KEY_DOWN:
                st["idx"] = st.get("idx", 0) + 1
            elif ch == curses.KEY_UP:
                st["idx"] = max(0, st.get("idx", 0) - 1)
            elif ch in (curses.KEY_NPAGE, curses.KEY_PPAGE):
                st["idx"] = max(0, st.get("idx", 0) + (
                    10 if ch == curses.KEY_NPAGE else -10))
            elif ch in (10, 13, curses.KEY_ENTER):
                backups = paladmin.list_backups(self.src.data_dir or "")
                if backups:
                    b = backups[min(st.get("idx", 0), len(backups) - 1)]
                    if paladmin.inspect_backup(b["path"])["ok"]:
                        self.admin = {"stage": "restore_confirm",
                                      "idx": st.get("idx", 0),
                                      "path": b["path"], "buf": ""}
            elif ch == 27:
                self.admin = {"stage": "menu"}
            return True
        if stage == "restore_confirm":
            if ch == 27:
                self.admin = {"stage": "backup", "idx": st.get("idx", 0)}
            elif ch in (10, 13, curses.KEY_ENTER):
                if st.get("buf", "").strip().lower() == "restore":
                    job = paladmin.Restore(self.admin_api, self.src.data_dir,
                                           st["path"])
                    job.start()
                    self.admin = {"stage": "restoring", "job": job}
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                st["buf"] = st.get("buf", "")[:-1]
            elif 32 <= ch < 127 and len(st.get("buf", "")) < 20:
                st["buf"] = st.get("buf", "") + chr(ch)
            return True
        if ch == 27:                            # esc backs out one step
            if stage == "menu":
                self.overlay, self.admin = None, None
            else:
                self.admin = {"stage": "menu"}
            return True

        if stage == "text":
            if ch in (10, 13, curses.KEY_ENTER):
                self.admin_exec(st["action"], st.get("buf", ""))
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                st["buf"] = st.get("buf", "")[:-1]
            elif 32 <= ch < 127 and len(st.get("buf", "")) < 120:
                st["buf"] = st.get("buf", "") + chr(ch)
            return True

        if stage == "player":
            rows = [r for r in self.roster() if r["online"]]
            if ord("1") <= ch <= ord("9"):
                i = ch - ord("1")
                if i < len(rows):
                    r = rows[i]
                    self.admin = {
                        "stage": "confirm", "action": st["action"],
                        "arg": {"uid": r["uid"], "name": r["name"]},
                        "prompt": "Kick %s from the server?" % r["name"]}
            return True

        if stage == "confirm":
            if ch in (ord("y"), ord("Y")):
                self.admin_exec(st["action"], st.get("arg"))
            else:
                self.admin = {"stage": "menu"}
            return True

        # stage == "menu"
        for key, label, sev, needs in (self.admin_api.menu()
                                       if self.admin_api else []):
            if ch != ord(key):
                continue
            if needs == "backup":
                self.admin = {"stage": "backup", "idx": 0}
            elif needs == "players":
                self.admin = {"stage": "players", "tab": 0, "idx": 0}
            elif needs == "text":
                self.admin = {"stage": "text", "action": key, "buf": ""}
            elif needs == "player":
                self.admin = {"stage": "player", "action": key}
            elif sev >= paladmin.DISRUPTIVE:
                self.admin = {
                    "stage": "confirm", "action": key,
                    "prompt": "%s - everyone online will be disconnected."
                              % label}
            else:
                self.admin_exec(key)
            return True
        return True                             # swallow stray keys in the menu

    # -- input -----------------------------------------------------------

    def handle(self, ch):
        if self.overlay == "admin":
            busy = (self.admin or {}).get("stage") in (
                "restoring", "restore_confirm") or bool(
                    (self.admin or {}).get("confirm"))
            if ch == ord("q") and not busy:
                return False
            self.admin_key(ch)
            return True
        if self.overlay == "pals" and self.pals_key(ch):
            return True
        if ch == 27:                                   # Esc steps back
            if self.overlay:
                self.overlay, self.scroll = None, 0
            elif self.page != "main":
                self.page, self.scroll = "main", 0
            else:
                return False
            return True
        if ch == ord("q"):
            return False
        if ch in (ord("+"), ord("=")):
            self.interval = min(30.0, self.interval * 2)
        elif ch in (ord("-"), ord("_")):
            self.interval = max(0.5, self.interval / 2)
        elif ch == ord("p"):
            self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
        elif ch == ord("a"):
            if self.overlay == "settings":
                self.settings_all = not self.settings_all
            else:
                self.show_all = not self.show_all
        elif ch == ord("t"):
            self.page = "main" if self.page == "trends" else "trends"
            self.overlay, self.scroll = None, 0
        elif ch == ord("c"):
            self.overlay = None if self.overlay == "commands" else "commands"
            self.scroll = 0
        elif ch == ord("b"):
            self.overlay = None if self.overlay == "bases" else "bases"
            self.scroll = 0
        elif ch == ord("x"):
            self.overlay = None if self.overlay == "crashes" else "crashes"
            self.scroll = 0
        elif ch == ord("d"):
            self.overlay = None if self.overlay == "database" else "database"
            self.scroll = 0
        elif ch == ord("A"):                   # shift, so it is deliberate
            self.overlay = "admin"
            self.admin = {"stage": "menu"}
        elif ch == ord("l"):
            self.overlay = None if self.overlay == "pals" else "pals"
            self.scroll = 0
            self.pal_player = None
        elif ch == ord("s"):
            self.overlay = None if self.overlay == "settings" else "settings"
            self.scroll = 0
        elif ch == ord("e"):
            self.overlay = None if self.overlay == "events" else "events"
            self.scroll = 0
        elif ch in (ord("h"), ord("?")):
            self.overlay = None if self.overlay == "help" else "help"
            self.scroll = 0
        elif ch == ord(" "):
            self.paused = not self.paused
        elif ch == ord("r"):
            self.src._slow_at = 0.0
            self.refresh()
        elif ch in (curses.KEY_NPAGE, ord("j")):
            self.scroll += 5
        elif ch in (curses.KEY_PPAGE, ord("k")):
            self.scroll = max(0, self.scroll - 5)
        elif ch in self.ctrl_arrows:           # ctrl+arrows work as well
            self.scroll = max(0, self.scroll - self.ctrl_arrows[ch][1])
        elif ch == curses.KEY_DOWN:
            self.scroll += 1
        elif ch == curses.KEY_UP:
            self.scroll = max(0, self.scroll - 1)
        elif ch == curses.KEY_HOME:
            self.scroll = 0
        return True

    # -- main loop -------------------------------------------------------

    def draw_splash(self, stdscr, th):
        """Shown until the first snapshot arrives."""
        stdscr.erase()
        cv = Canvas(stdscr)
        waited = time.monotonic() - self.started
        w = min(cv.w - 4, 64)
        y, x = max(0, cv.h // 2 - 3), max(0, (cv.w - w) // 2)
        iy, ix, ih, iw = draw_box(cv, y, x, 6, w, "paltop", th, 0,
                                  color="cpu_box")
        cv.text(iy, ix, "connecting to %s ..." % self.src.container,
                th.c("value", bold=True), maxw=iw)
        cv.text(iy + 1, ix, "waiting %.0fs" % waited, th.c("dim"), maxw=iw)
        err = getattr(self.poller, "error", None)
        if err:
            cv.text(iy + 2, ix, err, th.c("crit"), maxw=iw)
        elif waited > 4:
            cv.text(iy + 2, ix, "the server is slow to answer - it may be hung",
                    th.c("warn"), maxw=iw)
        cv.text(cv.h - 1, 1, "q quit", th.c("dim"))
        stdscr.noutrefresh()
        curses.doupdate()

    def run(self, stdscr):
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)
        self.ctrl_arrows = resolve_ctrl_arrows()
        th = Theme()
        self.started = time.monotonic()
        self.poller = Poller(self)
        self.poller.start()
        try:
            while True:
                fresh = self.poller.take()
                if fresh is not None:
                    self.ingest(fresh)
                if self.snap:
                    self.draw(stdscr, th)
                    self.frames += 1
                else:
                    self.draw_splash(stdscr, th)
                stdscr.timeout(250)             # redraw at ~4 fps regardless
                ch = stdscr.getch()
                if ch in (-1, curses.KEY_RESIZE):
                    continue
                if not self.snap:
                    if ch in (ord("q"), 27):
                        return
                    continue
                if not self.handle(ch):
                    return
        finally:
            self.poller.stop()


def dump_json(app):
    """Machine-readable snapshot on stdout."""
    import json
    app.poll()
    time.sleep(0.6)
    app.poll()
    s = dict(app.snap)
    s["roster"] = app.roster()
    s.pop("store", None)
    disk = s.get("disk")
    if disk is not None:
        s["disk"] = {"total": disk.total, "used": disk.used, "free": disk.free}

    def encode(o):
        if isinstance(o, datetime):
            return to_local(o).isoformat(timespec="seconds")
        if isinstance(o, set):
            return sorted(o)
        return str(o)

    print(json.dumps(s, default=encode, indent=2, sort_keys=True))


def dump(app):
    """Plain-text snapshot; handy for cron, ssh one-liners and debugging."""
    app.poll()
    time.sleep(0.6)
    app.poll()
    s, now = app.snap, app.snap["now"]
    info, m = s.get("info") or {}, s.get("metrics") or {}
    print("%s  (%s)" % (info.get("servername", "?"), info.get("version", "?")))
    print("  status      %s/%s  up %s  restarts %s" % (
        s.get("status"), s.get("health"), fmt_dur(m.get("uptime") or 0),
        s.get("restart_count")))
    print("  world       day %s, %s base camps, %s" % (
        m.get("days"), m.get("basecampnum"),
        (s.get("settings") or {}).get("Difficulty", "?")))
    print("  perf        %.0f fps (avg %.1f), %.2f ms, cpu %.0f%%, mem %s" % (
        m.get("serverfps") or 0, m.get("serverfpsaverage") or 0,
        m.get("serverframetime") or 0, s["container"]["cpu_pct"],
        fmt_bytes(s["container"]["mem"])))
    print("  players     %s/%s online" % (m.get("currentplayernum"),
                                          m.get("maxplayernum")))
    for r in app.roster():
        print("    %s %-22s lvl %-4s %7.1fh  %s" % (
            "*" if r["online"] else " ", trunc(r["name"], 22),
            r["level"] or "--", r["playtime"] / 3600.0,
            "online" if r["online"] else fmt_ago(r["last_seen"], now)))
    alerts = s.get("alerts") or []
    if alerts:
        print("  alerts")
        for a in alerts:
            print("    [%-4s] %s" % (a["level"], a["text"]))
    diff = s.get("settings_diff")
    if diff:
        print("  settings    %d changed from default: %s" % (
            len(diff), ", ".join(r["key"] for r in diff[:5])
            + (" ..." if len(diff) > 5 else "")))
    cmds = s.get("commands") or []
    if cmds:
        print("  admin cmds  %d recorded, latest: %s by %s" % (
            len(cmds), cmds[-1]["detail"], cmds[-1]["name"]))
    fj = s.get("failed_joins") or []
    if fj:
        print("  failed joins %d (latest %s at %s)" % (
            len(fj), fj[-1]["name"], fmt_stamp(fj[-1]["ts"])))
    st = s.get("db") or {}
    if st:
        print("  history     %s events, %s samples, %s players (%s of %s)" % (
            st.get("events"), st.get("samples"), st.get("players"),
            fmt_bytes(st.get("bytes", 0)), fmt_bytes(st.get("cap", 0))))
    for inc in (s.get("incidents") or [])[:5]:
        print("  crash log   %s  %s" % (fmt_stamp(_dtu(inc["start"])),
                                        palcrash.summary(inc)))
    b = (s.get("backups") or [None])[0]
    if b:
        bdt = datetime.fromtimestamp(b["mtime"], tz=timezone.utc)
        print("  last backup %s (%s), %s" % (fmt_stamp(bdt),
                                             fmt_ago(bdt, now),
                                             fmt_bytes(b["size"])))
    bc, rc = s.get("backup_cron") or {}, s.get("restart_cron") or {}
    print("  next backup %s" % (fmt_stamp(bc.get("next")) if bc.get("active")
                                else "not scheduled"))
    print("  auto-restart %s" % ("%s" % fmt_stamp(rc.get("next"))
                                 if rc.get("active")
                                 else "not scheduled (cron %r not installed)"
                                 % rc.get("expr", "")))


def main():
    locale.setlocale(locale.LC_ALL, "")
    ap = argparse.ArgumentParser(
        description="btop-style dashboard for a Palworld dedicated server "
                    "(strictly read-only)")
    ap.add_argument("-c", "--container", default=os.environ.get(
        "PALTOP_CONTAINER", "palworld-server"), help="docker container name")
    ap.add_argument("-i", "--interval", type=float, default=2.0,
                    help="refresh interval in seconds (default 2)")
    ap.add_argument("-H", "--host", default="127.0.0.1",
                    help="REST API host (default 127.0.0.1)")
    ap.add_argument("--dump", action="store_true",
                    help="print one plain-text snapshot and exit")
    ap.add_argument("--json", action="store_true",
                    help="print one snapshot as JSON and exit")
    ap.add_argument("--no-net", action="store_true",
                    help="skip the Steam build check (no outbound requests)")
    ap.add_argument("--no-admin", action="store_true",
                    help="disable the admin menu, leaving paltop read-only")
    ap.add_argument("--collect", action="store_true",
                    help="run headless, recording history in the background "
                         "(the same as core/palcollect.py, which "
                         "paltop-collector.service runs)")
    args = ap.parse_args()

    app = App(args.container, max(0.5, args.interval), args.host,
              use_net=not args.no_net, allow_admin=not args.no_admin)
    if app.src.err:
        for e in app.src.err:
            print("paltop: %s" % e, file=sys.stderr)
    if args.collect:               # the collector used to live here
        return palcollect.collect(app.src, args.interval
                                  if args.interval != 2.0
                                  else palcollect.INTERVAL)
    if args.json:
        dump_json(app)
        return 0
    if args.dump:
        dump(app)
        return 0
    if not sys.stdout.isatty():
        print("paltop: not a terminal - use --dump for plain text",
              file=sys.stderr)
        return 1
    try:
        curses.wrapper(app.run)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
