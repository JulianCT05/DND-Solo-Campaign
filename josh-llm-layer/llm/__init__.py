"""LLM layer for DND Solo Play (Josh's part).

parser.py     player text -> action_request JSON (never dice or outcomes)
narrator.py   engine events -> 2-4 sentences per turn, plus dice log lines
npc.py        NPC replies to `talk` actions, from data/npcs.json notes
summary.py    rolling story summary kept in state["summary"]
rules_qa.py   rules_question answers from data/rules/*.json
client.py     Anthropic / OpenAI-compatible clients: retries, key safety, metrics
dm.py         the whole turn loop, against the engine in-process or over HTTP
"""
from .client import FakeClient, LLMError, make_client
from .config import LLMConfig
from .dm import DungeonMaster, HttpEngine, LocalEngine, build_dm
from .narrator import Narrator, dice_log, facts_for_turn
from .npc import NPCVoice
from .parser import KeywordParser, Parser, schema_errors
from .rules_qa import RulesQA
from .summary import RollingSummary

__all__ = ["FakeClient", "LLMError", "make_client", "LLMConfig", "DungeonMaster", "HttpEngine",
           "LocalEngine", "build_dm", "Narrator", "dice_log", "facts_for_turn", "NPCVoice",
           "KeywordParser", "Parser", "schema_errors", "RulesQA", "RollingSummary"]
