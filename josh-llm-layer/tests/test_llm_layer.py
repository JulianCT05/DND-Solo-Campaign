"""Tests for the LLM layer (Josh). No network: every model reply is scripted
with FakeClient, and the real engine runs in-process for the integration tests."""
import io
import json
import urllib.error

import pytest

from llm import FakeClient, KeywordParser, LLMError, Narrator, Parser, schema_errors
from llm.client import AnthropicClient, extract_json
from llm.config import LLMConfig, find_file
from llm.context import parser_context
from llm.dm import DungeonMaster, LocalEngine, build_dm
from llm.narrator import dice_log, facts_for_turn, guard
from llm.npc import NPCVoice, _player_words
from llm.rules_qa import RulesQA
from llm.summary import RollingSummary


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def engine(tmp_path):
    return LocalEngine(saves_dir=tmp_path)


@pytest.fixture
def state(engine):
    # demo_cellar: barbarian_1 at (1,6); giant_frog_1 (10,3), zombie_1 (11,6), knife_1 (9,1)
    return engine.new_game("barbarian_orc_berserker", "demo_cellar", seed=7)


def reply(actions, confidence=0.9, question=None):
    return json.dumps({"actions": actions, "confidence": confidence, "clarification_needed": question})


# ---------------------------------------------------------------- client
class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, retry_after=None):
    headers = {"retry-after": retry_after} if retry_after else {}
    return urllib.error.HTTPError("u", code, "err", headers, io.BytesIO(b'{"error":{"message":"busy sk-secret-123"}}'))


def test_client_retries_then_succeeds():
    calls = []

    def opener(req, timeout):
        calls.append(req)
        if len(calls) < 3:
            raise http_error(529)
        return FakeResp(json.dumps({"content": [{"type": "text", "text": '"ok": true}'}], "usage": {}}).encode())

    cfg = LLMConfig(provider="anthropic", api_key="sk-secret-123", max_retries=2, log_path="")
    c = AnthropicClient(cfg, sleep=lambda s: None, opener=opener)
    assert json.loads(c.complete("s", "u", "m", json_mode=True)) == {"ok": True}
    assert len(calls) == 3


def test_client_no_retry_on_auth_error_and_key_is_scrubbed():
    calls = []

    def opener(req, timeout):
        calls.append(1)
        raise http_error(401)

    cfg = LLMConfig(provider="anthropic", api_key="sk-secret-123", max_retries=3, log_path="")
    with pytest.raises(LLMError) as e:
        AnthropicClient(cfg, sleep=lambda s: None, opener=opener).complete("s", "u", "m")
    assert len(calls) == 1
    assert "sk-secret-123" not in str(e.value)


def test_config_without_key_goes_offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    cfg = LLMConfig.from_env(dotenv=False)
    assert cfg.provider == "offline" and not cfg.online
    assert "sk-" not in repr(LLMConfig(api_key="sk-abc"))


def test_extract_json_tolerates_fences_and_chatter():
    assert extract_json('Sure!\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}


# ---------------------------------------------------------------- parser
def test_context_lists_ids_distances_and_spells(state):
    ctx = parser_context(state)
    assert "giant_frog_1" in ctx and "45 ft away" in ctx and "Greataxe" in ctx and "rage" in ctx


def test_parser_maps_names_to_ids_and_resolves_move(state):
    p = Parser(FakeClient([reply([{"type": "use_feature", "feature": "Rage"},
                                  {"type": "move", "toward": "the frog"},
                                  {"type": "attack", "target_id": "Giant Frog", "weapon": "greataxe",
                                   "advantage": True}])]))
    req = p.parse("I rage and chop the frog", state)
    assert schema_errors(req) == []
    rage, move, atk = req["actions"]
    assert rage == {"type": "use_feature", "feature": "rage"}
    assert max(abs(move["to"]["x"] - 10), abs(move["to"]["y"] - 3)) == 1   # a square next to the frog
    assert atk == {"type": "attack", "target_id": "giant_frog_1", "weapon": "Greataxe"}   # no "advantage"
    assert req["actor_id"] == "barbarian_1" and req["raw_text"] == "I rage and chop the frog"


def test_parser_uses_reachable_cells_when_given(state):
    p = Parser(FakeClient([reply([{"type": "move", "toward": "zombie_1"}])]))
    cells = {(2, 6): 5, (3, 6): 10, (4, 6): 15}
    req = p.parse("go at the zombie", state, reachable=lambda: cells)
    assert req["actions"][0]["to"] == {"x": 4, "y": 6}


def test_parser_repairs_bad_output_once(state):
    p = Parser(FakeClient(["not json at all", reply([{"type": "end_turn"}])]))
    req = p.parse("I'm done", state)
    assert req["actions"] == [{"type": "end_turn"}] and p.last_source == "llm_repaired"


def test_parser_asks_when_target_is_unknown(state):
    p = Parser(FakeClient([reply([{"type": "attack", "target_id": "dragon"}])]))
    req = p.parse("attack the dragon", state)
    assert req["actions"] == [] and "dragon" in req["clarification_needed"]
    assert schema_errors(req) == []


def test_parser_passes_model_question_through(state):
    p = Parser(FakeClient([reply([], 0.3, "Which one: the frog or the zombie?")]))
    req = p.parse("attack", state)
    assert req["clarification_needed"].startswith("Which one")


def test_parser_low_confidence_becomes_question(state):
    p = Parser(FakeClient([reply([{"type": "dodge"}], confidence=0.1)]))
    req = p.parse("uhh duck i guess", state)
    assert req["actions"] == [] and req["clarification_needed"].startswith("Do you want to")


def test_parser_falls_back_to_keywords_when_model_is_down(state):
    p = Parser(FakeClient([LLMError("down")]))
    req = p.parse("I rage", state)
    assert req["actions"] == [{"type": "use_feature", "feature": "rage"}] and p.last_source == "keyword"


def test_parser_empty_text(state):
    assert Parser(FakeClient([])).parse("   ", state)["clarification_needed"]


def test_keyword_parser_basics(state):
    kp = KeywordParser()
    assert kp.parse("throw a javelin at the zombie", state)["actions"] == [
        {"type": "attack", "target_id": "zombie_1", "weapon": "Javelin", "modifiers": ["thrown"]}]
    assert kp.parse("attack", state)["clarification_needed"]


# ---------------------------------------------------------------- narrator
TURN = json.loads(find_file("data/example_turn_result.json").read_text(encoding="utf-8"))
STATE_EX = json.loads(find_file("data/example_game_state.json").read_text(encoding="utf-8"))


def test_facts_and_dice_log_from_example_turn():
    facts = facts_for_turn(TURN, STATE_EX)
    assert facts[0] == "You attack Giant Frog with Greataxe: a hit."
    assert "Giant Frog takes slashing damage" in facts[1]
    log = dice_log(TURN, STATE_EX)
    assert "1d20+5 [17] +5 = 22 vs AC 11 -> HIT" in log[0]


def test_guard_catches_numbers_deaths_and_strangers():
    assert guard("Your axe bites deep for 13 damage.", TURN, STATE_EX)
    assert guard("The frog dies with a croak.", TURN, STATE_EX)
    state = json.loads(json.dumps(STATE_EX))
    state["enemies"].append(dict(state["enemies"][0], id="crab_1", name="Giant Crab"))
    assert guard("A Giant Crab scuttles closer as your axe bites.", TURN, state)
    assert guard("Your greataxe bites into the Giant Frog, and it reels back, badly hurt.", TURN, state) == []


def test_narrator_one_call_for_many_turns():
    fake = FakeClient([json.dumps({"narrations": ["Your axe bites into the Giant Frog.", "The Giant Frog reels."]})])
    out = Narrator(fake).narrate([TURN, dict(TURN, turn=9)], STATE_EX)
    assert fake.calls == 1 and [o["source"] for o in out] == ["llm", "llm"]
    assert "no action left" in out[0]["text"]          # the engine's rejection is kept


def test_narrator_retries_then_uses_template():
    fake = FakeClient([json.dumps({"narrations": ["The frog is slain!"]}),
                       json.dumps({"narrations": ["It dies again for 13 damage."]})])
    out = Narrator(fake).narrate([TURN], STATE_EX)
    assert out[0]["source"] == "template" and "You attack Giant Frog" in out[0]["text"]


def test_narrator_does_not_call_model_for_rejections_or_questions():
    fake = FakeClient([])
    rej = {"schema_version": "1.0.0", "turn": 3, "actor_id": "barbarian_1", "status": "rejected",
           "events": [{"type": "rejected", "seq": 0, "actor_id": "barbarian_1", "reason_code": "out_of_range",
                       "message": "Giant Frog is 30 ft away."}]}
    q = {"schema_version": "1.0.0", "turn": 3, "actor_id": "barbarian_1", "status": "needs_clarification",
         "events": [], "narration_hint": "Which frog?"}
    out = Narrator(fake).narrate([rej, q], STATE_EX)
    assert fake.calls == 0 and out[0]["text"] == "Giant Frog is 30 ft away." and out[1]["text"] == "Which frog?"


# ---------------------------------------------------------------- npc, summary, rules
def test_npc_line_and_fallback():
    ev = {"schema_version": "1.0.0", "turn": 1, "actor_id": "barbarian_1", "status": "accepted",
          "narration_hint": "player says (friendly): Who are you?",
          "events": [{"type": "dialogue", "seq": 0, "actor_id": "barbarian_1", "speaker_id": "giant_frog_1",
                      "needs_llm": True, "topic": "Who are you?"}]}
    assert _player_words(ev["narration_hint"]) == "Who are you?"
    fake = FakeClient([json.dumps({"line": "Croak."})])
    assert NPCVoice(fake, notes={}).lines_for([ev], STATE_EX)[0]["line"] == "Croak."
    assert "Who are you?" in fake.prompts[0]["user"]
    line = NPCVoice(FakeClient([LLMError("x")]), notes={}).lines_for([ev], STATE_EX)[0]
    assert line["source"] == "template"


def test_summary_rewrites_on_death_and_caps_length():
    death = {"events": [{"type": "death", "seq": 0, "target_id": "giant_frog_1"}]}
    rs = RollingSummary(FakeClient([json.dumps({"summary": "word " * 200})]), every_n_turns=99)
    assert rs.add([{"text": "The frog falls.", "source": "llm", "facts": []}], [death])
    new = rs.rewrite(STATE_EX)
    assert len(new["text"].split()) <= 81 and new["updated_at_turn"] == STATE_EX["turn_counter"]
    assert rs.buffer == []


def test_rules_qa_uses_character_features():
    fake = FakeClient([json.dumps({"answer": "Rage is a bonus action. [Grruk > Rage]"})])
    out = RulesQA(fake).answer("How does rage work?", STATE_EX)
    assert out["source"] == "llm" and any("Rage" in s for s in out["sources"])
    assert "Bonus action to begin" in fake.prompts[0]["user"]


# ---------------------------------------------------------------- integration with the engine
def scripted_model(system, user):
    """Plays every role, deciding by which system prompt it got."""
    if system.startswith("You convert"):
        said = user.rsplit("Player:", 1)[-1].lower()
        if "rage" in said:
            return reply([{"type": "use_feature", "feature": "rage"}])
        if "end my turn" in said:
            return reply([{"type": "end_turn"}])
        return reply([{"type": "move", "toward": "zombie_1"}, {"type": "attack", "target_id": "zombie_1"}])
    if system.startswith("You are the narrator"):
        n = user.count("TURN ")
        return json.dumps({"narrations": ["You press forward through the cold dark."] * n})
    if system.startswith("You keep the running"):
        return json.dumps({"summary": "Grruk fought in the flooded cellar."})
    return json.dumps({"line": "...", "answer": "..."})


def test_full_turn_loop_against_engine(engine):
    cfg = LLMConfig(provider="offline", log_path="")
    dm = build_dm(engine=engine, config=cfg, client=FakeClient(scripted_model))
    dm.summary.every = 2
    dm.start("barbarian_orc_berserker", "demo_cellar", seed=11)
    for text in ("I rage", "charge the zombie and hit it", "end my turn"):
        out = dm.handle(text)
        assert schema_errors(out["request"]) == []
        for tr in out["turn_results"]:
            assert tr["status"] in ("accepted", "rejected", "needs_clarification")
        assert all(n["source"] in ("llm", "engine", "template") for n in out["narrations"])
    assert out["state"]["mode"] in ("combat", "exploration", "victory", "game_over")
    assert dm.state()["summary"]["text"] == "Grruk fought in the flooded cellar."


def test_gui_click_path_skips_parser(engine):
    fake = FakeClient(scripted_model)
    dm = build_dm(engine=engine, config=LLMConfig(provider="offline", log_path=""), client=fake)
    dm.start("barbarian_orc_berserker", "demo_cellar", seed=11)
    click = {"schema_version": "1.0.0", "actor_id": "barbarian_1", "raw_text": "(click)", "confidence": 1.0,
             "actions": [{"type": "move", "to": {"x": 2, "y": 5}}]}
    out = dm.handle_request(click)
    assert out["parser_source"] == "gui"
    assert not any(p["system"].startswith("You convert") for p in fake.prompts)
