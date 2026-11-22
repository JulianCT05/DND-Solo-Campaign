"""Plays a seeded game through the same code the API uses and prints a
readable dice log. No server, no LLM. Same seed = same game every time.

    python play_demo.py            # seed 11, Barbarian
    python play_demo.py 7 ranger_wood_elf_hunter
"""
import sys
import tempfile

from api.service import GameService
from engine import grid as G

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 11
character = sys.argv[2] if len(sys.argv) > 2 else "barbarian_orc_berserker"


def line(e, names):
    n = lambda i: names.get(i, i)  # noqa: E731
    t, who = e["type"], n(e.get("actor_id", ""))
    r = e.get("roll")
    dice = f"{r['expr']} {r['dice']} = {r['total']}" if r else ""
    if t == "move":
        p = e["path"][-1]
        oa = f"  (provokes {', '.join(map(n, e['provoked_opportunity_attack_from']))})" \
            if e.get("provoked_opportunity_attack_from") else ""
        return f"{who} moves to ({p['x']},{p['y']}), {e['feet_used']} ft{oa}"
    if t == "attack":
        return f"{who} attacks {n(e['target_id'])} with {e['weapon']}: {dice} vs AC {e['target_ac']} -> {e['outcome']}"
    if t == "damage":
        mod = "" if e.get("modifier_applied") in (None, "none") else f" ({e['modifier_applied']})"
        return f"   {e['amount']} {e['damage_type']} damage{mod}; {n(e['target_id'])} at {e['hp_after']} HP"
    if t == "heal":
        return f"   {n(e['target_id'])} heals {e['amount']} -> {e['hp_after']} HP"
    if t == "saving_throw":
        return f"   {n(e['target_id'])} {e['ability'].upper()} save {dice} vs DC {e['dc']}: {'success' if e['success'] else 'fail'}"
    if t == "ability_check":
        return f"{who} {e.get('skill') or e['ability']} check {dice} vs DC {e['dc']}: {'success' if e['success'] else 'fail'}"
    if t == "initiative":
        return f"   initiative {who}: {dice}"
    if t == "turn_start":
        return f"-- round {e['round']}: {who}'s turn"
    if t == "spell_cast":
        return f"{who} casts {e['spell_id']} on {', '.join(map(n, e.get('target_ids', [])))}"
    if t == "condition":
        return f"   {n(e['target_id'])}: {e['condition']} {e['change']}"
    if t == "death":
        return f"   *** {n(e['target_id'])} dies ***"
    if t == "combat_start":
        return "=== COMBAT STARTS ==="
    if t == "combat_end":
        return f"=== COMBAT ENDS: {e['result'].upper()} ==="
    if t == "rejected":
        return f"   REJECTED ({e['reason_code']}): {e['message']}"
    if t == "state_change" and e["field"] == "mode":
        return f"   [mode -> {e['value']}]"
    if t == "resource":
        return f"   {who} spends {e['resource']} ({e['remaining']} left)"
    return None


svc = GameService(saves_dir=tempfile.mkdtemp())
state = svc.new_game(character, seed=seed)
sid = state["session_id"]
names = {c["id"]: c["name"] for c in state["party"] + state["enemies"]}
me = state["party"][0]
print(f"Seed {seed}. You play {me['name']} ({me['id']}). {state['summary']['text']}\n")


def send(*actions, text=""):
    out = svc.submit(sid, {"schema_version": "1.0.0", "actor_id": me["id"], "raw_text": text,
                           "confidence": 1.0, "actions": list(actions)})
    for tr in out["turn_results"]:
        for e in tr["events"]:
            s = line(e, names)
            if s:
                print(s)
    return out["state"]


print('> "I wade into the cellar"')
state = send({"type": "move", "to": {"x": 6, "y": 6}})
raged = False
for _ in range(25):
    if state["mode"] != "combat":
        break
    me = state["party"][0]
    foes = [e for e in state["enemies"] if e["alive"]]
    g = svc.sessions[sid].flow.grid()
    seen = [e for e in foes if g.line_of_sight(me["position"], e["position"])] or foes
    target = min(seen, key=lambda e: (G.distance_ft(me["position"], e["position"]), e["id"]))
    acts = []
    if me["ref"] == "barbarian_orc_berserker" and not raged:
        acts.append({"type": "use_feature", "feature": "rage"})
        raged = True
    weapon = None
    can_see = g.line_of_sight(me["position"], target["position"])
    shooter = me["ref"] in ("ranger_wood_elf_hunter", "cleric_dwarf_life")
    if me["ref"] == "ranger_wood_elf_hunter":
        weapon = "Longbow"
    if (not shooter or not can_see) and not G.adjacent(me["position"], target["position"]):
        goals = [(target["position"]["x"] + dx, target["position"]["y"] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
        path, _, _ = G.best_step_toward(svc.sessions[sid].flow.grid(), me, goals, me["turn"]["movement_left_ft"])
        if path:
            acts.append({"type": "move", "to": G.as_dict(path[-1])})
    if me["ref"] == "cleric_dwarf_life":
        acts.append({"type": "cast", "spell_id": "sacred_flame", "target_ids": [target["id"]]})
    else:
        acts.append({"type": "attack", "target_id": target["id"], **({"weapon": weapon} if weapon else {})})
    acts.append({"type": "end_turn"})
    print(f'\n> player: {", ".join(a["type"] for a in acts)} ({names[target["id"]]})')
    state = send(*acts)

print(f"\nMode: {state['mode']}.  HP: " + ", ".join(
    f"{c['name']} {c['hp']}/{c['max_hp']}" for c in state["party"] + state["enemies"]))
