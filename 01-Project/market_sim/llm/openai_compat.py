"""OpenAI-compatible provider (DeepSeek / OpenAI / OpenRouter / local vLLM).

Implements the provider-neutral ``tool_turn`` against any endpoint that speaks the
OpenAI Chat Completions API with function calling. DeepSeek is just this provider with
``base_url=https://api.deepseek.com`` and a ``DEEPSEEK_API_KEY`` (use a tool-capable
model such as ``deepseek-v4-pro`` / ``deepseek-v4-flash``, not ``deepseek-reasoner``).

Retry/backoff/pace mirror the Gemini provider; transient errors are detected from the
openai SDK exception types plus the shared substring heuristic.
"""

from __future__ import annotations

import json
import os
import time

from .base import LLMProvider, _is_transient, _load_env


def _openai_transient(e) -> bool:
    """True for rate-limit / timeout / connection / 5xx errors that clear on retry."""
    try:
        from openai import (RateLimitError, APITimeoutError, APIConnectionError,
                            InternalServerError)
        if isinstance(e, (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)):
            return True
    except Exception:  # noqa: BLE001 — openai not importable; fall back to the string heuristic
        pass
    code = getattr(e, "status_code", None)
    if code in (408, 409, 429, 500, 502, 503, 504):
        return True
    return _is_transient(str(e))


class OpenAIProvider(LLMProvider):
    def __init__(self, model: str | None = None, *, base_url: str | None = None,
                 api_key_env: str = "OPENAI_API_KEY", default_model_env: str | None = None,
                 temperature: float = 0.7, max_output_tokens: int = 2048,
                 max_retries: int = 5, backoff_base: float = 2.0, pace: float = 0.25,
                 thinking: bool = False, reasoning_effort: str | None = None,
                 extra_body: dict | None = None) -> None:
        _load_env()
        self.model = (model
                      or (os.environ.get(default_model_env) if default_model_env else None)
                      or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.api_key = os.environ.get(api_key_env)
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.pace = pace
        # Reasoning / "thinking" mode (e.g. DeepSeek v4). `thinking=True` turns on the
        # DeepSeek chain-of-thought via extra_body={"thinking":{"type":"enabled"}}; the model
        # still emits tool_calls (verified) and returns reasoning_content separately (ignored —
        # never re-sent, so no signature concern). reasoning_effort ("low"/"medium"/"high") and a
        # raw extra_body can also be passed for any compatible endpoint.
        self.thinking = bool(thinking)
        self.reasoning_effort = reasoning_effort
        # DeepSeek enables chain-of-thought via this flag; only inject it for DeepSeek models
        # (OpenAI/others would 400 on the unknown field). OpenAI reasoning models (o-series)
        # reason natively via reasoning_effort instead. This keeps a MIXED run safe: with
        # `thinking` on globally, a plain chat model (e.g. gpt-4o) just ignores it.
        self._is_deepseek = "deepseek" in (self.model or "").lower()
        extra = dict(extra_body or {})
        if self.thinking and self._is_deepseek and "thinking" not in extra:
            extra["thinking"] = {"type": "enabled"}
        self.extra_body = extra or None
        self._client = None

    def client(self):
        if self._client is None:
            from openai import OpenAI
            kw = {}
            if self.api_key:
                kw["api_key"] = self.api_key
            if self.base_url:
                kw["base_url"] = self.base_url
            self._client = OpenAI(**kw)
        return self._client

    def _is_reasoning(self) -> bool:
        """Reasoning models (OpenAI o-series, deepseek-reasoner) reject a custom temperature."""
        m = (self.model or "").lower()
        return m.startswith(("o1", "o3", "o4")) or "reasoner" in m

    def _use_max_completion_tokens(self) -> bool:
        """OpenAI o-series require ``max_completion_tokens`` instead of ``max_tokens``;
        DeepSeek (incl. reasoner) and gpt-4o-class models keep ``max_tokens``."""
        return (self.model or "").lower().startswith(("o1", "o3", "o4"))

    def _to_messages(self, system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            role = m.get("role")
            if role == "user":
                out.append({"role": "user", "content": m.get("text") or ""})
            elif role == "assistant":
                content = m.get("text") or None
                tcs = m.get("tool_calls") or []
                # an OpenAI assistant message MUST carry content or tool_calls; a thinking-only
                # turn can have neither (empty content, no calls) -> substitute a placeholder so
                # the request validates (DeepSeek/OpenAI return 400 "content or tool_calls must
                # be set" otherwise).
                if not content and not tcs:
                    content = "(no content)"
                a = {"role": "assistant", "content": content}
                if tcs:
                    a["tool_calls"] = [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"],
                                      "arguments": json.dumps(tc.get("args") or {}, ensure_ascii=False)}}
                        for tc in tcs]
                out.append(a)
            elif role == "tool":
                res = m.get("result")
                out.append({"role": "tool", "tool_call_id": m.get("tool_call_id"),
                            "content": res if isinstance(res, str)
                                       else json.dumps(res, ensure_ascii=False)})
        return out

    def tool_turn(self, messages: list[dict], tools: list[dict], *, system: str,
                  temperature: float | None = None) -> dict:
        temp = self.temperature if temperature is None else temperature
        kwargs = dict(model=self.model, messages=self._to_messages(system, messages))
        if tools:
            kwargs["tools"] = tools
        if self._use_max_completion_tokens():
            kwargs["max_completion_tokens"] = self.max_output_tokens
        else:
            kwargs["max_tokens"] = self.max_output_tokens
        # reasoning is "active" only for models that actually reason: DeepSeek with thinking on,
        # or OpenAI o-series (and the always-reasoning deepseek-reasoner). For those, drop the
        # custom temperature (rejected) and pass reasoning_effort. A non-reasoning model in a
        # mixed run (gpt-4o with thinking on) keeps its temperature and sends no reasoning args.
        reasoning_active = self._is_reasoning() or (
            self.thinking and (self._is_deepseek or self._use_max_completion_tokens()))
        if not reasoning_active:
            kwargs["temperature"] = temp
        if self.reasoning_effort and reasoning_active:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body

        transient_tries = 0
        backoff_s = 0.0
        while True:
            if self.pace:
                time.sleep(self.pace)
            try:
                resp = self.client().chat.completions.create(**kwargs)
            except Exception as e:  # noqa: BLE001
                error = str(e)
                if _openai_transient(e) and transient_tries < self.max_retries:
                    transient_tries += 1
                    sleep_s = self.backoff_base ** transient_tries
                    backoff_s += sleep_s
                    print(f"[rate-limit] {self.model}: transient error, retry "
                          f"{transient_tries}/{self.max_retries} after {sleep_s:.0f}s backoff "
                          f"({error[:60]})", flush=True)
                    time.sleep(sleep_s)
                    continue
                return {"assistant": None, "function_calls": [], "text": "",
                        "error": error, "api_error": _openai_transient(e),
                        "retries": transient_tries, "backoff_s": backoff_s}

            msg = resp.choices[0].message
            text = msg.content or ""
            fcs = []
            for tc in (getattr(msg, "tool_calls", None) or []):
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:  # noqa: BLE001 — model emitted non-JSON arguments
                    args = {}
                if not isinstance(args, dict):
                    args = {"value": args}
                fcs.append({"id": tc.id, "name": tc.function.name, "args": args})
            assistant = {"role": "assistant", "text": text, "tool_calls": fcs, "_native": None}
            return {"assistant": assistant, "function_calls": fcs, "text": text,
                    "error": None, "api_error": False,
                    "retries": transient_tries, "backoff_s": backoff_s}
