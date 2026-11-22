"""Answers `rules_question` actions from the team's rules JSON, not from the
model's memory.

Julian's rules files go in data/rules/*.json (dm_role_and_flow, core_mechanics,
combat_rules, reference_tables, spellcasting_rules). Any JSON shape works: the
files are flattened into small chunks ("combat_rules > opportunity_attacks"),
the chunks that share the most words with the question are picked, and only
those go to the model. spells.json and the player's sheet are searched too.
"""
import json
import re
from pathlib import Path

from .client import LLMError, extract_json
from .config import load_prompt
from .context import load_data, player

RULES_DIR = Path(__file__).resolve().parent.parent / "data" / "rules"
STOP = set("a an the and or of to in on at is are was be can i my me do does how what when if it for with you your "
           "this that from by as into get have has will would should could about".split())


def _terms(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1]


def flatten(obj, path):
    """Yield (path, text) chunks of up to ~600 characters from any JSON value."""
    if isinstance(obj, dict):
        simple = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
        if simple and len(json.dumps(simple)) < 600:
            yield path, "; ".join(f"{k}: {v}" for k, v in simple.items())
            rest = {k: v for k, v in obj.items() if isinstance(v, (dict, list))}
        else:
            rest = obj
        for k, v in rest.items():
            yield from flatten(v, f"{path} > {k}")
    elif isinstance(obj, list):
        if all(not isinstance(x, (dict, list)) for x in obj) and len(json.dumps(obj)) < 600:
            yield path, "; ".join(map(str, obj))
        else:
            for i, v in enumerate(obj):
                label = v.get("name") or v.get("id") or v.get("title") or str(i) if isinstance(v, dict) else str(i)
                yield from flatten(v, f"{path} > {label}")
    elif obj not in (None, ""):
        text = str(obj)
        for i in range(0, len(text), 600):
            yield path, text[i:i + 600]


class RulesIndex:
    def __init__(self, rules_dir=RULES_DIR):
        self.chunks = []
        d = Path(rules_dir)
        if d.exists():
            for p in sorted(d.glob("*.json")):
                try:
                    self.chunks += list(flatten(json.loads(p.read_text(encoding="utf-8")), p.stem))
                except ValueError:
                    continue
        for sid, sp in load_data()["spells"].items():
            self.chunks.append((f"spells > {sp.get('name', sid)}",
                                f"{sp.get('name')}: level {sp.get('level')}, {sp.get('casting_time')}, range "
                                f"{sp.get('range')}, {sp.get('duration')}. {sp.get('description', '')[:450]}"))

    def search(self, question, k=4, extra=()):
        q = set(_terms(question))
        pool = self.chunks + list(extra)
        scored = []
        for path, text in pool:
            words = _terms(path + " " + text)
            score = sum(2 if w in _terms(path) else 1 for w in q if w in words)
            if score:
                scored.append((score, -len(text), path, text))
        scored.sort(reverse=True)
        return [(p, t) for _, _, p, t in scored[:k]]


class RulesQA:
    def __init__(self, client, model="", index=None, max_tokens=300):
        self.client = client
        self.model = model
        self.index = index or RulesIndex()
        self.max_tokens = max_tokens
        self.system = load_prompt("rules_system")

    def answer(self, question, state=None):
        extra = []
        me = player(state) if state else None
        if me:
            sheet = load_data()["characters"].get(me.get("ref"), {})
            for f in sheet.get("class_features", []) + sheet.get("species_traits", []) + sheet.get("feats", []):
                extra.append((f"{sheet.get('name', 'character')} > {f['name']}", f["description"]))
        hits = self.index.search(question, extra=extra)
        if not hits:
            return {"answer": "I don't have that rule in the game's rules files. Check with your table.",
                    "sources": [], "source": "template"}
        excerpts = "\n\n".join(f"[{p}]\n{t}" for p, t in hits)
        user = f"Rule excerpts:\n{excerpts}\n\nQuestion: {question}"
        try:
            reply = self.client.complete(self.system, user, self.model, self.max_tokens, 0.0, json_mode=True)
            text = str(extract_json(reply).get("answer", "")).strip()
            if text:
                return {"answer": text, "sources": [p for p, _ in hits], "source": "llm"}
        except (LLMError, ValueError):
            pass
        p, t = hits[0]
        return {"answer": f"From {p}: {t[:400]}", "sources": [p], "source": "template"}
