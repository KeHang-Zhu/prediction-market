"""Provider-neutral LLM interface and shared helpers.

The agentic ToolLoopAgent talks to models through `LLMProvider`, so a single run can
mix providers (Gemini via Vertex AI plus any OpenAI-compatible endpoint such as
DeepSeek). The conversation crossing this boundary is a provider-NEUTRAL list of plain
dicts; each provider translates it to/from its native request shape internally.

Neutral message shapes (the items of ``messages``):

    {"role": "user", "text": "<briefing / nudge>"}

    {"role": "assistant", "text": "<model text, may be ''>",
     "tool_calls": [{"id": "<id>", "name": "<tool>", "args": {...}}],   # [] if none
     "_native": <opaque provider object or None>}

    {"role": "tool", "tool_call_id": "<id>", "name": "<tool>", "result": {...}}

``_native`` lets a provider stash its raw turn object (a google.genai ``types.Content``)
so it can be re-sent VERBATIM — required for Gemini-3 to keep its ``thought_signature``;
a hand-rebuilt part would be rejected. OpenAI-style providers leave it ``None`` and
rebuild the assistant turn from ``text`` + ``tool_calls``. Everything except ``_native``
is plain JSON-able data, and a ``types.Content`` pickles, so the whole conversation
survives a save/resume round-trip.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from pathlib import Path

# project root = .../01-Project (this file is market_sim/llm/base.py)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# substrings that mark a transient/infra error (retry with backoff, not a model failure).
# Network/DNS/connection blips and 429/5xx all clear on a backoff-and-retry; treating them
# as hard failures would make an agent needlessly hold the round.
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


def new_call_id() -> str:
    """A short unique id for one tool call within a turn (so OpenAI can pair each call
    with its result; Gemini ignores it and matches results by name + order)."""
    return f"call_{uuid.uuid4().hex[:8]}"


class LLMProvider(ABC):
    """Provider-neutral interface for the agentic tool loop. Only ``tool_turn`` is
    required; ``complete()`` (forced-JSON, used by the simple LLMAgent and the offline
    eval) stays Gemini-specific and is NOT part of this ABC."""

    @abstractmethod
    def tool_turn(self, messages: list[dict], tools: list[dict], *, system: str,
                  temperature: float | None = None) -> dict:
        """One model call with native function-calling. ``messages`` is the neutral list
        documented above; ``tools`` is a list of OpenAI function-tool dicts. Returns::

            {"assistant": <neutral assistant dict | None>,   # append to the conversation
             "function_calls": [{"id","name","args"}],        # calls to execute (may be [])
             "text": str, "error": str | None, "api_error": bool,
             "retries": int, "backoff_s": float}

        On a hard failure: ``assistant=None, function_calls=[], text=""``.
        """
        raise NotImplementedError
