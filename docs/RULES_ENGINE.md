# DND Solo Play: rules engine (EJ)

This is the rules-resolution half of the engine. It is plain Python with no LLM and no network. It takes an action, rolls the dice, updates the game state, and returns events that match `schemas/action_event.schema.json`.

```
engine/
  dice.py     seeded dice, NdM+K parser, d20 with advantage/disadvantage
  rules.py    attacks, saves, checks, damage, healing, spells, Rage, items, rests, turn hooks
  spells.py   numbers for the demo spells (spells.json only has prose)
  data.py     loads characters.json / enemies.json, builds new combatants
  events.py   EventLog (assigns seq) + schema validators
data/         characters.json, enemies.json (converted from the team doc), example_game_state.json
schemas/      Julian's two schemas, unchanged
tests/        27 pytest tests
demo.py       a seeded fight printed as a dice log
```

## Run it

```
pip install jsonschema pytest
python -m pytest tests
python demo.py
```

## How Leena's code calls it

```python
from engine import Rules, GameData, Dice

data  = GameData()                                # load once at startup
rules = Rules(state, data, Dice(state["rng_seed"]))

# Leena checks it's legal first (turn, action left, range), then:
events = rules.resolve("barbarian_1", {"type": "attack", "target_id": "giant_frog_1"})
```

`resolve()` handles the action types `attack`, `cast`, `use_feature` (rage), `use_item` (potion of healing) and `check`. Movement and the simple actions (dash, dodge, disengage...) are Leena's.

The rules module still returns a `rejected` event when its own checks fail. These cover a dead target, no spell slot, a spell that isn't prepared, already raging, and casting while raging.

`resolve()` mutates `state` in place: HP, alive, effects, slots, uses and inventory. Pass `start_seq=` if you're appending to events already in the turn.

There are also turn hooks Leena should call from the turn loop:

| Call | When | What it does |
| --- | --- | --- |
| `rules.start_of_turn(actor_id)` | a creature's turn begins | clears Reckless Attack |
| `rules.end_of_turn(actor_id)` | a creature's turn ends | ticks down effects that creature created (Bless, Vex, Guiding Bolt) and its concentration |
| `rules.end_combat()` | combat ends | drops Rage, Reckless, Vex, Guiding Bolt |
| `rules.rest("short" \| "long")` | party rests | heals, restores slots/uses |

All of these return event lists.

For starting a game: `data.new_pc(ref, id, controller, (x, y))`, `data.new_enemy(ref, id, (x, y), tactic)` and `data.starting_inventory(ref, id)` build schema-valid entries.

To check output: `validate_event(ev)`, `validate_state(state)` and `validate_turn_result(tr)` return a list of errors. An empty list means valid. They also catch misspelled field names, which the schema alone lets through.

## Rules implemented

- **Attacks.** A natural 20 is a critical hit and a natural 1 is a miss. Advantage and disadvantage cancel each other.
- **Crits (house rule).** A critical hit doubles the total damage roll.
- **Damage modifiers.** Immunity, resistance and vulnerability apply, and temp HP absorbs damage first.
- **Death (house rule).** 0 HP is death, with no death saves.
- **Advantage sources.** Reckless Attack (enemies also get advantage until your next turn), Vex, Guiding Bolt, and conditions: prone, poisoned, restrained, paralyzed, stunned, blinded, frightened.
- **Rage.** Uses come from the resource pool. It gives +2 damage on non-bow weapons, resistance to bludgeoning/piercing/slashing, advantage on Strength checks and saves, and blocks spellcasting.
- **Spells.** Sacred Flame, Guiding Bolt (with upcast), Cure Wounds (with upcast and Disciple of Life), Bless, Shield of Faith, and Hunter's Mark (spends a Favored Enemy free use first). Slots are spent, and casting a second concentration spell ends the first.
- **Other.** Potion of Healing (2d4+2), ability checks (DC taken from the map feature), short and long rests.

## Deliberately left out (prototype)

- **Combat rules not implemented.** Concentration saves on damage, Undead Fortitude, Relentless Endurance, Frenzy, Cleave and Slow, Channel Divinity and Preserve Life, Healer feat rerolls, and the Danger Sense and Dodge advantage on saves.
- **Simplified behaviour.** Rage lasts until combat ends.
- **Data shortcuts.** Character numbers are used as written in the data file, and the giant frog stats are a placeholder.

## To add a spell

Add an entry to `engine/spells.py` using one of the existing `kind`s, and make sure the spell id is in the caster's prepared list.
