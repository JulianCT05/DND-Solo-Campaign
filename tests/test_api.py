"""API logic (service layer) and the scripted playthroughs.
test_server.py checks the FastAPI wiring itself when FastAPI is installed."""
import json

import pytest

from api.service import BadRequest, GameNotFound, GameService, validate_action_request
from engine import validate_state, validate_turn_result
from engine import grid as G


@pytest.fixture
def svc(tmp_path):
    return GameService(saves_dir=tmp_path)


def req(actor, *actions, text="test"):
    return {"schema_version": "1.0.0", "actor_id": actor, "raw_text": text,
            "confidence": 1.0, "actions": list(actions)}


def test_new_game_is_schema_valid_and_saved(svc, tmp_path):
    st = svc.new_game("barbarian_orc_berserker", seed=11)
    assert validate_state(st) == []
    assert st["mode"] == "exploration" and st["party"][0]["controller"] == "player"
    assert [p["id"] for p in st["party"]] == ["barbarian_1", "cleric_1", "ranger_1"]
    assert (tmp_path / f"{st['session_id']}.json").exists()
    assert svc.get_state(st["session_id"]) is st


def test_new_game_errors(svc):
    with pytest.raises(BadRequest):
        svc.new_game("wizard_gnome")
    with pytest.raises(BadRequest):
        svc.new_game("barbarian_orc_berserker", room_id="no_such_room")


def test_unknown_or_unsafe_session_ids(svc):
    with pytest.raises(GameNotFound):
        svc.get_state("nope")
    with pytest.raises(GameNotFound):
        svc.get_state("../../etc/passwd")


def test_bad_action_request_is_refused_with_reasons(svc):
    st = svc.new_game("barbarian_orc_berserker", seed=1)
    bad = req("barbarian_1", {"type": "move"})          # no 'to'
    with pytest.raises(BadRequest) as e:
        svc.submit(st["session_id"], bad)
    assert e.value.errors
    assert validate_action_request(req("barbarian_1", {"type": "end_turn"})) == []


def test_example_action_file_is_accepted(svc):
    """Julian's example_action.json goes through the endpoint unchanged."""
    from pathlib import Path
    example = json.loads((Path(__file__).resolve().parent.parent / "data" / "example_action.json").read_text())
    assert validate_action_request(example) == []


def test_reachable_cells_for_gui(svc):
    st = svc.new_game("barbarian_orc_berserker", seed=1)
    cells = svc.reachable(st["session_id"], "barbarian_1")
    assert cells and all(set(c) == {"x", "y", "feet"} for c in cells)


def _play(svc, sid, max_turns=40):
    """Drive the player with a simple script: rage, walk up, hit the nearest enemy."""
    all_results = []
    raged = False
    for _ in range(max_turns):
        st = svc.get_state(sid)
        if st["mode"] in ("game_over", "victory"):
            break
        me = st["party"][0]
        foes = [e for e in st["enemies"] if e["alive"] and not any(x["name"] == "Fled" for x in e["effects"])]
        if not foes:
            break
        target = min(foes, key=lambda e: (G.distance_ft(me["position"], e["position"]), e["id"]))
        acts = []
        if st["mode"] == "combat" and not raged:
            acts.append({"type": "use_feature", "feature": "rage"})
            raged = True
        if not G.adjacent(me["position"], target["position"]):
            from engine.combat import CombatFlow
            flow = svc.sessions[sid].flow
            goals = [(target["position"]["x"] + dx, target["position"]["y"] + dy)
                     for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            budget = me["turn"]["movement_left_ft"] if st["mode"] == "combat" else G.INF
            path, _, _ = G.best_step_toward(flow.grid(), me, goals, budget)
            if path:
                acts.append({"type": "move", "to": G.as_dict(path[-1])})
            end = path[-1] if path else G.pos(me)
        else:
            end = G.pos(me)
        if G.adjacent(G.at(me, end), target):     # only swing if the move gets us there
            acts.append({"type": "attack", "target_id": target["id"]})
        if st["mode"] == "combat":
            acts.append({"type": "end_turn"})
        if not acts:
            break
        out = svc.submit(sid, req("barbarian_1", *acts))
        for tr in out["turn_results"]:
            assert validate_turn_result(tr) == [], validate_turn_result(tr)
        assert validate_state(out["state"]) == [], validate_state(out["state"])
        all_results += out["turn_results"]
    return all_results


def test_scripted_playthrough_reaches_an_ending(svc):
    st = svc.new_game("barbarian_orc_berserker", seed=11)
    results = _play(svc, st["session_id"])
    st = svc.get_state(st["session_id"])
    types = {e["type"] for tr in results for e in tr["events"]}
    assert {"combat_start", "initiative", "turn_start", "move", "attack", "damage", "death"} <= types
    alive_foes = [e for e in st["enemies"] if e["alive"]]
    assert st["mode"] == "game_over" or not alive_foes


def test_same_seed_same_game(tmp_path):
    a = GameService(saves_dir=tmp_path / "a")
    b = GameService(saves_dir=tmp_path / "b")
    sa = a.new_game("barbarian_orc_berserker", seed=99)
    sb = b.new_game("barbarian_orc_berserker", seed=99)
    ra = _play(a, sa["session_id"])
    rb = _play(b, sb["session_id"])
    assert json.dumps(ra) == json.dumps(rb)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
def test_many_seeds_never_break_the_schemas(svc, seed):
    for pc in ("barbarian_orc_berserker", "cleric_dwarf_life", "ranger_wood_elf_hunter"):
        st = svc.new_game(pc, seed=seed)
        sid, pid = st["session_id"], st["party"][0]["id"]
        # walk the player toward the frog, then just end turns and let the AI fight it out
        svc.submit(sid, req(pid, {"type": "move", "to": {"x": 6, "y": 6}}))
        for _ in range(30):
            if svc.get_state(sid)["mode"] != "combat":
                break
            out = svc.submit(sid, req(pid, {"type": "end_turn"}))
            for tr in out["turn_results"]:
                assert validate_turn_result(tr) == []
            assert validate_state(out["state"]) == []


def test_save_and_load_continues_with_the_same_dice(tmp_path):
    svc = GameService(saves_dir=tmp_path)
    st = svc.new_game("barbarian_orc_berserker", seed=21)
    sid = st["session_id"]
    svc.submit(sid, req("barbarian_1", {"type": "move", "to": {"x": 6, "y": 6}}))
    snapshot = (tmp_path / f"{sid}.json").read_text()

    # Branch 1: keep playing in memory
    out1 = svc.submit(sid, req("barbarian_1", {"type": "end_turn"}))
    # Branch 2: a new server process loads the snapshot and does the same thing
    other_dir = tmp_path / "copy"
    other_dir.mkdir()
    (other_dir / f"{sid}.json").write_text(snapshot)
    fresh = GameService(saves_dir=other_dir)
    fresh.load(sid)
    out2 = fresh.submit(sid, req("barbarian_1", {"type": "end_turn"}))
    assert out1["turn_results"] and json.dumps(out1["turn_results"]) == json.dumps(out2["turn_results"])


def test_event_log_is_appended_to_disk(tmp_path):
    svc = GameService(saves_dir=tmp_path)
    st = svc.new_game("barbarian_orc_berserker", seed=3)
    svc.submit(st["session_id"], req("barbarian_1", {"type": "move", "to": {"x": 3, "y": 6}}))
    lines = (tmp_path / f"{st['session_id']}.events.jsonl").read_text().splitlines()
    assert lines and json.loads(lines[0])["type"] == "move"
    assert any(s["session_id"] == st["session_id"] for s in svc.list_saves())


def test_server_reloads_a_game_it_has_never_seen(tmp_path):
    svc = GameService(saves_dir=tmp_path)
    st = svc.new_game("ranger_wood_elf_hunter", seed=3)
    other = GameService(saves_dir=tmp_path)       # e.g. after a restart
    assert other.get_state(st["session_id"])["session_id"] == st["session_id"]
