"""Gemini (Vertex AI) provider.

``complete()`` is forced-JSON with on-disk caching (used by the simple LLMAgent and the
offline eval) and is Gemini-specific. ``tool_turn()`` implements the provider-neutral
``LLMProvider`` interface for the agentic ToolLoopAgent: it converts the neutral message
list to google.genai ``types.Content`` — re-sending the model's own returned turn
VERBATIM (via the neutral assistant dict's ``_native`` handle) so the Gemini-3
``thought_signature`` is preserved — and converts the neutral tool specs to
``types.FunctionDeclaration``.

Auth is ADC (machine-level). Config comes from the project ``.env``
(GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION / GEMINI_MODEL). complete() responses are
cached by (model, temperature, system, user, key) so re-running an eval is free.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass

from ..schema import SYSTEM_PROMPT, AgentResponse
from .base import LLMProvider, _PROJECT_ROOT, _is_transient, _load_env, new_call_id

_CACHE_DIR = _PROJECT_ROOT / "eval_runs" / "cache"


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


# --- neutral JSON-schema (our tool params) -> google.genai types.Schema ---

def _json_schema_to_gemini(schema):
    """Convert the JSON-Schema subset our tool specs use to a ``types.Schema``:
    object/array/string/integer/number/boolean + description/enum/properties/required/items."""
    from google.genai import types
    if schema is None:
        return None
    T = types.Type
    type_map = {"object": T.OBJECT, "string": T.STRING, "integer": T.INTEGER,
                "number": T.NUMBER, "boolean": T.BOOLEAN, "array": T.ARRAY}
    jt = schema.get("type", "object")
    kwargs = {"type": type_map.get(jt, T.OBJECT)}
    if schema.get("description"):
        kwargs["description"] = schema["description"]
    if schema.get("enum"):
        kwargs["enum"] = list(schema["enum"])
    if jt == "object":
        props = schema.get("properties") or {}
        kwargs["properties"] = {k: _json_schema_to_gemini(v) for k, v in props.items()}
        if schema.get("required"):
            kwargs["required"] = list(schema["required"])
    if jt == "array" and schema.get("items"):
        kwargs["items"] = _json_schema_to_gemini(schema["items"])
    return types.Schema(**kwargs)


def tool_specs_to_gemini(tool_specs):
    """Convert neutral OpenAI function-tool dicts to a ``list[types.Tool]`` (or None)."""
    from google.genai import types
    if not tool_specs:
        return None
    decls = [types.FunctionDeclaration(
                name=t["function"]["name"],
                description=t["function"].get("description", ""),
                parameters=_json_schema_to_gemini(t["function"].get("parameters")))
             for t in tool_specs]
    return [types.Tool(function_declarations=decls)]


class GeminiProvider(LLMProvider):
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
        self.thinking_budget = thinking_budget
        self.thinking_level = thinking_level
        self._client = None
        self._tools_cache = None            # (id(tool_specs), native_tools) — see _native_tools

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

    def _cache_path(self, user: str, temperature: float, key: str):
        h = hashlib.sha256(
            json.dumps([self.model, temperature, SYSTEM_PROMPT, user, key], ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:32]
        return _CACHE_DIR / f"{h}.json"

    # --- one raw call (forced JSON) ---

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

    # --- neutral <-> native conversion for the agentic tool loop ---

    def _to_contents(self, messages: list[dict]) -> list:
        """Neutral message list -> google.genai ``types.Content`` list. Assistant turns
        produced by THIS provider are re-sent verbatim from ``_native`` (preserving the
        thought_signature); consecutive tool results are coalesced into one user Content
        of function_response parts (Gemini matches them to calls by name + order)."""
        from google.genai import types
        contents: list = []
        i, n = 0, len(messages)
        while i < n:
            m = messages[i]
            role = m.get("role")
            if role == "user":
                contents.append(types.Content(role="user",
                                              parts=[types.Part(text=m.get("text") or "")]))
                i += 1
            elif role == "assistant":
                nat = m.get("_native")
                if nat is not None:
                    contents.append(nat)                       # VERBATIM -> keeps thought_signature
                else:
                    # only reached for an assistant turn that did NOT originate from this
                    # provider (cannot happen for one agent — one provider per run); rebuild best-effort.
                    parts = []
                    if m.get("text"):
                        parts.append(types.Part(text=m["text"]))
                    for tc in m.get("tool_calls") or []:
                        parts.append(types.Part(function_call=types.FunctionCall(
                            name=tc["name"], args=tc.get("args") or {})))
                    contents.append(types.Content(role="model",
                                                  parts=parts or [types.Part(text="(no content)")]))
                i += 1
            elif role == "tool":
                parts = []
                while i < n and messages[i].get("role") == "tool":
                    tm = messages[i]
                    res = tm.get("result")
                    parts.append(types.Part(function_response=types.FunctionResponse(
                        name=tm.get("name"),
                        response=res if isinstance(res, dict) else {"value": res})))
                    i += 1
                contents.append(types.Content(role="user", parts=parts))
            else:
                i += 1
        return contents

    def _native_tools(self, tool_specs):
        if not tool_specs:
            return None
        # the agent reuses one tool_specs list object for the whole run; cache by identity.
        if self._tools_cache is not None and self._tools_cache[0] == id(tool_specs):
            return self._tools_cache[1]
        native = tool_specs_to_gemini(tool_specs)
        self._tools_cache = (id(tool_specs), native)
        return native

    def tool_turn(self, messages: list[dict], tools: list[dict], *, system: str,
                  temperature: float | None = None) -> dict:
        from google.genai import types
        temp = self.temperature if temperature is None else temperature
        contents = self._to_contents(messages)
        native_tools = self._native_tools(tools)
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
                        tools=native_tools,
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
                return {"assistant": None, "function_calls": [], "text": "",
                        "error": error, "api_error": _is_transient(error),
                        "retries": transient_tries, "backoff_s": backoff_s}

            cand = resp.candidates[0] if resp.candidates else None
            content = cand.content if cand else None
            parts = (content.parts if content and content.parts else []) or []
            fcs = [{"id": new_call_id(), "name": p.function_call.name,
                    "args": dict(p.function_call.args or {})}
                   for p in parts if getattr(p, "function_call", None)]
            text = "".join(p.text for p in parts if getattr(p, "text", None))
            # Keep the native Content under _native so it is re-sent verbatim next turn.
            # Some models occasionally return a Content with NO parts; re-sending that
            # poisons the conversation (every later request 400s on "must include at least
            # one parts field"), so in that case drop _native and substitute a text turn.
            if content is not None and getattr(content, "parts", None):
                assistant = {"role": "assistant", "text": text, "tool_calls": fcs,
                             "_native": content}
            else:
                assistant = {"role": "assistant", "text": text or "(no content)",
                             "tool_calls": fcs, "_native": None}
            return {"assistant": assistant, "function_calls": fcs, "text": text,
                    "error": None, "api_error": False,
                    "retries": transient_tries, "backoff_s": backoff_s}
