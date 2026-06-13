"""Gemini (Vertex AI) provider — forced-JSON completion with retry + on-disk cache.

Auth is ADC (machine-level). Config comes from the project `.env`
(GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION / GEMINI_MODEL). Responses are cached
by (model, temperature, system, user, key) so re-running an eval is reproducible and
free.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .schema import SYSTEM_PROMPT, AgentResponse

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CACHE_DIR = _PROJECT_ROOT / "eval_runs" / "cache"

# substrings that mark a transient/infra error (retry with backoff, not a model failure).
# Includes network/DNS/connection blips (e.g. a momentary failure to resolve
# oauth2.googleapis.com during the ADC token exchange, or a dropped socket): these are
# infra hiccups in the same class as a 429/503 — backing off and retrying clears them,
# whereas treating them as hard failures makes an agent needlessly hold the round.
_TRANSIENT = ("429", "resource_exhausted", "rate limit", "503", "unavailable",
              "500", "internal", "deadline", "timeout", "temporarily", "overloaded",
              "nameresolution", "failed to resolve", "max retries exceeded", "getaddrinfo",
              "temporary failure in name", "connection", "connection aborted",
              "connection reset", "connection refused", "newconnectionerror", "eof occurred")


def _is_transient(msg: str) -> bool:
    m = (msg or "").lower()
    return any(s in m for s in _TRANSIENT)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(_PROJECT_ROOT / ".env")
    except Exception:
        pass


@dataclass
class Completion:
    ok: bool                       # produced a schema-valid response (within retries)
    parse_ok: bool                 # the FIRST real model reply was already schema-valid
    parsed: AgentResponse | None
    raw: str
    error: str | None
    attempts: int
    api_error: bool = False        # final failure was transient/infra (429/5xx), not the model
    cached: bool = False


class GeminiProvider:
    def __init__(self, model: str | None = None, temperature: float = 0.7,
                 use_cache: bool = True, max_output_tokens: int = 2048,
                 max_retries: int = 5, backoff_base: float = 2.0, pace: float = 0.5,
                 thinking_budget: int | None = None, thinking_level: str | None = None) -> None:
        _load_env()
        self.project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
        self.temperature = temperature
        self.use_cache = use_cache
        self.max_output_tokens = max_output_tokens
        self.max_retries = max_retries      # backoff retries for transient errors
        self.backoff_base = backoff_base    # sleep = backoff_base ** try (2,4,8,16,32)
        self.pace = pace                    # seconds slept before each real API call
        # thinking: thinking_level ("low"/"high", gemini-3) takes precedence over
        # thinking_budget (a token count). Both None -> model default; budget 0 -> OFF.
        # Keeping some thinking on improves decisions; "low" stays fast enough to still
        # reliably emit the function call.
        self.thinking_budget = thinking_budget
        self.thinking_level = thinking_level
        self._client = None

    def _thinking(self):
        from google.genai import types
        if self.thinking_level is not None:
            return types.ThinkingConfig(thinking_level=self.thinking_level)
        if self.thinking_budget is not None:
            return types.ThinkingConfig(thinking_budget=self.thinking_budget)
        return None

    # --- client (lazy) ---

    def client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(vertexai=True, project=self.project, location=self.location)
        return self._client

    # --- caching ---

    def _cache_path(self, user: str, temperature: float, key: str) -> Path:
        h = hashlib.sha256(
            json.dumps([self.model, temperature, SYSTEM_PROMPT, user, key], ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:32]
        return _CACHE_DIR / f"{h}.json"

    # --- one raw call ---

    def _call(self, user: str, temperature: float) -> str:
        from google.genai import types
        resp = self.client().models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=temperature,
                max_output_tokens=self.max_output_tokens,
                response_mime_type="application/json",
                response_schema=AgentResponse,
                thinking_config=self._thinking(),
            ),
        )
        return resp.text or ""

    @staticmethod
    def _parse(raw: str) -> AgentResponse:
        return AgentResponse.model_validate_json(raw)

    # --- public: forced-JSON completion with one retry ---

    def complete(self, user: str, *, temperature: float | None = None, key: str = "") -> Completion:
        """Forced-JSON completion. Transient API errors (429/5xx) are retried with
        exponential backoff; a model reply that isn't schema-valid JSON gets ONE
        retry with an error note. Only successful completions are cached (so a
        rate-limited call is retried on the next run, not served from cache)."""
        temp = self.temperature if temperature is None else temperature
        cache_file = self._cache_path(user, temp, key)
        if self.use_cache and cache_file.exists():
            d = json.loads(cache_file.read_text(encoding="utf-8"))
            parsed = AgentResponse.model_validate(d["response"]) if d.get("response") else None
            return Completion(d["ok"], d["parse_ok"], parsed, d["raw"], d["error"],
                              d["attempts"], api_error=d.get("api_error", False), cached=True)

        attempts = 0
        parse_ok = False
        first_reply_seen = False    # parse_ok is decided by the FIRST real model reply
        parse_retry_used = False
        transient_tries = 0
        raw = ""
        error = None
        api_error = False
        parsed: AgentResponse | None = None
        prompt = user

        while True:
            attempts += 1
            if self.pace:
                time.sleep(self.pace)
            try:
                raw = self._call(prompt, temp)
            except Exception as e:  # noqa: BLE001
                error = str(e)
                if _is_transient(error) and transient_tries < self.max_retries:
                    transient_tries += 1
                    sleep_s = self.backoff_base ** transient_tries  # 2,4,8,16,32s
                    print(f"[rate-limit] {self.model}: transient error, retry "
                          f"{transient_tries}/{self.max_retries} after {sleep_s:.0f}s backoff "
                          f"({error[:60]})", flush=True)
                    time.sleep(sleep_s)
                    continue
                api_error = _is_transient(error)
                parsed = None
                break
            # got a model reply — try to parse it
            try:
                parsed = self._parse(raw)
                if not first_reply_seen:
                    parse_ok = True
                error = None
                api_error = False
                break
            except Exception as pe:  # noqa: BLE001 — schema-invalid reply
                if not first_reply_seen:
                    parse_ok = False
                first_reply_seen = True
                error = str(pe)
                parsed = None
                if not parse_retry_used:
                    parse_retry_used = True
                    prompt = (user + f"\n\n[Your previous reply was not valid JSON for the required "
                              f"schema: {error}. Return ONLY the JSON object, nothing else.]")
                    continue
                break

        ok = parsed is not None
        result = Completion(ok, parse_ok, parsed, raw, error, attempts, api_error=api_error)

        if self.use_cache and ok:  # cache successes only
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({
                "ok": ok, "parse_ok": parse_ok, "attempts": attempts, "error": error,
                "api_error": api_error, "raw": raw, "response": parsed.model_dump() if parsed else None,
            }, ensure_ascii=False), encoding="utf-8")
        return result

    # --- public: one tool-calling turn (for the agentic ToolLoopAgent) -----------

    def tool_turn(self, contents: list, tools: list, *, system: str,
                  temperature: float | None = None) -> dict:
        """One model call with native function-calling. Returns a dict with:
          - ``content``: the model's returned Content object — append it VERBATIM to
            the conversation (it carries the Gemini-3 thought_signature; a hand-rebuilt
            Part would be rejected).
          - ``function_calls``: list of {"name", "args"} the model wants executed.
          - ``text``: any text parts. ``error`` / ``api_error`` on failure.
        Transient (429/5xx) errors are retried with backoff; the caller decides what to
        do on a hard failure (the agent simply holds the round).
        """
        from google.genai import types
        temp = self.temperature if temperature is None else temperature
        transient_tries = 0
        backoff_s = 0.0      # total seconds slept on transient-error (429/5xx) backoff
        while True:
            if self.pace:
                time.sleep(self.pace)
            try:
                resp = self.client().models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        tools=tools,
                        temperature=temp,
                        max_output_tokens=self.max_output_tokens,
                        thinking_config=self._thinking(),
                    ),
                )
            except Exception as e:  # noqa: BLE001
                error = str(e)
                if _is_transient(error) and transient_tries < self.max_retries:
                    transient_tries += 1
                    sleep_s = self.backoff_base ** transient_tries
                    backoff_s += sleep_s
                    print(f"[rate-limit] {self.model}: transient error, retry "
                          f"{transient_tries}/{self.max_retries} after {sleep_s:.0f}s backoff "
                          f"({error[:60]})", flush=True)
                    time.sleep(sleep_s)
                    continue
                return {"content": None, "function_calls": [], "text": "",
                        "error": error, "api_error": _is_transient(error),
                        "retries": transient_tries, "backoff_s": backoff_s}

            cand = resp.candidates[0] if resp.candidates else None
            content = cand.content if cand else None
            parts = (content.parts if content and content.parts else []) or []
            fcs = [{"name": p.function_call.name, "args": dict(p.function_call.args or {})}
                   for p in parts if getattr(p, "function_call", None)]
            text = "".join(p.text for p in parts if getattr(p, "text", None))
            return {"content": content, "function_calls": fcs, "text": text,
                    "error": None, "api_error": False,
                    "retries": transient_tries, "backoff_s": backoff_s}
