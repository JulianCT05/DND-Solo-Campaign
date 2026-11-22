"""Settings for the LLM layer (Josh's part).

Everything comes from environment variables, optionally loaded from a `.env`
file in the project root. `.env` is already in .gitignore: never commit keys.

    LLM_PROVIDER        anthropic (default) | openai | offline
    ANTHROPIC_API_KEY   key for provider=anthropic
    OPENAI_API_KEY      key for provider=openai (any OpenAI-compatible server)
    OPENAI_BASE_URL     default https://api.openai.com/v1
    LLM_PARSER_MODEL    model for the parser   (default: provider default)
    LLM_NARRATOR_MODEL  model for the narrator, NPCs, summary and rules answers
    LLM_TIMEOUT_S       seconds per HTTP call (default 20)
    LLM_MAX_RETRIES     extra attempts on 429/5xx/timeouts (default 2)
    LLM_LOG_PATH        where call metrics go (default saves/llm_calls.jsonl).
                        Only timings and token counts are logged, never prompts or keys.

With provider=offline (or no key set) the layer still works: the parser falls
back to a keyword parser and the narrator to template sentences. That keeps the
GUI and tests runnable without an API key.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

DEFAULT_MODELS = {
    "anthropic": {"parser": "claude-haiku-5-5", "narrator": "claude-haiku-5-5"},
    "openai": {"parser": "gpt-4o-mini", "narrator": "gpt-4o-mini"},
    "offline": {"parser": "offline", "narrator": "offline"},
}


def load_dotenv(path=None):
    """Tiny .env reader (KEY=value lines). Existing env vars win. No dependency."""
    path = Path(path or ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class LLMConfig:
    provider: str = "anthropic"
    api_key: str = field(default="", repr=False)   # repr=False: never printed
    base_url: str = ""
    parser_model: str = ""
    narrator_model: str = ""
    timeout_s: float = 20.0
    max_retries: int = 2
    log_path: str = ""

    @property
    def online(self):
        return self.provider != "offline" and bool(self.api_key)

    @classmethod
    def from_env(cls, dotenv=True):
        if dotenv:
            load_dotenv()
        provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
        if provider not in DEFAULT_MODELS:
            raise ValueError(f"LLM_PROVIDER must be one of {sorted(DEFAULT_MODELS)}, not {provider!r}")
        key = {"anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
               "openai": os.environ.get("OPENAI_API_KEY", "")}.get(provider, "")
        if provider != "offline" and not key:
            provider = "offline"   # no key -> run offline instead of crashing
        defaults = DEFAULT_MODELS[provider]
        return cls(
            provider=provider,
            api_key=key,
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            parser_model=os.environ.get("LLM_PARSER_MODEL", defaults["parser"]),
            narrator_model=os.environ.get("LLM_NARRATOR_MODEL", defaults["narrator"]),
            timeout_s=float(os.environ.get("LLM_TIMEOUT_S", 20)),
            max_retries=int(os.environ.get("LLM_MAX_RETRIES", 2)),
            log_path=os.environ.get("LLM_LOG_PATH", str(ROOT / "saves" / "llm_calls.jsonl")),
        )


def load_prompt(name):
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
