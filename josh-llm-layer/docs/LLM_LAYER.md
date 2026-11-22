# LLM layer (Josh's part)

The engine decides everything that must be correct. This layer does the two
language jobs around it: turning what the player types into `action_request`
JSON (step 2 of a turn), and turning the engine's events into story text
(step 6). It also voices NPCs, answers rules questions from the team's rules
files, and keeps the rolling story summary.

```
llm/
  config.py      settings from .env (provider, key, models, timeouts)
  client.py      Anthropic + OpenAI-compatible clients: retries, key safety, metrics
  context.py     game state -> short prompt text (ids, distances, wound levels)
  parser.py      text -> action_request; KeywordParser offline fallback
  narrator.py    events -> 2-4 sentences per turn; fact list, guard, dice log
  npc.py         replies to `talk` actions from data/npcs.json
  summary.py     rolling summary in state["summary"]
  rules_qa.py    answers rules_question from data/rules/*.json
  dm.py          the whole loop; LocalEngine (in-process) or HttpEngine (API)
  routes.py      FastAPI routes: /games/{id}/say, /act, /summary, /llm/status
  prompts/       the five system prompts, as plain Markdown so anyone can edit them
play_cli.py      play in the terminal
eval/            parser test set + scorer (eval_parser.py writes report.md)
tests/           test_llm_layer.py (24 tests), test_llm_routes.py
```

## One turn

1. The player types `I rage and chop the frog`.
2. **Parser.** The model gets `parser_context()` (the player's id, budget left,
   weapons, spell ids, every creature and map object with its id and distance)
   and answers `{"actions", "confidence", "clarification_needed"}`.
   `normalize_actions()` then fixes names to ids, slugs spells, drops any key the
   schema doesn't allow (so a model can't sneak in `"advantage": true`), and turns
   `{"type":"move","toward":"giant_frog_1"}` into a real square using the engine's
   `/reachable` cells. The model never does grid math. The result is checked
   against `action_event.schema.json`. If it fails, the model gets one repair try
   with the errors, then the player is asked to rephrase.
3. to 5. **Engine.** The request goes to `/games/{id}/actions`.
6. **Narrator.** Each `turn_result` becomes a fact list (`facts_for_turn`), with
   names and wound levels but no numbers. All turns from one response go to the
   model in **one call**. Each reply is checked by `guard()`: no numbers, no death
   unless there was a death event, no creature that wasn't in the turn, at most 4
   sentences. A failing turn gets one retry, then the template text (the facts
   themselves) is used. Rejected turns and clarification questions are shown as
   the engine wrote them, with no model call.
7. The GUI gets a **TurnOutput** (below).
8. Enemy and ally turns come back in the same response and are narrated in order.

## Rules the prompts and code enforce

| Rule from the design doc | Where it's enforced |
| --- | --- |
| The parser never sets dice or advantage | The schema has no such field, and `normalize_actions()` whitelists keys per action type |
| Unclear text → a question, not a guess | Prompt rule 8; unknown or ambiguous names raise `Clarify`; confidence under 0.35 becomes "Do you want to ...?" |
| The narrator never invents outcomes | It only sees `facts_for_turn()`; `guard()` checks every reply; the template fallback |
| Narration is 2 to 4 sentences | Prompt plus the `guard()` length check |
| Keep prompts short | The parser context is about 15 lines; the narrator sees facts plus an 80-word summary, never the chat history |
| The API key is safe | Read from `.env` only, never logged, scrubbed from errors, `repr=False`; the browser calls our routes, never the model |
| Retries on failures | `client._post`: 408/409/429/5xx/529 and timeouts, exponential backoff plus `retry-after`; 4xx auth errors fail fast |
| The game works when the model is down | Offline fallbacks: `KeywordParser`, template narration, template NPC line, best rules excerpt |

## TurnOutput (what `/games/{id}/say` and `/act` return)

See `docs/example_turn_output.json` for a real one.

```json
{
  "request":      { "...the action_request sent to the engine..." },
  "parser_source":"llm | llm_repaired | keyword | gui",
  "turn_results": [ "...exactly what the engine returned..." ],
  "narrations": [
    {"turn": 6, "actor_id": "barbarian_1", "status": "accepted",
     "text": "Your greataxe bites deep into the zombie...",
     "source": "llm | template | engine",
     "dice": ["Grruk -> Human Zombie (Greataxe): 1d20+5+1d4 [15, 3] +5 = 23 vs AC 8 -> HIT", "..."],
     "facts": ["You attack Human Zombie with Greataxe: a hit.", "..."]}
  ],
  "npc_lines":    [{"speaker_id": "ferol_sal_1", "speaker": "Ferol Sal", "line": "...", "source": "llm"}],
  "rules_answer": {"answer": "...", "sources": ["combat_rules > ..."], "source": "llm"},
  "question":     "Which one: Human Zombie 1 or Human Zombie 2?",
  "state":        { "...game state after the turn..." }
}
```

## For each teammate

**Leena.** Add two lines to `api/server.py` after `service = GameService()`:

```python
from llm.routes import make_router
app.include_router(make_router(service))
```

That adds `POST /games/{id}/say`, `POST /games/{id}/act`, `PUT /games/{id}/summary`
and `GET /llm/status`. The summary PUT is how the rolling summary gets saved into
the game state. Nothing in the engine changes.

**Yari.** Typed text → `POST /games/{id}/say {"text": "..."}`. Clicks → build the
`action_request` as before and send it to `POST /games/{id}/act` instead of
`/actions` to get narration with it (`/actions` still works with no narration).
Show `question` when it's set, or else each `narrations[i].text` in order, with
`narrations[i].dice` in the dice log and `npc_lines` and `rules_answer` in the chat.
The key never reaches the browser.

**Julian.** Put the rules files in `data/rules/` (see the README there). Any JSON
shape works for `rules_qa.py`. If a schema change adds or renames action fields,
`ALLOWED_KEYS` in `llm/parser.py` and the action list in `prompts/parser_system.md`
need the same change.

**E'Niyah.** NPC notes go in `data/npcs.json` (copy `data/npcs.example.json`).
NPCs only share what's under `knows`.

**Julian and E'Niyah (test set).** `eval/sample_sentences.json` has 31 starter
cases in the format to extend to about 50. Each expected action only has to match
the keys it lists, so `{"type": "move"}` accepts any square.

## Running it

```
cp .env.example .env               # add ANTHROPIC_API_KEY (or OPENAI_API_KEY)
python -m pytest                   # all tests, no network needed
python play_cli.py --seed 11       # play in the terminal
python eval/eval_parser.py         # parser accuracy on the sample sentences -> eval/report.md
python eval/eval_parser.py --keyword   # offline baseline
```

With no key, everything runs offline. The keyword baseline scores 21/31 (68%)
on the starter set (`eval/report_keyword_baseline.md`). The model run is the
number to report.

## Known limits

- `guard()` catches numbers, invented deaths, extra creatures and length. It
  can't catch every subtle contradiction (for example a hit described as a
  near miss). The facts-only prompt is the main defence.
- `move toward` picks the reachable square closest to the target. It doesn't
  plan around opportunity attacks. The engine still validates the path.
- Free text can't place a spell area on a square yet (`target_point`). Those
  spells should be cast with a map click.
- The model names in `config.py` are defaults. Change them in `.env`.
