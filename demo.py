"""Run a short seeded fight with no LLM and print the dice log.
    python demo.py          (same seed = same fight every time)
"""
import sys
sys.path.insert(0, "tests")
from conftest import make_state, DATA
from engine import Dice, Rules, validate_event


def show(events):
    for e in events:
        assert not validate_event(e), validate_event(e)
        t, who = e["type"], e.get("actor_id", "")
        if t == "attack":
            print(f"  {who} attacks {e['target_id']} with {e['weapon']}: "
                  f"{e['roll']['expr']} {e['roll']['dice']} = {e['roll']['total']} vs AC {e['target_ac']} -> {e['outcome']}")
        elif t == "damage":
            print(f"    {e['amount']} {e['damage_type']} damage ({e.get('modifier_applied', 'none')}), "
                  f"{e['target_id']} at {e['hp_after']} HP")
        elif t == "death":
            print(f"    *** {e['target_id']} dies ***")
        elif t == "saving_throw":
            print(f"  {e['target_id']} {e['ability'].upper()} save {e['roll']['total']} vs DC {e['dc']}: "
                  f"{'success' if e['success'] else 'fail'}")
        elif t == "heal":
            print(f"    {e['target_id']} heals {e['amount']} -> {e['hp_after']} HP")
        elif t == "rejected":
            print(f"  REJECTED: {e['message']}")
        else:
            print(f"  [{t}] " + ", ".join(f"{k}={v}" for k, v in e.items() if k not in ("type", "seq")))


state = make_state()
rules = Rules(state, DATA, Dice(seed=7))
script = [
    ("barbarian_1", {"type": "use_feature", "feature": "rage"}),
    ("barbarian_1", {"type": "attack", "target_id": "zombie_1", "modifiers": ["reckless"]}),
    ("cleric_1", {"type": "cast", "spell_id": "bless", "target_ids": ["barbarian_1", "cleric_1", "ranger_1"]}),
    ("ranger_1", {"type": "cast", "spell_id": "hunters_mark", "target_ids": ["frog_1"]}),
    ("zombie_1", {"type": "attack", "target_id": "barbarian_1"}),
    ("frog_1", {"type": "attack", "target_id": "ranger_1"}),
    ("barbarian_1", {"type": "attack", "target_id": "zombie_1"}),
    ("ranger_1", {"type": "attack", "target_id": "frog_1", "weapon": "Longbow"}),
    ("cleric_1", {"type": "cast", "spell_id": "sacred_flame", "target_ids": ["frog_1"]}),
    ("barbarian_1", {"type": "attack", "target_id": "zombie_1"}),
    ("ranger_1", {"type": "attack", "target_id": "frog_1", "weapon": "Longbow"}),
    ("cleric_1", {"type": "cast", "spell_id": "cure_wounds", "target_ids": ["barbarian_1"]}),
    ("cleric_1", {"type": "cast", "spell_id": "fireball", "target_ids": ["frog_1"]}),
]
for actor, action in script:
    print(f"\n{actor}: {action['type']} {action.get('spell_id') or action.get('feature') or action.get('target_id')}")
    show(rules.resolve(actor, action))
print("\nHP:", {c["id"]: c["hp"] for c in state["party"] + state["enemies"]})
