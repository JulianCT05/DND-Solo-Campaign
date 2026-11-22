"""Loads characters.json and enemies.json and answers stat questions
(ability modifiers, save bonuses, weapons, AC, damage resistances).

Also builds fresh combatant dicts in the shape game_state.schema.json expects,
so Leena's "start new game" endpoint can call new_pc() / new_enemy().
"""
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

ABILITY_NAMES = {"strength": "str", "dexterity": "dex", "constitution": "con",
                 "intelligence": "int", "wisdom": "wis", "charisma": "cha"}

SKILL_ABILITY = {
    "acrobatics": "dex", "animal_handling": "wis", "arcana": "int", "athletics": "str",
    "deception": "cha", "history": "int", "insight": "wis", "intimidation": "cha",
    "investigation": "int", "medicine": "wis", "nature": "int", "perception": "wis",
    "performance": "cha", "persuasion": "cha", "religion": "int",
    "sleight_of_hand": "dex", "stealth": "dex", "survival": "wis",
}

RANGED_WEAPONS = {"longbow", "shortbow", "light crossbow", "heavy crossbow",
                  "hand crossbow", "sling", "dart"}


def slug(name):
    """'Hunter's Mark' -> 'hunters_mark', 'Animal Handling' -> 'animal_handling'."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower().replace("'", "").replace("’", "")).strip("_")


def parse_damage(text):
    """'1d6+2 bludgeoning plus 2d6 necrotic' -> [('1d6+2','bludgeoning'), ('2d6','necrotic')]"""
    parts = []
    for chunk in re.split(r"\s+plus\s+", text.strip(), flags=re.I):
        m = re.match(r"([\dd+\-\s]+?)\s+([a-zA-Z]+)$", chunk.strip())
        if m:
            parts.append((m.group(1).replace(" ", ""), m.group(2).lower()))
    return parts


class GameData:
    def __init__(self, data_dir=DATA_DIR):
        data_dir = Path(data_dir)
        chars = json.loads((data_dir / "characters.json").read_text(encoding="utf-8"))
        enemies = json.loads((data_dir / "enemies.json").read_text(encoding="utf-8"))
        self.characters = {c["id"]: c for c in chars["characters"]}
        self.enemies = {e["id"]: e for e in enemies["enemies"]}

    # ------------------------------------------------------------ lookups
    def stats(self, ref):
        if ref in self.characters:
            return self.characters[ref]
        if ref in self.enemies:
            return self.enemies[ref]
        raise KeyError(f"No character or enemy with id {ref!r} in the data files")

    @staticmethod
    def is_pc(stats):
        return "abilities" in stats

    def ability_mod(self, stats, ability):
        if self.is_pc(stats):
            return stats["abilities"][ability]["mod"]
        return (stats["stats"][ability] - 10) // 2

    def prof(self, stats):
        return stats.get("proficiency_bonus", 2)

    def save_bonus(self, stats, ability):
        if self.is_pc(stats):
            profs = {ABILITY_NAMES.get(p.lower(), p.lower()) for p in stats.get("saving_throw_proficiencies", [])}
            return self.ability_mod(stats, ability) + (self.prof(stats) if ability in profs else 0)
        if ability in stats.get("saving_throws", {}):
            return stats["saving_throws"][ability]
        return self.ability_mod(stats, ability)

    def check_bonus(self, stats, ability, skill=None):
        if skill and not self.is_pc(stats) and skill in stats.get("skills", {}):
            return stats["skills"][skill]
        bonus = self.ability_mod(stats, ability)
        if skill and self.is_pc(stats):
            if skill in {slug(s) for s in stats.get("skill_proficiencies", [])}:
                bonus += self.prof(stats)
        return bonus

    def ac(self, stats):
        return stats["ac"]

    def weapons(self, stats):
        """Attack options with an attack bonus (skips spell rows like Sacred Flame)."""
        rows = stats.get("weapons") if self.is_pc(stats) else stats.get("actions", [])
        return [w for w in rows or [] if "atk_bonus" in w and "damage" in w]

    def find_weapon(self, stats, name=None):
        options = self.weapons(stats)
        if not options:
            return None
        if not name:
            return options[0]
        for w in options:
            if w["name"].lower() == name.lower():
                return w
        return None

    def damage_traits(self, stats):
        return (set(stats.get("damage_immunities", [])),
                set(stats.get("damage_resistances", [])),
                set(stats.get("damage_vulnerabilities", [])))

    def spellcasting(self, stats):
        sc = stats.get("spellcasting") or {}
        if not sc or sc.get("ability") in (None, "None"):
            return None
        return sc

    # ------------------------------------------------------------ builders
    def new_pc(self, ref, instance_id, controller="player", position=(0, 0)):
        s = self.characters[ref]
        sc = self.spellcasting(s)
        slots, prepared = {}, []
        if sc:
            for key, n in sc.get("spell_slots", {}).items():
                slots[key.replace("level_", "")] = {"max": n, "used": 0}
            prepared = [slug(x) for x in sc.get("cantrips", [])]
            for names in sc.get("prepared_spells", {}).values():
                prepared += [slug(x) for x in names]
        uses = {}
        if s["class"] == "Barbarian":
            uses["rage"] = {"max": 3, "used": 0, "recharge": "long_rest"}
        if s["class"] == "Ranger":
            uses["favored_enemy"] = {"max": 2, "used": 0, "recharge": "long_rest"}
            if "hunters_mark" not in prepared:
                prepared.append("hunters_mark")
        resources = {"hit_dice_left": s["level"], "spell_slots": slots, "uses": uses}
        if prepared:
            resources["prepared_spells"] = prepared
        return {
            "id": instance_id, "ref": ref, "name": s["name"],
            "hp": s["hp"], "max_hp": s["hp"], "temp_hp": 0, "alive": True,
            "position": {"x": position[0], "y": position[1]},
            "conditions": [], "concentrating_on": None, "effects": [],
            "controller": controller,
            "turn": {"action": True, "bonus_action": True, "reaction": True,
                     "movement_left_ft": s.get("speed", 30)},
            "resources": resources,
        }

    def new_enemy(self, ref, instance_id, position=(0, 0), tactic="melee_rush"):
        s = self.enemies[ref]
        return {
            "id": instance_id, "ref": ref, "name": s["name"],
            "hp": s["hp"], "max_hp": s.get("max_hp", s["hp"]), "temp_hp": 0, "alive": True,
            "position": {"x": position[0], "y": position[1]},
            "conditions": [], "concentrating_on": None, "effects": [],
            "ai": {"tactic": tactic, "awake": True, "target_id": None},
        }

    def starting_inventory(self, ref, instance_id):
        s = self.characters[ref]
        inv = []
        if any("potion of healing" in e.lower() for e in s.get("equipment", [])):
            inv.append({"item": "Potion of Healing", "qty": 1, "owner_id": instance_id})
        for e in s.get("equipment", []):
            m = re.match(r"(?:\w+ \()?(\d+)\)?\s*javelins", e.lower())
            if m:
                inv.append({"item": "Javelin", "qty": int(m.group(1)), "owner_id": instance_id})
        return inv
