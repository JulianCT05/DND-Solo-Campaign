"""HTTP tests for llm/routes.py. Skipped if FastAPI/httpx aren't installed
(pip install -r requirements.txt)."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.service import GameService  # noqa: E402
from llm import FakeClient, LLMConfig  # noqa: E402
from llm.routes import make_router  # noqa: E402


def model(system, user):
    if system.startswith("You convert"):
        return json.dumps({"actions": [{"type": "use_feature", "feature": "rage"}], "confidence": 0.9,
                           "clarification_needed": None})
    return json.dumps({"narrations": ["Fury fills you."] * max(user.count("TURN "), 1)})


@pytest.fixture
def client(tmp_path):
    service = GameService(saves_dir=tmp_path)
    app = FastAPI()
    app.include_router(make_router(service, LLMConfig(provider="offline", log_path=""), FakeClient(model)))
    return TestClient(app), service


def test_say_act_and_summary(client):
    http, service = client
    gid = service.new_game("barbarian_orc_berserker", "demo_cellar", seed=3)["session_id"]
    out = http.post(f"/games/{gid}/say", json={"text": "I rage"}).json()
    assert out["request"]["actions"] == [{"type": "use_feature", "feature": "rage"}]
    assert out["narrations"][0]["text"] == "Fury fills you."
    click = {"schema_version": "1.0.0", "actor_id": "barbarian_1", "raw_text": "(click)", "confidence": 1.0,
             "actions": [{"type": "move", "to": {"x": 2, "y": 5}}]}
    assert http.post(f"/games/{gid}/act", json=click).status_code == 200
    r = http.put(f"/games/{gid}/summary", json={"text": "Grruk raged.", "updated_at_turn": 2})
    assert r.json()["text"] == "Grruk raged." and service.get_state(gid)["summary"]["text"] == "Grruk raged."
    assert http.post("/games/nope/say", json={"text": "hi"}).status_code == 404
    assert http.get("/llm/status").json()["provider"] == "offline"
