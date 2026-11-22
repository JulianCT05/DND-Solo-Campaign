"""Event collection and validation against action_event.schema.json.

EventLog numbers events with `seq` in the order they happen inside one turn.
validate_event() checks the official schema AND rejects unknown field names,
because the schema itself lets misspelled fields (e.g. 'hp_afterr') through.
"""
import json
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


class EventLog:
    def __init__(self, start_seq=0):
        self.events = []
        self.start_seq = start_seq

    def emit(self, type_, **fields):
        ev = {"type": type_, "seq": self.start_seq + len(self.events)}
        ev.update({k: v for k, v in fields.items() if v is not None})
        self.events.append(ev)
        return ev

    def reject(self, actor_id, reason_code, message):
        return self.emit("rejected", actor_id=actor_id, reason_code=reason_code, message=message)


# ---------------------------------------------------------------- validation
_validator = None
_allowed_fields = None


def _load():
    global _validator, _allowed_fields
    if _validator is not None:
        return
    import jsonschema
    from referencing import Registry, Resource

    ae = json.loads((SCHEMA_DIR / "action_event.schema.json").read_text())
    gs = json.loads((SCHEMA_DIR / "game_state.schema.json").read_text())
    registry = Registry().with_resources([
        (ae["$id"], Resource.from_contents(ae)),
        (gs["$id"], Resource.from_contents(gs)),
    ])
    event_schema = {"$schema": ae["$schema"], "$id": ae["$id"] + "#event-only",
                    "$defs": ae["$defs"], "$ref": "#/$defs/event"}
    _validator = {
        "event": jsonschema.Draft202012Validator(event_schema, registry=registry),
        "turn_result": jsonschema.Draft202012Validator(
            {**event_schema, "$ref": "#/$defs/turn_result"}, registry=registry),
        "game_state": jsonschema.Draft202012Validator(gs, registry=registry),
    }
    base = set(ae["$defs"]["event_base"]["properties"]) | {"type", "seq"}
    _allowed_fields = {}
    for name, d in ae["$defs"].items():
        if name.startswith("e_"):
            t = d["properties"]["type"]["const"]
            _allowed_fields[t] = base | set(d["properties"])


def validate_event(ev):
    """Returns a list of error strings (empty list = valid)."""
    _load()
    errors = [e.message for e in _validator["event"].iter_errors(ev)]
    allowed = _allowed_fields.get(ev.get("type"))
    if allowed is None:
        errors.append(f"unknown event type {ev.get('type')!r}")
    else:
        extra = set(ev) - allowed
        if extra:
            errors.append(f"unknown field(s) on {ev['type']} event: {sorted(extra)}")
    return errors


def validate_state(state):
    _load()
    return [f"{list(e.path)}: {e.message}" for e in _validator["game_state"].iter_errors(state)]


def validate_turn_result(tr):
    _load()
    errors = [e.message for e in _validator["turn_result"].iter_errors(tr)]
    for ev in tr.get("events", []):
        errors += validate_event(ev)
    return errors
