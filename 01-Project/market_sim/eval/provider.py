"""Back-compat shim.

The provider implementations moved to the ``market_sim.eval.providers`` package
(``base`` / ``gemini`` / ``openai_compat`` + the ``get_provider`` factory). This module
re-exports the names that used to live here so existing imports keep working
(``from market_sim.eval.provider import GeminiProvider`` in llm_agent.py and run_eval.py).
"""

from .providers.base import LLMProvider, _is_transient, _load_env, _TRANSIENT  # noqa: F401
from .providers.gemini import Completion, GeminiProvider  # noqa: F401
from .providers import get_provider  # noqa: F401
