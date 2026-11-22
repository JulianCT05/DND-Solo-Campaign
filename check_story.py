"""Validate a story file against schemas/story.schema.json, then check that every
id it uses exists, both inside the story and in the rest of the game:

  inside the story   flags registered, beats / objectives / clues / NPCs / endings
                     referenced exist, ids unique, start beat exists, beats reachable
  against the game   room ids = data/rooms/<id>.json, feature ids = the room's
                     map.features, enemy ids = the room's enemies, NPC ref = enemies.json

Problems fail the check. Warnings are things that will work but look wrong
(an NPC not placed in any room yet, an item nothing gives, an unreachable beat).

    python check_story.py                              # data/stories/flooded_cellar.json
    python check_story.py data/stories/my_story.json
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schemas" / "story.schema.json"
ROOMS_DIR = ROOT / "data" / "rooms"
ENEMIES_PATH = ROOT / "data" / "enemies.json"
DEFAULT_STORY = ROOT / "data" / "stories" / "flooded_cellar.json"
PARTY_IDS = {"barbarian_1", "cleric_1", "ranger_1"}  # engine/newgame.py INSTANCE_IDS
INTERACT_VERBS = {"open", "close", "unlock", "force", "disarm", "pull", "take", "examine"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


SCHEMA = read_json(SCHEMA_PATH)
Draft202012Validator.check_schema(SCHEMA)


def walk(x):
    """Every dict in the tree. Conditions and effects are the dicts with a 'type'."""
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)


def load_rooms(room_ids, rooms_dir=ROOMS_DIR):
    rooms = {}
    for rid in room_ids:
        p = Path(rooms_dir) / f"{rid}.json"
        if p.exists():
            rooms[rid] = read_json(p)
    return rooms


def check(path, rooms_dir=ROOMS_DIR):
    """Returns (problems, warnings), two lists of strings."""
    story = read_json(path)
    problems, warnings = [], []
    for e in Draft202012Validator(SCHEMA).iter_errors(story):
        problems.append("schema: " + "/".join(map(str, e.absolute_path)) + ": " + e.message[:160])
    if problems:
        return problems, warnings

    # ------------------------------------------------ ids defined in the story
    def ids(section):
        found = [x["id"] for x in story.get(section, [])]
        for dup in {i for i in found if found.count(i) > 1}:
            problems.append(f"duplicate id in {section}: {dup}")
        return set(found)

    beats, objs, clues = ids("beats"), ids("objectives"), ids("clues")
    npcs, endings, _ = ids("npcs"), ids("endings"), ids("triggers")

    # ------------------------------------------------ ids defined by the game
    room_ids = {story["start"]["room_id"]}
    for d in walk(story):
        if isinstance(d.get("room_id"), str):
            room_ids.add(d["room_id"])
    rooms = load_rooms(room_ids, rooms_dir)
    for rid in sorted(room_ids - set(rooms)):
        problems.append(f"room {rid}: no file data/rooms/{rid}.json")
    features = {f["id"] for r in rooms.values() for f in r.get("map", {}).get("features", [])}
    room_enemies = {e["id"]: e["ref"] for r in rooms.values() for e in r.get("enemies", [])}
    creatures = set(room_enemies) | PARTY_IDS
    room_flags = {k for r in rooms.values() for k in r.get("flags", {})}
    enemy_refs = {e["id"] for e in read_json(ENEMIES_PATH)["enemies"]}

    flags = set(story["flags"])
    for k in sorted(flags & room_flags):
        warnings.append(f"flag {k} is in both the story and a room file; the story's initial value wins")
    known_flags = flags | room_flags

    # ------------------------------------------------ start
    if story["start"]["first_beat"] not in beats:
        problems.append("start.first_beat is not a beat")

    # ------------------------------------------------ every condition and effect
    given_items = set()
    for d in walk(story):
        t = d.get("type")
        if t in ("flag", "set_flag") and d.get("key") not in known_flags:
            problems.append(f"unknown flag {d.get('key')} (add it to flags)")
        elif t == "beat" and "status" in d and d["id"] not in beats:
            problems.append(f"unknown beat {d['id']}")
        elif t in ("start_beat", "complete_beat") and d["beat_id"] not in beats:
            problems.append(f"unknown beat {d['beat_id']}")
        elif t == "objective" and "status" in d and d["id"] not in objs:
            problems.append(f"unknown objective {d['id']}")
        elif t == "set_objective" and d["objective_id"] not in objs:
            problems.append(f"unknown objective {d['objective_id']}")
        elif t in ("reveal_clue", "clue_found") and d["clue_id"] not in clues:
            problems.append(f"unknown clue {d['clue_id']}")
        elif t == "set_attitude" and d["npc_id"] not in npcs:
            problems.append(f"unknown npc {d['npc_id']}")
        elif t == "end_game" and d["ending_id"] not in endings:
            problems.append(f"unknown ending {d['ending_id']}")
        elif t in ("feature_state", "set_feature_state") and d["feature_id"] not in features:
            problems.append(f"unknown map feature {d['feature_id']} (not in the room files)")
        elif t in ("enemy_dead", "wake_enemy") and d["instance_id"] not in room_enemies:
            problems.append(f"unknown enemy {d['instance_id']} (not in the room files)")
        elif t == "start_combat":
            for s in d.get("surprised", []):
                if s not in room_enemies:
                    problems.append(f"start_combat: unknown enemy {s}")
        elif t in ("give_item", "remove_item"):
            if t == "give_item":
                given_items.add(d["item"])
            if d.get("owner_id") and d["owner_id"] not in PARTY_IDS:
                problems.append(f"{t} {d['item']}: owner {d['owner_id']} is not a party id")

    for d in walk(story):
        if d.get("type") == "has_item" and d["item"] not in given_items:
            warnings.append(f"has_item {d['item']}: nothing in the story gives it (fine if it's starting gear)")

    # ------------------------------------------------ beats
    for b in story["beats"]:
        for n in b.get("next", []):
            if n not in beats:
                problems.append(f"{b['id']}.next -> unknown beat {n}")
    reach, todo = set(), [story["start"]["first_beat"]]
    by_id = {b["id"]: b for b in story["beats"]}
    while todo:
        bid = todo.pop()
        if bid in reach or bid not in by_id:
            continue
        reach.add(bid)
        todo += by_id[bid].get("next", [])
        todo += [d["beat_id"] for d in walk(by_id[bid]) if d.get("type") == "start_beat"]
    for bid in sorted(beats - reach):
        warnings.append(f"beat {bid} can't be reached from start.first_beat through next")

    # ------------------------------------------------ npcs
    for n in story.get("npcs", []):
        if n.get("ref") and n["ref"] not in enemy_refs:
            problems.append(f"npc {n['id']}: ref {n['ref']} is not in enemies.json")
        if n["id"] not in room_enemies:
            warnings.append(f"npc {n['id']} is not placed in any room file yet, so the player can't talk to it")
        elif n.get("ref") and room_enemies[n["id"]] != n["ref"]:
            problems.append(f"npc {n['id']}: ref {n['ref']} but the room file says {room_enemies[n['id']]}")

    # ------------------------------------------------ clues
    revealed = {d["clue_id"] for d in walk(story) if d.get("type") == "reveal_clue"}
    for c in story.get("clues", []):
        fb = c.get("found_by")
        if fb is None and c["id"] not in revealed:
            problems.append(f"clue {c['id']} has no found_by and no reveal_clue effect, so it can't be found")
        if fb and fb["object_id"] not in features | npcs:
            problems.append(f"clue {c['id']}: found_by.object_id {fb['object_id']} is not a map feature or NPC")
        if fb and "skill" in fb and "dc" not in fb:
            problems.append(f"clue {c['id']}: found_by has a skill but no dc")

    # ------------------------------------------------ triggers
    for trg in story.get("triggers", []):
        m = trg["on"].get("match", {})
        for key in ("target_id", "actor_id", "killed_by", "speaker_id"):
            if key in m and m[key] not in creatures | npcs:
                problems.append(f"trigger {trg['id']}: {key} {m[key]} is not a creature in the room files or party")
        if "object_id" in m and m["object_id"] not in features:
            problems.append(f"trigger {trg['id']}: object_id {m['object_id']} is not a map feature")
        if "verb" in m and m["verb"] not in INTERACT_VERBS:
            problems.append(f"trigger {trg['id']}: verb {m['verb']} is not an interact verb")

    # ------------------------------------------------ endings
    kinds = {e["type"] for e in story["endings"]}
    if "victory" not in kinds:
        warnings.append("no victory ending")
    if "defeat" not in kinds:
        warnings.append("no defeat ending; the engine still ends the game when the player dies")

    return problems, warnings


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_STORY
    bad, warn = check(path)
    print("PASS story" if not bad else "FAIL story")
    for p in bad:
        print(" -", p)
    for w in warn:
        print(" ! warning:", w)
    sys.exit(1 if bad else 0)
