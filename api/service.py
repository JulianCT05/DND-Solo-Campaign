"""Game service: the API's logic with no web framework in it (Leena's part).

server.py maps HTTP routes onto these methods, so everything here can be
unit tested without starting a server.

Saving: after every accepted request the session is written to
saves/<session_id>.json:
    {"format": 1, "state": <game_state>, "dice": {"seed", "count", "rng_state"}}
The dice RNG state is saved too, so a loaded game continues with exactly the
dice it would have rolled. Every event is also appended to
saves/<session_id>.events.jsonl (the full log; state keeps only the last 30).
"""
import json
import re
import threading
from pathlib import Path

from engine import Dice, GameData, validate_state
from engine.combat import CombatFlow
from engine.events import SCHEMA_DIR
from engine.newgame import PREGENS, list_rooms, new_game

DEFAULT_SAVES = Path(__file__).resolve().parent.parent / "saves"
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class GameNotFound(Exception):
    pass


class BadRequest(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or []


# ---------------------------------------------------------------- request validation
_req_validator = None


def validate_action_request(req):
    """Errors for an action_request against Julian's schema ([] = valid)."""
    global _req_validator
    if _req_validator is None:
        import jsonschema
        ae = json.loads((SCHEMA_DIR / "action_event.schema.json").read_text())
        schema = {"$schema": ae["$schema"], "$defs": ae["$defs"], "$ref": "#/$defs/action_request"}
        _req_validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in _req_validator.iter_errors(req)]


# ---------------------------------------------------------------- service
class Session:
    def __init__(self, state, data, dice):
        self.state = state
        self.dice = dice
        self.flow = CombatFlow(state, data, dice)


class GameService:
    def __init__(self, saves_dir=DEFAULT_SAVES, data=None):
        self.saves_dir = Path(saves_dir)
        self.saves_dir.mkdir(parents=True, exist_ok=True)
        self.data = data or GameData()
        self.sessions = {}
        self.lock = threading.Lock()

    # ---------------- info
    def characters(self):
        return [{"id": ref, "name": self.data.characters[ref]["name"],
                 "class": self.data.characters[ref]["class"]} for ref in PREGENS if ref in self.data.characters]

    def rooms(self):
        return list_rooms()

    # ---------------- lifecycle
    def new_game(self, character, room_id="demo_cellar", allies=None, seed=None):
        try:
            state = new_game(self.data, character, room_id, allies, seed)
        except (KeyError, FileNotFoundError) as e:
            raise BadRequest(str(e).strip("'\""))
        with self.lock:
            s = Session(state, self.data, Dice(state["rng_seed"]))
            self.sessions[state["session_id"]] = s
            self._save(s, [])
        return state

    def get_state(self, session_id):
        return self._session(session_id).state

    def submit(self, session_id, request):
        errors = validate_action_request(request)
        if errors:
            raise BadRequest("The action_request doesn't match the schema.", errors)
        with self.lock:
            s = self._session(session_id)
            before = len(s.flow.full_log)
            results = s.flow.submit(request)
            self._save(s, s.flow.full_log[before:])
        return {"turn_results": results, "state": s.state}

    def reachable(self, session_id, actor_id):
        s = self._session(session_id)
        if s.flow.get(actor_id) is None:
            raise BadRequest(f"No creature {actor_id} in this game.")
        cells = s.flow.reachable(actor_id)
        return [{"x": x, "y": y, "feet": f} for (x, y), f in sorted(cells.items())]

    SETTINGS = {"auto_opportunity_attacks": bool}

    def settings(self, session_id, changes):
        """Player options stored in state flags. Only known keys with the right type."""
        bad = [k for k, v in changes.items()
               if k not in self.SETTINGS or not isinstance(v, self.SETTINGS[k])]
        if bad:
            raise BadRequest(f"Unknown or invalid settings: {', '.join(bad)}. "
                             f"Allowed: {', '.join(self.SETTINGS)} (true/false).")
        with self.lock:
            s = self._session(session_id)
            s.state["flags"].update(changes)
            self._save(s, [])
            return {k: s.state["flags"].get(k, True) for k in self.SETTINGS}

    # ---------------- save / load
    def save(self, session_id):
        with self.lock:
            s = self._session(session_id)
            return f"saves/{self._save(s, []).name}"

    def load(self, session_id):
        """Load a saved game from disk, replacing what's in memory."""
        path = self._path(session_id)
        if not path.exists():
            raise GameNotFound(session_id)
        blob = json.loads(path.read_text(encoding="utf-8"))
        state = blob["state"]
        errors = validate_state(state)
        if errors:
            raise BadRequest("Saved game doesn't match the game state schema.", errors)
        d = blob.get("dice", {})
        dice = Dice(d.get("seed", state.get("rng_seed")))
        if d.get("rng_state"):
            version, internal, gauss = d["rng_state"]
            dice.rng.setstate((version, tuple(internal), gauss))
            dice.count = d.get("count", 0)
        with self.lock:
            s = Session(state, self.data, dice)
            self.sessions[session_id] = s
        return state

    def list_saves(self):
        out = []
        for p in sorted(self.saves_dir.glob("*.json")):
            try:
                st = json.loads(p.read_text(encoding="utf-8"))["state"]
                out.append({"session_id": st["session_id"], "mode": st["mode"],
                            "room_id": st.get("location", {}).get("room_id"),
                            "turn_counter": st["turn_counter"]})
            except (ValueError, KeyError):
                continue
        return out

    # ---------------- internals
    def _path(self, session_id):
        if not _SESSION_ID.match(session_id or ""):
            raise GameNotFound(session_id)
        return self.saves_dir / f"{session_id}.json"

    def _session(self, session_id):
        s = self.sessions.get(session_id)
        if s is None:
            if self._path(session_id).exists():
                self.load(session_id)
                return self.sessions[session_id]
            raise GameNotFound(session_id)
        return s

    def _save(self, s, new_events):
        sid = s.state["session_id"]
        path = self._path(sid)
        version, internal, gauss = s.dice.rng.getstate()
        blob = {"format": 1, "state": s.state,
                "dice": {"seed": s.dice.seed, "count": s.dice.count,
                         "rng_state": [version, list(internal), gauss]}}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(blob), encoding="utf-8")
        tmp.replace(path)  # atomic: a crash mid-write never leaves a half-written save
        if new_events:
            with open(self.saves_dir / f"{sid}.events.jsonl", "a", encoding="utf-8") as f:
                for e in new_events:
                    f.write(json.dumps(e) + "\n")
        return path
