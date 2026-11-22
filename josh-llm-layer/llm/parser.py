"""Parser: player text -> action_request JSON (schema: action_event.schema.json).

    parser = Parser(client, model)
    request = parser.parse("I rage and chop the frog", state)          # always schema-valid
    request = parser.parse("run at the zombie", state, reachable=cells) # cells from /reachable

Pipeline
  1. The model sees a compact context (ids, distances, weapons, spells, budget)
     and returns {"actions", "confidence", "clarification_needed"}.
  2. normalize() repairs what models get wrong: names instead of ids, "Hunter's
     Mark" instead of hunters_mark, extra keys, "strength" instead of "str".
  3. Geometry is never left to the model. It may say {"type":"move","toward":"zombie_1"}
     or {"direction":"north","feet":10}; normalize() turns that into a real square,
     using the engine's reachable cells when given.
  4. The result is checked against Julian's schema. If it fails, the model gets
     one repair try with the errors; after that the player is asked to rephrase.
  5. If the model can't be reached (offline, outage), KeywordParser handles the
     common commands so the game keeps working.

The parser never sets dice, advantage or outcomes: the schema has no field for
them, and normalize() drops any key the schema doesn't allow.
"""
import json
import re
from pathlib import Path

from .client import LLMError, extract_json
from .config import find_file, load_prompt
from .context import (by_id, creatures, distance_ft, fled, load_data, parser_context,
                      player, slug)

SCHEMA_PATH = find_file("schemas/action_event.schema.json")
SCHEMA_VERSION = "1.0.0"
LOW_CONFIDENCE = 0.35

ALLOWED_KEYS = {
    "move": {"type", "to", "stay_adjacent_to"},
    "attack": {"type", "target_id", "weapon", "modifiers"},
    "cast": {"type", "spell_id", "slot_level", "target_ids", "target_point", "choice"},
    "use_feature": {"type", "feature", "target_ids"},
    "use_item": {"type", "item", "target_id"},
    "interact": {"type", "object_id", "verb"},
    "check": {"type", "ability", "skill", "target_id", "intent"},
    "talk": {"type", "target_id", "text", "tone"},
    "rest": {"type", "kind"},
    "end_turn": {"type"},
    "rules_question": {"type", "question"},
}
SIMPLE = {"dash", "dodge", "disengage", "help", "hide", "ready", "search", "stand_up"}
for _s in SIMPLE:
    ALLOWED_KEYS[_s] = {"type", "target_id"}

TYPE_ALIASES = {"spell": "cast", "cast_spell": "cast", "feature": "use_feature", "item": "use_item",
                "standup": "stand_up", "stand": "stand_up", "say": "talk", "speak": "talk",
                "endturn": "end_turn", "end": "end_turn", "ability_check": "check", "skill_check": "check",
                "question": "rules_question", "melee": "attack", "shoot": "attack"}
ABILITIES = {"strength": "str", "dexterity": "dex", "constitution": "con", "intelligence": "int",
             "wisdom": "wis", "charisma": "cha"}
SKILL_ABILITY = {"athletics": "str", "acrobatics": "dex", "sleight_of_hand": "dex", "stealth": "dex",
                 "arcana": "int", "history": "int", "investigation": "int", "nature": "int",
                 "religion": "int", "animal_handling": "wis", "insight": "wis", "medicine": "wis",
                 "perception": "wis", "survival": "wis", "deception": "cha", "intimidation": "cha",
                 "performance": "cha", "persuasion": "cha"}
VERBS = {"open", "close", "unlock", "force", "disarm", "pull", "take", "examine"}
VERB_ALIASES = {"inspect": "examine", "look": "examine", "check": "examine", "search": "examine",
                "break": "force", "bash": "force", "kick": "force", "shut": "close", "grab": "take",
                "loot": "take", "pick_up": "take", "pick": "unlock", "yank": "pull"}
TONES = {"neutral", "friendly", "threatening", "deceptive", "persuasive"}
DIRECTIONS = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0),
              "northeast": (1, -1), "northwest": (-1, -1), "southeast": (1, 1), "southwest": (-1, 1),
              "up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

_validator = None


def schema_errors(request):
    """Errors for an action_request against the team schema ([] = valid)."""
    global _validator
    if _validator is None:
        import jsonschema
        ae = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        _validator = jsonschema.Draft202012Validator(
            {"$schema": ae["$schema"], "$defs": ae["$defs"], "$ref": "#/$defs/action_request"})
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in _validator.iter_errors(request)]


class Clarify(Exception):
    """Raised inside normalize() when the player has to be asked something."""


# ====================================================================== resolving words to ids
def _words(s):
    return set(re.findall(r"[a-z0-9]+", str(s).lower()))


def resolve_creature(state, ref, prefer=None):
    """'frog', 'Giant Frog', 'giant_frog_1' -> 'giant_frog_1'. Clarify if ambiguous or unknown."""
    if not ref:
        return None
    ids = by_id(state)
    if ref in ids:
        return ref
    want = _words(ref.replace("_", " ")) - {"the", "a", "an", "at", "on", "my"}
    pool = [c for c in creatures(state) if c.get("alive", True) and not fled(c)] or creatures(state)
    if prefer:
        pool = [c for c in pool if (c in state.get("enemies", [])) == (prefer == "enemy")] or pool
    hits = [c for c in pool if want and want <= (_words(c["name"]) | _words(c["id"].replace("_", " ")))]
    if not hits:
        hits = [c for c in pool if want & (_words(c["name"]) - {"giant", "human", "the"})]
    if len(hits) == 1:
        return hits[0]["id"]
    if not hits:
        raise Clarify(f"I don't see \"{ref}\" here. Who do you mean?")
    names = " or ".join(f"{c['name']} ({c['id']})" for c in hits[:4])
    raise Clarify(f"Which one do you mean: {names}?")


def resolve_object(state, ref):
    feats = state.get("map", {}).get("features", []) or []
    if any(f["id"] == ref for f in feats):
        return ref
    want = _words(str(ref).replace("_", " ")) - {"the", "a", "an"}
    hits = [f for f in feats if want & (_words(f["id"].replace("_", " ")) | {f.get("kind", "")})]
    if len(hits) == 1:
        return hits[0]["id"]
    if not hits:
        raise Clarify(f"There's no \"{ref}\" here that I can see.")
    raise Clarify("Which one: " + " or ".join(f["id"].replace("_", " ") for f in hits) + "?")


def resolve_weapon(state, actor, name):
    sheet = load_data()["characters"].get(actor.get("ref"), {})
    weapons = [w["name"] for w in sheet.get("weapons", []) if "atk_bonus" in w]
    if not name:
        return None
    for w in weapons:
        if w.lower() == str(name).lower():
            return w
    for w in weapons:
        if _words(name) & _words(w):
            return w
    if _words(name) & {"fist", "fists", "punch", "unarmed", "kick"}:
        return None
    raise Clarify(f"{actor['name']} doesn't have a {name}. Weapons: {', '.join(weapons) or 'none'}.")


def resolve_spell(actor, name):
    spells = load_data()["spells"]
    prepared = actor.get("resources", {}).get("prepared_spells", []) or []
    sid = slug(name)
    if sid in prepared or sid in spells:
        return sid
    for s in prepared:
        if _words(name) & (_words(s.replace("_", " ")) - {"of", "the"}):
            return s
    for s, sp in spells.items():
        if _words(name) <= _words(sp.get("name", "")):
            return s
    raise Clarify(f"I don't know a spell called \"{name}\".")


def resolve_item(state, actor, name):
    inv = [i for i in state.get("inventory", []) or [] if i.get("owner_id") == actor["id"]]
    for i in inv:
        if i["item"].lower() == str(name).lower():
            return i["item"]
    hits = [i for i in inv if _words(name) & (_words(i["item"]) - {"of"})]
    if len(hits) == 1:
        return hits[0]["item"]
    if not hits:
        raise Clarify(f"You don't have a {name}.")
    raise Clarify("Which item: " + " or ".join(i["item"] for i in hits) + "?")


# ====================================================================== geometry
def _blocked_cells(state, mover_id):
    out = set()
    for c in creatures(state):
        if c["id"] != mover_id and c.get("alive", True) and not fled(c):
            out.add((c["position"]["x"], c["position"]["y"]))
    return out


def _walkable(state, x, y):
    m = state.get("map", {})
    if not (0 <= x < m.get("width", 0) and 0 <= y < m.get("height", 0)):
        return False
    ch = m["terrain"][y][x]
    kind = (m.get("legend") or {}).get(ch, {"#": "wall", "D": "door"}.get(ch, "floor"))
    if kind == "wall":
        return False
    if kind == "door":
        f = next((f for f in m.get("features", []) if f["position"] == {"x": x, "y": y}), None)
        return bool(f and f.get("state") == "open")
    return True


def cell_near(state, actor, target_pos, reachable=None):
    """Best square next to target_pos: reachable now if we know, else closest walkable."""
    ax, ay = actor["position"]["x"], actor["position"]["y"]
    tx, ty = target_pos["x"], target_pos["y"]
    blocked = _blocked_cells(state, actor["id"])
    if reachable:
        cells = [(x, y, f) for (x, y), f in reachable.items() if (x, y) not in blocked]
        if cells:
            x, y, _ = min(cells, key=lambda c: (max(abs(c[0] - tx), abs(c[1] - ty)), c[2]))
            return {"x": x, "y": y}
    ring = [(tx + dx, ty + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
    ring = [(x, y) for x, y in ring if _walkable(state, x, y) and (x, y) not in blocked]
    if not ring:
        raise Clarify("There's no open space next to that.")
    x, y = min(ring, key=lambda c: (max(abs(c[0] - ax), abs(c[1] - ay)), abs(c[0] - ax) + abs(c[1] - ay)))
    return {"x": x, "y": y}


def cell_in_direction(state, actor, direction, feet, reachable=None):
    d = DIRECTIONS.get(slug(direction).replace("_", ""))
    if d is None:
        raise Clarify(f"Which way is \"{direction}\"? Try north, south, east or west.")
    cell = state.get("map", {}).get("cell_ft", 5)
    steps = max(1, int(feet or cell) // cell)
    x, y = actor["position"]["x"], actor["position"]["y"]
    best = None
    for _ in range(steps):
        x, y = x + d[0], y + d[1]
        if not _walkable(state, x, y):
            break
        if reachable is not None and (x, y) not in reachable:
            break
        if (x, y) not in _blocked_cells(state, actor["id"]):
            best = {"x": x, "y": y}
    if best is None:
        raise Clarify(f"You can't move {direction} from here.")
    return best


# ====================================================================== normalize
def normalize_actions(raw_actions, state, actor, reachable=None):
    out = []
    for a in raw_actions or []:
        if not isinstance(a, dict):
            continue
        a = dict(a)
        t = slug(a.get("type", "")).replace("__", "_")
        t = TYPE_ALIASES.get(t.replace("_", ""), TYPE_ALIASES.get(t, t))
        a["type"] = t
        if t not in ALLOWED_KEYS:
            raise Clarify("I'm not sure how to do that. Could you say it another way?")

        if t == "move":
            if callable(reachable):   # fetched only when a move actually needs it
                reachable = reachable()
            if "toward" in a or "target_id" in a:
                ref = a.get("toward") or a.get("target_id")
                c = by_id(state).get(ref)
                pos = None
                if c is None:
                    try:
                        c = by_id(state)[resolve_creature(state, ref)]
                    except Clarify:
                        pos = next((f["position"] for f in state.get("map", {}).get("features", [])
                                    if f["id"] == resolve_object(state, ref)), None)
                pos = pos or c["position"]
                if distance_ft(actor["position"], pos) <= 5:
                    continue  # already next to it: no move needed
                a["to"] = cell_near(state, actor, pos, reachable)
            elif "direction" in a:
                a["to"] = cell_in_direction(state, actor, a["direction"], a.get("feet"), reachable)
            elif isinstance(a.get("to"), dict):
                a["to"] = {"x": int(a["to"].get("x", 0)), "y": int(a["to"].get("y", 0))}
            else:
                raise Clarify("Where do you want to move?")
        elif t == "attack":
            a["target_id"] = resolve_creature(state, a.get("target_id") or a.get("target"), prefer="enemy")
            if not a["target_id"]:
                raise Clarify("Who do you attack?")
            raw_weapon = str(a.get("weapon") or "")
            a["weapon"] = resolve_weapon(state, actor, raw_weapon)
            mods = [m for m in (a.get("modifiers") or []) if m in ("reckless", "thrown", "offhand", "unarmed")]
            if a["weapon"] is None and _words(raw_weapon) & {"fist", "fists", "punch", "unarmed", "kick"}:
                mods.append("unarmed")
            a["modifiers"] = sorted(set(mods), key=mods.index)
        elif t == "cast":
            a["spell_id"] = resolve_spell(actor, a.get("spell_id") or a.get("spell") or "")
            tids = a.get("target_ids") or ([a["target_id"]] if a.get("target_id") else [])
            a["target_ids"] = [resolve_creature(state, x) for x in tids]
            if a.get("slot_level") in (0, None):
                a.pop("slot_level", None)
            else:
                a["slot_level"] = int(a["slot_level"])
        elif t == "use_feature":
            a["feature"] = slug(a.get("feature", ""))
            if a.get("target_ids"):
                a["target_ids"] = [resolve_creature(state, x) for x in a["target_ids"]]
        elif t == "use_item":
            a["item"] = resolve_item(state, actor, a.get("item", ""))
            if a.get("target_id"):
                a["target_id"] = resolve_creature(state, a["target_id"])
        elif t == "interact":
            a["object_id"] = resolve_object(state, a.get("object_id") or a.get("object", ""))
            v = slug(a.get("verb", "examine"))
            a["verb"] = v if v in VERBS else VERB_ALIASES.get(v, "examine")
        elif t == "check":
            skill = slug(a.get("skill") or "")
            ab = str(a.get("ability") or "").lower()
            ab = ABILITIES.get(ab, ab[:3])
            if skill in SKILL_ABILITY:
                a["skill"] = skill
                ab = ab if ab in ABILITIES.values() else SKILL_ABILITY[skill]
            else:
                a.pop("skill", None)
            if ab not in ABILITIES.values():
                raise Clarify("What kind of check is that: Strength, Dexterity, Wisdom...?")
            a["ability"] = ab
        elif t == "talk":
            a["text"] = str(a.get("text") or "").strip()
            if not a["text"]:
                raise Clarify("What do you say?")
            if a.get("target_id"):
                a["target_id"] = resolve_creature(state, a["target_id"])
            if a.get("tone") not in TONES:
                a.pop("tone", None)
        elif t == "rest":
            a["kind"] = "long" if "long" in str(a.get("kind", "")).lower() else "short"
        elif t == "rules_question":
            a["question"] = str(a.get("question") or "").strip() or "?"
        elif t in SIMPLE and a.get("target_id"):
            a["target_id"] = resolve_creature(state, a["target_id"])

        clean = {k: v for k, v in a.items() if k in ALLOWED_KEYS[t] and v not in (None, [], "")}
        out.append(clean)
    return out


def make_request(actor_id, raw_text, actions, confidence, clarification=None):
    req = {"schema_version": SCHEMA_VERSION, "actor_id": actor_id, "raw_text": raw_text,
           "confidence": round(max(0.0, min(1.0, float(confidence))), 2), "actions": actions}
    if clarification:
        req["clarification_needed"] = clarification
        req["actions"] = []
    return req


def describe(actions, state):
    """Short English for a list of actions (used in low-confidence questions)."""
    names = {c["id"]: c["name"] for c in creatures(state)}
    parts = []
    for a in actions:
        t = a["type"]
        if t == "attack":
            parts.append(f"attack {names.get(a['target_id'], a['target_id'])}"
                         + (f" with your {a['weapon']}" if a.get("weapon") else ""))
        elif t == "cast":
            tg = ", ".join(names.get(x, x) for x in a.get("target_ids", []))
            parts.append(f"cast {a['spell_id'].replace('_', ' ')}" + (f" on {tg}" if tg else ""))
        elif t == "move":
            parts.append(f"move to ({a['to']['x']},{a['to']['y']})")
        elif t == "use_feature":
            parts.append(f"use {a['feature'].replace('_', ' ')}")
        elif t == "interact":
            parts.append(f"{a['verb']} the {a['object_id'].replace('_', ' ')}")
        else:
            parts.append(t.replace("_", " "))
    return " and then ".join(parts)


# ====================================================================== the parser
class Parser:
    def __init__(self, client, model="", max_tokens=400):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.system = load_prompt("parser_system")
        self.fallback = KeywordParser()
        self.last_source = None   # "llm", "llm_repaired", "keyword", "empty"

    def parse(self, text, state, actor_id=None, reachable=None):
        me = by_id(state).get(actor_id) if actor_id else player(state)
        if me is None:
            raise ValueError("No player character in the game state.")
        text = (text or "").strip()
        if not text:
            self.last_source = "empty"
            return make_request(me["id"], "", [], 0.0, "What do you do?")
        user = f"{parser_context(state, me['id'])}\n\nPlayer: {json.dumps(text)}"
        try:
            reply = self.client.complete(self.system, user, self.model, self.max_tokens, 0.0, json_mode=True)
        except LLMError:
            self.last_source = "keyword"
            return self.fallback.parse(text, state, me["id"], reachable)

        req, errors = self._build(reply, text, state, me, reachable)
        self.last_source = "llm"
        if errors:
            fix = (f"{user}\n\nYour last answer was:\n{reply}\n\nIt had these problems:\n- "
                   + "\n- ".join(errors[:6]) + "\nReply again with corrected JSON only.")
            try:
                reply2 = self.client.complete(self.system, fix, self.model, self.max_tokens, 0.0, json_mode=True)
                req, errors = self._build(reply2, text, state, me, reachable)
                self.last_source = "llm_repaired"
            except LLMError:
                pass
        if errors:
            return make_request(me["id"], text, [], 0.0,
                                "Sorry, I didn't catch that. Could you say it another way?")
        return req

    def _build(self, reply, text, state, me, reachable):
        try:
            obj = extract_json(reply)
        except (ValueError, json.JSONDecodeError) as e:
            return None, [f"not valid JSON ({e})"]
        conf = obj.get("confidence", 0.5)
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = 0.5
        question = obj.get("clarification_needed")
        if question:
            return make_request(me["id"], text, [], conf, str(question)), []
        try:
            actions = normalize_actions(obj.get("actions") or [], state, me, reachable)
        except Clarify as c:
            return make_request(me["id"], text, [], conf, str(c)), []
        except (KeyError, TypeError, ValueError) as e:
            return None, [f"bad action shape: {e}"]
        if not actions:
            return make_request(me["id"], text, [], conf, "What would you like to do?"), []
        if conf < LOW_CONFIDENCE:
            return make_request(me["id"], text, [], conf, f"Do you want to {describe(actions, state)}?"), []
        req = make_request(me["id"], text, actions, conf)
        return req, schema_errors(req)


# ====================================================================== offline fallback
class KeywordParser:
    """Rule-based parser for common commands. Used when the model can't be reached,
    and as the baseline in eval/eval_parser.py. Deliberately simple."""

    ATTACK = r"\b(attack|hit|swing|chop|slash|stab|strike|smash|cleave|shoot|fire|throw|hurl|punch|bash)\b"
    MOVE = r"\b(move|go|walk|run|step|approach|charge|head|dash over|close in|back away)\b"

    def parse(self, text, state, actor_id=None, reachable=None):
        me = by_id(state).get(actor_id) if actor_id else player(state)
        low = text.lower()
        try:
            actions = self._actions(low, text, state, me)
            actions = normalize_actions(actions, state, me, reachable)
        except Clarify as c:
            return make_request(me["id"], text, [], 0.3, str(c))
        if not actions:
            return make_request(me["id"], text, [], 0.2,
                                "I didn't understand that. Try something like \"attack the frog\" or \"move north 10 feet\".")
        req = make_request(me["id"], text, actions, 0.6)
        return req if not schema_errors(req) else make_request(me["id"], text, [], 0.2, "Could you rephrase that?")

    # ------------------------------------------------------------------
    def _mentioned(self, low, state, enemies_only=False):
        pool = [c for c in (state.get("enemies", []) if enemies_only else creatures(state))
                if c.get("alive", True) and not fled(c)]
        words = _words(low)
        hits = []
        for c in pool:
            key = _words(c["name"]) - {"giant", "human", "the", "damaged", "animated"}
            if c["id"] in low or (key and key & words):
                hits.append(c)
        return hits

    def _target(self, low, state, me):
        hits = [c for c in self._mentioned(low, state, enemies_only=True)]
        if len(hits) == 1:
            return hits[0]["id"]
        if len(hits) > 1:
            raise Clarify("Which one: " + " or ".join(f"{c['name']} ({c['id']})" for c in hits) + "?")
        alive = [c for c in state.get("enemies", []) if c.get("alive") and not fled(c)]
        if len(alive) == 1:
            return alive[0]["id"]
        raise Clarify("Who is your target?")

    def _actions(self, low, text, state, me):
        acts = []
        sheet = load_data()["characters"].get(me.get("ref"), {})
        weapons = [w for w in sheet.get("weapons", []) if "atk_bonus" in w]

        if re.search(r"\b(rules?|how does|how do|can i|am i allowed)\b", low) and text.strip().endswith("?"):
            return [{"type": "rules_question", "question": text.strip()}]
        q = re.search(r"[\"“](.+?)[\"”]", text)
        if q:
            npc = self._mentioned(low, state)
            npc = [c for c in npc if c["id"] != me["id"]]
            act = {"type": "talk", "text": q.group(1)}
            if len(npc) == 1:
                act["target_id"] = npc[0]["id"]
            return [act]
        if re.search(r"\b(short|long) rest\b", low):
            return [{"type": "rest", "kind": "long" if "long rest" in low else "short"}]
        if re.search(r"\brage\b", low):
            acts.append({"type": "use_feature", "feature": "rage"})
        if re.search(r"\b(drink|quaff|use)\b.*\bpotion\b", low):
            acts.append({"type": "use_item", "item": "Potion of Healing"})

        # spells: any prepared spell named in the text
        for sid in me.get("resources", {}).get("prepared_spells", []) or []:
            name = sid.replace("_", " ")
            if name in low.replace("'", ""):
                spell = load_data()["spells"].get(sid, {})
                act = {"type": "cast", "spell_id": sid}
                if re.search(r"\b(me|myself)\b", low):
                    act["target_ids"] = [me["id"]]
                else:
                    allies = [c for c in self._mentioned(low, state) if c in state.get("party", [])]
                    if allies:
                        act["target_ids"] = [allies[0]["id"]]
                    elif "heal" not in (spell.get("description", "")[:200].lower()) and sid not in ("bless", "shield_of_faith", "guidance", "aid"):
                        act["target_ids"] = [self._target(low, state, me)]
                acts.append(act)
                break

        if not any(a["type"] == "cast" for a in acts) and re.search(self.ATTACK, low):
            target = self._target(low, state, me)
            weapon = next((w["name"] for w in weapons if w["name"].lower() in low), None)
            mods = []
            if re.search(r"\b(throw|hurl)\b", low):
                mods.append("thrown")
                weapon = weapon or next((w["name"] for w in weapons if "thrown" in (w.get("notes") or "").lower()), None)
            if re.search(r"\b(shoot|fire)\b", low):
                weapon = weapon or next((w["name"] for w in weapons if re.search(r"\d+/\d+", w.get("notes") or "")
                                         and "thrown" not in (w.get("notes") or "").lower()), None)
            if "reckless" in low:
                mods.append("reckless")
            weapon = weapon or (weapons[0]["name"] if weapons else None)
            t = by_id(state)[target]
            ranged = mods == ["thrown"] or (weapon and any(w["name"] == weapon and re.search(r"\d+/\d+", w.get("notes") or "")
                                                            for w in weapons))
            if not ranged and distance_ft(me["position"], t["position"]) > 5:
                acts.append({"type": "move", "toward": target})
            act = {"type": "attack", "target_id": target}
            if weapon:
                act["weapon"] = weapon
            if mods:
                act["modifiers"] = mods
            acts.append(act)
        elif not acts and re.search(self.MOVE, low):
            m = re.search(r"(\d+)\s*(?:ft|feet|foot)?\s*(north|south|east|west|northeast|northwest|southeast|southwest)", low) \
                or re.search(r"(north|south|east|west|northeast|northwest|southeast|southwest)\s*(\d+)?", low)
            xy = re.search(r"\(?\s*(\d+)\s*,\s*(\d+)\s*\)?", low)
            if xy and not m:
                acts.append({"type": "move", "to": {"x": int(xy.group(1)), "y": int(xy.group(2))}})
            elif m:
                g = m.groups()
                feet, direction = (g[0], g[1]) if g[0] and g[0].isdigit() else (g[1], g[0])
                acts.append({"type": "move", "direction": direction, "feet": int(feet or 5)})
            else:
                who = [c for c in self._mentioned(low, state) if c["id"] != me["id"]]
                obj = [f for f in state.get("map", {}).get("features", []) or []
                       if f.get("kind", "") in low or f["id"].replace("_", " ") in low]
                if len(who) == 1:
                    acts.append({"type": "move", "toward": who[0]["id"]})
                elif len(obj) == 1:
                    acts.append({"type": "move", "toward": obj[0]["id"]})
                else:
                    raise Clarify("Where do you want to go?")

        for verb in ("unlock", "open", "close", "force", "disarm", "pull", "examine", "inspect", "take", "kick", "break"):
            if re.search(rf"\b{verb}\b", low):
                obj = [f for f in state.get("map", {}).get("features", []) or []
                       if f.get("kind", "") in low or f["id"].replace("_", " ") in low]
                if len(obj) == 1:
                    acts.append({"type": "interact", "object_id": obj[0]["id"], "verb": verb})
                    break
        for simple in ("dash", "dodge", "disengage", "hide", "search"):
            if re.search(rf"\b{simple}\b", low) and not any(a["type"] == simple for a in acts):
                if simple == "search" and any(a["type"] == "interact" for a in acts):
                    continue
                acts.append({"type": simple})
        if re.search(r"\bstand up\b|\bget up\b", low):
            acts.append({"type": "stand_up"})
        if re.search(r"\b(look around|perception|listen)\b", low):
            acts.append({"type": "check", "ability": "wis", "skill": "perception", "intent": "look around"})
        if re.search(r"\b(end( my)? turn|i'?m done|pass)\b", low):
            acts.append({"type": "end_turn"})
        return acts
