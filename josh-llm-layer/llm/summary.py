"""Rolling story summary: a few sentences the model rewrites now and then, so
narration stays consistent without sending the whole chat history every turn.

It lives in state["summary"] = {"text", "updated_at_turn"} (game state schema).

Rewrite triggers (whichever comes first):
  * every `every_n_turns` turn_results with story content
  * combat ends, someone dies, a new room is entered, or the game ends
Offline, the newest key facts are appended and the text is trimmed to 80 words.
"""
from .client import LLMError, extract_json
from .config import load_prompt
from .context import player

MAX_WORDS = 80
BIG_EVENTS = {"combat_end", "death"}


def _trim(text, max_words=MAX_WORDS):
    words = text.split()
    return text if len(words) <= max_words else "... " + " ".join(words[-max_words:])


class RollingSummary:
    def __init__(self, client, model="", every_n_turns=5, max_tokens=250):
        self.client = client
        self.model = model
        self.every = every_n_turns
        self.max_tokens = max_tokens
        self.system = load_prompt("summary_system")
        self.buffer = []          # narration texts since the last rewrite
        self.key_facts = []       # big-event fact lines since the last rewrite

    def add(self, narrations, turn_results):
        for n in narrations:
            if n.get("text") and n.get("source") in ("llm", "template"):
                self.buffer.append(n["text"])
                self.key_facts += [f for f in n.get("facts", []) if any(
                    w in f for w in (" dies", "fight is over", "flees", "new area", "adventure"))]
        return self.due(turn_results)

    def note(self, text):
        """Add a line that isn't narration (e.g. 'Grruk spoke with Ferol: ...')."""
        if text:
            self.buffer.append(text)

    def due(self, turn_results):
        if not self.buffer:
            return False
        for tr in turn_results:
            for e in tr.get("events", []):
                if e["type"] in BIG_EVENTS:
                    return True
                if e["type"] == "state_change" and e["field"] in ("location.room_id", "mode"):
                    return True
        return len(self.buffer) >= self.every

    def rewrite(self, state):
        """Returns the new summary object, or None if there was nothing to add."""
        if not self.buffer:
            return None
        old = (state.get("summary") or {}).get("text", "")
        me = player(state)
        user = (f"Player character: {me['name'] if me else 'the hero'}.\n"
                f"Old summary: {old or '(none yet)'}\n\nNew events:\n- " + "\n- ".join(self.buffer[-15:]))
        text = None
        try:
            reply = self.client.complete(self.system, user, self.model, self.max_tokens, 0.3, json_mode=True)
            text = str(extract_json(reply).get("summary", "")).strip() or None
        except (LLMError, ValueError):
            text = None
        if text is None:   # offline: keep the old summary and add only the big moments
            text = f"{old} {' '.join(self.key_facts[-3:])}".strip()
        new = {"text": _trim(text), "updated_at_turn": int(state.get("turn_counter", 0))}
        self.buffer, self.key_facts = [], []
        return new
