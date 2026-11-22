"""DND Solo Play rules engine (no LLM inside)."""
from .data import GameData
from .dice import Dice
from .events import EventLog, validate_event, validate_state, validate_turn_result
from .rules import Rules
from .spells import SPELLS

__all__ = ["GameData", "Dice", "EventLog", "Rules", "SPELLS",
           "validate_event", "validate_state", "validate_turn_result"]
