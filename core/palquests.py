#!/usr/bin/env python3
"""
palquests.py - the quest list: which quests each player has done.

The quests come from the game data (palpak: DT_PalQuestData, each quest's
blueprint for its title, description and the quests it hands out next), and
what each player has done and taken on from their save
(Players/<guid>.sav: CompletedQuestArray_FullRelease and
OrderedQuestArray_FullRelease, see palsave).

    story   the main quests in the order the game hands them out, from
            STORY_ROOT. The data still has the tutorial chains from before
            1.0; they are left out unless a player on the server has done
            one of them. Main quests nothing leads to (the last World Tree
            fights) come at the end.
    side    the side quests, grouped by who gives them
"""

from __future__ import annotations

import re

STORY_ROOT = "Main_UnlockFastTravel"      # where the 1.0 story starts

# side-quest groups whose id does not say it well: id prefix -> label
GROUP = {"PalDisplay": "Pal Critic", "PalCaptureCountReward": "Rewards",
         "BossDefeatReward": "Rewards", "PaldexReward": "Rewards",
         "FoodReward": "Rewards", "Delivery": "Deliveries",
         "StrongOldMan": "Captured Adventurer", "Kigurumi": "Kigurumi",
         "HowSurviveWorldTree": "World Tree", "Yakushima": "Yakushima"}


def _chain(quests, root):
    out, todo = [], [root]
    while todo:
        q = todo.pop(0)
        if q in out or q not in quests:
            continue
        out.append(q)
        todo += quests[q].get("next") or []
    return out


def story_order(game, done_anywhere=()):
    """The main quests to show, in order."""
    main = {q["id"]: q for q in game.get("quests") or [] if q["type"] == "Main"}
    led_to = {n for q in main.values() for n in q.get("next") or []}
    order = _chain(main, STORY_ROOT) if STORY_ROOT in main else []
    for q in main:                      # the ends nothing leads to
        if q not in led_to and q not in order and \
                len(_chain(main, q)) == 1:
            order.append(q)
    for q in main:                      # old ones someone here has done
        if q not in order and q in done_anywhere:
            order.append(q)
    return order


def group_of(qid):
    body = qid.split("_", 1)[1]
    for key, label in GROUP.items():
        if body.startswith(key):
            return label
    body = re.sub(r"_?\d+$", "", body).split("_")[0]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", body)


def entries(game, containers, players):
    """{"story": [quest], "side": [{group, quests}], "players": {pid:
    {done, active}}} where a quest is {id, title, desc, done_by}."""
    quests = {q["id"]: q for q in game.get("quests") or []}
    state = {}
    for pid, c in (containers or {}).items():
        if pid in players:
            state[pid] = {"done": [q for q in c.get("quests_done") or []
                                   if q in quests],
                          "active": [q for q in c.get("quests_active") or []
                                     if q in quests]}
    done_anywhere = {q for s in state.values() for q in s["done"]}
    done_by = {}
    for pid, s in state.items():
        for q in s["done"]:
            done_by.setdefault(q, []).append(players[pid])

    def row(qid):
        q = quests[qid]
        return {"id": qid, "title": q["title"], "desc": q.get("desc") or "",
                "done_by": sorted(done_by.get(qid, []), key=str.lower)}
    story = [row(q) for q in story_order(game, done_anywhere)]
    groups = {}
    for qid, q in quests.items():
        if q["type"] == "Sub":
            groups.setdefault(group_of(qid), []).append(row(qid))
    side = [{"group": g, "quests": rows} for g, rows in groups.items()]
    return {"story": story, "side": side, "players": state}
