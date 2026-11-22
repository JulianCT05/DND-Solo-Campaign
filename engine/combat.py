"""Combat flow (Leena's part of the engine).

This module decides WHETHER an action is allowed right now and handles
everything about time and space:

  * exploration <-> combat mode switch
  * initiative, turn order, rounds
  * the per-turn budget: action, bonus action, reaction, movement, free object interaction
  * grid movement, range, line of sight, difficult terrain, opportunity attacks
  * simple actions: dash, dodge, disengage, help, hide, search, stand_up
  * doors/chests/levers (interact), talk, rest, end_turn
  * running enemy and ally turns (tactics.py)

When an action is legal, attacks, spells, features, items and checks are handed
to EJ's Rules.resolve(), which rolls dice and returns events. Everything comes
back as turn_result objects that match action_event.schema.json.

Entry point:
    flow = CombatFlow(state, GameData(), Dice(state["rng_seed"]))
    results = flow.submit(action_request)   # list of turn_result dicts
"""
from . import grid as G
from .data import GameData, slug
from .dice import Dice
from .events import EventLog
from .rules import Rules
from .spells import SPELLS

SCHEMA_VERSION = "1.0.0"
RECENT_EVENTS_MAX = 30
# How far an awake enemy can spot the party, by the room's light (state.location.light).
# Darkvision treats darkness as dim and dim as bright, out to its range.
# Blindsight notices anything within its range.
SPOT_FT = {"bright": 60, "dim": 30, "darkness": 0}
DIM_PERCEPTION_PENALTY = 5   # lightly obscured: disadvantage on Perception = -5 to passive
HIDE_DC = 15                 # 2024 Hide action
FLEE_ESCAPE_FT = 60      # a fleeing enemy this far from (or out of sight of) the party leaves combat
FOLLOW_FT = 10           # allies stay this close to the player while exploring
MAX_AI_TURNS = 200       # safety stop for the automatic turn loop

INCAPACITATING = {"incapacitated", "paralyzed", "stunned", "unconscious", "petrified"}
SPEED_ZERO = {"grappled", "restrained", "paralyzed", "stunned", "unconscious", "petrified"}

# Range and casting time for the spells EJ's engine resolves. range 0 = self, 5 = touch.
SPELL_INFO = {
    "sacred_flame":    {"range_ft": 60,  "time": "action",       "hostile": True, "ignores_cover": True},
    "guiding_bolt":    {"range_ft": 120, "time": "action",       "hostile": True},
    "cure_wounds":     {"range_ft": 5,   "time": "action",       "hostile": False},
    "bless":           {"range_ft": 30,  "time": "action",       "hostile": False},
    "shield_of_faith": {"range_ft": 60,  "time": "bonus_action", "hostile": False},
    "hunters_mark":    {"range_ft": 90,  "time": "bonus_action", "hostile": True},
}

# What each feature / item costs. Anything not listed costs an action.
FEATURE_COST = {"rage": "bonus_action"}
ITEM_COST = {"potion of healing": "bonus_action"}   # 2024 rules: drinking a potion is a Bonus Action

SIMPLE_ACTIONS = {"dash", "dodge", "disengage", "help", "hide", "ready", "search", "stand_up"}


class CombatFlow:
    def __init__(self, state, data=None, dice=None):
        self.state = state
        self.data = data or GameData()
        self.dice = dice or Dice(state.get("rng_seed"))
        self.rules = Rules(state, self.data, self.dice)
        self.full_log = []   # every event this process has produced; the API appends it to disk
        for c in self.everyone():
            c.setdefault("size", self.stats(c).get("size", "Medium"))

    # =================================================================== lookups
    def everyone(self):
        return self.state["party"] + self.state["enemies"]

    def get(self, cid):
        return self.rules.get(cid)

    def stats(self, c):
        return self.data.stats(c["ref"])

    @staticmethod
    def is_pc(c):
        return "controller" in c

    def hostile(self, a, b):
        return self.is_pc(a) != self.is_pc(b)

    def player(self):
        return next(c for c in self.state["party"] if c["controller"] == "player")

    def grid(self):
        return G.Grid(self.state["map"], self.everyone())

    def speed(self, c):
        if any(cond["name"] in SPEED_ZERO for cond in c.get("conditions", [])):
            return 0
        return G.parse_speed(self.stats(c).get("speed", 30))

    def can_swim(self, c):
        return G.has_swim_speed(self.stats(c).get("speed"))

    def incapacitated(self, c):
        return any(cond["name"] in INCAPACITATING for cond in c.get("conditions", []))

    def fled(self, c):
        return any(e["name"] == "Fled" for e in c.get("effects", []))

    def in_combat(self):
        return self.state["mode"] == "combat" and self.state.get("combat")

    def active_id(self):
        cb = self.state.get("combat")
        if not cb or not cb["initiative_order"]:
            return None
        return cb["initiative_order"][cb["active_index"]]["id"]

    def living_hostiles_of(self, c):
        return [o for o in self.everyone() if o["alive"] and not self.fled(o) and self.hostile(c, o)]

    def reach_ft(self, c):
        """Melee reach: the longest melee weapon reach, normally 5 ft."""
        best = 5
        for w in self.data.weapons(self.stats(c)):
            kind, r, _ = G.weapon_range(w)
            if kind == "melee":
                best = max(best, r)
        return best

    def melee_weapon(self, c):
        for w in self.data.weapons(self.stats(c)):
            if G.weapon_range(w)[0] == "melee":
                return w
        return None

    # =================================================================== turn economy
    def fresh_turn(self, c):
        return {"action": True, "bonus_action": True, "reaction": True,
                "movement_left_ft": self.speed(c), "free_interaction": True,
                "once_per_turn_used": []}

    def turn(self, c):
        if "turn" not in c:
            c["turn"] = self.fresh_turn(c)
        c["turn"].setdefault("free_interaction", True)
        c["turn"].setdefault("once_per_turn_used", [])
        return c["turn"]

    def spend(self, c, cost):
        """cost: 'action' | 'bonus_action' | 'reaction' | None (free).
        Returns None if paid, else a (reason_code, message) pair.
        Outside combat nothing is spent."""
        if cost is None or not self.in_combat():
            return None
        t = self.turn(c)
        if not t[cost]:
            label = {"action": "action", "bonus_action": "bonus action", "reaction": "reaction"}[cost]
            return ("no_action_left", f"You have no {label} left this turn.")
        t[cost] = False
        return None

    def refund(self, c, cost):
        if cost and self.in_combat():
            self.turn(c)[cost] = True

    # =================================================================== public entry
    def submit(self, request):
        """Process one action_request from the parser or the GUI.
        Returns a list of turn_result dicts: the player's, then one per
        enemy/ally turn that ran automatically afterwards."""
        actor_id = request["actor_id"]
        results = []

        if request.get("clarification_needed"):
            results.append(self._result(actor_id, "needs_clarification", [],
                                        hint=request["clarification_needed"]))
            return results

        events = []
        reason = self._gatekeep(actor_id)
        if reason:
            events.append(self._reject(actor_id, *reason))
            results.append(self._result(actor_id, "rejected", events))
            return results

        accepted = 0
        hint = None
        for action in request.get("actions", []):
            was_fighting = bool(self.in_combat())
            evs, h = self.perform(actor_id, action)
            hint = h or hint
            events += evs
            if any(e["type"] == "rejected" for e in evs):
                break
            accepted += 1
            events += self.check_end()
            if self.state["mode"] in ("game_over", "victory"):
                break
            if action["type"] == "end_turn" or (self.in_combat() and self.active_id() != actor_id):
                break
            if was_fighting and not self.in_combat():
                break  # the fight just ended; the rest of the request was meant for combat
        # nothing left to do this turn -> end it automatically
        if accepted and self.in_combat() and self.active_id() == actor_id and self._turn_spent(self.get(actor_id)):
            events += self.end_turn(actor_id)

        status = "accepted" if accepted else "rejected"
        if not request.get("actions"):
            events.append(self._reject(actor_id, "unknown_command", "No action was given."))
            status = "rejected"
        if accepted:
            self.state["turn_counter"] += 1
        results.append(self._result(actor_id, status, events, hint=hint))
        results += self.run_ai_turns()
        return results

    def _gatekeep(self, actor_id):
        mode = self.state["mode"]
        if mode == "game_over":
            return ("condition_prevents", "The adventure is over: your character has died.")
        if mode == "victory":
            return ("condition_prevents", "The adventure is complete.")
        actor = self.get(actor_id)
        if actor is None or not self.is_pc(actor) or actor["controller"] != "player":
            return ("invalid_target", f"{actor_id} isn't the player's character.")
        if not actor["alive"]:
            return ("condition_prevents", f"{actor['name']} is dead.")
        if self.in_combat() and self.active_id() != actor_id:
            other = self.get(self.active_id())
            return ("not_your_turn", f"It's {other['name']}'s turn, not yours.")
        return None

    def _turn_spent(self, c):
        t = self.turn(c)
        return not t["action"] and not t["bonus_action"] and t["movement_left_ft"] == 0

    # =================================================================== dispatch
    def perform(self, actor_id, action):
        """Validate and resolve one action. Returns (events, narration_hint)."""
        actor = self.get(actor_id)
        t = action["type"]
        if self.in_combat() and self.incapacitated(actor) and t != "end_turn":
            return [self._reject(actor_id, "condition_prevents",
                                 f"{actor['name']} is incapacitated and can't act.")], None
        handler = {
            "move": self.do_move, "attack": self.do_attack, "cast": self.do_cast,
            "use_feature": self.do_feature, "use_item": self.do_item, "check": self.do_check,
            "interact": self.do_interact, "talk": self.do_talk, "rest": self.do_rest,
            "end_turn": self.do_end_turn, "rules_question": self.do_rules_question,
        }.get(t)
        if handler is None and t in SIMPLE_ACTIONS:
            handler = self.do_simple
        if handler is None:
            return [self._reject(actor_id, "unknown_command", f"Unknown action '{t}'.")], None
        out = handler(actor, action)
        if isinstance(out, tuple):
            return out
        return out, None

    # =================================================================== movement
    def do_move(self, actor, action):
        return self.move(actor, G.pos(action["to"]))

    def move(self, actor, dest, follow=True):
        aid = actor["id"]
        g = self.grid()
        combat = self.in_combat()
        crawling = any(c["name"] == "prone" for c in actor.get("conditions", []))
        budget = self.turn(actor)["movement_left_ft"] if combat else G.INF
        if dest == G.pos(actor["position"]):
            return [self._reject(aid, "blocked_path", "You're already standing there.")]
        if combat and budget <= 0:
            return [self._reject(aid, "not_enough_movement", "You have no movement left this turn.")]
        path, feet = G.path_to(g, actor, dest, budget, crawling, self.can_swim(actor))
        if path is None:
            if feet == "not_enough_movement":
                return [self._reject(aid, "not_enough_movement",
                                     f"That's too far: you have {budget} ft of movement left.")]
            return [self._reject(aid, "blocked_path", "You can't get there from here.")]

        events, walked, provoked, spent = [], [], [], 0
        cur = G.pos(actor["position"])
        for cell in path:
            if not combat and actor.get("controller") == "player" and walked and self.check_aggro(dry_run=True):
                break  # spotted: stop where you are, combat starts below
            if combat:
                for oa_events, enemy_id in self._opportunity_attacks(actor, cur, cell):
                    provoked.append(enemy_id)
                    events += oa_events
                if not actor["alive"]:
                    break
            before = set(G.footprint(actor, cur))
            entered = [p for p in G.footprint(actor, cell) if p not in before] or [cell]
            step = max(g.enter_cost(p, self.can_swim(actor)) for p in entered) * (2 if crawling else 1)
            spent += step
            walked.append(cell)
            cur = cell
            actor["position"] = G.as_dict(cur)
            if combat:
                events += self._readied_triggers(actor)
                if not actor["alive"]:
                    break
        if combat:
            t = self.turn(actor)
            t["movement_left_ft"] = max(0, t["movement_left_ft"] - spent)
        move_ev = {"type": "move", "seq": 0, "actor_id": aid,
                   "path": [G.as_dict(c) for c in walked] or [G.as_dict(cur)],
                   "feet_used": spent}
        if provoked:
            move_ev["provoked_opportunity_attack_from"] = provoked
        events.insert(0, move_ev)

        if not combat and follow and actor.get("controller") == "player":
            events += self._allies_follow(actor)
            events += self.check_aggro()
        return events

    def _opportunity_attacks(self, mover, frm, to):
        """Yields (events, attacker_id) for every hostile whose reach the mover
        leaves when stepping frm -> to. Disengage prevents them."""
        if self.rules.effect(mover, "Disengage"):
            return
        for h in self.living_hostiles_of(mover):
            if not mover["alive"]:
                return
            if self.incapacitated(h) or not self.turn(h)["reaction"]:
                continue
            if h.get("controller") == "player" and not self.state["flags"].get("auto_opportunity_attacks", True):
                continue  # the player turned automatic opportunity attacks off
            reach = self.reach_ft(h)
            if G.distance_ft(h, G.at(mover, frm)) <= reach and G.distance_ft(h, G.at(mover, to)) > reach:
                w = self.melee_weapon(h)
                if w is None:
                    continue
                self.turn(h)["reaction"] = False
                evs = self._resolve_with_cover(h["id"], {"type": "attack", "target_id": mover["id"],
                                                          "weapon": w["name"]},
                                               {mover["id"]: self.cover_bonus(h, mover)})
                yield evs, h["id"]

    # =================================================================== ready
    def do_ready(self, actor, action):
        """Ready an attack (2024 Ready action, attack only in this prototype).
        The trigger is fixed: an enemy (the target_id one, if given) moves into
        reach of your melee weapon, or into range and sight of your ranged one.
        It costs your action now and your reaction when it fires, and lasts
        until the start of your next turn."""
        aid = actor["id"]
        if not self.in_combat():
            return [self._reject(aid, "unknown_command", "You can only ready an action during combat.")]
        target = None
        if action.get("target_id"):
            target = self.get(action["target_id"])
            if target is None or not target["alive"] or not self.hostile(actor, target):
                return [self._reject(aid, "invalid_target", "Ready needs an enemy as the target, or no target.")]
        melee = [w for w in self.data.weapons(self.stats(actor)) if G.weapon_range(w)[0] == "melee"]
        ranged = [w for w in self.data.weapons(self.stats(actor)) if G.weapon_range(w)[0] == "ranged"]
        far = target is not None and G.distance_ft(actor, target) > self.reach_ft(actor)
        weapon = (ranged[0] if (far or not melee) and ranged else (melee or ranged or [None])[0])
        if weapon is None:
            return [self._reject(aid, "no_resource", f"{actor['name']} has no attack to ready.")]
        if not self.turn(actor)["reaction"]:
            return [self._reject(aid, "no_action_left", "You've already used your reaction this round.")]
        why = self.spend(actor, "action")
        if why:
            return [self._reject(aid, *why)]
        e = self.rules.add_effect(actor, "Readied", source_id=aid,
                                  target_id=target["id"] if target else None, rounds_left=None)
        e["data"] = {"weapon": weapon["name"]}
        ev = {"type": "condition", "seq": 0, "actor_id": aid, "target_id": aid,
              "condition": "Readied", "change": "applied", "source": "ready"}
        hint = f"{actor['name']} readies {weapon['name']}" + (f" against {target['name']}" if target else "")
        return [ev], hint

    def _readied_triggers(self, mover):
        """After each square `mover` steps into, fire any enemy's readied attack
        whose trigger it just met."""
        events = []
        for r in self.everyone():
            if not mover["alive"]:
                break
            e = self.rules.effect(r, "Readied")
            if e is None or not r["alive"] or not self.hostile(r, mover) or self.incapacitated(r):
                continue
            if e.get("target_id") and e["target_id"] != mover["id"]:
                continue
            if not self.turn(r)["reaction"]:
                continue
            weapon = self.data.find_weapon(self.stats(r), e.get("data", {}).get("weapon"))
            if weapon is None:
                continue
            kind, normal, long_ = G.weapon_range(weapon)
            dist = G.distance_ft(r, mover)
            if kind == "melee" and dist > normal:
                continue
            err, adv, dis, _ = self.attack_situation(r, mover, weapon)
            if err:
                continue
            self.turn(r)["reaction"] = False
            self.rules.remove_effect(r, "Readied")
            events.append({"type": "condition", "seq": 0, "actor_id": r["id"], "target_id": r["id"],
                           "condition": "Readied", "change": "removed", "source": "trigger"})
            events += self._resolve_with_cover(r["id"], {"type": "attack", "target_id": mover["id"],
                                                         "weapon": weapon["name"]},
                                               {mover["id"]: self.cover_bonus(r, mover)}, adv, dis)
        return events

    def _allies_follow(self, player):
        events = []
        ppos = G.pos(player["position"])
        for ally in self.state["party"]:
            if ally is player or not ally["alive"]:
                continue
            if G.distance_ft(ally, ppos) <= FOLLOW_FT:
                continue
            g = self.grid()
            goals = [(ppos[0] + dx, ppos[1] + dy) for dx in range(-2, 3) for dy in range(-2, 3)
                     if (dx, dy) != (0, 0)]
            path, feet, _ = G.best_step_toward(g, ally, goals, G.INF, can_swim=self.can_swim(ally))
            if path:
                ally["position"] = G.as_dict(path[-1])
                events.append({"type": "move", "seq": 0, "actor_id": ally["id"],
                               "path": [G.as_dict(c) for c in path], "feet_used": feet})
        return events

    def reachable(self, actor_id):
        """Cells the actor could move to right now: {(x, y): feet}. For GUI highlights."""
        actor = self.get(actor_id)
        budget = self.turn(actor)["movement_left_ft"] if self.in_combat() else 60
        crawling = any(c["name"] == "prone" for c in actor.get("conditions", []))
        return G.reachable_cells(self.grid(), actor, budget, crawling, self.can_swim(actor))

    # =================================================================== attacks
    def attack_situation(self, actor, target, weapon, thrown=False):
        """Range + line of sight + map-based advantage. Returns
        (error_or_None, adv_list, dis_list, is_ranged)."""
        g = self.grid()
        kind, normal, long_ = G.weapon_range(weapon, thrown)
        dist = G.distance_ft(actor, target)
        if dist > long_:
            return (("out_of_range", f"{target['name']} is {dist} ft away; your {weapon['name']} "
                     f"reaches {long_} ft."), [], [], kind == "ranged")
        if not g.line_of_sight(actor, target):
            return (("no_line_of_sight", f"You can't see {target['name']} from there."), [], [],
                    kind == "ranged")
        if g.cover(actor, target) is None:
            return (("no_line_of_sight", f"{target['name']} is behind total cover."), [], [],
                    kind == "ranged")
        adv, dis = [], []
        if kind == "ranged":
            if dist > normal:
                dis.append("long_range")
            if any(G.adjacent(h, actor) and not self.incapacitated(h)
                   for h in self.living_hostiles_of(actor)):
                dis.append("hostile_adjacent")
        if self.rules.effect(target, "Dodge") and not self.incapacitated(target):
            dis.append("dodge")
        if self.rules.effect(target, "Helped"):
            adv.append("help")
        if self.rules.effect(actor, "Hidden"):
            adv.append("hidden")
        return None, adv, dis, kind == "ranged"

    def cover_bonus(self, attacker, target):
        """0, 2 (half) or 5 (three-quarters). Total cover is handled as no line of sight."""
        return self.grid().cover(attacker, target) or 0

    def _resolve_with_cover(self, actor_id, action, bonuses, adv=(), dis=()):
        """Run EJ's resolve() with a temporary Cover effect on each target that
        has one, so his AC and Dex-save code adds the bonus. Removed afterwards."""
        added = []
        for tid, bonus in bonuses.items():
            t = self.get(tid)
            if bonus and t is not None:
                t.setdefault("effects", []).append({"name": "Cover", "source_id": "_cover",
                                                    "data": {"bonus": bonus}})
                added.append(t)
        try:
            return self.rules.resolve(actor_id, action, adv=adv, dis=dis)
        finally:
            for t in added:
                self.rules.remove_effect(t, "Cover", source_id="_cover")

    def _consume_attack_buffs(self, actor, target):
        for c, name in ((target, "Helped"), (actor, "Hidden")):
            if self.rules.effect(c, name):
                self.rules.remove_effect(c, name)

    def do_attack(self, actor, action):
        aid = actor["id"]
        target = self.get(action["target_id"])
        if target is None or not target["alive"]:
            return [self._reject(aid, "invalid_target", "That target isn't there or is already dead.")]
        if target["id"] == aid:
            return [self._reject(aid, "invalid_target", "You can't attack yourself.")]
        if not self.in_combat() and self.hostile(actor, target):
            return self._open_fight(actor, action)
        weapon = self.data.find_weapon(self.stats(actor), action.get("weapon"))
        if weapon is None:
            name = action.get("weapon") or "weapon"
            return [self._reject(aid, "invalid_target", f"{actor['name']} has no {name}.")]

        mods = list(action.get("modifiers", []))
        notes = (weapon.get("notes") or "").lower()
        thrown = "thrown" in mods
        if not thrown and "thrown" in notes and \
                G.distance_ft(actor, target) > self.reach_ft(actor):
            thrown = True
            mods.append("thrown")
        err, adv, dis, _ranged = self.attack_situation(actor, target, weapon, thrown)
        if err:
            return [self._reject(aid, *err)]

        ammo = None
        if thrown:
            ammo = next((i for i in self.state.get("inventory", [])
                         if i["owner_id"] == aid and i["item"].lower() == weapon["name"].lower()), None)
            if ammo is not None and ammo["qty"] <= 0:
                return [self._reject(aid, "no_resource", f"You have no {weapon['name']}s left to throw.")]

        why = self.spend(actor, "action")
        if why:
            return [self._reject(aid, *why)]
        act = {"type": "attack", "target_id": target["id"], "weapon": weapon["name"]}
        if mods:
            act["modifiers"] = mods
        cover = self.cover_bonus(actor, target)
        events = self._resolve_with_cover(aid, act, {target["id"]: cover}, adv, dis)
        if any(e["type"] == "rejected" for e in events):
            self.refund(actor, "action")
            return events
        note = None
        if cover:
            note = f"{target['name']} has {'half' if cover == 2 else 'three-quarters'} cover (+{cover} AC)"
        self._consume_attack_buffs(actor, target)
        if self.rules.effect(actor, "Hidden"):
            self.rules.remove_effect(actor, "Hidden")
        if ammo is not None:
            ammo["qty"] -= 1
            events.append({"type": "resource", "seq": 0, "actor_id": aid, "resource": slug(weapon["name"]),
                           "delta": -1, "remaining": ammo["qty"]})
        return (events, note) if note else events

    # =================================================================== spells
    def do_cast(self, actor, action):
        aid = actor["id"]
        sid = action["spell_id"]
        info = SPELL_INFO.get(sid)
        if info is None:
            # let EJ's module produce the right rejection (unknown / not prepared)
            return self.rules.resolve(aid, action)
        targets = [self.get(t) for t in action.get("target_ids", [])]
        if any(t is None for t in targets):
            return [self._reject(aid, "invalid_target", "One of those targets isn't here.")]
        if info["hostile"] and not self.in_combat() and any(self.hostile(actor, t) for t in targets):
            return self._open_fight(actor, action)

        g = self.grid()
        for t in targets:
            if t["id"] == aid:
                continue
            dist = G.distance_ft(actor, t)
            if dist > info["range_ft"]:
                reach = "touch" if info["range_ft"] == 5 else f"{info['range_ft']} ft"
                return [self._reject(aid, "out_of_range",
                                     f"{t['name']} is {dist} ft away; that spell's range is {reach}.")]
            if not g.line_of_sight(actor, t):
                return [self._reject(aid, "no_line_of_sight", f"You can't see {t['name']} from there.")]
            if info["hostile"] and g.cover(actor, t) is None:
                return [self._reject(aid, "no_line_of_sight", f"{t['name']} is behind total cover.")]

        # 2024 rule: only one spell slot can be spent per turn
        leveled = SPELLS.get(sid, {}).get("level", 0) > 0
        if self.in_combat() and leveled and "spell_slot" in self.turn(actor)["once_per_turn_used"]:
            return [self._reject(aid, "no_action_left",
                                 "You've already cast a leveled spell this turn.")]
        cost = info["time"]
        why = self.spend(actor, cost)
        if why:
            return [self._reject(aid, *why)]

        adv, dis = [], []
        if info["hostile"] and targets:
            # spell attacks (Guiding Bolt) get the same ranged-attack penalties
            fake_weapon = {"name": sid, "range": {"normal": info["range_ft"], "long": info["range_ft"]}}
            _, adv, dis, _ = self.attack_situation(actor, targets[0], fake_weapon)
        covers = {}
        if info["hostile"] and not info.get("ignores_cover"):
            covers = {t["id"]: self.cover_bonus(actor, t) for t in targets if t["id"] != aid}
        events = self._resolve_with_cover(aid, action, covers, adv, dis)
        if any(e["type"] == "rejected" for e in events):
            self.refund(actor, cost)
            return events
        if leveled and self.in_combat():
            self.turn(actor)["once_per_turn_used"].append("spell_slot")
        return events

    # =================================================================== features, items, checks
    def do_feature(self, actor, action):
        cost = FEATURE_COST.get(slug(action["feature"]), "action")
        why = self.spend(actor, cost)
        if why:
            return [self._reject(actor["id"], *why)]
        events = self.rules.resolve(actor["id"], action)
        if any(e["type"] == "rejected" for e in events):
            self.refund(actor, cost)
        return events

    def do_item(self, actor, action):
        target_id = action.get("target_id", actor["id"])
        target = self.get(target_id)
        if target is None:
            return [self._reject(actor["id"], "invalid_target", "That target isn't here.")]
        if target_id != actor["id"] and G.distance_ft(actor, target) > 5:
            return [self._reject(actor["id"], "out_of_range", f"You need to be next to {target['name']}.")]
        cost = ITEM_COST.get(action["item"].lower(), "action")
        why = self.spend(actor, cost)
        if why:
            return [self._reject(actor["id"], *why)]
        events = self.rules.resolve(actor["id"], action)
        if any(e["type"] == "rejected" for e in events):
            self.refund(actor, cost)
        return events

    def do_check(self, actor, action):
        feature = self.feature(action.get("target_id"))
        if feature and G.distance_ft(actor, feature) > 5:
            return [self._reject(actor["id"], "out_of_range", "You need to be next to that first.")]
        why = self.spend(actor, "action")
        if why:
            return [self._reject(actor["id"], *why)]
        return self.rules.resolve(actor["id"], action)

    # =================================================================== simple actions
    def do_simple(self, actor, action):
        aid = actor["id"]
        t = action["type"]
        combat = self.in_combat()
        if t == "ready":
            return self.do_ready(actor, action)
        if t == "stand_up":
            if not self.rules.has_condition(actor, "prone"):
                return [self._reject(aid, "invalid_target", "You're already standing.")]
            cost_ft = self.speed(actor) // 2
            if combat:
                tt = self.turn(actor)
                if tt["movement_left_ft"] < cost_ft:
                    return [self._reject(aid, "not_enough_movement",
                                         f"Standing up costs {cost_ft} ft of movement.")]
                tt["movement_left_ft"] -= cost_ft
            actor["conditions"] = [c for c in actor["conditions"] if c["name"] != "prone"]
            return [{"type": "condition", "seq": 0, "actor_id": aid, "target_id": aid,
                     "condition": "prone", "change": "removed", "source": "stand_up"}]
        if not combat and t in ("dash", "dodge", "disengage", "help"):
            return [self._reject(aid, "unknown_command", f"You can only {t} during combat.")]

        if t == "help":
            target = self.get(action.get("target_id", ""))
            if target is None or not target["alive"] or not self.hostile(actor, target):
                return [self._reject(aid, "invalid_target", "Help needs an enemy within 5 ft as the target.")]
            if not G.adjacent(actor, target):
                return [self._reject(aid, "out_of_range", f"You need to be next to {target['name']}.")]
        if t == "hide":
            g = self.grid()
            if combat:   # 2024: you must be out of every enemy's line of sight
                seen_by = [h for h in self.living_hostiles_of(actor) if g.line_of_sight(h, actor)]
            else:        # exploring: you can't hide from an enemy that has already noticed you
                was = self.rules.effect(actor, "Hidden")
                if was:
                    self.rules.remove_effect(actor, "Hidden")
                seen_by = [h for h in self.living_hostiles_of(actor)
                           if h.get("ai", {}).get("awake", True) and self.notices(h, actor, g)]
                if was:
                    actor.setdefault("effects", []).append(was)
            if seen_by:
                return [self._reject(aid, "condition_prevents",
                                     f"{seen_by[0]['name']} can see you; get out of sight first.")]

        why = self.spend(actor, "action")
        if why:
            return [self._reject(aid, *why)]

        if t == "dash":
            tt = self.turn(actor)
            tt["movement_left_ft"] += self.speed(actor)
            return [{"type": "state_change", "seq": 0, "actor_id": aid,
                     "field": f"{aid}.turn.movement_left_ft", "value": tt["movement_left_ft"]}]
        if t in ("dodge", "disengage"):
            name = t.capitalize()
            self.rules.add_effect(actor, name, source_id=aid, rounds_left=None)
            return [{"type": "condition", "seq": 0, "actor_id": aid, "target_id": aid,
                     "condition": name, "change": "applied", "source": t, "rounds": 1}]
        if t == "help":
            target = self.get(action["target_id"])
            self.rules.add_effect(target, "Helped", source_id=aid, rounds_left=None)
            return [{"type": "condition", "seq": 0, "actor_id": aid, "target_id": target["id"],
                     "condition": "Helped", "change": "applied", "source": "help", "rounds": 1}]
        if t == "hide":
            log = []
            ok = self._check(log, aid, "dex", "stealth", HIDE_DC, "hide")
            if ok:
                e = self.rules.add_effect(actor, "Hidden", source_id=aid, rounds_left=None)
                e["data"] = {"stealth": log[-1]["roll"]["total"]}
                log.append({"type": "condition", "seq": 0, "actor_id": aid, "target_id": aid,
                            "condition": "Hidden", "change": "applied", "source": "hide"})
            return log
        if t == "search":
            log = []
            self._check(log, aid, "wis", "perception", 10, "search the area")
            return log
        return [self._reject(aid, "unknown_command", f"Unknown action '{t}'.")]

    def _check(self, out, actor_id, ability, skill, dc, intent):
        log = EventLog()
        ok = self.rules.ability_check(log, actor_id, ability, skill, dc=dc, intent=intent)
        out += log.events
        return ok

    # =================================================================== interact
    def feature(self, fid):
        if not fid:
            return None
        return next((f for f in self.state["map"].get("features", []) if f["id"] == fid), None)

    def do_interact(self, actor, action):
        aid = actor["id"]
        f = self.feature(action["object_id"])
        verb = action["verb"]
        if f is None:
            return [self._reject(aid, "invalid_target", f"There's no {action['object_id']} here.")]
        if G.distance_ft(actor, f) > 5:
            return [self._reject(aid, "out_of_range", "You need to be next to that first.")]

        # cost: 'force' and 'disarm' are checks (action); the rest use the free object interaction
        events = []
        if verb in ("force", "disarm"):
            why = self.spend(actor, "action")
            if why:
                return [self._reject(aid, *why)]
        elif self.in_combat() and verb != "examine":
            t = self.turn(actor)
            if t["free_interaction"]:
                t["free_interaction"] = False
            else:
                why = self.spend(actor, "action")
                if why:
                    return [self._reject(aid, "no_action_left",
                                         "You've used your free interaction and have no action left.")]

        state = f.get("state", "closed")
        result = "success"
        if verb == "examine":
            pass
        elif verb == "open":
            if state == "locked":
                result = "failure"
            elif state == "closed":
                f["state"] = "open"
        elif verb == "close":
            if self.grid().occupant(f["position"]) is not None:
                result = "failure"
            elif state == "open":
                f["state"] = "closed"
        elif verb == "unlock":
            key = self.state["flags"].get(f"has_key_{f['id']}") or self.state["flags"].get("has_key")
            if state == "locked" and key:
                f["state"] = "closed"
            elif state == "locked":
                result = "failure"
        elif verb == "force":
            ok = self._check(events, aid, "str", "athletics", f.get("dc", 10), f"force the {f['kind']}")
            if ok and state in ("locked", "closed"):
                f["state"] = "open"
            result = "success" if ok else "failure"
        elif verb == "disarm":
            ok = self._check(events, aid, "dex", "sleight_of_hand", f.get("dc", 10), "disarm the trap")
            if ok:
                f["state"] = "used"
            result = "success" if ok else "failure"
        elif verb in ("pull", "take"):
            if state == "used":
                result = "nothing_found"
            else:
                f["state"] = "used"
        if f.get("state") != state:
            self.state["flags"][f"{f['id']}_{f['state']}"] = True
        events.append({"type": "interact", "seq": 0, "actor_id": aid, "object_id": f["id"],
                       "verb": verb, "result": result})
        return events

    # =================================================================== talk, rules, rest
    def do_talk(self, actor, action):
        ev = {"type": "dialogue", "seq": 0, "actor_id": actor["id"],
              "speaker_id": action.get("target_id") or actor["id"], "needs_llm": True,
              "topic": action["text"][:120]}
        return [ev], f"player says ({action.get('tone', 'neutral')}): {action['text']}"

    def do_rules_question(self, actor, action):
        return [], f"rules_question: {action['question']}"

    def do_rest(self, actor, action):
        if self.in_combat():
            return [self._reject(actor["id"], "condition_prevents", "You can't rest during combat.")]
        if self.check_aggro(dry_run=True):
            return [self._reject(actor["id"], "condition_prevents", "Enemies are close by; you can't rest.")]
        return self.rules.rest(action["kind"])

    # =================================================================== combat start / end
    # =================================================================== spotting
    def senses(self, c):
        """{'darkvision': ft, 'blindsight': ft, 'passive': n} from the stat block's
        senses text, e.g. 'darkvision 60 ft., passive Perception 12'."""
        import re
        text = str(self.stats(c).get("senses") or "")
        def num(pattern, default=0):
            m = re.search(pattern, text, re.I)
            return int(m.group(1)) if m else default
        passive = num(r"passive perception\s*(\d+)", 10)
        return {"darkvision": num(r"darkvision\s*(\d+)"), "blindsight": num(r"blindsight\s*(\d+)"),
                "passive": passive}

    def spot_range(self, watcher):
        """(range_ft, sees_as) for this creature in the room's light."""
        light = self.state.get("location", {}).get("light", "bright")
        dv = self.senses(watcher)["darkvision"]
        if light == "bright":
            return SPOT_FT["bright"], "bright"
        if light == "dim":
            if dv:
                return max(SPOT_FT["dim"], min(dv, SPOT_FT["bright"])), "bright"
            return SPOT_FT["dim"], "dim"
        # darkness
        if dv:
            return min(dv, SPOT_FT["dim"]), "dim"
        return SPOT_FT["darkness"], "dark"

    def notices(self, watcher, target, g=None):
        """Would this enemy notice that party member right now? Needs line of
        sight and the target within spotting range for the light, unless the
        watcher's blindsight covers it. A Hidden target is only noticed if the
        watcher's passive Perception (-5 in dim light) beats the Stealth roll."""
        g = g or self.grid()
        if not watcher["alive"] or not target["alive"]:
            return False
        dist = G.distance_ft(watcher, target)
        if not g.line_of_sight(watcher, target):
            return False
        sn = self.senses(watcher)
        if sn["blindsight"] and dist <= sn["blindsight"]:
            return True
        rng, sees_as = self.spot_range(watcher)
        if dist > rng:
            return False
        hidden = self.rules.effect(target, "Hidden")
        if hidden:
            passive = sn["passive"] - (DIM_PERCEPTION_PENALTY if sees_as == "dim" else 0)
            return passive >= hidden.get("data", {}).get("stealth", HIDE_DC)
        return True

    def check_aggro(self, dry_run=False):
        """In exploration, an awake enemy that notices a party member starts combat."""
        if self.state["mode"] != "exploration":
            return []
        g = self.grid()
        for e in self.state["enemies"]:
            if not e["alive"] or self.fled(e) or not e.get("ai", {}).get("awake", True):
                continue
            for pc in self.state["party"]:
                if self.notices(e, pc, g):
                    if dry_run:
                        return [True]
                    return self.start_combat()
        return []

    def start_combat(self, attacker=None, surprised=None):
        """Roll initiative and switch to combat mode. Enemies that were asleep
        are surprised (disadvantage on initiative) and wake up."""
        if self.in_combat():
            return []
        events = []
        enemies = [e for e in self.state["enemies"] if e["alive"] and not self.fled(e)]
        party = [p for p in self.state["party"] if p["alive"]]
        if surprised is None:
            # asleep, or never noticed the hidden attacker who started the fight
            g = self.grid()
            surprised = [e["id"] for e in enemies if not e.get("ai", {}).get("awake", True)
                         or (attacker is not None and self.rules.effect(attacker, "Hidden")
                             and not self.notices(e, attacker, g))]
        for e in enemies:
            e.setdefault("ai", {"tactic": "melee_rush", "awake": True, "target_id": None})["awake"] = True
        everyone = party + enemies
        events.append({"type": "combat_start", "seq": 0,
                       "participants": [c["id"] for c in everyone],
                       **({"surprised": list(surprised)} if surprised else {})})
        rolled = []
        for c in everyone:
            st = self.stats(c)
            bonus = st["initiative"] if self.is_pc(c) and isinstance(st.get("initiative"), int) \
                else self.data.ability_mod(st, "dex")
            mode = "disadvantage" if c["id"] in surprised else "normal"
            roll, _ = self.dice.d20(bonus, mode)
            events.append({"type": "initiative", "seq": 0, "actor_id": c["id"], "roll": roll})
            dex = self.data.ability_mod(st, "dex")
            rolled.append((roll["total"], bonus, dex, self.is_pc(c), c["id"]))
        # ties: higher bonus, then higher Dex, then the party goes first, then id
        rolled.sort(key=lambda r: (-r[0], -r[1], -r[2], not r[3], r[4]))
        self.state["combat"] = {"round": 1,
                                "initiative_order": [{"id": r[4], "initiative": r[0]} for r in rolled],
                                "active_index": 0}
        if surprised:
            self.state["combat"]["surprised_ids"] = list(surprised)
        self.state["mode"] = "combat"
        events.append({"type": "state_change", "seq": 0, "field": "mode", "value": "combat"})
        for c in everyone:
            c["turn"] = self.fresh_turn(c)
        events += self.begin_turn(self.active_id())
        return events

    def _open_fight(self, actor, action):
        """Attacking or casting at an enemy outside combat starts combat. If the
        attacker wins initiative the action happens straight away; otherwise it
        waits for their turn."""
        events = self.start_combat(attacker=actor)
        if self.active_id() == actor["id"]:
            more, _ = self.perform(actor["id"], action)
            return events + more, None
        return events, "combat started before the action: the player acts on their turn"

    def check_end(self):
        """Combat ends when the player dies (defeat) or no enemy in the fight is
        left standing (victory). The player dying at any time is game over."""
        events = []
        player = self.player()
        if not player["alive"] and self.state["mode"] != "game_over":
            if self.in_combat():
                events.append({"type": "combat_end", "seq": 0, "result": "defeat"})
                events += self.rules.end_combat()
            self.state["combat"] = None
            self.state["mode"] = "game_over"
            events.append({"type": "state_change", "seq": 0, "field": "mode", "value": "game_over"})
            return events
        if not self.in_combat():
            return events
        ids = {i["id"] for i in self.state["combat"]["initiative_order"]}
        fighting = [e for e in self.state["enemies"] if e["id"] in ids and e["alive"] and not self.fled(e)]
        if fighting:
            return events
        events.append({"type": "combat_end", "seq": 0, "result": "victory"})
        events += self.rules.end_combat()
        for c in self.everyone():
            self.rules.remove_effect(c, "Dodge")
            self.rules.remove_effect(c, "Disengage")
            self.rules.remove_effect(c, "Helped")
        self.state["combat"] = None
        final = self.state["flags"].get("final_encounter", False)
        self.state["mode"] = "victory" if final else "exploration"
        events.append({"type": "state_change", "seq": 0, "field": "mode", "value": self.state["mode"]})
        return events

    # =================================================================== turn order
    def begin_turn(self, cid):
        c = self.get(cid)
        c["turn"] = self.fresh_turn(c)
        events = [{"type": "turn_start", "seq": 0, "actor_id": cid,
                   "round": self.state["combat"]["round"]}]
        events += self.rules.start_of_turn(cid)
        # Dodge and Help last until the start of the creature's next turn
        if self.rules.remove_effect(c, "Dodge", source_id=cid):
            events.append({"type": "condition", "seq": 0, "actor_id": cid, "target_id": cid,
                           "condition": "Dodge", "change": "expired"})
        for o in self.everyone():
            self.rules.remove_effect(o, "Helped", source_id=cid)
        if self.rules.remove_effect(c, "Readied"):
            events.append({"type": "condition", "seq": 0, "actor_id": cid, "target_id": cid,
                           "condition": "Readied", "change": "expired"})
        # a creature that was surprised loses that status once its turn comes up
        sur = self.state["combat"].get("surprised_ids")
        if sur and cid in sur:
            sur.remove(cid)
        return events

    def end_turn(self, cid):
        events = self.rules.end_of_turn(cid)
        c = self.get(cid)
        if c and self.rules.remove_effect(c, "Disengage", source_id=cid):
            events.append({"type": "condition", "seq": 0, "actor_id": cid, "target_id": cid,
                           "condition": "Disengage", "change": "expired"})
        events += self.check_end()
        if self.in_combat():
            events += self._advance()
        return events

    def do_end_turn(self, actor, action):
        if not self.in_combat():
            return [], "end_turn outside combat: nothing happens"
        return self.end_turn(actor["id"])

    def _advance(self):
        cb = self.state["combat"]
        order = cb["initiative_order"]
        # drop creatures that died or fled from the order, keeping the pointer right
        current = order[cb["active_index"]]["id"]
        keep = [o for o in order if (self.get(o["id"])["alive"] and not self.fled(self.get(o["id"])))
                or o["id"] == current]
        cb["initiative_order"] = keep
        idx = next(i for i, o in enumerate(keep) if o["id"] == current)
        if not (self.get(current)["alive"] and not self.fled(self.get(current))):
            keep.pop(idx)
            idx -= 1
        events = []
        if not keep:
            return events
        nxt = idx + 1
        if nxt >= len(keep):
            nxt = 0
            cb["round"] += 1
            events += self._tick_areas()
        cb["active_index"] = nxt
        events += self.begin_turn(keep[nxt]["id"])
        return events

    def _tick_areas(self):
        events, keep = [], []
        for a in self.state["map"].get("areas", []):
            if a.get("rounds_left") is not None:
                a["rounds_left"] -= 1
                if a["rounds_left"] <= 0:
                    events.append({"type": "state_change", "seq": 0,
                                   "field": f"map.areas.{a['id']}", "value": "expired"})
                    continue
            keep.append(a)
        if "areas" in self.state["map"]:
            self.state["map"]["areas"] = keep
        return events

    def add_area(self, area):
        """Put a spell or hazard area on the map (shape/origin/size_ft/effect/rounds_left)."""
        self.state["map"].setdefault("areas", []).append(area)
        return [{"type": "state_change", "seq": 0, "field": f"map.areas.{area['id']}", "value": "created"}]

    # =================================================================== AI turns
    def run_ai_turns(self):
        """Run enemy and ally turns until it's the player's turn or combat ends."""
        from .tactics import take_turn
        results = []
        for _ in range(MAX_AI_TURNS):
            if not self.in_combat():
                break
            cid = self.active_id()
            c = self.get(cid)
            if c.get("controller") == "player":
                if self.incapacitated(c):
                    evs = self.end_turn(cid)
                    results.append(self._result(cid, "accepted", evs, hint="player is incapacitated"))
                    continue
                break
            events = []
            if not self.incapacitated(c):
                # the AI may try things that turn out illegal; those attempts
                # are internal and are not shown to the narrator
                events += [e for e in take_turn(self, c) if e["type"] != "rejected"]
            if self.state["mode"] in ("game_over", "victory"):
                results.append(self._result(cid, "accepted", events))
                break
            if self.in_combat() and self.active_id() == cid:
                events += self._maybe_flee_out(c)
                events += self.end_turn(cid)
            self.state["turn_counter"] += 1
            results.append(self._result(cid, "accepted", events))
        return results

    def _maybe_flee_out(self, c):
        """A fleeing enemy that is far away or out of sight leaves the fight."""
        if self.is_pc(c) or not self.rules.effect(c, "Fleeing"):
            return []
        g = self.grid()
        party = [p for p in self.state["party"] if p["alive"]]
        if all(G.distance_ft(c, p) >= FLEE_ESCAPE_FT or
               not g.line_of_sight(c, p) for p in party):
            self.rules.add_effect(c, "Fled", source_id=c["id"], rounds_left=None)
            return [{"type": "state_change", "seq": 0, "actor_id": c["id"],
                     "field": f"{c['id']}.fled", "value": True}]
        return []

    # =================================================================== helpers
    @staticmethod
    def _reject(actor_id, code, message):
        return {"type": "rejected", "seq": 0, "actor_id": actor_id, "reason_code": code, "message": message}

    def _result(self, actor_id, status, events, hint=None):
        for i, e in enumerate(events):
            e["seq"] = i
        self.full_log += [dict(e, turn=self.state["turn_counter"]) for e in events]
        recent = (self.state.get("recent_events", []) + [dict(e) for e in events])[-RECENT_EVENTS_MAX:]
        self.state["recent_events"] = recent
        tr = {"schema_version": SCHEMA_VERSION, "turn": self.state["turn_counter"],
              "actor_id": actor_id, "status": status, "events": events}
        if hint:
            tr["narration_hint"] = hint
        return tr


