"""Builds a fresh, schema-valid game state from a room file (Leena's part).

Room files live in data/rooms/<room_id>.json. Keys the loader reads:
  room_id, light, summary, map (game_state map object),
  party_spawn [[x, y], ...]   one cell per party member, player first
  enemies [{ref, id, position [x, y], tactic, awake}]
  flags {name: bool}
Any key starting with '_' is ignored (notes).
"""
import json
import uuid
from pathlib import Path

from .data import DATA_DIR

ROOMS_DIR = DATA_DIR / "rooms"
PREGENS = ["barbarian_orc_berserker", "cleric_dwarf_life", "ranger_wood_elf_hunter"]
INSTANCE_IDS = {"barbarian_orc_berserker": "barbarian_1",
                "cleric_dwarf_life": "cleric_1",
                "ranger_wood_elf_hunter": "ranger_1"}


def load_room(room_id, rooms_dir=ROOMS_DIR):
    path = Path(rooms_dir) / f"{room_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No room file {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_rooms(rooms_dir=ROOMS_DIR):
    return sorted(p.stem for p in Path(rooms_dir).glob("*.json"))


def new_game(data, character, room_id="demo_cellar", allies=None, seed=None, session_id=None,
             rooms_dir=ROOMS_DIR):
    """character: the pregen the player picked. allies: other pregens run by the
    engine (default: the other two)."""
    if character not in data.characters:
        raise KeyError(f"Unknown character {character!r}; choose one of {PREGENS}")
    if allies is None:
        allies = [p for p in PREGENS if p != character and p in data.characters]
    room = load_room(room_id, rooms_dir)
    spawn = room.get("party_spawn") or [[1, 1], [2, 1], [1, 2]]
    party = []
    for i, ref in enumerate([character] + list(allies)):
        cell = spawn[i] if i < len(spawn) else spawn[-1]
        party.append(data.new_pc(ref, INSTANCE_IDS.get(ref, f"{ref}_1"),
                                 "player" if i == 0 else "engine_ally", tuple(cell)))
        party[-1]["size"] = data.stats(ref).get("size", "Medium")
    enemies = []
    for e in room.get("enemies", []):
        en = data.new_enemy(e["ref"], e["id"], tuple(e["position"]), e.get("tactic", "melee_rush"))
        en["ai"]["awake"] = e.get("awake", True)
        en["size"] = data.stats(e["ref"]).get("size", "Medium")
        enemies.append(en)
    inventory = []
    for pc in party:
        inventory += data.starting_inventory(pc["ref"], pc["id"])
    seed = seed if seed is not None else uuid.uuid4().int % (2**31)
    return {
        "schema_version": "1.0.0",
        "session_id": session_id or uuid.uuid4().hex[:12],
        "rules_edition": "srd_5.2.1",
        "rng_seed": seed,
        "mode": "exploration",
        "turn_counter": 0,
        "location": {"room_id": room["room_id"], "light": room.get("light", "bright")},
        "party": party,
        "enemies": enemies,
        "map": room["map"],
        "combat": None,
        "inventory": inventory,
        "flags": dict(room.get("flags", {})),
        "summary": {"text": room.get("summary", ""), "updated_at_turn": 0},
        "recent_events": [],
    }
