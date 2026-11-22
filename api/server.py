"""FastAPI endpoints for the DND Solo Play engine (Leena's part).

Run from the dnd/ folder:
    uvicorn api.server:app --reload
Then open http://127.0.0.1:8000/docs for the interactive API page.

Endpoints
  GET  /health                       is the server up
  GET  /characters                   the pregens the player can pick
  GET  /rooms                        room files that can start a game
  POST /games                        start a new game -> game state
  GET  /games                        saved games on disk
  GET  /games/{id}                   current game state
  POST /games/{id}/actions           submit an action_request -> turn_results + new state
  GET  /games/{id}/reachable?actor_id=...   cells that creature can move to (GUI highlight)
  POST /games/{id}/save              write the game to disk (also happens after every action)
  POST /games/{id}/load              reload the game from disk
"""
from typing import List, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .service import BadRequest, GameNotFound, GameService

app = FastAPI(title="DND Solo Play engine", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
service = GameService()


class NewGame(BaseModel):
    character: str = "barbarian_orc_berserker"
    room_id: str = "demo_cellar"
    allies: Optional[List[str]] = None
    seed: Optional[int] = None


def _call(fn, *args):
    try:
        return fn(*args)
    except GameNotFound as e:
        raise HTTPException(status_code=404, detail=f"No game with id {e}")
    except BadRequest as e:
        raise HTTPException(status_code=422, detail={"message": str(e), "errors": e.errors})


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/characters")
def characters():
    return service.characters()


@app.get("/rooms")
def rooms():
    return service.rooms()


@app.post("/games", status_code=201)
def create_game(body: NewGame):
    return _call(service.new_game, body.character, body.room_id, body.allies, body.seed)


@app.get("/games")
def list_games():
    return service.list_saves()


@app.get("/games/{session_id}")
def get_game(session_id: str):
    return _call(service.get_state, session_id)


@app.post("/games/{session_id}/actions")
def submit_action(session_id: str, action_request: dict = Body(...)):
    return _call(service.submit, session_id, action_request)


@app.get("/games/{session_id}/reachable")
def reachable(session_id: str, actor_id: str):
    return _call(service.reachable, session_id, actor_id)


@app.post("/games/{session_id}/save")
def save_game(session_id: str):
    return {"saved_to": _call(service.save, session_id)}


@app.post("/games/{session_id}/load")
def load_game(session_id: str):
    return _call(service.load, session_id)
