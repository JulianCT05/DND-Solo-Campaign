"""Helpers for the combat-flow tests: tiny maps and states built in code."""
from conftest import DATA, ScriptedDice
from engine import Dice, validate_state, validate_turn_result
from engine.combat import CombatFlow

OPEN_MAP = ["############",
            "#..........#",
            "#..........#",
            "#..........#",
            "#..........#",
            "#..........#",
            "############"]


def build(party=(("barbarian_orc_berserker", "barbarian_1", (2, 2)),),
          enemies=(("giant_frog", "frog_1", (6, 2), "melee_rush"),),
          terrain=OPEN_MAP, features=(), mode="exploration", awake=True):
    pcs = []
    for i, (ref, iid, p) in enumerate(party):
        pcs.append(DATA.new_pc(ref, iid, "player" if i == 0 else "engine_ally", p))
    ens = []
    for ref, iid, p, tactic in enemies:
        e = DATA.new_enemy(ref, iid, p, tactic)
        e["ai"]["awake"] = awake
        ens.append(e)
    inv = []
    for pc in pcs:
        inv += DATA.starting_inventory(pc["ref"], pc["id"])
    state = {"schema_version": "1.0.0", "session_id": "t", "rules_edition": "srd_5.2.1",
             "rng_seed": 1, "mode": mode, "turn_counter": 0,
             "location": {"room_id": "test", "light": "bright"},
             "party": pcs, "enemies": ens,
             "map": {"width": len(terrain[0]), "height": len(terrain), "cell_ft": 5,
                     "terrain": list(terrain), "legend": {"D": "door", "~": "difficult", "W": "water"},
                     "features": list(features)},
             "combat": None, "inventory": inv, "flags": {},
             "summary": {"text": "", "updated_at_turn": 0}, "recent_events": []}
    assert validate_state(state) == [], validate_state(state)
    return state


def flow_for(state, dice_values=None, seed=1):
    dice = ScriptedDice(dice_values) if dice_values is not None else Dice(seed)
    return CombatFlow(state, DATA, dice)


def req(actor, *actions, **extra):
    r = {"schema_version": "1.0.0", "actor_id": actor, "raw_text": "test",
         "confidence": 1.0, "actions": list(actions)}
    r.update(extra)
    return r


def check(results, state):
    """Every turn_result and the state must match Julian's schemas."""
    for tr in results:
        errs = validate_turn_result(tr)
        assert errs == [], f"{tr} -> {errs}"
    errs = validate_state(state)
    assert errs == [], errs
    return results


def events(results, type_=None):
    out = [e for tr in results for e in tr["events"]]
    return [e for e in out if e["type"] == type_] if type_ else out


def force_combat(flow, order):
    """Put the game straight into combat with a fixed initiative order."""
    st = flow.state
    st["mode"] = "combat"
    st["combat"] = {"round": 1, "initiative_order": [{"id": i, "initiative": 20 - n}
                                                     for n, i in enumerate(order)],
                    "active_index": 0}
    for c in st["party"] + st["enemies"]:
        c["turn"] = flow.fresh_turn(c)
