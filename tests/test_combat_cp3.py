"""Combat-flow features added after checkpoint 2: cover, Large creatures, the
Ready action, the opportunity-attack setting, enemies opening doors, and
spotting by light level and Perception vs Stealth. Every result is checked
against both schemas."""
import pytest

from api.service import BadRequest, GameService
from engine import grid as G
from flow_helpers import build, check, events, flow_for, force_combat, req

BARB = ("barbarian_orc_berserker", "barbarian_1")
RANGER = ("ranger_wood_elf_hunter", "ranger_1")
CLERIC = ("cleric_dwarf_life", "cleric_1")


def P(ref_id, x, y):
    return (ref_id[0], ref_id[1], (x, y))


ROW = ["##########", "#........#", "#........#", "#........#", "##########"]


# ------------------------------------------------------------------ cover
def test_pillar_gives_half_cover_plus_two_ac():
    terrain = ["##########", "#........#", "#...#....#", "#........#", "##########"]
    st = build(party=(P(RANGER, 1, 2),), enemies=(("giant_frog", "frog_1", (7, 2), "melee_rush"),),
               terrain=terrain)
    flow = flow_for(st, [15, 3])
    force_combat(flow, ["ranger_1", "frog_1"])
    res = check(flow.submit(req("ranger_1", {"type": "attack", "target_id": "frog_1", "weapon": "Longbow"})), st)
    assert events(res, "attack")[0]["target_ac"] == 11 + 2
    assert "half cover" in res[0]["narration_hint"]
    assert not any(e["name"] == "Cover" for e in st["enemies"][0]["effects"])   # temporary only


def test_creature_in_the_way_gives_half_cover():
    st = build(party=(P(RANGER, 1, 2), P(BARB, 4, 2)),
               enemies=(("giant_frog", "frog_1", (7, 2), "melee_rush"),))
    flow = flow_for(st, [15, 3])
    force_combat(flow, ["ranger_1", "barbarian_1", "frog_1"])
    res = check(flow.submit(req("ranger_1", {"type": "attack", "target_id": "frog_1", "weapon": "Longbow"})), st)
    assert events(res, "attack")[0]["target_ac"] == 13


def test_total_cover_cannot_be_targeted():
    terrain = ["##########", "#...#....#", "#...#....#", "#...#....#", "##########"]
    st = build(party=(P(RANGER, 1, 2),), enemies=(("giant_frog", "frog_1", (7, 2), "melee_rush"),),
               terrain=terrain)
    flow = flow_for(st, [])
    force_combat(flow, ["ranger_1", "frog_1"])
    res = check(flow.submit(req("ranger_1", {"type": "attack", "target_id": "frog_1", "weapon": "Longbow"})), st)
    assert events(res, "rejected")[0]["reason_code"] == "no_line_of_sight"
    assert st["party"][0]["turn"]["action"] is True


def test_sacred_flame_ignores_cover():
    terrain = ["##########", "#........#", "#...#....#", "#........#", "##########"]
    st = build(party=(P(CLERIC, 1, 2),), enemies=(("giant_frog", "frog_1", (7, 2), "melee_rush"),),
               terrain=terrain)
    flow = flow_for(st, [10, 4])
    force_combat(flow, ["cleric_1", "frog_1"])
    res = check(flow.submit(req("cleric_1", {"type": "cast", "spell_id": "sacred_flame", "target_ids": ["frog_1"]})), st)
    save = events(res, "saving_throw")[0]
    assert save["roll"]["total"] == 10 + 1     # frog's normal Dex save, no +2


def test_cover_adds_to_dex_saves_in_the_rules_engine():
    st = build()
    flow = flow_for(st, [10])
    frog = st["enemies"][0]
    frog["effects"].append({"name": "Cover", "source_id": "_cover", "data": {"bonus": 5}})
    from engine.events import EventLog
    log = EventLog()
    flow.rules.saving_throw(log, "frog_1", "dex", 13)
    assert log.events[0]["roll"]["total"] == 10 + 1 + 5


# ------------------------------------------------------------------ Large creatures
def octopus_state(**kw):
    st = build(party=(P(BARB, 1, 1),), enemies=(("giant_octopus", "octo_1", (4, 1), "melee_rush"),),
               terrain=ROW, **kw)
    return st


def test_large_creature_fills_four_squares():
    st = octopus_state()
    flow = flow_for(st, [])
    octo = st["enemies"][0]
    assert octo["size"] == "Large"
    g = flow.grid()
    assert {G.pos(c) for c in [(4, 1), (5, 1), (4, 2), (5, 2)]} == set(G.footprint(octo))
    assert all(g.occupant(c)["id"] == "octo_1" for c in [(4, 1), (5, 1), (4, 2), (5, 2)])
    force_combat(flow, ["barbarian_1", "octo_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 5, "y": 2}})), st)
    assert events(res, "rejected")[0]["reason_code"] == "blocked_path"


def test_distance_and_reach_use_the_closest_square():
    st = octopus_state()
    flow = flow_for(st, [15, 6])
    force_combat(flow, ["barbarian_1", "octo_1"])
    # (6,3) is diagonal to the octopus's bottom-right square (5,2)
    check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 6, "y": 3}})), st)
    me, octo = st["party"][0], st["enemies"][0]
    assert G.distance_ft(me, octo) == 5 and G.adjacent(me, octo)
    res = check(flow.submit(req("barbarian_1", {"type": "attack", "target_id": "octo_1"})), st)
    assert events(res, "attack")[0]["outcome"] == "hit"


def test_large_creature_cannot_fit_through_a_one_square_gap():
    terrain = ["##########", "#....#...#", "#........#", "#....#...#", "##########"]
    st = build(party=(P(BARB, 8, 2),), enemies=(("giant_octopus", "octo_1", (1, 1), "melee_rush"),),
               terrain=terrain)
    flow = flow_for(st, [])
    octo = st["enemies"][0]
    path, why = G.path_to(flow.grid(), octo, (6, 1))
    assert path is None
    st["map"]["terrain"] = ["##########", "#........#", "#........#", "#....#...#", "##########"]
    path, feet = G.path_to(flow.grid(), octo, (6, 1))
    assert path and feet == 25


def test_leaving_a_large_creatures_reach_provokes():
    st = octopus_state()
    st["party"][0]["position"] = {"x": 3, "y": 2}
    flow = flow_for(st, [2])
    force_combat(flow, ["barbarian_1", "octo_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 1, "y": 2}})), st)
    assert events(res, "move")[0]["provoked_opportunity_attack_from"] == ["octo_1"]


def test_ai_moves_a_large_creature_next_to_its_target():
    # the octopus only walks 10 ft on land (swim 60 ft), so start it 15 ft away
    st = build(party=(P(BARB, 1, 1),), enemies=(("giant_octopus", "octo_1", (4, 2), "melee_rush"),),
               terrain=ROW)
    flow = flow_for(st, [2])
    force_combat(flow, ["octo_1", "barbarian_1"])
    res = check(flow.run_ai_turns(), st)
    assert G.adjacent(st["enemies"][0], st["party"][0])
    assert events(res, "attack")[0]["actor_id"] == "octo_1"


def test_slow_large_creature_dashes_to_close_in():
    st = build(party=(P(BARB, 1, 1),), enemies=(("giant_octopus", "octo_1", (6, 2), "melee_rush"),),
               terrain=ROW)
    flow = flow_for(st, [])
    force_combat(flow, ["octo_1", "barbarian_1"])
    res = check(flow.run_ai_turns(), st)
    assert events(res, "move")[0]["feet_used"] == 20 and events(res, "attack") == []
    assert G.adjacent(st["enemies"][0], st["party"][0])


# ------------------------------------------------------------------ Ready
def test_readied_attack_fires_when_enemy_steps_into_reach():
    st = build(enemies=(("srd_zombie", "zombie_1", (6, 2), "melee_rush"),))
    flow = flow_for(st, [15, 6, 2])   # readied greataxe hits for 6+3; zombie's slam misses
    force_combat(flow, ["barbarian_1", "zombie_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "ready", "target_id": "zombie_1"},
                                {"type": "end_turn"})), st)
    attacks = events(res, "attack")
    assert attacks[0]["actor_id"] == "barbarian_1" and attacks[0]["weapon"] == "Greataxe"
    assert attacks[1]["actor_id"] == "zombie_1"          # the zombie still gets its swing after
    assert st["party"][0]["turn"]["reaction"] is True    # new turn: reaction is back
    conds = [(e["condition"], e["change"]) for e in events(res, "condition")]
    assert ("Readied", "applied") in conds and ("Readied", "removed") in conds


def test_readied_bow_fires_when_enemy_moves_in_view():
    st = build(party=(P(RANGER, 1, 2),), enemies=(("srd_zombie", "zombie_1", (8, 2), "melee_rush"),))
    flow = flow_for(st, [15, 3])
    force_combat(flow, ["ranger_1", "zombie_1"])
    res = check(flow.submit(req("ranger_1", {"type": "ready", "target_id": "zombie_1"},
                                {"type": "end_turn"})), st)
    first = events(res, "attack")[0]
    assert first["actor_id"] == "ranger_1" and first["weapon"] == "Longbow"


def test_unused_readied_action_expires_on_your_next_turn():
    st = build(enemies=(("srd_zombie", "zombie_1", (10, 5), "guard"),))
    flow = flow_for(st, [])
    force_combat(flow, ["barbarian_1", "zombie_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "ready"}, {"type": "end_turn"})), st)
    assert ("Readied", "expired") in [(e["condition"], e["change"]) for e in events(res, "condition")]
    assert not any(e["name"] == "Readied" for e in st["party"][0]["effects"])


def test_ready_only_in_combat():
    st = build()
    res = check(flow_for(st, []).submit(req("barbarian_1", {"type": "ready"})), st)
    assert res[0]["status"] == "rejected"


# ------------------------------------------------------------------ OA setting
def test_player_can_turn_off_automatic_opportunity_attacks(tmp_path):
    st = build(party=(P(BARB, 2, 2),), enemies=(("srd_zombie", "zombie_1", (3, 2), "melee_rush"),))
    st["flags"]["auto_opportunity_attacks"] = False
    flow = flow_for(st, [])
    force_combat(flow, ["zombie_1", "barbarian_1"])
    zombie = st["enemies"][0]
    evs = flow.move(zombie, (6, 2))
    assert [e for e in evs if e["type"] == "attack"] == []
    assert st["party"][0]["turn"]["reaction"] is True

    svc = GameService(saves_dir=tmp_path)
    sid = svc.new_game("barbarian_orc_berserker", seed=1)["session_id"]
    assert svc.settings(sid, {"auto_opportunity_attacks": False}) == {"auto_opportunity_attacks": False}
    assert svc.get_state(sid)["flags"]["auto_opportunity_attacks"] is False
    with pytest.raises(BadRequest):
        svc.settings(sid, {"auto_opportunity_attacks": "no"})
    with pytest.raises(BadRequest):
        svc.settings(sid, {"god_mode": True})


# ------------------------------------------------------------------ AI and doors
DOOR_ROOM = ["##########", "#....#...#", "#....D...#", "#....#...#", "##########"]


def door(state="closed"):
    return {"id": "door_1", "kind": "door", "position": {"x": 5, "y": 2}, "state": state}


def test_enemy_opens_a_closed_door_to_reach_you():
    st = build(party=(P(BARB, 8, 2),), enemies=(("srd_zombie", "zombie_1", (2, 2), "melee_rush"),),
               terrain=DOOR_ROOM, features=[door()])
    flow = flow_for(st, [])
    force_combat(flow, ["zombie_1", "barbarian_1"])
    res = check(flow.run_ai_turns(), st)
    opened = events(res, "interact")
    assert opened and opened[0]["verb"] == "open" and opened[0]["result"] == "success"
    assert st["map"]["features"][0]["state"] == "open"


def test_enemy_cannot_get_through_a_locked_door():
    st = build(party=(P(BARB, 8, 2),), enemies=(("srd_zombie", "zombie_1", (2, 2), "melee_rush"),),
               terrain=DOOR_ROOM, features=[door("locked")])
    flow = flow_for(st, [])
    force_combat(flow, ["zombie_1", "barbarian_1"])
    res = check(flow.run_ai_turns(), st)
    assert events(res, "interact") == [] and st["map"]["features"][0]["state"] == "locked"
    assert G.pos(st["enemies"][0]) == (2, 2)


# ------------------------------------------------------------------ spotting
def frog_far(light, dist_cells):
    st = build(party=(P(BARB, 1, 1),), enemies=(("giant_frog", "frog_1", (1 + dist_cells, 1), "melee_rush"),),
               terrain=["#" * 20, "#" + "." * 18 + "#", "#" * 20])
    st["location"]["light"] = light
    return st


@pytest.mark.parametrize("light,cells,seen", [
    ("bright", 12, True),     # 60 ft in bright light
    ("bright", 13, False),
    ("dim", 6, True),         # giant frog: darkvision 30 ft, so dim counts as bright out to 30 ft
    ("dim", 7, False),
    ("darkness", 6, True),    # darkvision sees darkness as dim, out to 30 ft
    ("darkness", 7, False),
])
def test_spotting_range_depends_on_light_and_darkvision(light, cells, seen):
    st = frog_far(light, cells)
    flow = flow_for(st, [])
    assert flow.notices(st["enemies"][0], st["party"][0]) is seen


def test_blindsight_notices_in_darkness():
    st = build(party=(P(BARB, 1, 1),), enemies=(("animated_knife", "knife_1", (12, 1), "melee_rush"),),
               terrain=["#" * 20, "#" + "." * 18 + "#", "#" * 20])
    st["location"]["light"] = "darkness"
    flow = flow_for(st, [])
    assert flow.notices(st["enemies"][0], st["party"][0])   # blindsight 60 ft


def test_hiding_lets_you_sneak_up_and_surprise():
    st = frog_far("dim", 10)
    # hide: stealth 18+2 = 20 beats the frog's passive Perception 12
    # then attack from hiding: initiative (you 15, frog surprised: 3 and 9 -> 3),
    # your attack with advantage (12, 16 -> 16) hits, 6 damage
    flow = flow_for(st, [18, 15, 3, 9, 12, 16, 6])
    res = check(flow.submit(req("barbarian_1", {"type": "hide"})), st)
    assert events(res, "ability_check")[0]["success"]
    hidden = next(e for e in st["party"][0]["effects"] if e["name"] == "Hidden")
    assert hidden["data"]["stealth"] == 20
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 10, "y": 1}})), st)
    assert st["mode"] == "exploration"                  # walked right up without being noticed
    res = check(flow.submit(req("barbarian_1", {"type": "attack", "target_id": "frog_1"})), st)
    assert events(res, "combat_start")[0]["surprised"] == ["frog_1"]
    assert events(res, "attack")[0]["roll"]["mode"] == "advantage"


def test_cannot_hide_from_an_enemy_that_already_sees_you():
    st = frog_far("bright", 5)
    st["enemies"][0]["ai"]["awake"] = True
    flow = flow_for(st, [])
    st["mode"] = "exploration"
    res = check(flow.submit(req("barbarian_1", {"type": "hide"})), st)
    assert events(res, "rejected")[0]["reason_code"] == "condition_prevents"
