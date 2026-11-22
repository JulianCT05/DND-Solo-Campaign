import json
from pathlib import Path
from conftest import DATA, assert_valid, rules_with, types
from engine import Dice, Rules, validate_event, validate_state


# ------------------------------------------------------------- attacks & damage
def test_hit_and_damage(state):
    r = rules_with(state, [15, 6])                      # d20=15 hits AC 8, d12=6
    ev = r.resolve("barbarian_1", {"type": "attack", "target_id": "zombie_1", "weapon": "Greataxe"})
    assert_valid(ev)
    assert types(ev) == ["attack", "damage"]
    assert ev[0]["outcome"] == "hit" and ev[1]["amount"] == 9
    assert r.get("zombie_1")["hp"] == 22 - 9 == ev[1]["hp_after"]


def test_crit_doubles_total_damage(state):
    r = rules_with(state, [20, 5])
    ev = r.resolve("barbarian_1", {"type": "attack", "target_id": "frog_1"})
    assert ev[0]["outcome"] == "critical_hit"
    assert ev[1]["critical"] and ev[1]["amount"] == (5 + 3) * 2


def test_natural_one_misses(state):
    ev = rules_with(state, [1]).resolve("barbarian_1", {"type": "attack", "target_id": "zombie_1"})
    assert types(ev) == ["attack"] and ev[0]["outcome"] == "critical_miss"


def test_zero_hp_is_death_and_dead_cannot_be_targeted(state):
    r = rules_with(state, [20, 12])                     # (12+3)*2 = 30 > 22
    ev = r.resolve("barbarian_1", {"type": "attack", "target_id": "zombie_1"})
    assert_valid(ev)
    assert types(ev) == ["attack", "damage", "death"]
    assert r.get("zombie_1")["alive"] is False and r.get("zombie_1")["hp"] == 0
    ev = r.resolve("barbarian_1", {"type": "attack", "target_id": "zombie_1"})
    assert ev[0]["type"] == "rejected" and ev[0]["reason_code"] == "invalid_target"


def test_immunity_and_temp_hp(state):
    r = rules_with(state, [4])
    from engine.events import EventLog
    log = EventLog()
    r.deal_damage(log, "cleric_1", "armor_1", r.dice.roll("1d4"), "poison")
    assert log.events[0]["amount"] == 0 and log.events[0]["modifier_applied"] == "immunity"
    cleric = r.get("cleric_1"); cleric["temp_hp"] = 5
    r.dice.values = [8]
    r.deal_damage(log, "frog_1", "cleric_1", r.dice.roll("1d8"), "piercing")
    assert cleric["temp_hp"] == 0 and cleric["hp"] == cleric["max_hp"] - 3
    assert_valid(log.events)


# ------------------------------------------------------------- rage
def test_rage_bonus_resistance_and_uses(state):
    r = rules_with(state, [15, 6, 12, 6])
    ev = r.resolve("barbarian_1", {"type": "use_feature", "feature": "rage"})
    assert_valid(ev)
    assert types(ev) == ["resource", "condition"] and ev[0]["remaining"] == 2
    ev = r.resolve("barbarian_1", {"type": "attack", "target_id": "zombie_1", "weapon": "Greataxe"})
    assert ev[1]["amount"] == 6 + 3 + 2                 # Rage +2
    ev = r.resolve("zombie_1", {"type": "attack", "target_id": "barbarian_1"})
    assert ev[1]["amount"] == (6 + 1) // 2 and ev[1]["modifier_applied"] == "resistance"
    again = r.resolve("barbarian_1", {"type": "use_feature", "feature": "Rage"})
    assert again[0]["reason_code"] == "no_resource"


def test_reckless_gives_enemies_advantage_until_next_turn(state):
    r = rules_with(state, [3, 18, 6, 2, 19, 6])
    ev = r.resolve("barbarian_1", {"type": "attack", "target_id": "zombie_1", "modifiers": ["reckless"]})
    assert ev[0]["roll"]["mode"] == "advantage" and ev[0]["roll"]["kept"] == [18]
    ev = r.resolve("zombie_1", {"type": "attack", "target_id": "barbarian_1"})
    assert ev[0]["roll"]["mode"] == "advantage"
    assert_valid(r.start_of_turn("barbarian_1"))
    assert r.effect(r.get("barbarian_1"), "Reckless") is None


# ------------------------------------------------------------- spells
def test_sacred_flame_cantrip_no_slot(state):
    r = rules_with(state, [2, 7])                       # zombie dex save 2-2=0 < 13, d8=7
    ev = r.resolve("cleric_1", {"type": "cast", "spell_id": "sacred_flame", "target_ids": ["zombie_1"]})
    assert_valid(ev)
    assert types(ev) == ["spell_cast", "saving_throw", "damage"]
    assert ev[1]["success"] is False and ev[2]["amount"] == 7
    assert r.get("cleric_1")["resources"]["spell_slots"]["1"]["used"] == 0


def test_cure_wounds_life_domain_and_cap(state):
    r = rules_with(state, [3, 4])
    barb = r.get("barbarian_1"); barb["hp"] = 10
    ev = r.resolve("cleric_1", {"type": "cast", "spell_id": "cure_wounds", "target_ids": ["barbarian_1"]})
    assert_valid(ev)
    assert types(ev) == ["resource", "spell_cast", "heal"]
    assert ev[2]["amount"] == 3 + 4 + 3 + 3             # 2d8 + Wis 3 + Disciple of Life (2+1)
    r.dice.values = [8, 8]
    barb["hp"] = barb["max_hp"] - 2
    ev = r.resolve("cleric_1", {"type": "cast", "spell_id": "cure_wounds", "target_ids": ["barbarian_1"]})
    assert ev[-1]["amount"] == 2 and barb["hp"] == barb["max_hp"]


def test_out_of_slots_is_rejected(state):
    r = rules_with(state, [])
    r.get("cleric_1")["resources"]["spell_slots"]["1"]["used"] = 4
    ev = r.resolve("cleric_1", {"type": "cast", "spell_id": "guiding_bolt", "target_ids": ["zombie_1"]})
    assert ev[0]["type"] == "rejected" and ev[0]["reason_code"] == "no_resource"


def test_cannot_cast_unprepared_or_unknown(state):
    r = rules_with(state, [])
    assert r.resolve("barbarian_1", {"type": "cast", "spell_id": "bless"})[0]["reason_code"] == "not_prepared"
    assert r.resolve("cleric_1", {"type": "cast", "spell_id": "fireball"})[0]["reason_code"] == "unknown_spell"


def test_guiding_bolt_then_advantage(state):
    r = rules_with(state, [15, 1, 1, 1, 1, 4, 12, 3])
    ev = r.resolve("cleric_1", {"type": "cast", "spell_id": "guiding_bolt", "target_ids": ["frog_1"]})
    assert_valid(ev)
    assert "condition" in types(ev)
    ev = r.resolve("barbarian_1", {"type": "attack", "target_id": "frog_1"})
    assert ev[0]["roll"]["mode"] == "advantage" and ev[0]["roll"]["kept"] == [12]


def test_hunters_mark_free_use_and_extra_damage(state):
    r = rules_with(state, [15, 5, 4])
    ev = r.resolve("ranger_1", {"type": "cast", "spell_id": "hunters_mark", "target_ids": ["zombie_1"]})
    assert_valid(ev)
    assert ev[0]["resource"] == "favored_enemy"
    ev = r.resolve("ranger_1", {"type": "attack", "target_id": "zombie_1", "weapon": "Longbow"})
    assert_valid(ev)
    assert types(ev) == ["attack", "damage", "damage"]
    assert ev[2]["source"] == "Hunter's Mark" and ev[2]["damage_type"] == "force"


def test_bless_adds_d4_and_concentration_swap(state):
    r = rules_with(state, [10, 3, 2])
    ev = r.resolve("cleric_1", {"type": "cast", "spell_id": "bless",
                                "target_ids": ["barbarian_1", "cleric_1", "ranger_1"]})
    assert_valid(ev)
    ev = r.resolve("barbarian_1", {"type": "attack", "target_id": "frog_1"})
    assert ev[0]["roll"]["total"] == 10 + 5 + 3
    ev = r.resolve("cleric_1", {"type": "cast", "spell_id": "shield_of_faith"})
    assert_valid(ev)
    assert r.effect(r.get("barbarian_1"), "Bless") is None          # Bless dropped
    assert r.current_ac(r.get("cleric_1")) == 18


def test_vex_gives_advantage_on_next_attack(state):
    r = rules_with(state, [15, 3, 2, 14, 3])
    r.resolve("ranger_1", {"type": "attack", "target_id": "frog_1", "weapon": "Shortsword"})
    ev = r.resolve("ranger_1", {"type": "attack", "target_id": "frog_1", "weapon": "Shortsword"})
    assert ev[0]["roll"]["mode"] == "advantage"


def test_effects_expire_on_casters_turn_end(state):
    r = rules_with(state, [])
    r.resolve("cleric_1", {"type": "cast", "spell_id": "bless", "target_ids": ["barbarian_1"]})
    for _ in range(10):
        ev = r.end_of_turn("cleric_1")
        assert_valid(ev)
    assert r.effect(r.get("barbarian_1"), "Bless") is None
    assert r.get("cleric_1")["concentrating_on"] is None


# ------------------------------------------------------------- items, checks, rests
def test_potion_of_healing(state):
    r = rules_with(state, [2, 3])
    barb = r.get("barbarian_1"); barb["hp"] = 5
    ev = r.resolve("barbarian_1", {"type": "use_item", "item": "Potion of Healing"})
    assert_valid(ev)
    assert ev[-1]["amount"] == 7 and barb["hp"] == 12
    assert r.resolve("barbarian_1", {"type": "use_item", "item": "Potion of Healing"})[0]["type"] == "rejected"


def test_ability_check_uses_feature_dc(state):
    r = rules_with(state, [10])
    ev = r.resolve("barbarian_1", {"type": "check", "ability": "str", "skill": "athletics",
                                   "target_id": "door_east", "intent": "force the door"})
    assert_valid(ev)
    assert ev[0]["dc"] == 13 and ev[0]["roll"]["total"] == 10 + 3 + 2 and ev[0]["success"]


def test_rests(state):
    r = rules_with(state, [6, 6])
    barb = r.get("barbarian_1"); barb["hp"] = 30
    r.get("cleric_1")["resources"]["spell_slots"]["1"]["used"] = 3
    ev = r.rest("short")
    assert_valid(ev)
    assert barb["hp"] == 30 + 8 + 6 == barb["max_hp"]
    ev = r.rest("long")
    assert_valid(ev)
    assert "spell_slot_1" in ev[0]["resources_restored"]
    assert r.get("cleric_1")["resources"]["spell_slots"]["1"]["used"] == 0


# ------------------------------------------------------------- integration
def test_every_event_in_a_random_brawl_is_valid(state):
    """Seeded fight until someone wins; every event must match the schema."""
    r = Rules(state, DATA, Dice(2026))
    r.resolve("barbarian_1", {"type": "use_feature", "feature": "rage"})
    for _ in range(40):
        for actor, target in [("barbarian_1", "zombie_1"), ("ranger_1", "frog_1"),
                              ("cleric_1", "armor_1"), ("zombie_1", "barbarian_1"),
                              ("frog_1", "ranger_1"), ("armor_1", "cleric_1")]:
            if r.get(actor)["alive"] and r.get(target)["alive"]:
                assert_valid(r.resolve(actor, {"type": "attack", "target_id": target}))
    assert validate_state(state) == []


def test_example_game_state_works():
    state = json.loads(Path(__file__).resolve().parent.parent.joinpath(
        "data/example_game_state.json").read_text())
    r = Rules(state, DATA, Dice(state.get("rng_seed", 1)))
    ev = r.resolve("barbarian_1", {"type": "attack", "target_id": "giant_frog_1", "weapon": "Greataxe"})
    assert_valid(ev)
    assert validate_state(state) == []


def test_validator_catches_misspelled_fields():
    bad = {"type": "damage", "seq": 0, "target_id": "x", "amount": 3,
           "damage_type": "fire", "hp_after": 1, "hp_afterr": 1}
    assert validate_event(bad) != []
