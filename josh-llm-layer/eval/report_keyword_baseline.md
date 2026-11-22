# Parser evaluation

- Parser: keyword parser (offline)
- Cases: 31 (eval/sample_sentences.json)
- Correct: **21/31 (68%)**
- Average time per sentence: 1 ms
- Run: 2026-11-10 19:17

## By expected action

| Kind | Cases | Correct |
| --- | --- | --- |
| attack | 7 | 4 |
| cast | 5 | 3 |
| check | 1 | 1 |
| clarify | 4 | 4 |
| end_turn | 1 | 1 |
| hide | 1 | 0 |
| interact | 3 | 2 |
| move | 4 | 1 |
| rest | 1 | 1 |
| rules_question | 1 | 1 |
| talk | 1 | 1 |
| use_feature | 1 | 1 |
| use_item | 1 | 1 |

## Every case

| Id | Text | Result | Why | Parser output |
| --- | --- | --- | --- | --- |
| b01 | I rage | pass | ok | `[{"type": "use_feature", "feature": "rage"}]` |
| b02 | I rage and chop the frog with my greataxe | pass | ok | `[{"type": "use_feature", "feature": "rage"}, {"type": "move", "to": {"x": 9, "y": 4}}, {"type": "attack", "target_id": "giant_frog_1", "weapon": "Greataxe"}]` |
| b03 | Throw a javelin at the zombie | pass | ok | `[{"type": "attack", "target_id": "zombie_1", "weapon": "Javelin", "modifiers": ["thrown"]}]` |
| b04 | attack | pass | asked a question | `Who is your target?` |
| b05 | I walk 10 feet north | pass | ok | `[{"type": "move", "to": {"x": 1, "y": 4}}]` |
| b06 | kick the cellar door open | **fail** | action 0: expected {"type": "interact", "object_id": "cellar_door", "verb": "force"} | `[{"type": "interact", "object_id": "cellar_door", "verb": "open"}]` |
| b07 | I examine the old chest | pass | ok | `[{"type": "interact", "object_id": "old_chest", "verb": "examine"}]` |
| b08 | drink my healing potion | pass | ok | `[{"type": "use_item", "item": "Potion of Healing"}]` |
| b09 | I cast fireball at the frog | pass | asked a question | `I didn't understand that. Try something like "attack the frog" or "move north 10 feet".` |
| b10 | attack the dragon | pass | asked a question | `Who is your target?` |
| b11 | "Show yourself, coward!" I yell at the knife | pass | ok | `[{"type": "talk", "text": "Show yourself, coward!", "target_id": "knife_1"}]` |
| b12 | How does reckless attack work? | pass | ok | `[{"type": "rules_question", "question": "How does reckless attack work?"}]` |
| b13 | I look around the cellar for anything strange | pass | ok | `[{"type": "check", "ability": "wis", "skill": "perception", "intent": "look around"}]` |
| b14 | let's take a short rest | pass | ok | `[{"type": "rest", "kind": "short"}]` |
| b15 | I try to sneak along the wall | **fail** | asked: I didn't understand that. Try something like "attack the frog" or "move north 10 feet". | `I didn't understand that. Try something like "attack the frog" or "move north 10 feet".` |
| c01 | Sacred flame on the zombie | pass | ok | `[{"type": "cast", "spell_id": "sacred_flame", "target_ids": ["zombie_1"]}]` |
| c02 | I hurl a guiding bolt at the giant frog | pass | ok | `[{"type": "cast", "spell_id": "guiding_bolt", "target_ids": ["giant_frog_1"]}]` |
| c03 | cast cure wounds on myself | pass | ok | `[{"type": "cast", "spell_id": "cure_wounds", "target_ids": ["cleric_1"]}]` |
| c04 | Guiding bolt at the frog using a 2nd level slot | **fail** | action 0: expected {"type": "cast", "spell_id": "guiding_bolt", "target_ids": ["giant_frog_1"], "slot_level": 2} | `[{"type": "cast", "spell_id": "guiding_bolt", "target_ids": ["giant_frog_1"]}]` |
| c05 | I smack the knife with my warhammer | **fail** | asked: I didn't understand that. Try something like "attack the frog" or "move north 10 feet". | `I didn't understand that. Try something like "attack the frog" or "move north 10 feet".` |
| c06 | pray | pass | asked a question | `I didn't understand that. Try something like "attack the frog" or "move north 10 feet".` |
| c07 | I'll heal the barbarian | **fail** | asked: I didn't understand that. Try something like "attack the frog" or "move north 10 feet". | `I didn't understand that. Try something like "attack the frog" or "move north 10 feet".` |
| r01 | shoot the frog with my longbow | pass | ok | `[{"type": "attack", "target_id": "giant_frog_1", "weapon": "Longbow"}]` |
| r02 | Hunter's mark the zombie then shoot it | **fail** | expected 2 action(s), got 1 | `[{"type": "cast", "spell_id": "hunters_mark", "target_ids": ["zombie_1"]}]` |
| r03 | I move next to the cleric | **fail** | asked: Where do you want to go? | `Where do you want to go?` |
| r04 | I dash toward the chest | **fail** | expected 2 action(s), got 1 | `[{"type": "dash"}]` |
| x01 | I swing my greataxe at the frog | pass | ok | `[{"type": "move", "to": {"x": 4, "y": 4}}, {"type": "attack", "target_id": "giant_frog_1", "weapon": "Greataxe"}]` |
| x02 | I'm done, end my turn | pass | ok | `[{"type": "end_turn"}]` |
| x03 | Disengage and back off | **fail** | expected 2 action(s), got 1 | `[{"type": "disengage"}]` |
| x04 | Get up close and recklessly hack at the frog | **fail** | expected 2 action(s), got 1 | `[{"type": "stand_up"}]` |
| x05 | try to break down the east door | pass | ok | `[{"type": "interact", "object_id": "door_east", "verb": "force"}]` |
