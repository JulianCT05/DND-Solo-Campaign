"""NPC dialogue: voices the reply when the engine returns a `dialogue` event
with needs_llm: true (the player used a `talk` action).

NPC notes come from data/npcs.json (E'Niyah's story notes), matched by the
creature's instance id or its ref. Format:

    {"npcs": [{"id": "ferol_sal_1", "ref": "ferol_sal", "name": "...",
               "personality": "...", "voice": "...", "goal": "...",
               "knows": ["fact the NPC may share", ...],
               "attitude": "neutral"}]}

The NPC may only share what is in "knows" and can't change the game state.
"""
import json
import re
from pathlib import Path

from .client import LLMError, extract_json
from .config import load_prompt
from .context import by_id, player, scene_context

NPC_FILE = Path(__file__).resolve().parent.parent / "data" / "npcs.json"


def load_npc_notes(path=NPC_FILE):
    p = Path(path)
    if not p.exists():
        return {}
    notes = {}
    for n in json.loads(p.read_text(encoding="utf-8")).get("npcs", []):
        for k in (n.get("id"), n.get("ref")):
            if k:
                notes[k] = n
    return notes


class NPCVoice:
    def __init__(self, client, model="", notes=None, max_tokens=250):
        self.client = client
        self.model = model
        self.notes = notes if notes is not None else load_npc_notes()
        self.max_tokens = max_tokens
        self.system = load_prompt("npc_system")

    def lines_for(self, turn_results, state):
        """[{speaker_id, speaker, line, source}] for every dialogue event that needs one."""
        out = []
        me = player(state)
        for tr in turn_results:
            said = _player_words(tr.get("narration_hint"))
            for e in tr.get("events", []):
                if e["type"] != "dialogue" or not e.get("needs_llm"):
                    continue
                if me and e["speaker_id"] == me["id"]:
                    continue  # talking to nobody in particular: nothing to answer
                out.append(self.speak(e, said or e.get("topic", ""), state))
        return out

    def speak(self, event, player_said, state):
        npc = by_id(state).get(event["speaker_id"], {"id": event["speaker_id"], "name": event["speaker_id"]})
        notes = self.notes.get(npc["id"]) or self.notes.get(npc.get("ref")) or {}
        attitude = event.get("attitude") or notes.get("attitude") or \
            ("hostile" if npc in state.get("enemies", []) and npc.get("ai", {}).get("awake", True) else "neutral")
        user = (f"{scene_context(state)}\n\nNPC: {npc['name']} ({npc['id']}). Attitude: {attitude}.\n"
                f"NPC notes: {json.dumps({k: v for k, v in notes.items() if k not in ('id', 'ref')}) if notes else 'none'}\n\n"
                f"The player says: {json.dumps(player_said)}")
        try:
            reply = self.client.complete(self.system, user, self.model, self.max_tokens, 0.9, json_mode=True)
            line = str(extract_json(reply).get("line", "")).strip()
            if line:
                return {"speaker_id": npc["id"], "speaker": npc["name"], "line": line, "source": "llm"}
        except (LLMError, ValueError):
            pass
        fallback = {"hostile": "snarls and gives no answer.", "unfriendly": "eyes you coldly and says nothing.",
                    "friendly": "nods, but seems unsure what to say.", "helpful": "listens closely and nods.",
                    }.get(attitude, "regards you silently.")
        return {"speaker_id": npc["id"], "speaker": npc["name"], "line": f"{npc['name']} {fallback}",
                "source": "template"}


def _player_words(hint):
    """Engine hint looks like: player says (friendly): Who are you?"""
    if not hint:
        return ""
    m = re.match(r"player says \([^)]*\):\s*(.*)", hint, re.S)
    return m.group(1).strip() if m else ""
