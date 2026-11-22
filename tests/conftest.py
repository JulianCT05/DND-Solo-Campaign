import copy
import pytest
from engine import Dice, GameData, Rules, validate_event, validate_state

DATA = GameData()

MAP = {"width": 8, "height": 6, "cell_ft": 5,
       "terrain": ["########", "#......#", "#..~~..#", "#......#", "#......D", "########"],
       "legend": {"D": "door", "~": "difficult"},
       "features": [{"id": "door_east", "kind": "door", "position": {"x": 7, "y": 4},
                     "state": "locked", "dc": 13}]}


class ScriptedDice(Dice):
    """Dice that return a fixed list of results, in order, so tests know every roll."""
    def __init__(self, values):
        super().__init__(seed=0)
        self.values = list(values)

    def die(self, sides):
        self.count += 1
        v = self.values.pop(0)
        assert 1 <= v <= sides, f"scripted {v} on a d{sides}"
        return v


def make_state():
    party = [DATA.new_pc("barbarian_orc_berserker", "barbarian_1", "player", (2, 2)),
             DATA.new_pc("cleric_dwarf_life", "cleric_1", "engine_ally", (1, 2)),
             DATA.new_pc("ranger_wood_elf_hunter", "ranger_1", "engine_ally", (1, 3))]
    enemies = [DATA.new_enemy("srd_zombie", "zombie_1", (3, 2)),
               DATA.new_enemy("animated_armor", "armor_1", (4, 2)),
               DATA.new_enemy("giant_frog", "frog_1", (5, 3))]
    inv = []
    for pc in party:
        inv += DATA.starting_inventory(pc["ref"], pc["id"])
    return {"schema_version": "1.0.0", "session_id": "test", "rules_edition": "srd_5.2.1",
            "rng_seed": 42, "mode": "combat", "turn_counter": 0,
            "location": {"room_id": "test_room", "light": "bright"},
            "party": party, "enemies": enemies, "map": copy.deepcopy(MAP),
            "combat": {"round": 1, "initiative_order": [{"id": "barbarian_1", "initiative": 15}],
                       "active_index": 0},
            "inventory": inv, "flags": {}, "summary": {"text": "", "updated_at_turn": 0}}


@pytest.fixture
def state():
    s = make_state()
    assert validate_state(s) == []
    return s


def rules_with(state, values):
    return Rules(state, DATA, ScriptedDice(values))


def assert_valid(events):
    for ev in events:
        errs = validate_event(ev)
        assert errs == [], f"{ev} -> {errs}"


def types(events):
    return [e["type"] for e in events]
