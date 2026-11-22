"""Thin clients for the hosted model API. Standard library only (urllib).

    client = make_client(LLMConfig.from_env())
    text = client.complete(system="...", user="...", model=cfg.parser_model,
                           max_tokens=400, temperature=0, json_mode=True)

Safety and reliability:
  * The API key lives only in the request header. It is never logged, and it is
    scrubbed from any error message before that message is raised.
  * 408/409/429/5xx/529 answers, timeouts and dropped connections are retried
    with exponential backoff plus jitter (and the server's retry-after, if sent).
  * 400/401/403/404 are not retried: they won't fix themselves.
  * Each call appends one line of metrics (model, ms, tokens, attempts, ok) to
    LLM_LOG_PATH. Prompts and outputs are not logged.
"""
import json
import random
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(Exception):
    """The model call failed after all retries. Callers fall back to offline behaviour."""


class LLMClient:
    name = "base"

    def __init__(self, config=None, sleep=time.sleep, opener=None):
        self.config = config
        self.sleep = sleep
        self.opener = opener or urllib.request.urlopen
        self.calls = 0

    # -------------------------------------------------- public
    def complete(self, system, user, model, max_tokens=400, temperature=0.0, json_mode=False):
        raise NotImplementedError

    # -------------------------------------------------- shared helpers
    def _post(self, url, headers, body):
        """POST JSON with retries. Returns (parsed_json, attempts)."""
        retries = self.config.max_retries if self.config else 2
        timeout = self.config.timeout_s if self.config else 20
        data = json.dumps(body).encode("utf-8")
        last = None
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with self.opener(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8")), attempt + 1
            except urllib.error.HTTPError as e:
                detail = self._read_error(e)
                last = f"HTTP {e.code}: {detail}"
                if e.code not in RETRY_STATUS or attempt == retries:
                    break
                self.sleep(self._backoff(attempt, e.headers.get("retry-after") if e.headers else None))
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
                last = f"network error: {getattr(e, 'reason', e)}"
                if attempt == retries:
                    break
                self.sleep(self._backoff(attempt))
        raise LLMError(self._scrub(f"{self.name} call failed after {attempt + 1} attempt(s): {last}"))

    @staticmethod
    def _backoff(attempt, retry_after=None):
        if retry_after:
            try:
                return min(float(retry_after), 20.0)
            except ValueError:
                pass
        return min(0.5 * (2 ** attempt), 8.0) + random.uniform(0, 0.25)

    @staticmethod
    def _read_error(e):
        try:
            body = json.loads(e.read().decode("utf-8"))
            err = body.get("error", body)
            return err.get("message", str(err))[:300] if isinstance(err, dict) else str(err)[:300]
        except Exception:
            return e.reason if hasattr(e, "reason") else "no detail"

    def _scrub(self, text):
        key = getattr(self.config, "api_key", "") if self.config else ""
        return text.replace(key, "***") if key else text

    def _log(self, model, started, attempts, ok, usage=None):
        self.calls += 1
        path = getattr(self.config, "log_path", "") if self.config else ""
        if not path:
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": round(time.time(), 3), "provider": self.name, "model": model,
                                    "ms": int((time.time() - started) * 1000), "attempts": attempts,
                                    "ok": ok, "usage": usage or {}}) + "\n")
        except OSError:
            pass  # metrics must never break a turn


class AnthropicClient(LLMClient):
    name = "anthropic"

    def complete(self, system, user, model, max_tokens=400, temperature=0.0, json_mode=False):
        started, attempts = time.time(), 0
        headers = {"content-type": "application/json", "x-api-key": self.config.api_key,
                   "anthropic-version": ANTHROPIC_VERSION}
        messages = [{"role": "user", "content": user}]
        if json_mode:
            # Prefill the answer with "{" so the model starts straight in JSON.
            messages.append({"role": "assistant", "content": "{"})
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
                "system": system, "messages": messages}
        try:
            out, attempts = self._post(ANTHROPIC_URL, headers, body)
        except LLMError:
            self._log(model, started, self.config.max_retries + 1, False)
            raise
        text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        if json_mode:
            text = "{" + text
        self._log(model, started, attempts, True, out.get("usage"))
        return text


class OpenAIClient(LLMClient):
    """Any OpenAI-compatible chat completions endpoint (OpenAI, a school proxy, a local server)."""
    name = "openai"

    def complete(self, system, user, model, max_tokens=400, temperature=0.0, json_mode=False):
        started = time.time()
        headers = {"content-type": "application/json", "authorization": f"Bearer {self.config.api_key}"}
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            out, attempts = self._post(f"{self.config.base_url}/chat/completions", headers, body)
        except LLMError:
            self._log(model, started, self.config.max_retries + 1, False)
            raise
        self._log(model, started, attempts, True, out.get("usage"))
        return out["choices"][0]["message"]["content"] or ""


class OfflineClient(LLMClient):
    """No model at all. Every call raises, so callers use their offline fallback."""
    name = "offline"

    def complete(self, *a, **k):
        raise LLMError("offline mode: no model configured (set ANTHROPIC_API_KEY or OPENAI_API_KEY)")


class FakeClient(LLMClient):
    """For tests: returns scripted answers in order, or calls a function(system, user)."""
    name = "fake"

    def __init__(self, responses):
        super().__init__(config=None)
        self.responses = responses if callable(responses) else list(responses)
        self.prompts = []

    def complete(self, system, user, model="fake", max_tokens=400, temperature=0.0, json_mode=False):
        self.prompts.append({"system": system, "user": user})
        self.calls += 1
        if callable(self.responses):
            out = self.responses(system, user)
        else:
            if not self.responses:
                raise LLMError("FakeClient ran out of scripted responses")
            out = self.responses.pop(0)
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, str) else json.dumps(out)


def make_client(config):
    if not config.online:
        return OfflineClient(config)
    return {"anthropic": AnthropicClient, "openai": OpenAIClient}[config.provider](config)


def extract_json(text):
    """Pull the first JSON object out of a model reply (tolerates ``` fences and chatter)."""
    if text is None:
        raise ValueError("empty reply")
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object in reply")
