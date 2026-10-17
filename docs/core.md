# Paltop core

What the terminal and the web page share: reading the server, the save files
and the game's data, the history database and the background collector
(`core/palcollect.py`, the `paltop-collector` service), and the Pal logic
(roles, team builder, breeding, battles, bosses, Palpedia, achievements).

## Safety

Apart from the admin menu described above - which is the only thing in paltop
that changes anything, and is confined to `paladmin.py` - everything here is
read-only by construction:

* **REST API** - HTTP `GET` only, against an allow-list of four informational
  endpoints (`/info`, `/players`, `/metrics`, `/settings`). `palsource.Rest.get()`
  raises on any path outside that list, so the mutating endpoints
  (`/announce`, `/kick`, `/ban`, `/save`, `/shutdown`, `/stop`) cannot be
  reached even by accident.
* **Docker** - only `inspect`, `logs`, and `exec cat` of the crontab file.
* **Filesystem** - stat/read of the save and backup directories, `/proc`, `/sys`.
  The `.sav` files are never opened or parsed, only sized.

The game pak is opened read-only; `palpak.py` decompresses individual entries in memory - nothing is
ever extracted to disk. Save files are opened `'rb'` and never written back.
Inside this folder paltop writes `state/paltop.db`, the cached lookup tables
(`elements.json`, `palnames.json`, `conditions.json`) and,
if you use the admin menu, `state/admin.log`. The
one outbound network request is the optional Steam build check described
below, which sends only the app id and is disabled by `--no-net`.

## Reading the save files

`Level.sav` and the player saves are GVAS wrapped in Oodle Kraken compression.
Oodle is proprietary and the game's own copy cannot be borrowed - it is
statically linked into `PalServer-Linux-Shipping` with the public API
unexported, and the binary is non-PIE so it cannot be loaded either.

paltop therefore uses a locally built decompressor from
[zao/ooz](https://github.com/zao/ooz), an open-source reimplementation
of Kraken/Mermaid/Selkie/Leviathan. ooz's main decompressor file (`kraken.cpp`) is GPLv3-or-later,
the other files carry no license notice and the repository has no license
file, so it is not included: `core/oodle/build.sh` (which `install.sh` runs) downloads a fixed
commit of it and builds only the decompressor half into
`core/oodle/libooz.so`. The script documents the two adjustments needed (the
source's own `OOZ_BUILD_DLL` guard drops its Windows-only CLI, and
`simde_compat.h` maps the `simde` submodule's names onto native SSE2, which is
a one-to-one rename on x86_64). Decompressing the 47 MB `Level.sav` takes about
0.04 s, and `palsave.py` re-reads it only when it changes on disk.

Paltop never ships ooz or `libooz.so` (`tools/package.sh` strips both from
release archives). Anyone who distributes Paltop *together with* a built
`libooz.so` has to do so under the GPLv3, which Paltop's MIT license allows.
None of this affects running it on your own server.

What this unlocks:

* **Base camps** - names, world positions and owning guild. Press `b` for an
  overview of how each one is doing: where it is, how many structures are
  built there, how many worker Pals are assigned against the base's capacity, and its most-built structures. Structure counts
  come from `MapObjectSaveData`, where every built object records the base it
  belongs to (world scenery carries an all-zero base id and is skipped);
  worker numbers come from the base's `WorkerDirector`, which names the Pal
  container the base draws its workers from.
* **Worker problems** - sickness, starvation, low sanity and slacking off, per
  base and per Pal, with the worst cases also raised as alerts on the
  dashboard. UE only serialises properties that differ from their defaults, so
  a healthy, well-fed Pal has no `WorkerSick`, `HungerType` or `PhysicalHealth`
  field at all - the presence of one *is* the problem signal. Sanity comes from
  `SanityValue` (only written when below full) and slacking from
  `BaseCampWorkerEventType`.
  All four on this server decode onto forest cells, which independently
  confirms both the east/north axis mapping and the 256 m cell size.
* **Player levels for everyone**, online or not, straight from
  `CharacterSaveParameterMap`. Offline players no longer show `--`, and
  players who have not connected since paltop started appear at all.
* **Pals** - every creature on the server with its species, level, gender and
  IVs, grouped by owner. Press `l` to list each player's best Pals in a single
  column under their name, up to `PALS_PER_PLAYER` (24) each. A player and
  their Pals form one block: blocks stack down a column, and a block that will
  not fit before the bottom starts a new column rather than being split. If
  that would need more columns than the width allows, fewer Pals are shown per
  player until it fits.

Species names are shown without their `BOSS_` prefix (an alpha, marked with a
leading alpha sign) and without the trailing `_Fire` / `_Dark` style variant
suffix, since the element is conveyed by colour instead. IVs are highlighted
at 80+ and again at a perfect 100.

### Pal names and element colours

Species are shown with their **in-game display names**, not the internal asset
ids the save files use: `Kitsunebi` is Foxparks, `KingBahamut` is Blazamut,
`AmaterasuWolf_Dark` is Kitsun Noct. These come from
`DT_PalNameText_Common` in the pak's English localisation folder, where each
row is a `TextProperty` - four flag bytes, a history-type byte, then namespace,
key and source text. 322 species resolve, covering all but three of the 2231
characters on this server; the stragglers are captured humans rather than Pals.

The save and the text table do not always agree on capitalisation (`Sheepball`
vs `SheepBall`, `GhostAnglerFish` vs `GhostAnglerfish`) and boss forms appear
with both `BOSS_` and `Boss_`, so lookups are case-insensitive and fall back
through the id's variants. Illness names come from the same source - the shared
UI text table's `COMMON_CONDITION_NAME_*` rows, which turn `GastricUlcer` into
"Ulcer" and `DepressionSprain` into "Depressed".

### One player's Pals

In the pals overlay (`l`), `↑` `↓` picks a player and `Enter` opens **all** of
their Pals (the overview only shows the best of each). `Esc` goes back.

The list shows each Pal's name (element colour; nicknames where given), `✦` for
lucky and `α` for alpha, level, gender, IVs, condenser stars and where it is -
`team`, `box` (Palbox) or `base1`..`base5`. `o` sorts by best (lucky, stars,
good passives, IVs), level, IVs, name or place; `f` shows all, only the team,
the Palbox, the bases, or only lucky and alpha Pals. `↑` `↓` `PgUp` `PgDn`
`Home` `End` move through it.

Next to the list (below it on a narrow screen), everything known about the
selected Pal:

- elements, level, gender, Paldeck number, current HP, trust, where it is
  (team slot, Palbox page, which base)
- IV bars, condenser stars and soul upgrade levels. All of them count in the
  Pal's stats wherever paltop scores or fights with it (team builder, boss
  planner, battles; `palteam.stats`): level and IVs, then its traits, +5% HP,
  attack and defense per condenser star, +3% of a stat per soul level at the
  Statue of Power (20 levels, at most +60%), and x1.1 for an awakened Pal (`bIsAwakening` in the save;
  the 1.1 is the game's `AwakeningStatusMultiply`). The star and soul numbers
  are not in the game's data tables, so they are the values the game shows
  (`STAR_BONUS`, `SOUL_BONUS`, `SOUL_MAX`). Battles use the skills the Pal has equipped,
  status-only ones such as Poison Fog too.
- passives with their in-game names and effects, coloured by rank (orange for
  the top rank such as Lucky and Legend, blue for rank 3, red for negative
  ones), and the attack/defense/work/move bonus they add up to
- equipped active skills with power, cooldown and element, plus the others it
  knows
- partner skill, work suitabilities, sickness or hunger, when it was caught
- **breeding**: what it gives with each of the owner's other Pals of the
  opposite gender, one line per child, rarest first, with the best partner for
  that child

All names and numbers come from the game's own data tables in the pak
(`DT_PassiveSkill_Main`, `DT_WazaDataTable`, `DT_PalMonsterParameter`,
`DT_PalCombiUnique`, `DT_SkillNameText_Common`), cached in
`state/gamedata.json` and rebuilt when the game updates. Breeding uses the
game's rule: the child is the species whose CombiRank is closest to
`(a + b + 1) // 2`, a tie goes to the higher CombiRank, the special pairs in
`DT_PalCombiUnique` come first, and the same species gives itself. Checked
against version 1.0: Lamball + Cattiva gives Daedream, Chikipi + Lamball gives
Teafant, Relaxaurus + Sparkit gives Relaxaurus Lux. Which container is the team
and which the Palbox is read from each player's `Players/<id>.sav`.

### Elements, partner skills, passives and search

- **Elements** are shown as coloured letters, both of them for Pals with two:
  N Neutral, F Fire, W Water, Gr Grass, E Electric, I Ice, Gd Ground, Dk Dark,
  Dr Dragon - so Jormuntide Ignis is **DrF**. (Dragon/Dark and Grass/Ground
  share a first letter, hence the two-letter ones.)
- **Partner skills** are shown with the game's own description, with the
  numbers for the Pal's condenser level filled in from
  `DT_PartnerSkillParameter` (checked against the wiki: Eidrolon Ignis gives
  +2% per Dragon/Fire teammate at level 1, up to +6%), and short labels in the
  list: fly, ride, swim, team+ (buffs teammates), synergy (grows with
  teammates of an element), speed (the same, but only movement speed),
  player+, heal, carry.
- **Passives**: on the web page, hover a passive's name for the game's own
  description (made from its effects when the game has none).
- **Search** (`/` in the terminal, the search box on the web page) filters a
  player's Pals by name, nickname, species, element, passive or partner skill -
  "legend", "fire", "lucky". The team builder's candidate list has its own
  search.

**Buffs for the Pal fighting beside you, and healers.** Blazamut (+5-10%
attack and defense per kill, up to 5 stacks), Orserk (+1-5% per bullet hit, up
to 30 stacks) and Celesdir Noct (+40-80% attack, but the Pal slowly loses
Health) help whichever Pal is out, so they count for every member of the
team, and the buffer gets credit for what it gives the others. Stacking buffs
are counted at a third of their full stacks (Blazamut 2 of 5, Orserk 10 of
30); the team builder says so and shows the full value too. Different
beside-you buffs all apply at once; "does not stack" means the same skill
twice counts once, so of two Pals with the same one only the strongest
(most condenser stars) counts. Celesdir Noct's
buff is paid for in Health: the other Pal out with you (never Celesdir Noct
itself, which neither loses Health nor gets the buff) keeps losing it all
fight, and
the game data has no rate for it, so it counts as 55% less HP and, for the
attack goal, 30% less attack. What makes up for it is weighed for each Pal
that gets the buff, as a whole: a party healer in the team takes over half of
the drain away, the Pal's own life steal (Vampiric, Lifesteal, Immortality)
takes more away the harder it hits - and the buff makes it hit harder - and
faster HP regeneration (Idiosyncratic, Immortality) some more
(`DRAIN_*`, `LIFESTEAL_OFFSET`, `REGEN_OFFSET` in `palteam.py`). So
Celesdir Noct is worth taking with a healer, with Vampiric Pals or at high
condenser ranks, and not otherwise. Pals with such a skill are tagged
"drains HP", the team builder warns about it, and for a teammate without life
steal it says what Vampiric would be worth to it. Of several party heals only
the strongest counts. Party healers
(Lyleen, Lyleen Noct: 80-95% of the party's Health) count as a quarter of the
heal in extra HP for every member, for tank, fighter and fights against an
element. All numbers are the partner skill's own at the Pal's condenser
level.

**Team synergy overview.** Under the team, for fighting goals:

- how much stronger the team's partner skills make it together ("partner
  skills make this team +20% stronger": the team's total score with its
  synergy against the same five without)
- every link, who gives what to whom: "Orserk -> the whole team: +10% attack
  and defense to the Pal fighting beside you", "Aegidron, Orserk, Moldron ->
  Eidrolon Ignis: +6% attack each"
- skills in the team that do nothing: a second beside-you buff or healer
  (they do not stack), a booster with no teammate of its element, a Pal whose
  skill needs teammates it does not have
- swaps from the owner's other Pals that would add most synergy, with the
  gain and why ("slot 4: Blazamut Ryu -> Orserk, +6%"); **use** (web) or `u`
  (terminal, the best one) makes exactly that team, with the other slots
  locked, so you can keep improving it one swap at a time

**Synergy in the team builder.** For fighting goals, partner skills that work
together count: a Pal whose skill grows with teammates of some element
(Eidrolon Ignis with Dragon/Fire teammates, Magmoth with Fire/Ground), a Pal
whose element a teammate's skill boosts (Fire Pals with Flambelle in the
team), a skill that buffs the Pal fighting beside you (Blazamut), and skills
that call a named teammate (Necromus and Paladius). Only attack and defense
count - a skill that only adds movement speed does not make a team fight
better. The team builder says what each slot gets ("its partner skill: +6%
attack for each of 4 Dragon/Fire teammates").

**Status goals.** "status: Burn", "Soak (wet)", "Electrify (shock)",
"Freeze", "Ivy-Covered (vines)", "Muddy", "Blind (darkness)", "Poison" and
"Stun" build a team that puts that status on its targets. The game's names
and its own help text for each status are shown. A Pal scores by how fast
it builds the status up: every active skill that applies it
(`EffectType`/`EffectValue` in DT_WazaDataTable) counts as build-up per second
of cooldown, over the skills it has equipped *or learned* (at most three, as
many as a Pal can equip), times how well it fights, since a status is only
worth as much as the Pal applying it survives and hits. The team builder says
which skills do it ("Aqua Surge: Soak 100 every 24s"), when a learned skill
should be equipped ("(learned - equip it)"), and what the Pal learns later
("learns Geyser Gush at lv 70", from DT_WazaMasterLevel). "as if levelled"
counts the skills it would have learned by then. Where the game says the
status makes foes take more damage from an element (Soaked foes from
Electric, Ivy-Covered from Fire, Electrified from Water, Muddy from Grass),
Pals with strong skills of that element count too, so the team mixes Pals
that apply the status with Pals that cash in on it. A partner skill that
raises the whole party's damage to afflicted foes (Umihebi, Needoll,
Jormuntide Ignis, ...) does not stack, so one such Pal is swapped in when it
makes the team better. Under the team: who applies it, who hits for the
combo, the booster, and which foes are immune (Fire and Water Pals cannot be
Burned).

**Most synergy goal.** "most synergy (buffs between Pals)" builds the team
for the most buffs between the Pals instead of the strongest five on their
own. It searches many teams (a beam search over each species' best Pal, plus
every Pal with a skill that helps teammates), one Pal per species, and weighs
synergy twice, so a team full of buffs wins over a slightly stronger team
without them. Locked slots are kept. The overview says how much this costs:
"built for buffs: the strongest fighter team is +6% stronger in total".

It also finds **pairs** that are good together: what two Pals gain side by
side compared with each of them alone ("Eidrolon Ignis + Quivern +19%").
The overview lists the best pairs in the team and, below them, other good
pairs the owner has ("also good together: Moldron + Dumud +21%"). On the web
page, **try together** locks the pair in slots 1 and 2 and builds the rest of
the team around them.

### Passive planner

For any Pal: `e` in the terminal list, or **plan passives** in its details on
the web page. Pick a goal - the same ones as the team builder (strongest
attack, defense, tank, all-round fighter, a fight against each element, each
kind of base work) plus **breeding farm** (breeding and egg hatching speed) -
and then:

- see the score and stats (HP, attack, defense, work speed) with its own
  passives, with the planned set, and with the best possible set
- change any of the four slots (`Enter` / **change**): the list shows every
  passive worth offering, best first, with how much it changes the score, what
  it does, and where it comes from - **random** (can appear on any Pal),
  **surgery** (can be implanted at the Pal surgery table), **on your server**
  (a Pal here has it, so it can be bred over) - and can be searched (`/` / the
  search box)
- see what each planned passive adds on its own, and clear slots (`x` /
  **clear**)
- take the best set of four for the goal (`b` / **use the best set**), worked
  out one passive at a time from what adds most, keeping what the Pal already
  has when that is best (Lucky cannot be added to a Pal, but is kept on one
  that has it)
- see where to get each passive it does not have: the surgery table's price
  and implant, and the owner's own Pals that carry it - same species first,
  since passives are passed on by breeding

Only effects on the Pal itself count: passives such as Vanguard or Mine
Foreman that help the *player* are shown as "+10% player attack" and add
nothing to the Pal's score. Cooldown, stamina and similar effects are shown but
not scored.

### Breeding helper

`g` in a player's Pals (terminal) or **breeding helper** (web page). The
things that change what comes out of breeding - not just how fast - are
partner skills, found in the game data rather than listed by hand:

| Role | Pal (today) | Where it works |
|---|---|---|
| alpha eggs: chance an egg you pick up becomes an Alpha egg | Broncherry, Broncherry Aqua | in your party when you pick up eggs |
| extra eggs: chance of one extra egg | Grintale | in your party when you pick up eggs |
| egg production at the breeding farm | Braloha | anywhere in the base |
| incubation time | Dynamoff | anywhere in the base |

None of them stack, so for each role the helper shows the player's best one
(most condenser stars) with what it gives now and at four stars, marks the
others as spares with a tip to condense them into it, and for what the player
lacks - or a better version, like Broncherry Aqua over Broncherry - shows
pairs of their own Pals that breed it, special parent pairs from
`DT_PalCombiUnique`, and which other players on the server have one. Last, the
parents with **Philanthropist** (twice the breeding speed at the farm) or
**Babysitter**.

### Boss planner

**Boss planner** in the Pals tab (web page). The bosses come from the game
data: the tower bosses (their GYM_ Pals), the raids (RAID_ Pals) and the 104
field bosses from `DT_PalWildSpawner` with their spawn level and region. Each
one shows its elements, what it is weak to and resists, its skills (a tower
or raid boss's whole list - hard mode Bastigor has Water and Dark skills
besides its Ice ones - and a field boss's best three by its level), the
statuses it can inflict, and which players have beaten it (from
TowerBossDefeatFlag, NormalBossDefeatFlag and RaidBossDefeatCount in the
player records). Like the quest list, the bosses the picked player has
beaten have a green check mark and grey text, those only someone else on
the server has beaten a blue one, with who has beaten each under its
name; where it is shows when you pick it. "not beaten yet" narrows the list
to what is left.

For the picked player's Pals it works out, with the battle model's damage
formula (`palbattle`), the damage each turn with the equipped skills, how many
turns the Pal lasts against the boss's skills, and the share of the boss's HP
it takes on its own. The best ten are also fought out 40 times one against the
boss for a win %, the best five are suggested as a team, and a learned skill
that would hit harder than the equipped ones is pointed out. The game's data
does not have the bosses' extra HP or tower and raid levels, so the level
(towers and raids start at the player's level) and an HP multiplier are
picked on the page. Hard mode towers and raids are listed too ("hard mode" in
the filter): they are the game's own _2 forms of the boss Pals, with other and
stronger skills, and hard mode tower wins come from TowerBossDefeatCount,
which keeps `<tower>_Normal` and `<tower>_Hard` apart. The logic lives in
`palboss.py`.

### Passives in the team builder

The team builder can count the Pals' passives three ways (`p` in the terminal,
the **passives** buttons on the web page):

- **as they are** - the passives each Pal has
- **with my plans** - Pals you have made a plan for in the passive planner
  count with the planned passives
- **with the best passives** - every Pal counts as if it had its best set of
  four for the goal. This shows which Pals are worth investing in: ones with
  great species stats and IVs but poor passives today come out on top. (Worked
  out for a shortlist of the 40 best by species, level and IVs, plus the 20 best
  as they are.)

Each slot shows the passives it is counted with - those it does not have yet
marked `*` - and under the team, **to get there** lists every passive the team
still needs, for which Pals, and where to get it (surgery table or which of the
player's Pals to breed from). `e` (terminal) or **plan...** (web) on a slot
opens the passive planner for that Pal with the team's goal, and going back
returns to the team.

Plans are remembered: in the terminal in `state/plans.json`, on the web page in
the viewer's own browser (each player keeps their own).

### Best Pal for the job, and the team builder

Inside a player's Pals, `o` also sorts by **role**: attack, defense, tank
(HP x defense), all-round fighter and work. The list then shows a score (100 =
the player's best), and the detail says where the selected Pal ranks, why, and
which of its traits help or hurt *for that role* - Musclehead helps an
attacker, a SAN-drain trait hurts a base worker, a perfect 100/100/100 Pal can
still be let down by its traits.

`t` opens the **team builder**: the best team of five for a goal, from that
player's own Pals.

| Key | |
|---|---|
| `←` `→` | goal: strongest attack, best defense, tank, all-round fighter, fight vs each element, fastest worker, base work of each kind |
| `↑` `↓` | pick a slot |
| `Enter` | open the list of candidates for that slot; `↑` `↓` and `Enter` to pick one, `Esc` to cancel |
| `l` | lock the Pal in that slot, or unlock it |
| `r` | back to the suggested team (drops all choices and locks) |
| `n` | score at the Pals' current level, or as if all were levelled to the player's level (to find breeding stock worth levelling) |
| `Tab` | next player |
| `Esc` / `t` | back to the list |

paltop fills the five slots with the best Pals for the goal. A Pal you pick
for a slot, or lock there, stays put (marked `*` in the terminal, a padlock on
the web page), and the other slots are refilled around it with the best of
the rest - one of each species, and for attack and all-round goals leaning
towards elements the team does not have yet.

On the web page the team starts empty and holds only the Pals you pick:
**choose** on a slot lists the candidates, sortable by the goal's score, HP,
attack, defense, HP x defense, work speed, level, IVs or condenser stars,
each with how much better or worse it is (in %) than the Pal in the slot now -
the % is shown only for what the list is sorted by (both HP and defense for
HP x defense; none for IVs and condenser stars), in the list and in compare.
Each candidate has a **choose** button and a **compare** button; compare opens
a window with the two side by side (stats, the scores for the goal and the
main roles, weaknesses, passives, skills, partner skill with its star scale,
work) with the % difference for the sorted value, and a button to put either in the slot. For an
empty slot, compare one Pal and then another. **fill empty slots with the
best** fills the rest the way the terminal does, changing the goal keeps the
team, and **empty the team** clears it.

Pals that are awkward in a team are left out of it, the candidate lists and
the "ideal" line, and the team builder says which were left out and why - for
now only Panthalus, which is huge and can only be summoned in a few places. A
toggle (`i` in the terminal, a button on the web page) includes them anyway.
The list is `HARD_TO_SUMMON` in `palteam.py`: the game data has no field for
where a Pal can be summoned, so add a species there by hand.

The candidate list for a slot shows the player's best Pals for the goal that
are not already in the team, each with its score, why it scores that way, and
what it would do to the team: elements gained and lost, the team's weak spots
with it in, and "same species as slot N" if the team already has one.

For the selected slot it also shows why that Pal was picked, its good and bad
traits for the goal, and hints ("great IVs held back by its traits - breed it
with a Pal that has good ones"). Under the team: its elements,
its weak spot (e.g. "3 of 5 weak to Water"), and the strongest species in the
whole game for the goal with perfect IVs, marked *owned* when the player has
one. The team takes one of each species, and for attack and all-round fights
leans towards covering more elements.

How the score is made (`palteam.py`):

- stats are estimated from the species' base values, level and IVs with the
  commonly cited formulas (an IV of 100 adds 30% of the species' part), then
  the traits' own percentages from the game data are applied
- a fight vs an element also looks at the Pal's equipped skills (their element
  against the target, doubled when super effective, plus element-damage
  traits) and whether the Pal itself is weak to or resists that element
- work goals use the work suitability level (plus traits that add levels),
  work speed, and count SAN-drain and hunger traits against it
- condenser stars and soul upgrades are shown but not counted, since their
  exact effect is not in the data paltop reads

The numbers are estimates meant for comparing Pals with each other, not the
game's exact stats.

### Where element colours come from

Elements are not in the save - they are in the game's own
`DT_PalMonsterParameter` data table, inside the Oodle-compressed pak. With the
decompressor already built for the saves, `palpak.py` reads that entry out of
the pak, parses the package name table and walks the data table's rows,
yielding `ElementType1`/`ElementType2` for **727 species** including elemental
variants and boss forms. Not every boss form has its own row (`BOSS_Anubis`
does not, `Anubis` does), so the lookup falls back through the id's variants.
The table is cached in `state/elements.json`.

A player's `PlayerUId` in the save is a UE `FGuid`: four little-endian
uint32s. Reversing each 4-byte group gives exactly the id the REST API prints,
which is how save data is joined to live data.

Parsing all of it (2200+ characters) takes about 0.2 s, and only runs when
`Level.sav` changes on disk.

Everything stays read-only: saves are opened `'rb'` and nothing is written back
to the game's save directory.

## History and the background collector

All history lives in one SQLite database, `state/paltop.db`:

| Table | Contents | Kept |
|---|---|---|
| `samples` | server metrics (players, fps, cpu, memory, ...) once a minute | 60 days |
| `positions` | each online player's position once a minute | 60 days |
| `events` | joins, leaves, admin commands | 24 months |
| `status_log` | server status changes (ok / hung / down), container starts, host boots, autoheal restarts | 24 months |
| `crashes` | the game's crash reports (the game deletes its own after 2 days) | always |
| `levels` | when each player first reached each level | always |
| `players` | each player's last known name, level, position | one row each |
| `backups` | backup archives seen | last 500 |
| `meta` | small settings, collector heartbeat | - |

The file is capped at **50 MB**. Retention runs every hour; if the database is
still over the cap, the oldest day of positions and then of samples is deleted
until it fits (events and the last 24 hours are never cut for size). Freed
space goes back to the disk, so the file shrinks too. Expect around 0.1-0.2 MB
per day. The server panel shows the size against the cap (`db 272 KiB/50 MiB`), and `d`
opens the database window: size against the cap, file and write-ahead log,
retention, the last clean-up, the collector's heartbeat, and per table its rows
and oldest and newest entries.
Retention and the cap are constants at the top of `palstore.py`.

It can be queried directly, read-only so nothing is disturbed (needs the
`sqlite3` package: `sudo apt install sqlite3`):

```bash
sqlite3 -readonly state/paltop.db "SELECT datetime(ts,'unixepoch','localtime'), kind, name FROM events ORDER BY ts DESC LIMIT 20"
```


### The collector

`paltop-collector` is a systemd user service (set up by `install.sh`) that
runs `core/palcollect.py`: the same data gathering as the terminal, with no
user interface, every 30 seconds, so history is
recorded whether or not anyone has paltop open. Only one collector can run at
a time. It logs only when the server state changes, to the journal:

```bash
systemctl --user status paltop-collector
journalctl --user -u paltop-collector -f
```

With linger enabled for the user (`sudo loginctl enable-linger $USER`) it
keeps running after logout and starts at boot. It also writes
`state/web.json`, the snapshot the web page shows, and follows the server's
log (`docker logs -f`) for the in-game chat, keeping each `[CHAT]` line in
the `chat` table for the web page's chat (`core/palchat.py`). The terminal's footer shows
`collector ok`, and raises an alert if the collector's heartbeat stops.
`paltop --collect` still works and does the same.

## Other things it surfaces

* **Settings diff** - `s` shows only the settings that differ from the shipped
  `DefaultPalWorldSettings.ini` instead of all 118. Press
  `a` for the full list.
* **Admin command audit** - `c` lists every admin command run in-game, who ran
  it and when.
* **Failed joins** - a client that connects but never completes a join means a
  rejected password, a failed load or a crash. These are flagged in the event
  log as `never joined`.
* **Backup integrity** - the newest archive is verified with `gzip -t`, and you
  are warned if it is more than 30% smaller than the previous one.
* **Update check** - compares the installed build id from the Steam app
  manifest against the current public build. Sends only the app id to a public
  Steam metadata mirror; `--no-net` disables it entirely.

### Where the data comes from

Online players come from the REST API, which only reports who is connected
right now. Offline players are reconstructed from `docker logs` (join/leave
events, up to 14 days back) for names and playtime, and from
`state/players.json` for last-known level - paltop records each player's level
while they are online so it can still show it after they log off. A player who
has not been online since you first ran paltop shows `--` for level until they
next connect.

Log timestamps are UTC (the container runs `TZ=UTC`); paltop converts
everything to local time for display.

