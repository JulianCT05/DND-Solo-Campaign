"""Rules resolution (EJ's part of the engine).

Leena's combat loop decides WHETHER an action is allowed right now
(whose turn, action/bonus action left, range, movement). This module decides
WHAT HAPPENS when it resolves, updates the game state, and returns events
that match action_event.schema.json.

Main entry point:
    rules = Rules(state, GameData(), Dice(seed))
    events = rules.resolve("barbarian_1", {"type": "attack", "target_id": "giant_frog_1"})

Prototype scope: no concentration saves, no Undead Fortitude / Relentless
Endurance, no weapon masteries except Vex, Rage lasts until combat ends.
"""
from .data import GameData, RANGED_WEAPONS, SKILL_ABILITY, parse_damage, slug
from .dice import Dice, combine_mode
from .events import EventLog
from .spells import SPELLS

RAGE_RESISTS = {"bludgeoning", "piercing", "slashing"}


class Rules:
    def __init__(self, state, data=None, dice=None):
        self.state = state
        self.data = data or GameData()
        self.dice = dice or Dice(state.get("rng_seed"))

    # ================================================================ public
    def resolve(self, actor_id, action, start_seq=0, adv=(), dis=()):
        """Resolve one action dict from an action_request. Returns a list of events.
        Handles: attack, cast, use_feature, use_item, check. Movement and the
        simple actions (dash, dodge...) belong to the combat-flow module.
        adv / dis: extra advantage or disadvantage sources that depend on the
        map (long range, enemy adjacent to a ranged attacker, Dodge, Help),
        worked out by the combat-flow module. Used by attacks and spell attacks."""
        log = EventLog(start_seq)
        actor = self.get(actor_id)
        if actor is None:
            log.reject(actor_id, "invalid_target", f"There is no creature called {actor_id}.")
            return log.events
        if not actor["alive"]:
            log.reject(actor_id, "condition_prevents", f"{actor['name']} is dead.")
            return log.events
        t = action["type"]
        if t == "attack":
            self.attack(log, actor_id, action["target_id"], action.get("weapon"),
                        action.get("modifiers", []), adv, dis)
        elif t == "cast":
            self.cast(log, actor_id, action["spell_id"], action.get("target_ids"),
                      action.get("slot_level"), adv, dis)
        elif t == "use_feature":
            self.use_feature(log, actor_id, action["feature"])
        elif t == "use_item":
            self.use_item(log, actor_id, action["item"], action.get("target_id"))
        elif t == "check":
            self.ability_check(log, actor_id, action["ability"], action.get("skill"),
                               dc=self._feature_dc(action.get("target_id")),
                               intent=action.get("intent"))
        else:
            log.reject(actor_id, "unknown_command",
                       f"The rules module does not resolve '{t}' actions.")
        return log.events

    # ================================================================ lookups
    def get(self, cid):
        for c in self.state.get("party", []) + self.state.get("enemies", []):
            if c["id"] == cid:
                return c
        return None

    def stats(self, c):
        return self.data.stats(c["ref"])

    @staticmethod
    def effect(c, name, target_id=None):
        for e in c.get("effects", []):
            if e["name"] == name and (target_id is None or e.get("target_id") == target_id):
                return e
        return None

    @staticmethod
    def add_effect(c, name, source_id=None, target_id=None, rounds_left=None):
        c.setdefault("effects", [])
        c["effects"] = [e for e in c["effects"]
                        if not (e["name"] == name and e.get("source_id") == source_id)]
        e = {"name": name}
        if source_id:
            e["source_id"] = source_id
        if target_id:
            e["target_id"] = target_id
        e["rounds_left"] = rounds_left
        c["effects"].append(e)
        return e

    @staticmethod
    def remove_effect(c, name, source_id=None):
        before = len(c.get("effects", []))
        c["effects"] = [e for e in c.get("effects", [])
                        if not (e["name"] == name and (source_id is None or e.get("source_id") == source_id))]
        return len(c["effects"]) < before

    @staticmethod
    def has_condition(c, *names):
        return any(cond["name"] in names for cond in c.get("conditions", []))

    def current_ac(self, c):
        ac = self.data.ac(self.stats(c))
        if self.effect(c, "Shield of Faith"):
            ac += 2
        return ac + self.cover_bonus(c)

    def cover_bonus(self, c):
        """+2 half / +5 three-quarters cover. The combat-flow module puts a
        temporary "Cover" effect on the target while one attack or save is rolled."""
        e = self.effect(c, "Cover")
        return int(e.get("data", {}).get("bonus", 0)) if e else 0

    def _feature_dc(self, feature_id):
        for f in self.state.get("map", {}).get("features", []):
            if f["id"] == feature_id and "dc" in f:
                return f["dc"]
        return 10

    # ================================================================ d20 tests
    def saving_throw(self, log, target_id, ability, dc, source=None, actor_id=None):
        target = self.get(target_id)
        st = self.stats(target)
        adv, dis = [], []
        if ability == "str" and self.effect(target, "Rage"):
            adv.append("rage")
        if ability in ("str", "dex") and self.has_condition(target, "paralyzed", "stunned", "unconscious"):
            log.emit("saving_throw", actor_id=actor_id, target_id=target_id, ability=ability, dc=dc,
                     roll={"expr": "auto-fail", "dice": [], "modifier": 0, "total": 0},
                     success=False, source=source)
            return False
        if ability == "dex" and self.has_condition(target, "restrained"):
            dis.append("restrained")
        extra = "1d4" if self.effect(target, "Bless") else None
        bonus = self.data.save_bonus(st, ability) + (self.cover_bonus(target) if ability == "dex" else 0)
        roll, _ = self.dice.d20(bonus, combine_mode(adv, dis), extra)
        success = roll["total"] >= dc
        log.emit("saving_throw", actor_id=actor_id, target_id=target_id, ability=ability,
                 dc=dc, roll=roll, success=success, source=source)
        return success

    def ability_check(self, log, actor_id, ability, skill=None, dc=10, intent=None):
        actor = self.get(actor_id)
        st = self.stats(actor)
        if skill and not ability:
            ability = SKILL_ABILITY[skill]
        adv, dis = [], []
        if ability == "str" and self.effect(actor, "Rage"):
            adv.append("rage")
        if self.has_condition(actor, "poisoned", "frightened"):
            dis.append("condition")
        roll, _ = self.dice.d20(self.data.check_bonus(st, ability, skill), combine_mode(adv, dis))
        success = roll["total"] >= dc
        log.emit("ability_check", actor_id=actor_id, ability=ability, skill=skill, dc=dc,
                 roll=roll, success=success, intent=intent)
        return success

    # ================================================================ attacks
    def attack(self, log, actor_id, target_id, weapon_name=None, modifiers=(), adv=(), dis=()):
        actor, target = self.get(actor_id), self.get(target_id)
        if target is None or not target["alive"]:
            log.reject(actor_id, "invalid_target", "That target isn't there or is already dead.")
            return
        ast = self.stats(actor)
        weapon = self.data.find_weapon(ast, weapon_name)
        if weapon is None:
            log.reject(actor_id, "invalid_target",
                       f"{actor['name']} has no weapon called {weapon_name}.")
            return
        ranged = weapon["name"].lower() in RANGED_WEAPONS or "thrown" in modifiers

        adv, dis = list(adv), list(dis)
        if "reckless" in modifiers and not ranged:
            adv.append("reckless")
            self.add_effect(actor, "Reckless", source_id=actor_id, rounds_left=None)
        vex = self.effect(actor, "Vex", target_id)
        if vex:
            adv.append("vex")
            actor["effects"].remove(vex)
        if self.effect(target, "Reckless"):
            adv.append("target_reckless")
        bolt = self.effect(target, "Guiding Bolt")
        if bolt:
            adv.append("guiding_bolt")
            target["effects"].remove(bolt)
        if self.has_condition(target, "paralyzed", "stunned", "restrained", "unconscious", "blinded"):
            adv.append("target_condition")
        if self.has_condition(target, "prone"):
            (dis if ranged else adv).append("prone")
        if self.has_condition(actor, "poisoned", "blinded", "prone", "restrained", "frightened"):
            dis.append("attacker_condition")

        extra = "1d4" if self.effect(actor, "Bless") else None
        roll, nat = self.dice.d20(weapon["atk_bonus"], combine_mode(adv, dis), extra)
        ac = self.current_ac(target)
        if nat == 20:
            outcome = "critical_hit"
        elif nat == 1:
            outcome = "critical_miss"
        else:
            outcome = "hit" if roll["total"] >= ac else "miss"
        hit = outcome in ("hit", "critical_hit")

        mastery = None
        if hit and "vex" in weapon.get("notes", "").lower():
            mastery = "Vex"
        log.emit("attack", actor_id=actor_id, target_id=target_id, weapon=weapon["name"],
                 roll=roll, target_ac=ac, outcome=outcome, mastery_applied=mastery)
        if not hit:
            return

        crit = outcome == "critical_hit"
        rage_bonus = 2 if (self.effect(actor, "Rage") and weapon["name"].lower() not in RANGED_WEAPONS) else 0
        for i, (expr, dtype) in enumerate(parse_damage(weapon["damage"])):
            dmg = self.dice.roll(expr, bonus=rage_bonus if i == 0 else 0)
            if not self.deal_damage(log, actor_id, target_id, dmg, dtype, crit, weapon["name"]):
                return  # target died, stop rolling extra damage
        mark = self.effect(actor, "Hunter's Mark", target_id)
        if mark:
            dmg = self.dice.roll(SPELLS["hunters_mark"]["damage"])
            if not self.deal_damage(log, actor_id, target_id, dmg, "force", crit, "Hunter's Mark"):
                return
        if mastery == "Vex":
            # advantage on your next attack vs this target before the end of your next turn
            self.add_effect(actor, "Vex", source_id=actor_id, target_id=target_id, rounds_left=2)

    # ================================================================ damage / healing
    def deal_damage(self, log, actor_id, target_id, roll, dtype, critical=False,
                    source=None, half=False):
        """Apply a rolled damage amount. House rule: a crit doubles the total damage
        roll. Returns True if the target is still alive afterwards."""
        target = self.get(target_id)
        amount = max(0, roll["total"])
        if critical:
            amount *= 2
        applied = "none"
        if half:
            amount //= 2
            applied = "half_on_save"
        immune, resist, vuln = self.data.damage_traits(self.stats(target))
        if self.effect(target, "Rage"):
            resist = resist | RAGE_RESISTS
        if dtype in immune:
            amount, applied = 0, "immunity"
        elif dtype in resist:
            amount, applied = amount // 2, "resistance"
        elif dtype in vuln:
            amount, applied = amount * 2, "vulnerability"

        to_hp = amount
        if target["temp_hp"]:
            soaked = min(target["temp_hp"], to_hp)
            target["temp_hp"] -= soaked
            to_hp -= soaked
        target["hp"] = max(0, target["hp"] - to_hp)

        log.emit("damage", actor_id=actor_id, target_id=target_id, roll=roll, amount=amount,
                 damage_type=dtype, critical=critical, modifier_applied=applied,
                 source=source, hp_after=target["hp"])
        if target["hp"] == 0:
            self.kill(log, target_id, actor_id)
            return False
        return True

    def kill(self, log, target_id, killed_by=None):
        """House rule: 0 HP = dead. No death saves."""
        target = self.get(target_id)
        target["hp"] = 0
        target["alive"] = False
        target["temp_hp"] = 0
        if target.get("concentrating_on"):
            self.end_concentration(log, target_id, "broken")
        target["effects"] = []
        log.emit("death", target_id=target_id, killed_by=killed_by)

    def heal(self, log, actor_id, target_id, expr, bonus=0, source=None):
        target = self.get(target_id)
        roll = self.dice.roll(expr, bonus=bonus)
        if not target["alive"]:
            log.emit("heal", actor_id=actor_id, target_id=target_id, roll=roll, amount=0,
                     source=source, hp_after=0)
            return 0
        amount = min(max(0, roll["total"]), target["max_hp"] - target["hp"])
        target["hp"] += amount
        log.emit("heal", actor_id=actor_id, target_id=target_id, roll=roll, amount=amount,
                 source=source, hp_after=target["hp"])
        return amount

    # ================================================================ spells
    def cast(self, log, actor_id, spell_id, target_ids=None, slot_level=None, adv=(), dis=()):
        caster = self.get(actor_id)
        cst = self.stats(caster)
        spell = SPELLS.get(spell_id)
        sc = self.data.spellcasting(cst)
        if spell is None:
            log.reject(actor_id, "unknown_spell", f"{spell_id} isn't a spell this game knows.")
            return
        if sc is None:
            log.reject(actor_id, "not_prepared", f"{caster['name']} can't cast spells.")
            return
        if spell_id not in caster["resources"].get("prepared_spells", []):
            log.reject(actor_id, "not_prepared", f"{caster['name']} doesn't have {spell['name']} prepared.")
            return
        if self.effect(caster, "Rage"):
            log.reject(actor_id, "condition_prevents", "You can't cast spells while raging.")
            return

        target_ids = list(target_ids or [])
        if not target_ids:
            if spell["kind"] in ("buff", "heal"):
                target_ids = [actor_id]
            else:
                log.reject(actor_id, "invalid_target", f"Who are you casting {spell['name']} on?")
                return
        target_ids = target_ids[: spell["max_targets"]]
        for tid in target_ids:
            t = self.get(tid)
            if t is None or (not t["alive"] and spell["kind"] != "heal"):
                log.reject(actor_id, "invalid_target", f"{tid} isn't a valid target.")
                return

        # ---- pay for it
        level = spell["level"]
        slot = 0
        if level > 0:
            free = caster["resources"]["uses"].get("favored_enemy")
            if spell_id == "hunters_mark" and free and free["used"] < free["max"] and not slot_level:
                free["used"] += 1
                log.emit("resource", actor_id=actor_id, resource="favored_enemy", delta=-1,
                         remaining=free["max"] - free["used"])
                slot = 1
            else:
                slot = slot_level or level
                slots = caster["resources"]["spell_slots"]
                s = slots.get(str(slot))
                if slot < level or s is None or s["used"] >= s["max"]:
                    log.reject(actor_id, "no_resource", f"No level {slot} spell slots left.")
                    return
                s["used"] += 1
                log.emit("resource", actor_id=actor_id, resource=f"spell_slot_{slot}", delta=-1,
                         remaining=s["max"] - s["used"])

        if spell.get("concentration"):
            if caster.get("concentrating_on"):
                self.end_concentration(log, actor_id, "ended")
            caster["concentrating_on"] = {"spell_id": spell_id, "target_ids": target_ids,
                                          "rounds_left": spell.get("rounds")}
            log.emit("concentration", actor_id=actor_id, change="started", spell_id=spell_id)

        log.emit("spell_cast", actor_id=actor_id, spell_id=spell_id, slot_level=slot,
                 target_ids=target_ids)
        upcast = max(0, slot - level) if level else 0

        kind = spell["kind"]
        if kind == "save_damage":
            dc = sc["save_dc"]
            for tid in target_ids:
                saved = self.saving_throw(log, tid, spell["save"], dc, spell["name"], actor_id)
                if saved and spell["on_success"] == "none":
                    continue
                dmg = self.dice.roll(spell["damage"])
                self.deal_damage(log, actor_id, tid, dmg, spell["damage_type"], False,
                                 spell["name"], half=saved)
        elif kind == "attack_damage":
            for tid in target_ids:
                target = self.get(tid)
                roll, nat = self.dice.d20(sc["attack_bonus"], combine_mode(adv, dis),
                                          "1d4" if self.effect(caster, "Bless") else None)
                ac = self.current_ac(target)
                outcome = ("critical_hit" if nat == 20 else "critical_miss" if nat == 1
                           else "hit" if roll["total"] >= ac else "miss")
                log.emit("attack", actor_id=actor_id, target_id=tid, weapon=spell["name"],
                         roll=roll, target_ac=ac, outcome=outcome)
                if outcome in ("hit", "critical_hit"):
                    expr = spell["damage"] + (f"+{upcast}{spell['upcast'][1:]}" if upcast else "")
                    alive = self.deal_damage(log, actor_id, tid, self.dice.roll(expr),
                                             spell["damage_type"], outcome == "critical_hit",
                                             spell["name"])
                    if alive and spell.get("rider"):
                        self.add_effect(target, spell["rider"], source_id=actor_id, rounds_left=2)
                        log.emit("condition", actor_id=actor_id, target_id=tid,
                                 condition=spell["rider"], change="applied", source=spell["name"], rounds=2)
        elif kind == "heal":
            bonus = sc["modifier"]
            if cst.get("subclass") == "Life Domain":
                bonus += 2 + slot  # Disciple of Life
            expr = spell["heal"] + (f"+{2 * upcast}d8" if upcast else "")
            for tid in target_ids:
                self.heal(log, actor_id, tid, expr, bonus, spell["name"])
        elif kind == "buff":
            for tid in target_ids:
                self.add_effect(self.get(tid), spell["effect"], source_id=actor_id,
                                rounds_left=spell["rounds"])
                log.emit("condition", actor_id=actor_id, target_id=tid, condition=spell["effect"],
                         change="applied", source=spell["name"], rounds=spell["rounds"])
        elif kind == "mark":
            tid = target_ids[0]
            self.add_effect(caster, spell["effect"], source_id=actor_id, target_id=tid,
                            rounds_left=spell["rounds"])
            log.emit("condition", actor_id=actor_id, target_id=tid, condition=spell["effect"],
                     change="applied", source=spell["name"], rounds=spell["rounds"])

    def end_concentration(self, log, caster_id, change="ended"):
        caster = self.get(caster_id)
        conc = caster.get("concentrating_on")
        if not conc:
            return
        spell = SPELLS.get(conc["spell_id"], {})
        name = spell.get("effect")
        if name:
            for c in self.state.get("party", []) + self.state.get("enemies", []):
                if self.remove_effect(c, name, source_id=caster_id):
                    log.emit("condition", actor_id=caster_id, target_id=c["id"], condition=name,
                             change="removed", source=spell.get("name"))
        caster["concentrating_on"] = None
        log.emit("concentration", actor_id=caster_id, change=change, spell_id=conc["spell_id"])

    # ================================================================ features & items
    def use_feature(self, log, actor_id, feature):
        actor = self.get(actor_id)
        feature = slug(feature)
        if feature != "rage":
            log.reject(actor_id, "unknown_command", f"The feature '{feature}' isn't supported yet.")
            return
        use = actor.get("resources", {}).get("uses", {}).get("rage")
        if use is None:
            log.reject(actor_id, "no_resource", f"{actor['name']} can't rage.")
            return
        if self.effect(actor, "Rage"):
            log.reject(actor_id, "no_resource", "You're already raging.")
            return
        if use["used"] >= use["max"]:
            log.reject(actor_id, "no_resource", "You have no Rage uses left.")
            return
        use["used"] += 1
        log.emit("resource", actor_id=actor_id, resource="rage", delta=-1,
                 remaining=use["max"] - use["used"])
        if actor.get("concentrating_on"):
            self.end_concentration(log, actor_id, "broken")
        self.add_effect(actor, "Rage", source_id=actor_id, rounds_left=None)
        log.emit("condition", actor_id=actor_id, target_id=actor_id, condition="Rage",
                 change="applied", source="Rage")

    def use_item(self, log, actor_id, item, target_id=None):
        target_id = target_id or actor_id
        inv = self.state.setdefault("inventory", [])
        entry = next((i for i in inv if i["owner_id"] == actor_id
                      and i["item"].lower() == item.lower() and i["qty"] > 0), None)
        if entry is None:
            log.reject(actor_id, "no_resource", f"You don't have a {item}.")
            return
        if slug(item) != "potion_of_healing":
            log.reject(actor_id, "unknown_command", f"Using a {item} isn't supported yet.")
            return
        entry["qty"] -= 1
        log.emit("resource", actor_id=actor_id, resource="potion_of_healing", delta=-1,
                 remaining=entry["qty"])
        self.heal(log, actor_id, target_id, "2d4+2", 0, "Potion of Healing")

    # ================================================================ turn hooks
    def start_of_turn(self, actor_id, start_seq=0):
        """Call when actor_id's turn begins. Clears Reckless Attack."""
        log = EventLog(start_seq)
        actor = self.get(actor_id)
        if actor and self.remove_effect(actor, "Reckless"):
            log.emit("condition", actor_id=actor_id, target_id=actor_id, condition="Reckless",
                     change="expired")
        return log.events

    def end_of_turn(self, actor_id, start_seq=0):
        """Call when actor_id's turn ends. Ticks down every timed effect that
        actor_id created, and its concentration."""
        log = EventLog(start_seq)
        everyone = self.state.get("party", []) + self.state.get("enemies", [])
        for c in everyone:
            keep = []
            for e in c.get("effects", []):
                if e.get("source_id") == actor_id and e.get("rounds_left") is not None:
                    e["rounds_left"] -= 1
                    if e["rounds_left"] <= 0:
                        if e["name"] != "Vex":
                            log.emit("condition", actor_id=actor_id, target_id=c["id"],
                                     condition=e["name"], change="expired")
                        continue
                keep.append(e)
            c["effects"] = keep
        actor = self.get(actor_id)
        conc = actor.get("concentrating_on") if actor else None
        if conc and conc.get("rounds_left") is not None:
            conc["rounds_left"] -= 1
            if conc["rounds_left"] <= 0:
                self.end_concentration(log, actor_id, "ended")
        return log.events

    def end_combat(self, start_seq=0):
        """Call when combat ends. Rage, Reckless, Vex and Guiding Bolt drop off."""
        log = EventLog(start_seq)
        for c in self.state.get("party", []) + self.state.get("enemies", []):
            for name in ("Rage", "Reckless", "Vex", "Guiding Bolt"):
                if self.remove_effect(c, name) and name == "Rage":
                    log.emit("condition", actor_id=c["id"], target_id=c["id"],
                             condition=name, change="expired")
        return log.events

    # ================================================================ rests
    def rest(self, kind, start_seq=0):
        """Party rest. Long: full HP, slots, uses, hit dice. Short: each living PC
        spends Hit Dice until healed, and short-rest uses come back."""
        log = EventLog(start_seq)
        hp_restored, restored = {}, set()
        for pc in self.state.get("party", []):
            if not pc["alive"]:
                continue
            res = pc["resources"]
            st = self.stats(pc)
            before = pc["hp"]
            if kind == "long":
                pc["hp"] = pc["max_hp"]
                res["hit_dice_left"] = st["level"]
                for lvl, s in res["spell_slots"].items():
                    if s["used"]:
                        restored.add(f"spell_slot_{lvl}")
                    s["used"] = 0
                for name, u in res["uses"].items():
                    if u["used"]:
                        restored.add(name)
                    u["used"] = 0
            else:
                die = st["hit_dice"].split()[0]  # e.g. 'd12'
                con = self.data.ability_mod(st, "con")
                while res["hit_dice_left"] > 0 and pc["hp"] < pc["max_hp"]:
                    res["hit_dice_left"] -= 1
                    r = self.dice.roll("1" + die, bonus=con)
                    pc["hp"] = min(pc["max_hp"], pc["hp"] + max(0, r["total"]))
                for name, u in res["uses"].items():
                    if u["recharge"] == "short_rest" and u["used"]:
                        u["used"] = 0
                        restored.add(name)
            pc["temp_hp"] = 0
            if pc["hp"] > before:
                hp_restored[pc["id"]] = pc["hp"] - before
        log.emit("rest", kind=kind, hp_restored=hp_restored, resources_restored=sorted(restored))
        return log.events
