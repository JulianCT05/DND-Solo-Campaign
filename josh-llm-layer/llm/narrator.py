"""Narrator: engine events -> story text, plus the dice log lines for the GUI.

    narrator = Narrator(client, model)
    out = narrator.narrate(turn_results, state)
    # [{"turn": 8, "actor_id": "barbarian_1", "status": "accepted",
    #   "text": "...", "source": "llm" | "template" | "engine", "dice": ["..."]}]

How it keeps the story honest
  * The model never sees the raw state, only a fact list built from the events
    (facts_for_turn) plus the short story summary. If it isn't in the events,
    it isn't in the prompt.
  * Every reply is checked (guard): no numbers, no deaths without a death event,
    no creature that wasn't part of the turn. A failing turn gets one retry;
    if it fails again, the template narration is used instead.
  * All turns from one /actions response go in ONE model call, so a round with
    three enemy turns still costs one request.
  * Rejected and needs_clarification turns are not narrated by the model: the
    engine's message (or the parser's question) is shown as is.
"""
import json
import re

from .client import LLMError, extract_json
from .config import load_prompt
from .context import by_id, load_data, name_of, player, scene_context, wound_label

QUIET_EVENTS = {"turn_start", "initiative", "resource"}
DEATH_WORDS = re.compile(r"\b(dies|died|dead|slain|slays|kill(?:s|ed)?|lifeless|perish(?:es|ed)?|corpse)\b", re.I)


# ====================================================================== facts
def _who(state, cid):
    me = player(state)
    if me and cid == me["id"]:
        return "You"
    return name_of(state, cid)


def _v(subject, you_form, other_form):
    return you_form if subject == "You" else other_form


def spell_name(spell_id):
    return load_data()["spells"].get(spell_id, {}).get("name") or spell_id.replace("_", " ").title()


def _wound_after(state, cid, hp_after):
    c = by_id(state).get(cid)
    if c is None:
        return ""
    if hp_after <= 0:
        return "drops to 0 HP"
    probe = dict(c, hp=hp_after, alive=True)
    return f"now {wound_label(probe)}"


def fact(event, state):
    """One plain sentence for one event, or None if it isn't worth narrating."""
    t = event["type"]
    who = _who(state, event.get("actor_id"))
    tgt = _who(state, event.get("target_id")) if event.get("target_id") else None
    if t in QUIET_EVENTS:
        return None
    if t == "rejected":
        return f"(not allowed: {event['message']})"
    if t == "combat_start":
        names = ", ".join(_who(state, p) for p in event.get("participants", []))
        s = f"Combat begins between {names}."
        if event.get("surprised"):
            s += f" Caught by surprise: {', '.join(_who(state, p) for p in event['surprised'])}."
        return s
    if t == "move":
        s = f"{who} move{'s' if who != 'You' else ''} {event['feet_used']} ft."
        if event.get("provoked_opportunity_attack_from"):
            s += f" This provokes an opportunity attack from {', '.join(_who(state, p) for p in event['provoked_opportunity_attack_from'])}."
        return s
    if t == "attack":
        result = {"hit": "a hit", "miss": "a miss", "critical_hit": "a critical hit",
                  "critical_miss": "a wild miss"}[event["outcome"]]
        return f"{who} attack{'s' if who != 'You' else ''} {tgt} with {event['weapon']}: {result}."
    if t == "damage":
        return f"{tgt} {_v(tgt, 'take', 'takes')} {event['damage_type']} damage from {event.get('source') or who}" \
               f"{' (critical)' if event.get('critical') else ''}" \
               f"{', resisted' if event.get('modifier_applied') == 'resistance' else ''}" \
               f"{', it is especially vulnerable' if event.get('modifier_applied') == 'vulnerability' else ''}" \
               f"{', but is immune' if event.get('modifier_applied') == 'immunity' else ''}; " \
               f"{_wound_after(state, event['target_id'], event['hp_after'])}."
    if t == "heal":
        return f"{tgt} {_v(tgt, 'are', 'is')} healed by {event.get('source') or who}" \
               f"{' (temporary hit points)' if event.get('temp_hp') else ''}; {_wound_after(state, event['target_id'], event['hp_after'])}."
    if t == "saving_throw":
        return f"{tgt} {_v(tgt, 'make', 'makes')} a {event['ability'].upper()} save against {event.get('source') or 'an effect'} " \
               f"and {_v(tgt, 'succeed', 'succeeds') if event['success'] else _v(tgt, 'fail', 'fails')}."
    if t == "ability_check":
        what = event.get("skill") or event["ability"].upper()
        return f"{who} tr{'y' if who == 'You' else 'ies'} a {str(what).replace('_', ' ')} check" \
               f"{' to ' + event['intent'] if event.get('intent') else ''} and {'succeed' if who == 'You' else 'succeeds'}." \
            if event["success"] else \
            f"{who} tr{'y' if who == 'You' else 'ies'} a {str(what).replace('_', ' ')} check" \
            f"{' to ' + event['intent'] if event.get('intent') else ''} and {'fail' if who == 'You' else 'fails'}."
    if t == "condition":
        be = _v(tgt, "are", "is")
        verb = {"applied": f"{be} now affected by", "removed": f"{be} no longer affected by",
                "expired": f"{be} no longer affected by"}[event["change"]]
        return f"{tgt} {verb} {event['condition']}."
    if t == "spell_cast":
        spell = spell_name(event["spell_id"])
        tg = ", ".join(_who(state, x) for x in event.get("target_ids", []))
        return f"{who} cast{'s' if who != 'You' else ''} {spell}{' on ' + tg if tg else ''}."
    if t == "concentration":
        return f"{who} {_v(who, 'begin', 'begins')} concentrating on {spell_name(event['spell_id'])}." \
            if event["change"] == "started" else \
            f"{'Your' if who == 'You' else who + chr(39) + 's'} concentration on {spell_name(event['spell_id'])} is {event['change']}."
    if t == "death":
        by = f", killed by {_who(state, event['killed_by'])}" if event.get("killed_by") else ""
        return f"{tgt} {_v(tgt, 'die', 'dies')}{by}."
    if t == "interact":
        obj = event["object_id"].replace("_", " ")
        res = {"success": "succeeds", "failure": "fails", "triggered_trap": "sets off a trap",
               "nothing_found": "finds nothing"}[event["result"]]
        s = f"{who} tr{'y' if who == 'You' else 'ies'} to {event['verb']} the {obj}: {res}."
        if event.get("reveals"):
            s += f" Revealed: {', '.join(event['reveals'])}."
        return s
    if t == "dialogue":
        return None  # voiced separately by NPCVoice
    if t == "rest":
        return f"The party takes a {event['kind']} rest and recovers."
    if t == "combat_end":
        return {"victory": "The fight is over: the party has won.", "defeat": "The fight is lost.",
                "fled": "The fight ends as the enemy flees."}[event["result"]]
    if t == "state_change":
        f, v = event["field"], event["value"]
        if f == "mode":
            return {"victory": "The adventure is complete.", "game_over": "Your adventure ends here.",
                    "exploration": "The danger has passed for now."}.get(v)
        if f.endswith(".fled") and v:
            return f"{_who(state, f.split('.')[0])} flees the fight."
        if f == "location.room_id":
            return "The party moves into a new area."
        return None
    return None


def facts_for_turn(turn_result, state):
    return [s for s in (fact(e, state) for e in turn_result.get("events", [])) if s]


def involved_ids(turn_result):
    ids = {turn_result.get("actor_id")}
    for e in turn_result.get("events", []):
        for k in ("actor_id", "target_id", "killed_by", "speaker_id"):
            if e.get(k):
                ids.add(e[k])
        for k in ("participants", "target_ids", "provoked_opportunity_attack_from", "surprised"):
            ids.update(e.get(k) or [])
        if e["type"] == "state_change" and e["field"].endswith(".fled"):
            ids.add(e["field"].split(".")[0])
    return ids


# ====================================================================== dice log
def _roll(r):
    if not r:
        return ""
    dice = r.get("dice", [])
    mode = r.get("mode", "normal")
    tag = {"advantage": " adv", "disadvantage": " dis"}.get(mode, "")
    mod = r.get("modifier", 0)
    return f"{r['expr']}{tag} [{', '.join(map(str, dice))}]{f' {mod:+d}' if mod else ''} = {r['total']}"


def dice_log(turn_result, state):
    """Readable roll lines for the CLI and the GUI's dice log."""
    out = []
    for e in turn_result.get("events", []):
        who = name_of(state, e.get("actor_id"))
        t = e["type"]
        if t == "initiative":
            out.append(f"{who} initiative: {_roll(e['roll'])}")
        elif t == "attack":
            out.append(f"{who} -> {name_of(state, e['target_id'])} ({e['weapon']}): {_roll(e['roll'])} "
                       f"vs AC {e['target_ac']} -> {e['outcome'].replace('_', ' ').upper()}")
        elif t == "damage":
            crit = " x2 crit" if e.get("critical") else ""
            mod = f" ({e['modifier_applied']})" if e.get("modifier_applied") not in (None, "none") else ""
            roll = f"{_roll(e.get('roll'))}{crit} -> " if e.get("roll") else ""
            out.append(f"  damage to {name_of(state, e['target_id'])}: {roll}{e['amount']} {e['damage_type']}{mod}, "
                       f"HP {e['hp_after']}")
        elif t == "heal":
            out.append(f"  heal {name_of(state, e['target_id'])}: {_roll(e.get('roll')) + ' -> ' if e.get('roll') else ''}"
                       f"+{e['amount']}, HP {e['hp_after']}")
        elif t == "saving_throw":
            out.append(f"{name_of(state, e['target_id'])} {e['ability'].upper()} save: {_roll(e['roll'])} vs DC {e['dc']} "
                       f"-> {'SUCCESS' if e['success'] else 'FAIL'}")
        elif t == "ability_check":
            out.append(f"{who} {(e.get('skill') or e['ability']).replace('_', ' ')} check: {_roll(e['roll'])} "
                       f"vs DC {e['dc']} -> {'SUCCESS' if e['success'] else 'FAIL'}")
        elif t == "spell_cast" and e.get("attack_roll"):
            out.append(f"{who} spell attack ({e['spell_id']}): {_roll(e['attack_roll'])}")
        elif t == "concentration" and e.get("save"):
            s = e["save"]
            out.append(f"{who} concentration save: {_roll(s['roll'])} vs DC {s['dc']} -> "
                       f"{'KEPT' if s['success'] else 'BROKEN'}")
        elif t == "death":
            out.append(f"  {name_of(state, e['target_id'])} dies")
        elif t == "resource":
            out.append(f"  {who}: {e['resource'].replace('_', ' ')} {e['delta']:+d} ({e['remaining']} left)")
    return out


# ====================================================================== guard
def guard(text, turn_result, state):
    """Reasons this narration breaks the rules ([] = fine)."""
    problems = []
    if not text or not text.strip():
        return ["empty"]
    plain = text
    for cid in by_id(state):   # "Human Zombie 2" is a name, not a stated number
        plain = plain.replace(name_of(state, cid), "")
    if re.search(r"\d", plain):
        problems.append("it states a number; describe without numbers")
    has_death = any(e["type"] == "death" for e in turn_result.get("events", []))
    if not has_death and DEATH_WORDS.search(text):
        problems.append("it says something died, but nothing died this turn")
    allowed = involved_ids(turn_result)
    me = player(state)
    for c in by_id(state).values():
        if c["id"] in allowed or (me and c["id"] == me["id"]):
            continue
        # match the full display name ("Giant Crab"); the generic words would false-alarm
        if re.search(rf"\b{re.escape(c['name'])}\b", text, re.I):
            # allowed if another creature with the same name is involved
            if not any(by_id(state).get(i, {}).get("name") == c["name"] for i in allowed):
                problems.append(f"it mentions {c['name']}, who isn't part of this turn")
    sentences = len(re.findall(r"[.!?](?:\s|$)", text.strip()))
    if sentences > 5:
        problems.append("it is longer than 4 sentences")
    return problems


def template_text(facts):
    """Offline narration: the fact sentences themselves, lightly joined."""
    text = " ".join(f for f in facts if not f.startswith("(not allowed"))
    return text or "Nothing much happens."


# ====================================================================== narrator
class Narrator:
    def __init__(self, client, model="", max_tokens=700, temperature=0.8):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.system = load_prompt("narrator_system")

    def narrate(self, turn_results, state):
        outs, pending = [], []
        for tr in turn_results:
            facts = facts_for_turn(tr, state)
            item = {"turn": tr.get("turn"), "actor_id": tr.get("actor_id"), "status": tr["status"],
                    "text": "", "source": "engine", "dice": dice_log(tr, state), "facts": facts}
            if tr["status"] == "needs_clarification":
                item["text"] = tr.get("narration_hint") or "Could you say that another way?"
            elif tr["status"] == "rejected" and not any(not f.startswith("(not allowed") for f in facts):
                item["text"] = " ".join(e["message"] for e in tr["events"] if e["type"] == "rejected")
            elif not facts:
                item["text"] = ""   # e.g. a pure talk or rules question turn: handled elsewhere
            else:
                pending.append((item, tr))
            outs.append(item)
        if pending:
            self._fill(pending, state)
        for item in outs:   # an accepted-then-rejected turn: add the engine's reason after the story
            tr = next((t for t in turn_results if t.get("turn") == item["turn"] and t.get("actor_id") == item["actor_id"]), None)
            rej = [e["message"] for e in (tr or {}).get("events", []) if e["type"] == "rejected"]
            if rej and item["source"] != "engine":
                item["text"] = f"{item['text']} ({' '.join(rej)})".strip()
        return outs

    # ------------------------------------------------------------------
    def _prompt(self, pending, state, notes=None):
        blocks = []
        for i, (item, tr) in enumerate(pending, 1):
            actor = name_of(state, tr.get("actor_id"))
            hint = f"\nEngine note: {tr['narration_hint']}" if tr.get("narration_hint") else ""
            blocks.append(f"TURN {i} ({actor}'s turn):\n- " + "\n- ".join(
                f for f in item["facts"] if not f.startswith("(not allowed")) + hint)
        text = f"{scene_context(state)}\n\nFACTS (the only things that happened):\n\n" + "\n\n".join(blocks)
        text += f"\n\nWrite exactly {len(pending)} narration(s)."
        if notes:
            text += "\n\nFix these problems from your last answer:\n" + "\n".join(notes)
        return text

    def _ask(self, pending, state, notes=None):
        reply = self.client.complete(self.system, self._prompt(pending, state, notes), self.model,
                                     self.max_tokens, self.temperature, json_mode=True)
        texts = extract_json(reply).get("narrations", [])
        if not isinstance(texts, list):
            raise ValueError("narrations is not a list")
        return [str(t).strip() for t in texts] + [""] * (len(pending) - len(texts))

    def _fill(self, pending, state):
        try:
            texts = self._ask(pending, state)
        except (LLMError, ValueError, json.JSONDecodeError):
            for item, _ in pending:
                item.update(text=template_text(item["facts"]), source="template")
            return
        bad = []
        for (item, tr), text in zip(pending, texts):
            problems = guard(text, tr, state)
            if problems:
                bad.append(((item, tr), problems))
            else:
                item.update(text=text, source="llm")
        if not bad:
            return
        retry = [p for p, _ in bad]
        notes = [f"Turn {i}: {', '.join(probs)}." for i, (_, probs) in enumerate(bad, 1)]
        try:
            texts = self._ask(retry, state, notes)
        except (LLMError, ValueError, json.JSONDecodeError):
            texts = [""] * len(retry)
        for (item, tr), text in zip(retry, texts):
            if text and not guard(text, tr, state):
                item.update(text=text, source="llm")
            else:
                item.update(text=template_text(item["facts"]), source="template")
