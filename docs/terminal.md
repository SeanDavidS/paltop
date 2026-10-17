# Paltop terminal

A btop-style terminal dashboard for a Palworld dedicated server running in
Docker: server health, players, bases, Pals, the crash log and
an admin menu, in one screen.

```bash
paltop              # the dashboard
paltop --dump       # one plain-text snapshot, for scripts and ssh
paltop --json       # the same as JSON
paltop --no-admin   # read-only: no admin menu
```

Pure Python 3 standard library (curses). It discovers the server from
`docker inspect` (`-c NAME` or `PALTOP_CONTAINER` for another container
name). The Pals pages (team builder, passive planner, breeding) are described
in [core.md](core.md), since the web page has them too.

## When the server hangs

The container image's healthcheck is `pgrep PalServer-Linux` - it only asks
whether the process exists. When the game thread hangs, the process keeps
running (at 100% of a core) but stops answering, so Docker keeps reporting it
as `healthy` and `restart: unless-stopped` never kicks in.

paltop does not trust that. It classifies every REST failure:

| REST behaviour | Meaning | Shown as |
|---|---|---|
| accepts the connection, never answers | **hung** | `NOT RESPONDING` + `SERVER HUNG` alert with the restart command |
| connection refused / reset, container starting | starting up | `STARTING` |
| connection refused / reset otherwise | down | `DOWN` |

Data is fetched on a background thread, so the screen draws immediately and
keys always respond even while every request is timing out. After one timeout
in a refresh, the remaining REST calls are skipped, and the last good server
name, version and world day stay on screen.

## Crash log

`x` opens the crash log: every time the server went down, newest first, with
an automatic investigation of each. `↑` `↓` picks an incident.

**Where it comes from.** The background collector records every change in the
server's state (ok, hung, down), container starts, host boots and autoheal's
restarts in the database. It also copies the game's own crash reports from
`Pal/Saved/Crashes/` into the database, because the game deletes them after
two days (`CrashConfigPurgeDays=2`). Reports from before the collector existed
are still used - they just have no recorded outage.

**What kind of incident.** In this order:

| Kind | How it is recognised |
|---|---|
| hang | a game crash report of type Hang, or the REST API accepting connections and never answering |
| crash | a crash report of any other type |
| host reboot | the machine booted during the outage (counted from the last sign of life before it) |
| admin restart | a restart or shutdown from paltop's admin menu just before |
| scheduled restart | it went down in the first 10 minutes of `AUTO_REBOOT_LOCAL_HOUR` |
| outage | none of the above |

The last three are planned and shown dimmed; the rest raise an alert for 24 hours.

**The investigation**, for each incident:

- timeline: last sign of life, when it stopped answering, the crash report and
  how long the server had been up, autoheal, container start, back up
- the freeze point: the game-code frames at the top of the crash report's stack
- the server just before: lowest fps, memory, players online
- each player online (seen moving, or joined and not left): position as in-game
  map coordinates, and notes such as *joined 2 min before*,
  *fast-travelled 6.4 km 1 min before*, *levelled up to 77*, *at base 3*,
  *standing still*
- patterns across all unplanned incidents: the same freeze point in the game
  code (3 or more shared frames), the same player online, the same player
  within 300 m of the same place
- a likely cause: a known cause (the Panthalus freeze point is recorded in
  `KNOWN_CAUSES` in `palcrash.py`), then a location pattern, then a
  fast-travel just before, then "nobody online" (server-side)

For example, two hangs caused by summoning Panthalus are both matched: the
same freeze point (`0x775e20b ← … ← 0x80f46eb ← 0x82b7575`), the same player
online both times, and in the second one that player fast-travelled 6.4 km a
minute before the game froze.

`paltop --dump` lists the five newest incidents.

## Admin actions

Press **`A`** (capital, so it is deliberate) for a hotkey menu:

| Key | Action | Grade |
|---|---|---|
| `s` | Save world now | safe |
| `b` | Create a backup | safe |
| `a` | Announce a message | safe |
| `k` | Kick a player | disruptive |
| `r` | Restart with a 30s warning | **severe** |
| `R` | Restore a backup (see below) | **severe** |
| `p` | Players: bans and history (see below) | disruptive |

Anything beyond `safe` needs a confirmation keypress, and a kick needs a player
picked from the online list first. Restart uses the REST API's `shutdown` with
a countdown, so players get warned; the container's `restart: unless-stopped`
policy is what brings the server back. Backups run the container's own
`/usr/local/bin/backup`, the same script its cron uses.

Every attempt is appended to `state/admin.log` with its outcome, whether it
succeeded or not.

`--no-admin` disables the menu entirely and restores the read-only guarantee
below. All the write code lives in `paladmin.py` and nowhere else, so deleting
that one file has the same effect.

### Active or AFK

A player who is online but has not moved more than 5 m in 5 minutes is shown
as **AFK** (with how long), in paltop's player list and on the web page. It is
worked out from the positions the collector records once a minute, so it is
accurate to about a minute and needs the collector running.
`AFK_AFTER` in `core/palcollect.py` sets the 5 minutes.

### Players: bans and history

`A`, then `p`, opens the players window. `Tab` moves between three tabs.

**players** - everyone paltop has seen, online first, with level, when they
were last on, and whether they are *BANNED*. On the selected player: `k` kick
(online only), `b` ban, `u` unban - each asks for `y` first.

**bans** - the server's own ban list (`Pal/Saved/SaveGames/banlist.txt`, which
the server writes when someone is banned), with names where paltop knows them,
and `u` to unban. The global list Pocketpair publishes (`BanListURL`) is not
shown; it is applied by the server itself.

**history** - every kick, ban and unban made from paltop (from
`state/admin.log`), plus `/KickPlayer`, `/BanPlayer` and `/UnBanPlayer` typed in
the game (from the server log), newest first.

Players only seen in the save file (never in the server log) have no known user
id yet; kick and ban refuse them until they have joined once while paltop was
watching. `PALTOP_ADMIN_LOG` points the admin log elsewhere, for testing.

### Secrets in the server log

When an admin logs in in the game (`/AdminPassword ...`), the server writes the
whole command, password included, to its log. paltop keeps only
`AdminPassword ****`: the argument is masked when the log is read, and rows
stored before this was added are rewritten (and the database vacuumed so the
old text does not linger) when paltop starts. `docker logs` itself still shows
the full line; that is the server's own log, which paltop cannot change.

### Restoring a backup

`A`, then `R`, lists every backup newest first: the nightly ones, manual ones,
and paltop's own `paltop-before-restore-*` snapshots (in yellow). For the
selected one it opens the archive without unpacking it and shows whether it is
complete, which world it is, the **game day** (and how far behind today it
is), the number of Pals, and **each player's level** then and now. An archive
that is torn or has no world in it is marked *not usable* and cannot be picked.

`Enter` asks for confirmation: it says who is online (they will be
disconnected - announce it first), and you have to type `restore` and press
`Enter`. Then paltop runs the steps and shows each as it goes:

1. check the backup again, and refuse one from a different world
2. save the world, if the server answers
3. pause autoheal, so it cannot restart the server half-way
4. stop the container (`docker stop`: the game saves and exits cleanly)
5. snapshot the current world to `backups/paltop-before-restore-<time>.tar.gz`
   and check that snapshot - this is the undo
6. unpack the backup to a temporary folder and check it
7. swap `Saved/SaveGames` and `Saved/Config` for the backup's; the game's own
   rolling `backup` folders and the crash reports are kept. Any error here puts
   the old files back.
8. start the server and autoheal again - always, also after an error, and only
   those that were running before

The files are only swapped while the container is stopped, so the game never
sees a half-restored world. This does not use the image's `restore` script: it
is interactive, and it deletes its own safety copy once it succeeds, whereas
paltop keeps the snapshot as a normal backup. To undo a restore, restore that
snapshot. The restore keeps running even if paltop is closed half-way, and
every run is logged to `state/admin.log`.

Tested on a copy of the world with a stand-in for `docker`: a normal restore,
undoing it, a backup from another world, a torn archive, a disk error in the
middle of the swap, a server that will not stop, and autoheal already off.

## What it shows

| Panel | Contents |
|---|---|
| **server** | Server name, version, world GUID, in-game day, base camps, difficulty, uptime, container health, live player gauge, server FPS gauge + history graph, frametime |
| **host** | Per-core CPU with temperatures, load average, host RAM, the Palworld container's own CPU and memory, game PID and RSS, container network throughput, CPU history graph |
| **players** | Every player ever seen: online marker, name (and account name), level with a bar, ping, total playtime, and how long they have been online / when they were last seen |
| **ops** | Last restart and whether another is scheduled, last + next backup, backup archive size and age, last world save, total save size, free disk |
| **events** | Join / leave feed from the server log, in local time |
| **alerts** | Appears only when something needs attention: stale or corrupt backup, low disk, sustained low FPS, container unhealthy, server update available, no backup job installed |
| **trends** (`t`) | Activity heatmap (player-hours by weekday and hour), 24h player-count and FPS history, and world-save growth over the whole backup archive |

## Colors

paltop uses btop's built-in **Default** theme, with the same palette roles:

| Element | Color | btop role |
|---|---|---|
| `server` / `host` borders | green `#3d7b46` | `cpu_box` |
| `players` border | red `#923535` | `proc_box` |
| `ops` border | olive `#8a882e` | `mem_box` |
| `events` border | indigo `#423ba5` | `net_box` |
| Box titles / hotkey letter | `#eeeeee` / `#b54040` | `title` / `hi_fg` |
| Empty meter cells | `#404040` | `meter_bg` |

Meters and graphs use btop's three-stop gradients, interpolated start -> mid ->
end exactly as btop does: CPU load `#50f095 -> #f2e266 -> #fa1e1e`
(green-yellow-red), memory `used`, temperatures `#4897d4 -> #5474e8 -> #ff40b6`
(blue-purple-pink), network down/up in btop's indigo and orchid, and player
levels + server FPS on the `free` ramp `#223014 -> #b5e685 -> #dcff85`, so a
full bar reads as healthy rather than critical.

btop paints in 24-bit color; curses can only address the 256-color palette, so
each theme color is snapped to its nearest xterm-256 entry (6x6x6 cube or the
grey ramp). Redefining palette slots with `init_color()` would be exact but can
leave your terminal recolored after exit, so paltop deliberately does not do
that. On an 8-color terminal it falls back to the ANSI colors, and with no
color support at all it still renders in plain text.

Run `paltop` in a `TERM=xterm-256color` terminal to get the full palette.

## Keys

| Key | Action |
|---|---|
| `q` / `Esc` | quit (or close an overlay) |
| `+` / `-` | refresh interval, 0.5s to 30s |
| `p` | cycle player sort: level, name, ping, playtime, last seen |
| `a` | all known players / online only |
| `↑` `↓` `PgUp` `PgDn` | scroll the player list or an overlay |
| `t` | trends page: activity heatmap and history |
| `s` | world settings: changed-from-default, `a` for all |
| `e` | full event log overlay |
| `c` | admin command audit |
| `l` | pals: each player's best; `↑` `↓` + `Enter` opens all of a player's Pals |
| `b` | bases: what is built and who works each base camp |
| `x` | crash log: every crash, hang and restart, investigated |
| `d` | database: size, tables, retention, collector |
| `A` | admin menu: save, backup, announce, kick, restart |
| `h` / `?` | help |
| `space` | pause polling |
| `r` | force a full refresh |

## Other modes

```bash
paltop --dump                 # one plain-text snapshot, good for ssh/cron
paltop --json                 # full snapshot as JSON, for scripting
paltop --no-net               # skip the Steam update check
paltop -i 5                   # 5 second refresh
paltop -c other-container     # a different container
paltop -H server.lan           # REST API on another host
```

## Layout

Two columns at 104x30 or larger, a single stacked column below that, down to a
minimum of 56x16. Panels shed their least important rows rather than truncating,
so nothing important disappears on a small terminal.

## Auto-restart

paltop reads the container image's own auto-reboot job
(`AUTO_REBOOT_CRON_EXPRESSION` in the crontab the image installs, run by
supercronic) and shows the next restart in the ops panel, noting the
30-minute countdown if players are online. If the job is gated to a local
hour with `AUTO_REBOOT_TZ` and `AUTO_REBOOT_LOCAL_HOUR` (a cron like
`0 2,3 * * *` that only acts at, say, 04:00 local time across daylight saving
changes), paltop applies the same rule. An alert says so when no restart job
is installed at all.
