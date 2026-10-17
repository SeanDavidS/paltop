# Paltop web

A read-only web page for the players on a Palworld server: status,
Pals with the team builder and boss planner, Palpedia, bases, the crash log,
battles and achievements. It runs as the `paltop-web` service and reads what
the collector (`core/palcollect.py`) records.


`palweb.py` serves paltop in a browser, so everyone who plays can check the
server from a phone without SSH. It runs as the user service `paltop-web`, on
**https://<server address>:8765**, and starts by itself at boot.

```bash
systemctl --user status paltop-web
journalctl --user -u paltop-web -f
```

**Users.** Everyone logs in with their in-game name and a password of their
own. To make a user (**New here? Make a user** on the login page):

1. type the server's join password (`SERVER_PASSWORD` in docker-compose.yml,
   re-read every 5 minutes); only then does the form for the user appear,
   and it stays open for 15 minutes
2. type your name exactly as it is in the game, and the password you want
   (at least 6 characters, not the server password)
3. paltop looks the name up among the players with a character on the
   server. Found: the user is made and **linked to that player**, and a
   message says so ("We found the player Alice (level 74, 894 Pals) on the
   server and linked it to your user"), which you close with **OK**. Not
   found: it says so, and no user is made.

Names are unique: "alice" and "Alice" are the same name, and each player can
only be linked to one user. Users made before linking existed are linked to
their player the next time they open the page, with the same message.

Users and logins are kept in the history database, `state/paltop.db`, in the
tables `web_users` (name, the linked player and a salted scrypt hash of the password, never the
password itself) and `web_sessions` (only a hash of each browser's session
cookie). A login lasts 30 days. After logging in, the page shows your name
next to **log out** and opens your own Pals first.

Five wrong passwords from one address, or 30 from anywhere, block logins for
10 minutes; people already logged in are not affected. If the server has no
password the site refuses to start rather than run open.

The admin can list users, or remove one (for a forgotten password, or if
someone took a name that is not theirs; that also logs out their browsers, and
the player can then make the user again):

```bash
python3 web/palweb.py --list-users
```

```bash
python3 web/palweb.py --remove-user "NAME"
```

**What it shows**, with buttons as well as paltop's hotkeys (`o` `l` `b`
`x`):

- overview: server status, day, backups and next restart, who is on -
  *active* or *AFK* - and for how long, when the others were last on, the
  guild each is in (read from the save's GroupSaveDataMap), alerts, fps and
  players over the last 24 hours
- pals: each player's Pals with the same sorting (including the role scores),
  filters, details and breeding as in the terminal, and the team builder (it starts empty: choose a Pal for each slot, compare
  candidates side by side, or fill the rest with the best). **Wishes** say
  what the team should have - a flying, swimming or ground mount, elements, a
  healer, carry weight, team or player buffs - and paltop suggests Pals for
  the empty slots that meet them (one Pal can meet several, so as few slots
  as possible go to wishes), with the rest the best for the goal; "use these"
  puts them in. Wishes are kept in the browser, met ones turn green, and the
  choose list can be sorted by the wishes a Pal meets. Under the team,
  **recommended for this team** lists who to add next to the Pals already
  picked: how much each would grow the team's total with synergy, and why -
  the partner skills it gets and gives (buffs, heals, Pals it calls), new
  elements, one more weak to an element, the wishes it meets, and whether it
  copes with a teammate's Health drain (a healer, its own life steal). "add"
  puts it in the first empty slot, "compare" opens it next to the top one. The list shows 50 Pals per page (first,
  previous, next and last page above and below it); a new sort, filter,
  search or player starts on page 1, and picking a Pal does not reload the list
- **online now**: a line under the tabs, on every tab: who is in the game
  (level, how long they have been on, AFK) and who has this
  page open and on which tab ("away" when it is open but not in front; kept
  in memory only, from a ping every 20 seconds)
- Battles and background effects only run while the browser window has
  focus: without it everything stands still where it is (battles on their
  own clocks, so they go on from there when you come back).
- **fx** at the top picks the background: **abilities** (the default, below), **battles**
  or **off** (remembered in the browser). **Battles**: two players' Pals,
  each one from its player's pool (say one of Alice's and one of Bob's):
  the Pals in their party and their top 5 by all-round fighter, attack,
  defense and HP, each Pal once (so at most 25, often fewer; Panthalus is
  left out, as in the team builder), fight it out. The first is picked at random, the
  opponent to be as even as it can be: players of about the same strength,
  and among their best the Pal closest in score, are much likelier (so the
  big players mostly meet each other, and the small ones too).
  old-school monster-battle style - one against one, two against two or
  three against three, each as likely - at one side of the page (a random side for
  each fight), one low on the left and one high on the right, a turn every
  2.8 seconds: little
  pixel Pals (a shape made from the species, in its element colours), cards
  with name, level, owner and an HP bar, damage numbers, the skill's own
  animation on the target, and a text box ("Suzaku used Fire Ball! It's
  super effective!"). The server works the fight out (`palbattle.py`) from
  the real numbers: HP, attack and defense estimated from species, level,
  IVs and passives, and the skills the Pal has equipped, with their power,
  element, cooldown and status effects from DT_WazaDataTable, the element
  chart, the same-element bonus and the odd critical hit. Status effects
  happen as in the game: Burn and Poison hurt every turn, Freeze, Electrify
  and Stun cost a turn, Blind makes it miss, and the game's combos apply
  both ways (Soaked foes take more Electric and less Fire damage, Burned
  less Grass, Muddy more Grass and less Electric, Ivy-Covered more Fire,
  Electrified more Water). The cards show each Pal's statuses as badges
  (BRN, SOAK, PAR, FRZ, VINE, MUD, BLND, PSN, STUN), and the text box says
  when a combo kicks in. Each turn first shows whose turn it is - a
  little pointer over that Pal and its card lit up - and a second later
  what it does. With more than one a side, what each skill hits is decided
  skill by skill from the game's own description of it (`AREA` in
  palbattle.py, 145 of the 338 skills Pals learn): explosions, fans ("in a
  fan shape"), rain, storms, shock waves, sweeping or wide beams and things
  "around the enemy" hit every foe; bites, claws, charges, line attacks and
  single shots ("straight at an enemy") hit one, at random among those
  still standing. Skills that do something to their user do it too, as the
  descriptions say: Implode, Bee Quiet and Megaton Implode knock the user
  out, Roly Poly leaves Lamball dizzy for a turn, and Soul Drain heals the
  user by half the damage (the game gives no number). Lyleen's healing skill
  has no numbers in the game data, so it is not used. With more than one a
  side, the owner's name stands over their side's cards ("Alice's team"); a
  single Pal's card says it itself. `/api/battle?size=3` (or
  `paltopFx.battle(3)` in the browser console) starts a fight of that size.
  Damage is power x attack / defense, with
  nothing capping it, so a much stronger Pal can win in one hit. When one
  is done the next begins. It needs a login and room at a side of the
  page; on the login page, abilities play instead
- a background at the sides of the page and behind the login: now and then,
  a few seconds apart and rarely two at once, a Pal ability goes off in retro
  pixel style, in its element's colour (`web/static/bg.js`). They are real active
  skills from the game's data table (DT_WazaDataTable), the 42 that look like
  something without the Pal itself (claws, bites and charges are left out):
  - Electric: Lightning Bolt, Tri-Lightning, Spark Blast, Electric Ball,
    Shockwave
  - Fire: Ignis Blast, Fire Ball, Flare Arrow, Spirit Fire, Flare Storm,
    Volcanic Burst
  - Water: Bubble Blast, Aqua Gun, Hydro Laser, Acid Rain, Geyser Gush
  - Ice: Iceberg, Ice Missile, Blizzard Spike, Diamond Rain
  - Grass: Wind Cutter, Seed Machine Gun, Seed Mine, Spine Vine,
    Grass Tornado, Solar Blast
  - Ground: Rock Lance, Stone Cannon, Sand Tornado, Earth Impact
  - Dark: Shadow Burst, Dark Ball, Dark Laser, Poison Fog, Spirit Flame
  - Dragon: Dragon Meteor, Dragon Cannon, Dragon Burst, Dragon Breath
  - Neutral: Air Cannon, Power Shot, Power Bomb

  **Clicking at the sides** sets off a
  random one right where you click, one for every click, up to 40 at once;
  clicks on the paltop boxes work as always.
  `paltopFx.play("solarBlast")` in the browser console shows one
  (`paltopFx.names` lists them). Nothing is drawn between effects or when the
  window is too narrow to show them, and they are never on for browsers
  set to reduce motion
- **chat** (the light blue button in the bottom right corner, or `c`; drag
  the grip in the chat's top left corner to make it bigger or smaller, and the
  size is kept in the browser): the in-game chat and this page
  in one. What players write in the game comes from the server's log (the
  collector follows it and keeps the messages in the database, table
  `chat`); what is written here goes to the game with the REST API's
  announce and shows there as "[web] Name: message". Logged-in users only,
  one line of at most 200 characters, at most 5 messages a minute per user
  and 20 for everyone (`core/palchat.py`). New messages show as a count on
  the button, in the tab's title and as a short note in the corner while the
  chat is closed, and a short chime plays for each new message from someone
  else (a little different for the game and the page); the bell in the chat
  window mutes it, and that is kept in the browser. This is the only thing the
  page ever sends to the server.
- quests (`q`): every quest and whether the picked player (you, to begin
  with) has done it, read from their save (CompletedQuestArray_FullRelease
  and OrderedQuestArray_FullRelease): a check mark and grey text when it is
  done, "in progress" when they have taken it on, and how many others on the
  server have done it. The story is in the order the game hands it out (the
  quests each quest's blueprint names next, from the 1.0 start); the
  tutorial chains from before 1.0 that are still in the game data are left
  out unless someone here has done one. Side quests are grouped by who gives
  them. Search, "hide done", and a progress bar per section and group
  (`core/palquests.py`).
- battles (`a`) has **auto** (battles that play by themselves) and
  **manual**: pick one, two or three of your own Pals (with their stars and
  whether they are awakened), and paltop finds another player's Pals to
  match them, as evenly as it can. You see two of their three in 3 v 3, one
  of two in 2 v 2 and none in 1 v 1; a hidden Pal is a dark silhouette until
  it acts or is hit. On your Pals' turns you pick one of the skills they have
  equipped (a skill still cooling down cannot be used) and, for a
  single-target skill, which foe; the other side is the computer. Frozen,
  stunned or dizzy Pals lose their turn. The same rules as the auto battles
  (`core/palbattle.py`, `Fight`), one turn at a time on the server
  (`core/palduel.py`), which never sends a hidden Pal's name, level or HP.
- **music** (on the battles tab): an original chiptune battle loop in the
  style of the old handheld battle themes, made in the browser with the Web
  Audio API (`web/static/music.js`, no sound files): two pulse waves, a
  triangle bass and noise drums, 160 BPM in A minor. After a short intro it
  loops six parts of 8 bars (about 72 seconds), quieter and busier by turns:
  the theme, a tense part, a calm part with long notes and only hats, a
  build-up, the theme at full strength, and a brighter bridge in C major. The
  melody moves mostly by step in longer notes. A short fanfare or a sad tune
  plays when you win or lose a duel. It plays only while the battles tab
  is open and the window has focus; the switch and volume are kept in the
  browser. It is on unless you turn it off; browsers only allow sound after
  a click on the page, so it starts with the first one.
- **sounds** (on the battles tab, next to music; the same volume): a sound
  for every skill in the battles window. The 42 skills with an animation of
  their own have a sound of their own too (Lightning Bolt, Dragon Meteor,
  Diamond Rain, Seed Machine Gun, ...); every other skill gets its element's
  sound - Fire crackles and whooshes, Water bubbles, Electric buzzes, Ice
  rings, Grass rustles, Ground thuds, Dark and Dragon growl - varied by the
  skill's name (pitch, length, how many, an extra layer), so most skills
  sound different and each the same every time. Hits, critical hits, super
  effective and not very effective hits, misses, statuses and a Pal fainting
  have their own sounds. On unless turned off; only in the battles window.
- traitpedia (`t`): every trait (passive skill) a Pal can have - 115 of
  them, from the game's data: its tier (I-III, rainbow, World Tree, or bad),
  what it does and the game's description, what it is good for, and where to
  get it: random on any Pal, born with a species (Legend on Frostallion, ...),
  only lucky Pals, or the Pal Surgery Table with its price in gold and the
  implant it needs - an Implant is a key item you keep, a Disposable Implant
  is used up, and the Ancient Relic Recycler gives those (the chance per roll
  is shown). It also shows how many Pals each player has with it, and yours.
  Search, sort (tier, name, surgery price, most on the server, most of mine)
  and filters (good/bad, random, surgery, disposable implant, born with a
  species, on the server, mine or not mine, good for attack, work, ...).
- palpedia (`p`): every Pal in the game, one entry per Paldeck number, a
  better Paldeck than the game's own. Search by name, number, element, work
  or partner skill (see include active skills below); sort by Paldeck
  number, name, rarity,
  breeding power or **any team builder goal** (attack, tank, fights against
  an element, the status goals, each kind of base work, ... - scored among
  all species at level 60 with perfect IVs); show all, found on the server,
  not found yet, caught by me or not caught by me yet; and filter by element
  or base job. Each Pal shows its base stats, work, where it ranks for each
  goal, its partner skill, every skill it learns and at what level (with the
  status it builds up), its breeding power and the parent pairs that give it
  (special pairs first, then pairs the server has found), and who on the
  server has caught it, has it in their Paldeck and owns one now. "Found" is
  the players' own records from Players/<guid>.sav (the in-game Paldeck and
  capture counts) plus the Pals in the world. **Spoilers** (off by default,
  remembered in the browser): with it off, a Pal nobody on the server has
  found shows only its number and "???", in the list, in breeding pairs and
  in its details. **Compare**: "compare others with this" on a Pal makes it
  the one everything is measured against (remembered in the browser; "stop
  comparing" ends it). The list then gets a "vs <Pal>" column: each Pal's
  score against it in % for the goal the list is sorted by, or as an
  all-round fighter when the sort is not a goal. Every other Pal's details
  get a "compared with" section: base stats and work levels side by side
  with the difference, both partner skills side by side (what only one of
  them does is marked), the goals it is better and worse at (score as % of
  the best Pal and its place, and the % difference; "only <Pal>" when just
  one of them can do it, i.e. the other scores under 2% of the best), and every goal behind "every goal"
- **partner skill scaling**: every number in a partner skill's text that
  grows with the condenser is shown as its whole scale, for 0/1/2/3/4
  condenser stars ("by 50/53/56/60/65%", in green), in the Palpedia, when comparing,
  and in a Pal's details, where that Pal's own number is highlighted (in
  the terminal it is in [brackets]); numbers that do not change stay single.
  The values are the game's own, per rank, from DT_PartnerSkillParameter
- **rich partner skill texts** (web): the partner skill's name in
  turquoise, elements in their colour ("Ground" in brown), statuses in the
  colour of what causes them, and items a Pal gives in yellow - hover one
  for what it is: its name, kind, the game's own description, price and
  weight (DT_ItemDataTable and its texts)
- **Paldeck race** (a button in the Palpedia): how many Pals each player has
  found (their in-game Paldeck and everything they caught), how many they
  found first, how many only they have, how many they have caught and their
  latest find, with medals, and a feed of the latest finds on the server;
  each Pal's details say who found it first. The Paldeck keeps no dates, so
  firsts from before paltop started keeping track are estimated from the
  oldest Pal of that kind each player still owns (marked "estimated"); since
  then the collector notes each new find when the save shows it (table
  `paldeck_finds`)
- **include active skills** (next to every search box, off by default and
  remembered in the browser): when on, a search also matches the active
  skills a Pal has or learns and the statuses they apply, in any wording
  (Burn/Burned, Soak/Soaked/wet, Blind/Blinded/darkness, Ivy-Covered/vines,
  Electrify/shock, ...) - so "burn" finds every Pal with a skill that burns,
  also Lamball, whose Implode does. When off, only names, elements, work,
  passives and partner skills are searched
- battles (`a`): a window in the middle of the screen with its own battles,
  the same as the background's but big, laid out like the old games (the
  one side low on the left with its cards top left, the other high on
  the right with its cards bottom right, the text box along the bottom),
  with buttons for random, 1 v 1, 2 v 2 or 3 v 3, **pause** / **resume** (the
  fight, its animations and the time to the next turn all stand still) and
  "new battle", and a log
  of what happened, newest first. The background's battles pause while it
  is open
- achievements (`h`): 25 achievements with bronze, silver and gold tiers
  (1-3 points each, 75 in all; `palachieve.py`): a points table, each
  player's cards with the number behind it and how far to the next tier,
  the latest unlocks and the server's firsts (first to a level, from the
  level history). The numbers come from the players' own in-game records in
  Players/<guid>.sav (tower and field bosses, raids, predators, catches,
  effigies, fast travel points, areas, dungeons, oil rigs, fish, camps,
  treasure, arena, condensing), from their Pals (lucky, alpha, perfect IVs,
  four condenser stars, four top-rank passives) and from paltop's history
  (level, time played, the Paldeck race). The collector notes when each tier
  is reached (table `achievements`); tiers players had before paltop started
  keeping track have no date
- bases: base camps and the workers who need attention
- crashes: the crash log

**What it cannot do** - by design it is read-only and knows less than paltop:

- it has no admin actions at all. The only REST API call it makes is the
  chat's announce, which shows a logged-in player's message in the game
  (rate-limited, one line); for that it reads the admin password from the
  container's environment, as the collector does, and never shows or logs it
- live data comes from `state/web.json`, which the background collector writes
  every 30 s through an allow-list: no IP addresses, Steam/Xbox ids, account
  names, admin commands or alerts that mention admin tools
- Pal data comes from the save file, read the same way paltop reads it
- player names and nicknames are escaped before they reach the page, and the
  page is served with a strict Content-Security-Policy, `X-Frame-Options:
  DENY`, `no-store` caching and an HttpOnly, SameSite=Strict, Secure cookie

**HTTPS** uses a self-signed certificate made on first start
(`state/web/cert.pem`, valid 10 years), so the password is never sent in plain
text. Browsers show a warning the first time ("not private" / "potential
security risk"); after accepting it once the page works normally. Plain HTTP is
not served.

**Reaching it from outside** needs one change on the router: forward **TCP
port 8765** to the machine running paltop, the same way UDP 8211 is
forwarded for the game. Players then open `https://<the address they join the
game with>:8765`. (`./install.sh --web --port N` picks another port; many
routers keep 8443 for their own admin page.)

`--port`, `--bind` and `--http` (testing only) change how it listens.
`--test-no-login` exists for looking at the page during development; it
refuses to run on all interfaces or on port 8765.

