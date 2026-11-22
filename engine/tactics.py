"""Simple enemy and ally tactics (Leena's part).

Every decision is turned into ordinary action dicts and sent through
CombatFlow.perform(), so the AI is checked by exactly the same rules as the
player: it can't move further, shoot further or act more often than it may.

Enemy tactics (state enemies[].ai.tactic):
  melee_rush    go for the current target (or the nearest PC), Dash if too far, attack
  swarm         like melee_rush, but everyone piles onto the weakest PC
  flee_at_half  melee_rush until at half HP or lower, then Disengage/Dash away
  hit_and_run   move in, attack, then use leftover movement to back off
  ranged_kite   keep distance and shoot; melee_rush if it has no ranged attack
  guard         hold position; attack anyone who comes within 15 ft
  support       treated as ranged_kite (no support spells in the data yet)

Allies (engine_ally pregens) pick by class:
  Cleric   heal a badly hurt ally (Cure Wounds), else Bless on round 1, else Sacred Flame
  Ranger   Hunter's Mark (bonus action), then Longbow, Shortsword if an enemy is adjacent
  others   Rage if they can, then melee_rush
"""
from . import grid as G

HEAL_BELOW = 0.4   # cleric heals allies at or below 40% HP


def take_turn(flow, c):
    if flow.is_pc(c):
        cls = flow.stats(c).get("class")
        if cls == "Cleric":
            return cleric_turn(flow, c)
        if cls == "Ranger":
            return ranger_turn(flow, c)
        return brute_turn(flow, c)
    tactic = c.get("ai", {}).get("tactic", "melee_rush")
    return {
        "melee_rush": melee_rush, "swarm": swarm, "flee_at_half": flee_at_half,
        "hit_and_run": hit_and_run, "ranged_kite": ranged_kite, "guard": guard,
        "support": ranged_kite,
    }.get(tactic, melee_rush)(flow, c)


# ------------------------------------------------------------------ helpers
def _do(flow, c, action, out):
    """Perform an action; append its events. Returns True if it was accepted."""
    evs, _ = flow.perform(c["id"], action)
    out += evs
    out += flow.check_end()
    return not any(e["type"] == "rejected" for e in evs)


def _done(flow, c):
    return not c["alive"] or not flow.in_combat() or flow.state["mode"] in ("game_over", "victory")


def nearest(flow, c, candidates):
    cp = G.pos(c["position"])
    return min(candidates, key=lambda o: (G.distance_ft(cp, o["position"]), o["hp"], o["id"]), default=None)


def pick_target(flow, c, weakest=False, visible_first=False):
    foes = flow.living_hostiles_of(c)
    if not foes:
        return None
    if visible_first:
        g = flow.grid()
        seen = [f for f in foes if g.line_of_sight(c["position"], f["position"])]
        foes = seen or foes
        return nearest(flow, c, foes)
    if weakest:
        return min(foes, key=lambda o: (o["hp"], G.distance_ft(c["position"], o["position"]), o["id"]))
    ai = c.get("ai")
    if ai and ai.get("target_id"):
        cur = flow.get(ai["target_id"])
        if cur and cur["alive"]:
            return cur
    return nearest(flow, c, foes)


def cells_around(p, radius_ft, max_cells=60):
    x, y = G.pos(p)
    n = min(radius_ft // 5, max_cells)
    return [(x + dx, y + dy) for dx in range(-n, n + 1) for dy in range(-n, n + 1) if (dx, dy) != (0, 0)]


def approach(flow, c, target, within_ft, out, allow_dash=True):
    """Move toward target until within `within_ft` of it with line of sight.
    Dashes if needed and allowed. Returns True if in position afterwards."""
    g = flow.grid()

    def in_position(p):
        return G.distance_ft(p, target["position"]) <= within_ft and g.line_of_sight(p, target["position"])
    if in_position(c["position"]):
        return True
    goals = [p for p in cells_around(target["position"], within_ft)
             if g.in_bounds(p) and not g.blocks_movement(p) and in_position(p)]
    budget = flow.turn(c)["movement_left_ft"]
    path, feet, reached = G.best_step_toward(g, c, goals, budget, can_swim=flow.can_swim(c))
    if not reached and allow_dash and flow.turn(c)["action"]:
        # too far to reach this turn: Dash, then go as far as possible
        full, _, _ = G.best_step_toward(g, c, goals, budget + flow.speed(c), can_swim=flow.can_swim(c))
        if len(full) > len(path):
            _do(flow, c, {"type": "dash"}, out)
            budget = flow.turn(c)["movement_left_ft"]
            path, feet, reached = G.best_step_toward(flow.grid(), c, goals, budget,
                                                     can_swim=flow.can_swim(c))
    if path:
        _do(flow, c, {"type": "move", "to": G.as_dict(path[-1])}, out)
    return c["alive"] and in_position(c["position"])


def retreat(flow, c, out, keep_in_range_of=None, max_ft=None):
    """Move to the reachable cell furthest from all hostiles. If keep_in_range_of
    is (target, range_ft) the cell must stay within that range with line of sight."""
    foes = flow.living_hostiles_of(c)
    if not foes:
        return
    g = flow.grid()
    cells = flow.reachable(c["id"])
    if max_ft is not None:
        cells = {p: f for p, f in cells.items() if f <= max_ft}

    def score(p):
        return min(G.distance_ft(p, f["position"]) for f in foes)
    options = list(cells)
    if keep_in_range_of:
        tgt, rng = keep_in_range_of
        options = [p for p in options if G.distance_ft(p, tgt["position"]) <= rng
                   and g.line_of_sight(p, tgt["position"])]
    here = score(c["position"])
    best = max(options, key=lambda p: (score(p), -cells[p], p), default=None)
    if best is not None and score(best) > here:
        _do(flow, c, {"type": "move", "to": G.as_dict(best)}, out)


def weapon_options(flow, c):
    melee, ranged = [], []
    for w in flow.data.weapons(flow.stats(c)):
        kind, _, _ = G.weapon_range(w)
        (melee if kind == "melee" else ranged).append(w)
    return melee, ranged


def attack_if_adjacent(flow, c, out, prefer=None):
    """Attack prefer (if adjacent) or any adjacent hostile with a melee weapon."""
    if not flow.turn(c)["action"]:
        return False
    melee, _ = weapon_options(flow, c)
    if not melee:
        return False
    reach = flow.reach_ft(c)
    foes = [f for f in flow.living_hostiles_of(c) if G.distance_ft(c["position"], f["position"]) <= reach]
    if not foes:
        return False
    target = prefer if prefer in foes else min(foes, key=lambda f: (f["hp"], f["id"]))
    return _do(flow, c, {"type": "attack", "target_id": target["id"], "weapon": melee[0]["name"]}, out)


def _set_target(c, target):
    if "ai" in c and target is not None:
        c["ai"]["target_id"] = target["id"]


# ------------------------------------------------------------------ enemy tactics
def melee_rush(flow, c, weakest=False):
    out = []
    target = pick_target(flow, c, weakest)
    if target is None:
        return out
    _set_target(c, target)
    melee, ranged = weapon_options(flow, c)
    if not melee and ranged:
        return ranged_kite(flow, c)
    if not attack_if_adjacent(flow, c, out, prefer=target):
        approach(flow, c, target, flow.reach_ft(c), out)
        if not _done(flow, c):
            attack_if_adjacent(flow, c, out, prefer=target)
    return out


def swarm(flow, c):
    return melee_rush(flow, c, weakest=True)


def flee_at_half(flow, c):
    if c["hp"] * 2 > c["max_hp"]:
        return melee_rush(flow, c)
    out = []
    if not flow.rules.effect(c, "Fleeing"):
        flow.rules.add_effect(c, "Fleeing", source_id=c["id"], rounds_left=None)
        out.append({"type": "condition", "seq": 0, "actor_id": c["id"], "target_id": c["id"],
                    "condition": "Fleeing", "change": "applied", "source": "flee_at_half"})
    adjacent = any(G.adjacent(f["position"], c["position"]) for f in flow.living_hostiles_of(c))
    if flow.turn(c)["action"]:
        _do(flow, c, {"type": "disengage" if adjacent else "dash"}, out)
    retreat(flow, c, out)
    return out


def hit_and_run(flow, c):
    out = melee_rush(flow, c)
    if not _done(flow, c) and not flow.turn(c)["action"] and flow.turn(c)["movement_left_ft"] > 0:
        retreat(flow, c, out)
    return out


def ranged_kite(flow, c):
    out = []
    _, ranged = weapon_options(flow, c)
    if not ranged:
        return melee_rush(flow, c)
    w = ranged[0]
    _, normal, _ = G.weapon_range(w, thrown=True)
    target = pick_target(flow, c, visible_first=True)
    if target is None:
        return out
    _set_target(c, target)
    if any(G.adjacent(f["position"], c["position"]) for f in flow.living_hostiles_of(c)):
        retreat(flow, c, out, keep_in_range_of=(target, normal))
    if _done(flow, c):
        return out
    in_place = approach(flow, c, target, normal, out, allow_dash=False)
    if in_place and not _done(flow, c) and flow.turn(c)["action"]:
        _do(flow, c, {"type": "attack", "target_id": target["id"], "weapon": w["name"]}, out)
    return out


def guard(flow, c):
    out = []
    foes = [f for f in flow.living_hostiles_of(c) if G.distance_ft(c["position"], f["position"]) <= 15]
    if not foes:
        return out  # hold position
    target = nearest(flow, c, foes)
    _set_target(c, target)
    if not attack_if_adjacent(flow, c, out, prefer=target):
        approach(flow, c, target, flow.reach_ft(c), out, allow_dash=False)
        if not _done(flow, c):
            attack_if_adjacent(flow, c, out, prefer=target)
    return out


# ------------------------------------------------------------------ ally tactics
def _has_slot(c, level=1):
    s = c["resources"]["spell_slots"].get(str(level))
    return s is not None and s["used"] < s["max"]


def _prepared(c, spell):
    return spell in c["resources"].get("prepared_spells", [])


def cleric_turn(flow, c):  # noqa: C901
    out = []
    party = [p for p in flow.state["party"] if p["alive"]]
    hurt = [p for p in party if p["hp"] <= p["max_hp"] * HEAL_BELOW]
    if hurt and _has_slot(c) and _prepared(c, "cure_wounds"):
        patient = min(hurt, key=lambda p: (p["hp"] / p["max_hp"], p["id"]))
        if patient["id"] == c["id"] or approach(flow, c, patient, 5, out, allow_dash=False):
            if _do(flow, c, {"type": "cast", "spell_id": "cure_wounds", "slot_level": 1,
                             "target_ids": [patient["id"]]}, out):
                return out
    if _done(flow, c):
        return out
    if flow.state["combat"]["round"] == 1 and not c.get("concentrating_on") \
            and _has_slot(c) and _prepared(c, "bless"):
        near = [p for p in party if G.distance_ft(c["position"], p["position"]) <= 30]
        near.sort(key=lambda p: (p["controller"] != "player", p["id"] != c["id"], p["id"]))
        if _do(flow, c, {"type": "cast", "spell_id": "bless", "slot_level": 1,
                         "target_ids": [p["id"] for p in near[:3]]}, out):
            return out
    if _done(flow, c):
        return out
    target = pick_target(flow, c, visible_first=True)
    if target is None:
        return out
    if attack_if_adjacent(flow, c, out, prefer=target):
        return out
    in_place = approach(flow, c, target, 60, out, allow_dash=False)
    if in_place and not _done(flow, c) and _prepared(c, "sacred_flame"):
        _do(flow, c, {"type": "cast", "spell_id": "sacred_flame", "target_ids": [target["id"]]}, out)
    return out


def ranger_turn(flow, c):
    out = []
    target = pick_target(flow, c, visible_first=True)
    if target is None:
        return out
    g = flow.grid()
    conc = c.get("concentrating_on") or {}
    free = c["resources"]["uses"].get("favored_enemy")
    can_mark = _prepared(c, "hunters_mark") and (
        (free and free["used"] < free["max"]) or _has_slot(c))
    if conc.get("spell_id") != "hunters_mark" and can_mark \
            and G.distance_ft(c["position"], target["position"]) <= 90 \
            and g.line_of_sight(c["position"], target["position"]):
        _do(flow, c, {"type": "cast", "spell_id": "hunters_mark", "target_ids": [target["id"]]}, out)
    if _done(flow, c):
        return out
    melee, ranged = weapon_options(flow, c)
    adjacent = [f for f in flow.living_hostiles_of(c) if G.adjacent(f["position"], c["position"])]
    if adjacent and melee:
        prefer = target if target in adjacent else adjacent[0]
        _do(flow, c, {"type": "attack", "target_id": prefer["id"], "weapon": melee[0]["name"]}, out)
        return out
    if ranged:
        _, normal, _ = G.weapon_range(ranged[0])
        in_place = approach(flow, c, target, normal, out, allow_dash=False)
        if in_place and not _done(flow, c):
            _do(flow, c, {"type": "attack", "target_id": target["id"], "weapon": ranged[0]["name"]}, out)
        return out
    return out + melee_rush(flow, c)


def brute_turn(flow, c):
    out = []
    rage = c.get("resources", {}).get("uses", {}).get("rage")
    if rage and rage["used"] < rage["max"] and not flow.rules.effect(c, "Rage"):
        _do(flow, c, {"type": "use_feature", "feature": "rage"}, out)
    return out + melee_rush(flow, c)
