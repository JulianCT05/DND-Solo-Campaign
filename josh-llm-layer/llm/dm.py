"""The full turn loop for the LLM side: text in, story + dice log out.

    dm = build_dm(engine=LocalEngine())             # or HttpEngine("http://127.0.0.1:8000")
    state = dm.start("barbarian_orc_berserker", "demo_cellar", seed=11)
    out = dm.handle("I rage and chop the frog")     # typed text -> parser -> engine
    out = dm.handle_request(click_request)          # GUI clicks skip the parser

`out` (a TurnOutput dict) is what the GUI renders:
    request      the action_request that was sent to the engine
    turn_results the engine's raw turn_results (player first, then AI turns)
    narrations   one entry per turn_result: text, source, dice lines
    npc_lines    NPC replies to `talk` actions
    rules_answer answer to a rules_question, if one was asked
    question     set when the parser needs the player to clarify
    state        the game state after the turn

These are the 8 steps from the design doc, steps 2 and 6 being ours.
"""
import json
import urllib.error
import urllib.request

from .client import make_client
from .config import LLMConfig
from .narrator import Narrator
from .npc import NPCVoice
from .parser import Parser
from .rules_qa import RulesQA
from .summary import RollingSummary


# ====================================================================== engine adapters
class LocalEngine:
    """Runs Leena's GameService in-process (no server needed)."""

    def __init__(self, service=None, saves_dir=None):
        from api.service import GameService
        self.service = service or (GameService(saves_dir) if saves_dir else GameService())

    def new_game(self, character, room_id="demo_cellar", seed=None):
        return self.service.new_game(character, room_id, seed=seed)

    def state(self, gid):
        return self.service.get_state(gid)

    def submit(self, gid, request):
        return self.service.submit(gid, request)

    def reachable(self, gid, actor_id):
        return {(c["x"], c["y"]): c["feet"] for c in self.service.reachable(gid, actor_id)}

    def set_summary(self, gid, summary):
        self.service.get_state(gid)["summary"] = summary
        self.service.save(gid)
        return True


class HttpEngine:
    """Talks to the FastAPI server (uvicorn api.server:app)."""

    def __init__(self, base_url="http://127.0.0.1:8000", timeout=15):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.summary_endpoint = True

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode() or "null")

    def new_game(self, character, room_id="demo_cellar", seed=None):
        body = {"character": character, "room_id": room_id}
        if seed is not None:
            body["seed"] = seed
        return self._call("POST", "/games", body)

    def state(self, gid):
        return self._call("GET", f"/games/{gid}")

    def submit(self, gid, request):
        return self._call("POST", f"/games/{gid}/actions", request)

    def reachable(self, gid, actor_id):
        cells = self._call("GET", f"/games/{gid}/reachable?actor_id={actor_id}")
        return {(c["x"], c["y"]): c["feet"] for c in cells}

    def set_summary(self, gid, summary):
        """Uses PUT /games/{id}/summary, which llm/routes.py adds to the server.
        If the server doesn't have it, the summary is kept on our side and still used."""
        if not self.summary_endpoint:
            return False
        try:
            self._call("PUT", f"/games/{gid}/summary", summary)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (404, 405):
                self.summary_endpoint = False
            return False


# ====================================================================== the DM
class DungeonMaster:
    def __init__(self, engine, parser, narrator, npc, summary, rules):
        self.engine = engine
        self.parser = parser
        self.narrator = narrator
        self.npc = npc
        self.summary = summary
        self.rules = rules
        self.game_id = None
        self.local_summary = None   # used when the engine can't store it

    # ---------------------------------------------------------------- lifecycle
    def start(self, character="barbarian_orc_berserker", room_id="demo_cellar", seed=None):
        state = self.engine.new_game(character, room_id, seed)
        self.game_id = state["session_id"]
        return state

    def attach(self, game_id):
        self.game_id = game_id
        return self.state()

    def state(self):
        st = self.engine.state(self.game_id)
        if self.local_summary and self.local_summary["updated_at_turn"] >= st.get("summary", {}).get("updated_at_turn", 0):
            st = dict(st, summary=self.local_summary)
        return st

    # ---------------------------------------------------------------- turns
    def handle(self, text):
        state = self.state()
        me_id = _player_id(state)
        request = self.parser.parse(
            text, state, me_id, reachable=lambda: self.engine.reachable(self.game_id, me_id))
        return self.handle_request(request, parser_source=self.parser.last_source)

    def handle_request(self, request, parser_source="gui"):
        """Send a ready action_request (from the parser or a GUI click) and narrate the result."""
        resp = self.engine.submit(self.game_id, request)
        turn_results, state = resp["turn_results"], resp["state"]
        if self.local_summary:
            state = dict(state, summary=self.local_summary)

        out = {"request": request, "parser_source": parser_source, "turn_results": turn_results,
               "narrations": [], "npc_lines": [], "rules_answer": None, "question": None, "state": state}

        if request.get("clarification_needed"):
            out["question"] = request["clarification_needed"]
            return out

        out["narrations"] = self.narrator.narrate(turn_results, state)
        out["npc_lines"] = self.npc.lines_for(turn_results, state)

        for tr in turn_results:
            hint = tr.get("narration_hint") or ""
            if hint.startswith("rules_question:"):
                out["rules_answer"] = self.rules.answer(hint.split(":", 1)[1].strip(), state)

        # rolling summary
        due = self.summary.add(out["narrations"], turn_results)
        for line in out["npc_lines"]:
            self.summary.note(f"{line['speaker']} said: {line['line']}")
        if due:
            new = self.summary.rewrite(state)
            if new:
                if not self.engine.set_summary(self.game_id, new):
                    self.local_summary = new
                out["state"] = dict(state, summary=new)
        return out


def _player_id(state):
    return next(p["id"] for p in state["party"] if p.get("controller") == "player")


def build_dm(engine=None, config=None, client=None):
    """Wire everything from env settings. Pass `client` to inject a FakeClient in tests."""
    config = config or LLMConfig.from_env()
    client = client or make_client(config)
    return DungeonMaster(
        engine=engine or LocalEngine(),
        parser=Parser(client, config.parser_model),
        narrator=Narrator(client, config.narrator_model),
        npc=NPCVoice(client, config.narrator_model),
        summary=RollingSummary(client, config.narrator_model),
        rules=RulesQA(client, config.narrator_model),
    )
