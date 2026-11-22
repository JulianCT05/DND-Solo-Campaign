"""Turns the game state into short text the models can use.

The parser gets the ids it must use (creatures, map objects, spells, weapons),
distances and the turn budget. The narrator gets names and wound levels, never
raw stat blocks. Keeping this compact is what keeps turns fast and cheap.

Only reads files in data/ and the state dict, so it works the same whether the
engine runs in-process or behind the HTTP API.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from .config import SEARCH_ROOTS

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Where else to look when a file isn't in DATA_DIR: characters.json and
# enemies.json live in the main repo's data/, spells.json in this folder's data/.
FALLBACK_DATA_DIRS = [b / "data" for b in SEARCH_ROOTS]


def slug(name):
    """Same rule as engine/data.py: "Hunter's Mark" -> "hunters_mark"."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower().replace("'", "").replace("’", "")).strip("_")


@lru_cache(maxsize=4)
def load_data(data_dir=str(DATA_DIR)):
    d = Path(data_dir)

    def read(name, key):
        p = next((x / name for x in [d] + FALLBACK_DATA_DIRS if (x / name).exists()), None)
        if p is None:
            return {}
        return {x["id"]: x for x in json.loads(p.read_text(encoding="utf-8")).get(key, [])}

    spells = read("spells.json", "spells")
    if not spells:  # spells.json sits next to the schemas in some copies of the repo
        alt = d.parent / "spells.json"
        if alt.exists():
            spells = {x["id"]: x for x in json.loads(alt.read_text(encoding="utf-8"))["spells"]}
    return {"characters": read("characters.json", "characters"),
            "enemies": read("enemies.json", "enemies"),
            "spells": spells}


# ------------------------------------------------------------------ state helpers
def creatures(state):
    return list(state.get("party", [])) + list(state.get("enemies", []))


def by_id(state):
    return {c["id"]: c for c in creatures(state)}


def player(state):
    return next((p for p in state.get("party", []) if p.get("controller") == "player"), None)


def distance_ft(a, b, cell_ft=5):
    """2024 grid rule: every square, diagonal or not, costs one cell."""
    return max(abs(a["x"] - b["x"]), abs(a["y"] - b["y"])) * cell_ft


def wound_label(c):
    if not c.get("alive", True):
        return "dead"
    hp, mx = c.get("hp", 0), max(c.get("max_hp", 1), 1)
    r = hp / mx
    if r >= 1:
        return "unhurt"
    if r > 0.5:
        return "lightly wounded"
    if r > 0.25:
        return "bloodied"
    return "near death"


def fled(c):
    return any(e.get("name") == "Fled" for e in c.get("effects", []) or [])


def active_id(state):
    cb = state.get("combat") or {}
    order = cb.get("initiative_order") or []
    i = cb.get("active_index", 0)
    return order[i]["id"] if 0 <= i < len(order) else None


def weapon_kind(w):
    notes = (w.get("notes") or "").lower()
    if "dc" in w and "atk_bonus" not in w:
        return "spell"
    if "thrown" in notes:
        m = re.search(r"(\d+)\s*/\s*(\d+)", notes)
        return f"melee or thrown {m.group(1)}/{m.group(2)} ft" if m else "melee or thrown"
    m = re.search(r"(\d+)\s*/\s*(\d+)", notes)
    if m:
        return f"ranged {m.group(1)}/{m.group(2)} ft"
    return "melee 5 ft"


def actor_sheet(state, actor):
    data = load_data()
    sheet = data["characters"].get(actor.get("ref"), {})
    weapons = [w for w in sheet.get("weapons", []) if "atk_bonus" in w]
    uses = actor.get("resources", {}).get("uses", {}) or {}
    slots = actor.get("resources", {}).get("spell_slots", {}) or {}
    prepared = actor.get("resources", {}).get("prepared_spells", []) or []
    items = [f"{i['item']} x{i['qty']}" for i in state.get("inventory", []) or []
             if i.get("owner_id") == actor["id"] and i.get("qty", 0) > 0]
    spell_rows = []
    for sid in prepared:
        sp = data["spells"].get(sid, {})
        lvl = sp.get("level")
        spell_rows.append(f"{sid} ({'cantrip' if lvl == 0 else f'level {lvl}' if lvl else '?'})")
    return {
        "class": sheet.get("class", "?"),
        "weapons": [f"{w['name']} ({weapon_kind(w)})" for w in weapons],
        "features": [f"{k} ({v['max'] - v['used']} left)" for k, v in uses.items()],
        "slots": {k: v["max"] - v["used"] for k, v in slots.items()},
        "spells": spell_rows,
        "items": items,
        "skills": [slug(s) for s in sheet.get("skill_proficiencies", [])],
    }


# ------------------------------------------------------------------ parser context
def parser_context(state, actor_id=None):
    me = by_id(state).get(actor_id) if actor_id else player(state)
    if me is None:
        raise ValueError("No player character in the game state.")
    cell = state.get("map", {}).get("cell_ft", 5)
    sh = actor_sheet(state, me)
    mode = state.get("mode", "exploration")
    lines = [f"MODE: {mode}"]
    if mode == "combat":
        act = active_id(state)
        lines.append(f"ACTIVE TURN: {act}{' (the player)' if act == me['id'] else ''}, "
                     f"round {(state.get('combat') or {}).get('round', 1)}")
        t = me.get("turn", {})
        lines.append(f"BUDGET LEFT: action={'yes' if t.get('action') else 'no'}, "
                     f"bonus_action={'yes' if t.get('bonus_action') else 'no'}, "
                     f"movement={t.get('movement_left_ft', 0)} ft")
    lines.append(f"PLAYER: {me['id']} \"{me['name']}\" ({sh['class']}) at ({me['position']['x']},"
                 f"{me['position']['y']}), {me['hp']}/{me['max_hp']} HP")
    conds = [c["name"] for c in me.get("conditions", [])] + [e["name"] for e in me.get("effects", []) or []]
    if conds:
        lines.append(f"ON PLAYER: {', '.join(conds)}")
    lines.append(f"WEAPONS: {', '.join(sh['weapons']) or 'none'}")
    if sh["features"]:
        lines.append(f"FEATURES (use_feature): {', '.join(sh['features'])}")
    if sh["spells"]:
        slots = ", ".join(f"L{k}: {v}" for k, v in sorted(sh["slots"].items())) or "none"
        lines.append(f"SPELLS (spell_id): {', '.join(sh['spells'])}. Slots left: {slots}")
    if sh["items"]:
        lines.append(f"ITEMS: {', '.join(sh['items'])}")
    if sh["skills"]:
        lines.append(f"PROFICIENT SKILLS: {', '.join(sh['skills'])}")

    lines.append("CREATURES:")
    for c in creatures(state):
        if c["id"] == me["id"]:
            continue
        side = "ally" if c in state.get("party", []) else "enemy"
        status = "dead" if not c.get("alive", True) else ("fled" if fled(c) else wound_label(c))
        lines.append(f"- {c['id']} \"{c['name']}\" {side}, {status}, at ({c['position']['x']},"
                     f"{c['position']['y']}), {distance_ft(me['position'], c['position'], cell)} ft away")
    feats = state.get("map", {}).get("features", []) or []
    if feats:
        lines.append("MAP OBJECTS:")
        for f in feats:
            lines.append(f"- {f['id']} ({f.get('kind', '?')}, {f.get('state', '?')}) at "
                         f"({f['position']['x']},{f['position']['y']}), "
                         f"{distance_ft(me['position'], f['position'], cell)} ft away")
    m = state.get("map", {})
    lines.append(f"MAP: {m.get('width')}x{m.get('height')} squares of {cell} ft. x grows east, y grows south.")
    return "\n".join(lines)


# ------------------------------------------------------------------ narrator context
def name_of(state, cid, fallback=None):
    c = by_id(state).get(cid)
    if c:
        same = [x for x in creatures(state) if x["name"] == c["name"]]
        if len(same) > 1:  # two Human Zombies -> "Human Zombie 1" / "Human Zombie 2"
            return f"{c['name']} {same.index(c) + 1}"
        return c["name"]
    for f in state.get("map", {}).get("features", []) or []:
        if f["id"] == cid:
            return f"the {f.get('kind', 'object')}"
    return fallback or (cid.replace("_", " ") if cid else "someone")


def scene_context(state):
    me = player(state)
    loc = state.get("location", {}) or {}
    parts = [f"Room: {loc.get('room_id', '?')} (light: {loc.get('light', 'bright')}). Mode: {state.get('mode')}."]
    summ = (state.get("summary") or {}).get("text")
    if summ:
        parts.append(f"Story so far: {summ}")
    if me:
        parts.append(f"The player is {me['name']}.")
    alive = [f"{c['name']} ({wound_label(c)})" for c in creatures(state) if c.get("alive") and not fled(c)]
    parts.append("Present: " + ", ".join(alive) + ".")
    return "\n".join(parts)
