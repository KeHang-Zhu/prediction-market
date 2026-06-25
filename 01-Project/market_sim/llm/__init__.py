"""LLM provider package + the ``get_provider`` factory.

A run can mix providers per agent: each ToolLoopAgent picks its provider from its
``params`` (``provider`` and/or ``model``). Selection is explicit ``provider`` first,
else inferred from the model-name prefix. DeepSeek and other OpenAI-compatible endpoints
all run through one ``OpenAIProvider`` parameterized by base_url + api-key env var.
"""

from __future__ import annotations

import os

from .base import LLMProvider, _is_transient, _load_env, _TRANSIENT, new_call_id  # noqa: F401
from .gemini import GeminiProvider, tool_specs_to_gemini  # noqa: F401
from .openai_compat import OpenAIProvider  # noqa: F401

# OpenAI-compatible "kinds" all route to OpenAIProvider; "gemini" routes to GeminiProvider.
_OPENAI_KINDS = {"openai", "deepseek", "openrouter", "vllm", "openai_compat"}


def _infer_kind(model: str | None) -> str:
    m = (model or "").lower()
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    if m.startswith("gemini"):
        return "gemini"
    return "gemini"   # default: model=None falls back to env GEMINI_MODEL (existing behavior)


def _presets() -> dict:
    """Per-kind base_url / api-key env / model env. Read at call time, after _load_env()."""
    return {
        "deepseek": {"base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                     "api_key_env": "DEEPSEEK_API_KEY", "model_env": "DEEPSEEK_MODEL"},
        "openai": {"base_url": os.environ.get("OPENAI_BASE_URL") or None,
                   "api_key_env": "OPENAI_API_KEY", "model_env": "OPENAI_MODEL"},
        "openrouter": {"base_url": os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                       "api_key_env": "OPENROUTER_API_KEY", "model_env": "OPENROUTER_MODEL"},
        "vllm": {"base_url": os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1"),
                 "api_key_env": "VLLM_API_KEY", "model_env": "VLLM_MODEL"},
    }


def get_provider(model: str | None = None, provider: str | None = None, *,
                 temperature: float = 0.7, use_cache: bool = False,
                 max_retries: int = 5, pace: float = 0.25,
                 base_url: str | None = None, max_output_tokens: int | None = None,
                 thinking: bool = False, reasoning_effort: str | None = None,
                 extra_body: dict | None = None) -> LLMProvider:
    """Return the provider for an agent.

    Selection: explicit ``provider`` (gemini | openai | deepseek | openrouter | vllm)
    wins; otherwise infer from the model name. Gemini keeps ``thinking_level="low"``
    (the agentic default); OpenAI-compatible kinds resolve base_url / api-key / default
    model from the preset for that kind. ``thinking`` / ``reasoning_effort`` / ``extra_body``
    enable reasoning on OpenAI-compatible models (e.g. DeepSeek v4); they are ignored for
    Gemini (which already runs with thinking_level="low").
    """
    _load_env()
    kind = (provider or _infer_kind(model)).lower()
    if kind == "gemini":
        return GeminiProvider(model=model, temperature=temperature,
                              max_retries=max_retries, pace=pace, thinking_level="low")
    if kind in _OPENAI_KINDS:
        cfg = _presets().get(kind, {"base_url": None, "api_key_env": "OPENAI_API_KEY",
                                    "model_env": None})
        kw = {}
        if max_output_tokens:
            kw["max_output_tokens"] = max_output_tokens
        return OpenAIProvider(model=model, temperature=temperature,
                              base_url=base_url or cfg.get("base_url"),
                              api_key_env=cfg.get("api_key_env", "OPENAI_API_KEY"),
                              default_model_env=cfg.get("model_env"),
                              max_retries=max_retries, pace=pace,
                              thinking=thinking, reasoning_effort=reasoning_effort,
                              extra_body=extra_body, **kw)
    raise ValueError(f"unknown provider kind: {kind!r} (model={model!r})")
