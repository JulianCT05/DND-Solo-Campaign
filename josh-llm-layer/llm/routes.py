"""FastAPI routes for the LLM layer, so the browser never holds the API key.

Add two lines to api/server.py (after `service = GameService()`):

    from llm.routes import make_router
    app.include_router(make_router(service))

New endpoints
  GET  /llm/status                 which provider/model is active, online or offline
  POST /games/{id}/say             body {"text": "I rage and chop the frog"}
                                   -> parser -> engine -> narrator. Returns a TurnOutput.
  POST /games/{id}/act             body = an action_request from a GUI click.
                                   Skips the parser, still narrates. Returns a TurnOutput.
  PUT  /games/{id}/summary         body {"text", "updated_at_turn"}: stores the rolling summary.

TurnOutput = {"request", "parser_source", "turn_results", "narrations", "npc_lines",
              "rules_answer", "question", "state"}  (see llm/dm.py)
"""
import threading

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from api.service import BadRequest, GameNotFound

from .client import make_client
from .config import LLMConfig
from .dm import LocalEngine, build_dm


class Say(BaseModel):
    text: str


def make_router(service, config=None, client=None):
    config = config or LLMConfig.from_env()
    client = client or make_client(config)
    engine = LocalEngine(service)
    dms = {}
    lock = threading.Lock()
    router = APIRouter()

    def dm_for(gid):
        with lock:
            if gid not in dms:
                dm = build_dm(engine=engine, config=config, client=client)
                dm.attach(gid)
                dms[gid] = dm
            return dms[gid]

    def call(fn, *args):
        try:
            return fn(*args)
        except GameNotFound as e:
            raise HTTPException(status_code=404, detail=f"No game with id {e}")
        except BadRequest as e:
            raise HTTPException(status_code=422, detail={"message": str(e), "errors": e.errors})

    @router.get("/llm/status")
    def status():
        return {"provider": config.provider, "online": config.online,
                "parser_model": config.parser_model, "narrator_model": config.narrator_model}

    @router.post("/games/{session_id}/say")
    def say(session_id: str, body: Say):
        dm = call(dm_for, session_id)
        return call(dm.handle, body.text)

    @router.post("/games/{session_id}/act")
    def act(session_id: str, action_request: dict = Body(...)):
        dm = call(dm_for, session_id)
        return call(dm.handle_request, action_request)

    @router.put("/games/{session_id}/summary")
    def put_summary(session_id: str, summary: dict = Body(...)):
        text = str(summary.get("text", ""))[:1200]
        turn = int(summary.get("updated_at_turn", 0))
        state = call(service.get_state, session_id)
        state["summary"] = {"text": text, "updated_at_turn": max(turn, 0)}
        call(service.save, session_id)
        return state["summary"]

    return router
