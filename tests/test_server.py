"""HTTP-level tests for api/server.py. Skipped automatically if FastAPI
(or httpx, which its TestClient needs) isn't installed:
    pip install -r requirements.txt"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from api.service import GameService  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "service", GameService(saves_dir=tmp_path))
    return TestClient(server.app)


def test_full_http_flow(client):
    assert client.get("/health").json() == {"ok": True}
    assert any(c["id"] == "cleric_dwarf_life" for c in client.get("/characters").json())
    assert "demo_cellar" in client.get("/rooms").json()

    r = client.post("/games", json={"character": "barbarian_orc_berserker", "seed": 11})
    assert r.status_code == 201
    sid = r.json()["session_id"]
    assert client.get(f"/games/{sid}").json()["mode"] == "exploration"

    body = {"schema_version": "1.0.0", "actor_id": "barbarian_1", "raw_text": "walk east",
            "confidence": 0.9, "actions": [{"type": "move", "to": {"x": 5, "y": 6}}]}
    r = client.post(f"/games/{sid}/actions", json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["turn_results"][0]["status"] == "accepted"
    assert out["state"]["session_id"] == sid

    cells = client.get(f"/games/{sid}/reachable", params={"actor_id": "barbarian_1"}).json()
    assert isinstance(cells, list)
    assert client.post(f"/games/{sid}/save").status_code == 200
    assert client.post(f"/games/{sid}/load").json()["session_id"] == sid
    assert any(g["session_id"] == sid for g in client.get("/games").json())


def test_http_errors(client):
    assert client.get("/games/missing").status_code == 404
    r = client.post("/games", json={"character": "wizard"})
    assert r.status_code == 422
    sid = client.post("/games", json={"character": "barbarian_orc_berserker"}).json()["session_id"]
    r = client.post(f"/games/{sid}/actions", json={"actor_id": "barbarian_1"})
    assert r.status_code == 422 and r.json()["detail"]["errors"]


def test_settings_endpoint(client):
    sid = client.post("/games", json={"character": "barbarian_orc_berserker"}).json()["session_id"]
    r = client.put(f"/games/{sid}/settings", json={"auto_opportunity_attacks": False})
    assert r.status_code == 200 and r.json() == {"auto_opportunity_attacks": False}
    assert client.put(f"/games/{sid}/settings", json={"auto_opportunity_attacks": "no"}).status_code == 422
