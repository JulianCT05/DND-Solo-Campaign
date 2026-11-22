"""Every story in data/stories/ passes check_story, and the checker catches bad ids."""
import json
from pathlib import Path

import pytest

from check_story import ROOT, check

STORIES = sorted((ROOT / "data" / "stories").glob("*.json"))


@pytest.mark.parametrize("path", STORIES, ids=[p.stem for p in STORIES])
def test_story_passes(path):
    problems, _ = check(path)
    assert problems == []


def test_story_starts_in_a_real_room():
    story = json.loads((ROOT / "data" / "stories" / "flooded_cellar.json").read_text(encoding="utf-8"))
    assert (ROOT / "data" / "rooms" / f"{story['start']['room_id']}.json").exists()


def test_game_state_accepts_story_progress():
    from engine import GameData, validate_state
    from engine.newgame import new_game
    state = new_game(GameData(), "cleric_dwarf_life", seed=1)
    assert validate_state(state) == []          # no story block is still valid
    state["story"] = {"story_id": "flooded_cellar", "current_beat": "b_fight",
                      "beats": {"b_arrival": "complete", "b_fight": "active"},
                      "objectives": {"obj_clear": "active", "obj_chest": "hidden"},
                      "clues_found": [], "npc_attitudes": {"ferol_sal_1": "unfriendly"},
                      "fired_triggers": [], "ending_id": None}
    assert validate_state(state) == []
    state["story"]["beats"]["b_fight"] = "finished"
    assert validate_state(state) != []


def test_npc_notes_match_story():
    """npcs.json holds the story's NPCs with matching ids, and never the gated topics."""
    story = json.loads((ROOT / "data" / "stories" / "flooded_cellar.json").read_text(encoding="utf-8"))
    notes = json.loads((ROOT / "josh-llm-layer" / "data" / "npcs.json").read_text(encoding="utf-8"))
    by_id = {n["id"]: n for n in notes["npcs"]}
    for npc in story.get("npcs", []):
        assert npc["id"] in by_id
        note = by_id[npc["id"]]
        assert "topics" not in note
        for k in ("ref", "name", "attitude", "knows"):
            assert note.get(k) == npc.get(k), k


@pytest.mark.parametrize("mutate, expect", [
    (lambda s: s["start"].update(room_id="room_3"), "no file data/rooms/room_3.json"),
    (lambda s: s["triggers"][1]["on"]["match"].update(target_id="frog_99"), "frog_99"),
    (lambda s: s["clues"][0]["found_by"].update(object_id="crate_1"), "crate_1"),
    (lambda s: s["beats"][0]["next"].append("b_missing"), "b_missing"),
    (lambda s: s["flags"].pop("cellar_cleared"), "unknown flag cellar_cleared"),
    (lambda s: s["npcs"][0].update(ref="not_a_monster"), "not_a_monster"),
])
def test_checker_catches_bad_ids(tmp_path, mutate, expect):
    story = json.loads((ROOT / "data" / "stories" / "flooded_cellar.json").read_text(encoding="utf-8"))
    mutate(story)
    p = tmp_path / "story.json"
    p.write_text(json.dumps(story), encoding="utf-8")
    problems, _ = check(p)
    assert any(expect in x for x in problems), problems
