"""Combat flow: initiative, turn economy, movement, range, LOS, opportunity
attacks, mode switches, tactics. Every result is checked against both schemas."""
from engine import grid as G
from flow_helpers import build, check, events, flow_for, force_combat, req

BARB = ("barbarian_orc_berserker", "barbarian_1")
CLERIC = ("cleric_dwarf_life", "cleric_1")
RANGER = ("ranger_wood_elf_hunter", "ranger_1")


def P(ref_id, x, y):
    return (ref_id[0], ref_id[1], (x, y))


# ------------------------------------------------------------------ initiative
def test_initiative_order_and_switch_to_combat():
    st = build(enemies=(("giant_frog", "frog_1", (8, 4), "melee_rush"),))
    flow = flow_for(st, [5, 15])            # barbarian 5+2=7, frog 15+1=16
    evs = flow.start_combat()
    assert [e["type"] for e in evs] == ["combat_start", "initiative", "initiative", "state_change", "turn_start"]
    assert [o["id"] for o in st["combat"]["initiative_order"]] == ["frog_1", "barbarian_1"]
    assert st["mode"] == "combat" and evs[-1]["actor_id"] == "frog_1"
    check([flow._result("barbarian_1", "accepted", evs)], st)


def test_initiative_ties_go_to_higher_bonus():
    st = build(enemies=(("giant_frog", "frog_1", (8, 4), "melee_rush"),))
    flow = flow_for(st, [10, 11])           # both 12; barbarian bonus +2 beats frog +1
    flow.start_combat()
    assert st["combat"]["initiative_order"][0]["id"] == "barbarian_1"


def test_attacking_outside_combat_starts_it_and_sleeping_enemies_are_surprised():
    st = build(enemies=(("giant_frog", "frog_1", (3, 2), "melee_rush"),), awake=False)
    # init: barbarian 15, frog disadvantage (10, 12) -> 10; then attack 15 hits, damage 6+3
    flow = flow_for(st, [15, 10, 12, 15, 6])
    res = check(flow.submit(req("barbarian_1", {"type": "attack", "target_id": "frog_1"})), st)
    start = events(res, "combat_start")[0]
    assert start["surprised"] == ["frog_1"]
    frog_init = [e for e in events(res, "initiative") if e["actor_id"] == "frog_1"][0]
    assert frog_init["roll"]["mode"] == "disadvantage" and frog_init["roll"]["total"] == 11
    assert events(res, "attack")[0]["outcome"] == "hit"
    assert st["enemies"][0]["hp"] == 18 - 9


# ------------------------------------------------------------------ turn economy
def test_not_your_turn():
    st = build(enemies=(("giant_frog", "frog_1", (3, 2), "melee_rush"),))
    flow = flow_for(st, [])
    force_combat(flow, ["frog_1", "barbarian_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "attack", "target_id": "frog_1"})), st)
    assert res[0]["status"] == "rejected"
    assert res[0]["events"][0]["reason_code"] == "not_your_turn"


def test_one_action_per_turn():
    st = build(enemies=(("giant_frog", "frog_1", (3, 2), "melee_rush"),))
    flow = flow_for(st, [2])                # first attack misses (2+5=7 vs AC 11)
    force_combat(flow, ["barbarian_1", "frog_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "attack", "target_id": "frog_1"},
                                {"type": "attack", "target_id": "frog_1"})), st)
    assert res[0]["status"] == "accepted"
    assert events(res, "rejected")[0]["reason_code"] == "no_action_left"
    assert len(events(res, "attack")) == 1


def test_bonus_action_budget_rage_then_potion():
    st = build(enemies=(("giant_frog", "frog_1", (3, 2), "melee_rush"),))
    st["inventory"].append({"item": "Potion of Healing", "qty": 1, "owner_id": "barbarian_1"})
    flow = flow_for(st, [2])
    force_combat(flow, ["barbarian_1", "frog_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "use_feature", "feature": "rage"},
                                {"type": "use_item", "item": "Potion of Healing"})), st)
    assert events(res, "rejected")[0]["message"] == "You have no bonus action left this turn."
    res = check(flow.submit(req("barbarian_1", {"type": "attack", "target_id": "frog_1"})), st)
    assert res[0]["status"] == "accepted"   # the action is still free


def test_rejected_rules_action_refunds_the_cost():
    st = build()
    flow = flow_for(st, [2])
    force_combat(flow, ["barbarian_1", "frog_1"])
    st["party"][0]["resources"]["uses"]["rage"]["used"] = 3   # no rage left
    res = check(flow.submit(req("barbarian_1", {"type": "use_feature", "feature": "rage"})), st)
    assert res[0]["status"] == "rejected"
    assert st["party"][0]["turn"]["bonus_action"] is True


# ------------------------------------------------------------------ movement
def test_movement_budget_and_dash():
    st = build(enemies=(("giant_frog", "frog_1", (10, 5), "melee_rush"),))
    flow = flow_for(st, [])
    force_combat(flow, ["barbarian_1", "frog_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 8, "y": 2}})), st)
    assert events(res, "move")[0]["feet_used"] == 30
    assert st["party"][0]["turn"]["movement_left_ft"] == 0
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 9, "y": 2}})), st)
    assert events(res, "rejected")[0]["reason_code"] == "not_enough_movement"
    res = check(flow.submit(req("barbarian_1", {"type": "dash"}, {"type": "move", "to": {"x": 9, "y": 2}})), st)
    assert G.pos(st["party"][0]["position"]) == (9, 2)
    assert st["party"][0]["turn"]["movement_left_ft"] == 25


def test_difficult_terrain_costs_double():
    terrain = ["#########", "#.~~~~..#", "#.......#", "#########"]
    st = build(party=(P(BARB, 1, 1),), enemies=(("giant_frog", "frog_1", (7, 2), "melee_rush"),),
               terrain=terrain)
    flow = flow_for(st, [])
    force_combat(flow, ["barbarian_1", "frog_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 3, "y": 1}})), st)
    assert events(res, "move")[0]["feet_used"] == 15   # floor-ish diagonal around isn't cheaper here


def test_prone_creature_must_stand_or_crawl():
    st = build(enemies=(("giant_frog", "frog_1", (10, 5), "melee_rush"),))
    st["party"][0]["conditions"] = [{"name": "prone"}]
    flow = flow_for(st, [])
    force_combat(flow, ["barbarian_1", "frog_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "stand_up"},
                                {"type": "move", "to": {"x": 5, "y": 2}})), st)
    assert st["party"][0]["conditions"] == []
    assert events(res, "move")[0]["feet_used"] == 15
    assert st["party"][0]["turn"]["movement_left_ft"] == 0


# ------------------------------------------------------------------ range and line of sight
def test_melee_out_of_range():
    st = build(enemies=(("giant_frog", "frog_1", (6, 2), "melee_rush"),))
    flow = flow_for(st, [])
    force_combat(flow, ["barbarian_1", "frog_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "attack", "target_id": "frog_1", "weapon": "Greataxe"})), st)
    assert events(res, "rejected")[0]["reason_code"] == "out_of_range"
    assert st["party"][0]["turn"]["action"] is True


def test_wall_blocks_line_of_sight():
    terrain = ["#########", "#...#...#", "#...#...#", "#########"]
    st = build(party=(P(RANGER, 1, 1),), enemies=(("giant_frog", "frog_1", (6, 1), "melee_rush"),),
               terrain=terrain)
    flow = flow_for(st, [])
    force_combat(flow, ["ranger_1", "frog_1"])
    res = check(flow.submit(req("ranger_1", {"type": "attack", "target_id": "frog_1", "weapon": "Longbow"})), st)
    assert events(res, "rejected")[0]["reason_code"] == "no_line_of_sight"


def test_long_range_gives_disadvantage():
    terrain = ["#" * 30, "#" + "." * 28 + "#", "#" * 30]
    st = build(party=(P(RANGER, 1, 1),), enemies=(("giant_frog", "frog_1", (27, 1), "melee_rush"),),
               terrain=terrain)
    flow = flow_for(st, [15, 5])            # 130 ft: past 120 normal range
    force_combat(flow, ["ranger_1", "frog_1"])
    res = check(flow.submit(req("ranger_1", {"type": "attack", "target_id": "frog_1", "weapon": "Longbow"})), st)
    atk = events(res, "attack")[0]
    assert atk["roll"]["mode"] == "disadvantage" and atk["roll"]["kept"] == [5]


def test_ranged_attack_with_enemy_adjacent_has_disadvantage():
    st = build(party=(P(RANGER, 2, 2),),
               enemies=(("srd_zombie", "zombie_1", (3, 2), "melee_rush"),
                        ("giant_frog", "frog_1", (6, 2), "melee_rush")))
    flow = flow_for(st, [18, 3])
    force_combat(flow, ["ranger_1", "zombie_1", "frog_1"])
    res = check(flow.submit(req("ranger_1", {"type": "attack", "target_id": "frog_1", "weapon": "Longbow"})), st)
    assert events(res, "attack")[0]["roll"]["mode"] == "disadvantage"


def test_thrown_javelin_uses_up_inventory():
    st = build(enemies=(("giant_frog", "frog_1", (6, 2), "melee_rush"),))
    jav = next(i for i in st["inventory"] if i["item"] == "Javelin")
    before = jav["qty"]
    flow = flow_for(st, [15, 3])
    force_combat(flow, ["barbarian_1", "frog_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "attack", "target_id": "frog_1", "weapon": "Javelin"})), st)
    assert events(res, "attack")[0]["outcome"] == "hit"
    assert jav["qty"] == before - 1
    assert events(res, "resource")[-1]["resource"] == "javelin"


# ------------------------------------------------------------------ opportunity attacks
def test_leaving_reach_provokes_an_opportunity_attack():
    st = build(enemies=(("srd_zombie", "zombie_1", (3, 2), "melee_rush"),))
    flow = flow_for(st, [2])                # zombie's OA misses
    force_combat(flow, ["barbarian_1", "zombie_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 1, "y": 2}})), st)
    mv = events(res, "move")[0]
    assert mv["provoked_opportunity_attack_from"] == ["zombie_1"]
    assert events(res, "attack")[0]["actor_id"] == "zombie_1"
    assert st["enemies"][0]["turn"]["reaction"] is False


def test_disengage_prevents_opportunity_attacks():
    st = build(enemies=(("srd_zombie", "zombie_1", (3, 2), "melee_rush"),))
    flow = flow_for(st, [])
    force_combat(flow, ["barbarian_1", "zombie_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "disengage"}, {"type": "move", "to": {"x": 1, "y": 2}})), st)
    assert events(res, "attack") == []
    assert "provoked_opportunity_attack_from" not in events(res, "move")[0]


def test_moving_while_staying_in_reach_does_not_provoke():
    st = build(enemies=(("srd_zombie", "zombie_1", (3, 2), "melee_rush"),))
    flow = flow_for(st, [])
    force_combat(flow, ["barbarian_1", "zombie_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 2, "y": 3}})), st)
    assert events(res, "attack") == []


# ------------------------------------------------------------------ ending combat
def test_killing_the_last_enemy_returns_to_exploration():
    st = build(enemies=(("giant_frog", "frog_1", (3, 2), "melee_rush"),))
    st["enemies"][0]["hp"] = 1
    flow = flow_for(st, [15, 1])
    force_combat(flow, ["barbarian_1", "frog_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "use_feature", "feature": "rage"},
                                {"type": "attack", "target_id": "frog_1"},
                                {"type": "end_turn"})), st)
    assert events(res, "combat_end")[0]["result"] == "victory"
    assert st["mode"] == "exploration" and st["combat"] is None
    assert not any(e["name"] == "Rage" for e in st["party"][0]["effects"])
    assert len(res) == 1    # no enemy turns ran afterwards


def test_final_encounter_victory_ends_the_adventure():
    st = build(enemies=(("giant_frog", "frog_1", (3, 2), "melee_rush"),))
    st["enemies"][0]["hp"] = 1
    st["flags"]["final_encounter"] = True
    flow = flow_for(st, [15, 1])
    force_combat(flow, ["barbarian_1", "frog_1"])
    check(flow.submit(req("barbarian_1", {"type": "attack", "target_id": "frog_1"})), st)
    assert st["mode"] == "victory"


def test_player_death_is_game_over():
    st = build(enemies=(("giant_frog", "frog_1", (3, 2), "melee_rush"),))
    st["party"][0]["hp"] = 1
    flow = flow_for(st, [15, 6])            # frog bites: 18 vs AC 14, 7 piercing
    force_combat(flow, ["frog_1", "barbarian_1"])
    res = check(flow.run_ai_turns(), st)
    assert events(res, "death")[0]["target_id"] == "barbarian_1"
    assert events(res, "combat_end")[0]["result"] == "defeat"
    assert st["mode"] == "game_over" and st["combat"] is None
    res = check(flow.submit(req("barbarian_1", {"type": "end_turn"})), st)
    assert res[0]["events"][0]["reason_code"] == "condition_prevents"


# ------------------------------------------------------------------ enemy tactics
def test_melee_rush_moves_in_and_attacks():
    st = build(enemies=(("srd_zombie", "zombie_1", (6, 2), "melee_rush"),))
    flow = flow_for(st, [2])
    force_combat(flow, ["zombie_1", "barbarian_1"])
    res = check(flow.run_ai_turns(), st)
    assert [e["type"] for e in res[0]["events"]][:2] == ["move", "attack"]
    assert G.adjacent(st["enemies"][0]["position"], st["party"][0]["position"])
    assert flow.active_id() == "barbarian_1"


def test_melee_rush_dashes_when_too_far():
    st = build(party=(P(BARB, 1, 1),), enemies=(("srd_zombie", "zombie_1", (10, 5), "melee_rush"),))
    flow = flow_for(st, [])
    force_combat(flow, ["zombie_1", "barbarian_1"])
    res = check(flow.run_ai_turns(), st)
    types = [e["type"] for e in res[0]["events"]]
    assert "state_change" in types and "move" in types and "attack" not in types
    assert G.distance_ft(st["enemies"][0]["position"], st["party"][0]["position"]) == 5


def test_flee_at_half_disengages_and_backs_off():
    st = build(party=(P(BARB, 2, 2),), enemies=(("animated_knife", "knife_1", (3, 2), "flee_at_half"),))
    st["enemies"][0]["hp"] = 4
    flow = flow_for(st, [])
    force_combat(flow, ["knife_1", "barbarian_1"])
    res = check(flow.run_ai_turns(), st)
    conds = [e["condition"] for e in events(res, "condition")]
    assert "Fleeing" in conds and "Disengage" in conds
    assert G.distance_ft(st["enemies"][0]["position"], st["party"][0]["position"]) > 5
    assert st["party"][0]["turn"]["reaction"] is True     # no opportunity attack


def test_fleeing_enemy_that_gets_away_leaves_combat():
    terrain = ["#" * 22, "#" + "." * 20 + "#", "#" * 22]
    st = build(party=(P(BARB, 1, 1),), enemies=(("animated_knife", "knife_1", (17, 1), "flee_at_half"),),
               terrain=terrain)
    st["enemies"][0]["hp"] = 3
    flow = flow_for(st, [])
    force_combat(flow, ["knife_1", "barbarian_1"])
    res = check(flow.run_ai_turns(), st)
    assert events(res, "combat_end")[0]["result"] == "victory"
    assert st["mode"] == "exploration"


def test_end_turn_runs_enemy_turns_until_player_again():
    st = build(enemies=(("giant_frog", "frog_1", (10, 5), "melee_rush"),))
    flow = flow_for(st, seed=3)
    force_combat(flow, ["barbarian_1", "frog_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "end_turn"})), st)
    assert [r["actor_id"] for r in res] == ["barbarian_1", "frog_1"]
    assert flow.active_id() == "barbarian_1" and st["combat"]["round"] == 2


# ------------------------------------------------------------------ allies
def test_cleric_ally_blesses_on_round_one():
    st = build(party=(P(BARB, 2, 2), P(CLERIC, 2, 3)), enemies=(("srd_zombie", "zombie_1", (9, 4), "melee_rush"),))
    flow = flow_for(st, seed=5)
    force_combat(flow, ["cleric_1", "barbarian_1", "zombie_1"])
    res = check(flow.run_ai_turns(), st)
    cast = events(res, "spell_cast")[0]
    assert cast["spell_id"] == "bless" and "barbarian_1" in cast["target_ids"]


def test_cleric_ally_heals_badly_hurt_player():
    st = build(party=(P(BARB, 2, 2), P(CLERIC, 4, 4)), enemies=(("srd_zombie", "zombie_1", (9, 4), "melee_rush"),))
    st["party"][0]["hp"] = 10
    flow = flow_for(st, seed=5)
    force_combat(flow, ["cleric_1", "barbarian_1", "zombie_1"])
    res = check(flow.run_ai_turns(), st)
    assert events(res, "spell_cast")[0]["spell_id"] == "cure_wounds"
    assert st["party"][0]["hp"] > 10


def test_ranger_ally_marks_and_shoots():
    st = build(party=(P(BARB, 2, 2), P(RANGER, 1, 4)), enemies=(("srd_zombie", "zombie_1", (9, 4), "melee_rush"),))
    flow = flow_for(st, seed=8)
    force_combat(flow, ["ranger_1", "barbarian_1", "zombie_1"])
    res = check(flow.run_ai_turns(), st)
    assert events(res, "spell_cast")[0]["spell_id"] == "hunters_mark"
    assert events(res, "attack")[0]["weapon"] == "Longbow"


# ------------------------------------------------------------------ spells
def test_touch_spell_out_of_range_and_one_slot_per_turn():
    st = build(party=(P(CLERIC, 2, 2), P(RANGER, 6, 2)), enemies=(("giant_frog", "frog_1", (9, 4), "melee_rush"),))
    flow = flow_for(st, [1, 4])              # frog fails the Sacred Flame save, takes 4
    force_combat(flow, ["cleric_1", "frog_1", "ranger_1"])
    res = check(flow.submit(req("cleric_1", {"type": "cast", "spell_id": "cure_wounds",
                                             "slot_level": 1, "target_ids": ["ranger_1"]})), st)
    assert events(res, "rejected")[0]["reason_code"] == "out_of_range"
    res = check(flow.submit(req("cleric_1", {"type": "cast", "spell_id": "shield_of_faith", "slot_level": 1},
                                {"type": "cast", "spell_id": "guiding_bolt", "slot_level": 1,
                                 "target_ids": ["frog_1"]})), st)
    assert events(res, "rejected")[0]["message"] == "You've already cast a leveled spell this turn."
    res = check(flow.submit(req("cleric_1", {"type": "cast", "spell_id": "sacred_flame",
                                             "target_ids": ["frog_1"]})), st)
    assert events(res, "damage")[0]["amount"] == 4    # a cantrip is still fine


# ------------------------------------------------------------------ exploration
def test_walking_into_view_of_an_awake_enemy_starts_combat():
    st = build(party=(P(BARB, 1, 1),), enemies=(("giant_frog", "frog_1", (10, 1), "melee_rush"),))
    st["location"]["light"] = "dim"           # frog: darkvision 30 ft -> spots you at 30 ft
    flow = flow_for(st, seed=2)
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 8, "y": 1}})), st)
    assert G.pos(st["party"][0]["position"]) == (4, 1)    # stopped when spotted (30 ft)
    assert events(res, "combat_start")
    assert st["mode"] in ("combat", "game_over")


def test_sleeping_enemy_does_not_notice_you():
    st = build(party=(P(BARB, 1, 1),), enemies=(("giant_frog", "frog_1", (10, 1), "melee_rush"),), awake=False)
    flow = flow_for(st, [])
    check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 8, "y": 1}})), st)
    assert st["mode"] == "exploration"


def test_allies_follow_the_player_while_exploring():
    st = build(party=(P(BARB, 1, 1), P(CLERIC, 1, 2)), enemies=())
    flow = flow_for(st, [])
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 9, "y": 4}})), st)
    assert [e["actor_id"] for e in events(res, "move")] == ["barbarian_1", "cleric_1"]
    assert G.distance_ft(st["party"][1]["position"], st["party"][0]["position"]) <= 10


def test_doors_block_until_opened_and_can_be_forced():
    terrain = ["#######", "#..D..#", "#######"]
    door = {"id": "door_1", "kind": "door", "position": {"x": 3, "y": 1}, "state": "locked", "dc": 12}
    st = build(party=(P(BARB, 1, 1),), enemies=(), terrain=terrain, features=[door])
    flow = flow_for(st, [15])                 # Athletics: 15 + 3 + 2 = 20 vs DC 12
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 5, "y": 1}})), st)
    assert events(res, "rejected")[0]["reason_code"] == "blocked_path"
    res = check(flow.submit(req("barbarian_1", {"type": "move", "to": {"x": 2, "y": 1}},
                                {"type": "interact", "object_id": "door_1", "verb": "open"})), st)
    assert events(res, "interact")[0]["result"] == "failure"   # it's locked
    res = check(flow.submit(req("barbarian_1", {"type": "interact", "object_id": "door_1", "verb": "force"},
                                {"type": "move", "to": {"x": 5, "y": 1}})), st)
    assert events(res, "ability_check")[0]["success"] is True
    assert G.pos(st["party"][0]["position"]) == (5, 1)
    assert st["flags"]["door_1_open"] is True


def test_hide_needs_to_be_out_of_sight():
    st = build(enemies=(("giant_frog", "frog_1", (8, 2), "melee_rush"),))
    flow = flow_for(st, [])
    force_combat(flow, ["barbarian_1", "frog_1"])
    res = check(flow.submit(req("barbarian_1", {"type": "hide"})), st)
    assert events(res, "rejected")[0]["reason_code"] == "condition_prevents"


def test_rest_only_when_safe():
    st = build(enemies=(("giant_frog", "frog_1", (5, 2), "melee_rush"),))
    flow = flow_for(st, [])
    res = check(flow.submit(req("barbarian_1", {"type": "rest", "kind": "short"})), st)
    assert events(res, "rejected")[0]["message"].startswith("Enemies are close by")
    st2 = build(enemies=())
    st2["party"][0]["hp"] = 30
    flow2 = flow_for(st2, seed=4)
    res = check(flow2.submit(req("barbarian_1", {"type": "rest", "kind": "long"})), st2)
    assert events(res, "rest") and st2["party"][0]["hp"] == st2["party"][0]["max_hp"]


def test_clarification_question_changes_nothing():
    st = build()
    flow = flow_for(st, [])
    res = check(flow.submit(req("barbarian_1", clarification_needed="Which frog do you mean?")), st)
    assert res[0]["status"] == "needs_clarification"
    assert res[0]["narration_hint"] == "Which frog do you mean?" and st["turn_counter"] == 0
