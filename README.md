# DND Solo Play

An AI Dungeon Master that runs a solo D&D one-shot on the SRD 5.2.1 (2024) rules.
COMP 365 team project, Team Pink.

The game engine (plain Python, no LLM) owns everything that has to be correct:
dice, HP, turn order, movement and rules. The LLM layer only turns player text
into action JSON and writes narration from the engine's events.

## Quick start

```
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m pytest                   # run every test
python play_demo.py                # a seeded fight printed as a dice log
uvicorn api.server:app --reload    # start the API, then open http://127.0.0.1:8000/docs
```

## Layout

```
schemas/   game_state + action/event JSON schemas (Julian). The contract between all layers.
data/      characters.json, enemies.json, example files, rooms/ (E'Niyah)
engine/
  dice.py, rules.py, spells.py, data.py, events.py   rules resolution (EJ)
  grid.py, combat.py, tactics.py, newgame.py         combat flow, map, AI turns (Leena)
api/       service.py (game logic for the endpoints) + server.py (FastAPI routes) (Leena)
tests/     pytest tests for every part
docs/      RULES_ENGINE.md (EJ), COMBAT_FLOW_AND_API.md (Leena)
```

The LLM parser/narrator (Josh) and the browser GUI (Yari) talk to the API.
See docs/COMBAT_FLOW_AND_API.md for the endpoints and example requests.

## Team rules

- Schema changes go through Julian and bump `schema_version`.
- Run `python -m pytest` before you push. Don't push if it's red.
- Never commit API keys. Put them in a `.env` file (it's in `.gitignore`).

Rules content: System Reference Document 5.2.1 by Wizards of the Coast, licensed under CC-BY-4.0.
