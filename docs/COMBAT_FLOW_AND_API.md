# Combat flow, map and API (Leena's part)

This half of the engine decides **whether** an action is allowed right now, and
handles time and space. EJ's `Rules` decides **what happens** when it resolves.

```
engine/grid.py      distance, pathfinding, difficult terrain, doors, line of sight, spell areas, weapon ranges
engine/combat.py    CombatFlow: modes, initiative, turn budget, movement, opportunity attacks, simple actions
engine/tactics.py   enemy tactics + ally pregen behaviour (they go through the same checks as the player)
engine/newgame.py   builds a new game state from a room file in data/rooms/
api/service.py      the endpoint logic, framework-free, plus saving/loading
api/server.py       FastAPI routes that call service.py
play_demo.py        seeded game printed as a dice log (no server needed)
```

## Run it

```
pip install -r requirements.txt
python -m pytest                    # 98 tests (EJ's 27 + 71 for this part)
python play_demo.py 11              # same seed = same fight
uvicorn api.server:app --reload     # http://127.0.0.1:8000/docs
```

## Endpoints

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/health` | `{"ok": true}` |
| GET | `/characters` | the 3 pregens the player can choose |
| GET | `/rooms` | room files that can start a game |
| POST | `/games` | start a new game. Body: `{"character": "barbarian_orc_berserker", "room_id": "demo_cellar", "seed": 11}` (all optional). Returns the game state (201). |
| GET | `/games` | saved games on disk |
| GET | `/games/{id}` | current game state |
| POST | `/games/{id}/actions` | body = an `action_request` (schema). Returns `{"turn_results": [...], "state": {...}}` |
| GET | `/games/{id}/reachable?actor_id=barbarian_1` | `[{"x","y","feet"}]` cells that creature can move to now (for movement highlights) |
| POST | `/games/{id}/save` | write to disk (this also happens automatically after every action) |
| POST | `/games/{id}/load` | reload from disk |

Errors: 404 for an unknown game id. 422 when the body doesn't match the schema,
with `detail.errors` listing what's wrong.

### What `/actions` returns

`turn_results` is a list: first the player's turn, then **one entry per enemy or
ally turn that ran automatically** until it's the player's turn again. The
narrator should narrate them in order. Each one matches `turn_result` in
`action_event.schema.json`.

A rule break comes back as a `rejected` event with a plain message the GUI can show:

```json
{"schema_version": "1.0.0", "turn": 3, "actor_id": "barbarian_1", "status": "rejected",
 "events": [{"type": "rejected", "seq": 0, "actor_id": "barbarian_1",
             "reason_code": "out_of_range",
             "message": "Giant Frog is 30 ft away; your Greataxe reaches 5 ft."}]}
```

If a request has several actions (move + attack + end_turn), they run in order
and processing stops at the first rejected one. Everything before it still happened.

If the parser sets `clarification_needed`, nothing changes and the result has
`status: "needs_clarification"` with the question in `narration_hint`.

## For each teammate

**Yari (GUI).** Click to move: `{"type": "move", "to": {"x": 5, "y": 3}}`. Click an
enemy: `{"type": "attack", "target_id": "giant_frog_1"}`. Put the actions in an
`action_request` with `confidence: 1.0` and `raw_text: "(click)"`. Use
`/reachable` for the blue movement squares. Whose turn it is:
`state.combat.initiative_order[state.combat.active_index].id`. Budget left:
`party[i].turn` (`action`, `bonus_action`, `reaction`, `movement_left_ft`).
CORS is open, so the page can call the API from any local port.

**Josh (LLM).** Send the parser output straight to `/actions`. The engine never
trusts the parser on dice or advantage. Feed each `turn_result.events` (plus
`narration_hint` when present) to the narrator. `talk` actions come back as a
`dialogue` event with `needs_llm: true`.

**E'Niyah (rooms).** Room files go in `data/rooms/<room_id>.json`. Copy
`demo_cellar.json` (it's a placeholder). Keys: `room_id`, `light`, `summary`,
`map` (exactly the game-state `map` object), `party_spawn` `[[x, y], ...]`
(player first), `enemies` `[{"ref", "id", "position": [x, y], "tactic", "awake"}]`,
`flags`. Set `flags.final_encounter: true` in the last room: winning that fight sets
mode to `victory`. Enemies with `awake: false` don't notice the party and are
surprised when combat starts.

**EJ (rules).** One small change to `rules.py`: `resolve()`, `attack()` and
`cast()` take optional `adv=()` / `dis=()` lists, so map-based advantage
(long range, enemy next to an archer, Dodge, Help, Hide) reaches the d20. The
defaults keep the old behaviour, and all 27 of your tests still pass. Please check it.

## Rules implemented

- **Modes.** In `exploration` there are no turns or movement limits, and allies
  follow the player. An awake enemy that sees a party member within 30 ft starts
  combat. The player stops walking the moment they're spotted. Attacking or casting
  at an enemy also starts combat; if the player wins initiative, the attack happens
  right away. Combat ends in `victory` when no enemy in the fight is left (dead or
  fled), or `game_over` when the player dies (house rule: 0 HP = death).
- **Initiative.** Initiative is d20 plus the pregen's initiative bonus (enemies use
  their Dex modifier). Ties go to the higher bonus, then higher Dex, then the party.
  Surprised (sleeping) enemies roll with disadvantage. Dead and fled creatures drop
  out of the order.
- **Turn budget.** Every creature gets one action, one bonus action, one reaction,
  its speed in movement, and one free object interaction. Costs:
  - attack, Dash, Dodge, Disengage, Help, Hide, Search and ability checks take the **action**
  - Rage, Potion of Healing (2024 rules), Hunter's Mark and Shield of Faith take the **bonus action**
  - only **one spell slot per turn** (2024 rule)
  - standing up from prone costs half your speed
  
  If EJ's module rejects an action (no slot left, say), its cost is refunded. The
  turn ends automatically once the action, bonus action and movement are all used.
- **Movement.** Diagonals cost 5 ft (2024). Difficult terrain costs 10 ft per
  square, and water does too unless the creature can swim. Moving while prone
  costs double. Walls and closed or locked doors block. You can pass through allies
  but not stop on them, you can't pass through living enemies, corpses don't block,
  and you can't squeeze diagonally between two walls. Grappled or restrained
  creatures have speed 0.
- **Range and line of sight.** Melee uses 5 ft reach. Ranged and thrown weapons
  read their ranges from the notes ("Thrown 20/60", "120/600"). Attacks between
  normal and long range have disadvantage, and beyond long range they're rejected.
  A ranged attack with an enemy adjacent has disadvantage. Thrown javelins come out
  of the inventory. Spells use the range table in `combat.py` (`SPELL_INFO`).
  Attacks and spells need line of sight; walls and closed doors block it.
- **Opportunity attacks.** Leaving a hostile's reach provokes one melee attack,
  which uses that creature's reaction. It happens mid-move and can kill the mover.
  Disengage prevents it. The move event lists who was provoked.
- **Spell areas.** `grid.area_cells()` handles sphere, cylinder, cube, cone, line
  and emanation shapes, and walls block them. Areas in `map.areas` with
  `effect: "difficult_terrain"` slow movement, and their `rounds_left` ticks down
  each round.
- **Other actions.** Doors and chests can be opened, closed, unlocked (needs a
  `has_key` flag), forced (Athletics vs the door's DC) or examined. Traps can be
  disarmed. Levers can be pulled. Talk, rules questions, and short and long rests
  (only when no enemy is near) are also handled.
- **Saving.** `saves/<id>.json` holds the state plus the dice RNG state, so a
  loaded game rolls exactly the dice it would have. The full event log goes to
  `saves/<id>.events.jsonl` (the state keeps only the last 30 events). Saves are
  written atomically.

## Tactics

Enemy tactics:

| Tactic | Behaviour |
| --- | --- |
| melee_rush | Goes for its target (or the nearest PC). Dashes if it can't reach this turn, otherwise attacks. |
| swarm | melee_rush, but everyone goes for the PC with the lowest HP. |
| flee_at_half | At half HP or below, it Disengages if it's adjacent to someone (Dashes otherwise) and runs. It leaves the fight once it's 60 ft away or out of sight. |
| hit_and_run | Attacks, then backs off with the movement it has left. |
| ranged_kite | Steps out of melee and shoots. It acts like melee_rush if it has no ranged attack. |
| guard | Holds position and attacks anyone within 15 ft. |
| support | Acts like ranged_kite (no support spells in the data yet). |

Allies:

- **Cleric:** heals anyone at 40% HP or less (Cure Wounds), casts Bless in round 1, otherwise casts Sacred Flame.
- **Ranger:** casts Hunter's Mark as a bonus action, then shoots with the Longbow. Uses the Shortsword if an enemy is adjacent.
- **Barbarian:** Rages, then melee_rush.

## Known limitations (prototype)

- **Grid.** Every creature takes up one square, so there are no Large 2x2 tokens,
  and reach is 5 ft unless a weapon lists more. Line of sight is one
  center-to-center line, so there's no half or three-quarters cover bonus to AC.
  Light level is stored but doesn't change what anyone can see.
- **Reactions.** Opportunity attacks are the only reaction, and they're taken
  automatically, including the player's. Ready isn't supported.
- **AI.** It doesn't open doors, use cover, flank, or spread out against area
  spells. In exploration, allies move straight to the player rather than taking
  turns.
- **Data and rules.** Grapple riders in the enemy notes aren't applied (that's in
  EJ's scope). Spells that create areas don't exist in `spells.py` yet, but the
  area tools are ready.
- **Server.** One process with games held in memory and saved to JSON files. There
  are no logins and no multiplayer, and the GUI asks for state after each action
  instead of the server pushing updates.
- **Spotting distance.** It's a fixed 30 ft (`AGGRO_FT`), not Perception against
  Stealth.
