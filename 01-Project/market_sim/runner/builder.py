"""Scenario builder — expand a high-level web/CLI spec into a full Config + YAML.

The web "scenario builder" form sends a small spec (how many LLM agents, whether to
include market makers / noise bots, which tools are open, market/round settings) and
this module expands it into a complete :class:`Config` (mirroring ``demo5.yaml``) and
writes it as a reusable template YAML. Pure + deterministic + no web deps, so the CLI
and tests can use it too.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .config import AgentConfig, Capabilities, Config, MarketConfig, NewsConfig

# Built-in scenario run_names a user template must never clobber (they own runs/<name>/
# and the picker groups recordings by run_name).
BUILTIN_RUN_NAMES = frozenset({"demo", "demo5", "llm5_only", "llm5_open", "llm5_orders"})

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_DEFAULT_TRUE_PROBS = [0.65, 0.40, 0.55]


def slugify(name: str) -> str:
    """Filesystem- and run_name-safe slug: collapse runs of non-[alnum/-/_] to '-',
    strip leading/trailing separators, lowercase, cap length. Empty -> 'scenario'.
    This also defeats path traversal ('../x' -> 'x')."""
    s = _SLUG_RE.sub("-", (name or "").strip()).strip("-_").lower()[:48]
    return s or "scenario"


def _num(spec: dict, key: str, default):
    """Spec getter that treats missing/None as the default."""
    v = spec.get(key)
    return default if v is None else v


def build_config(spec: dict) -> Config:
    """Expand a high-level spec into a full Config. Deterministic, no I/O.

    Raises ``ValueError`` (via int()/float() coercion) or ``pydantic.ValidationError``
    on a malformed spec; the caller turns that into a user-facing error.
    """
    spec = spec or {}

    n_llm = int(_num(spec, "llm_agentic", 5))
    sig_min = float(_num(spec, "sigma_min", 0.04))
    sig_max = max(sig_min, float(_num(spec, "sigma_max", 0.12)))  # never descending
    temp = float(_num(spec, "temperature", 0.7))
    max_tc = int(_num(spec, "max_tool_calls", 8))
    model = spec.get("model") or None  # None -> provider falls back to its env default
    provider = spec.get("provider") or None  # gemini|openai|deepseek|... (else inferred from model)
    # optional per-agent model list -> different agents run different models in ONE run
    # (mixed-provider competition). models[i] overrides `model` for agent i (cycled).
    models = spec.get("models")
    if models and not isinstance(models, list):
        models = None
    thinking = bool(spec.get("thinking", False))            # reasoning mode (DeepSeek v4 etc.)
    reasoning_effort = spec.get("reasoning_effort") or None  # "low" | "medium" | "high"
    llm_cash = int(_num(spec, "llm_initial_cash", 200_000))

    # LLM agents. Two ways to specify them:
    #   1. `llm_groups`: a list of {model, count, thinking, reasoning_effort, [temperature],
    #      [max_tool_calls], [provider]} — each group expands to `count` agents that share its
    #      model + reasoning settings. This is the grouped editor: a run can MIX models AND
    #      give each group its own thinking depth. The signal noise σ is still spread best->worst
    #      across ALL llm agents (by global index), and temperature/max_tool_calls fall back to
    #      the global values unless a group overrides them.
    #   2. otherwise the flat form: `llm_agentic` count + a single `model` (or cycled `models`)
    #      + global `thinking`/`reasoning_effort`.
    groups = spec.get("llm_groups")
    agents: list[AgentConfig] = []

    if isinstance(groups, list) and groups:
        flat: list[dict] = []
        for g in groups:
            if not isinstance(g, dict):
                continue
            cnt = g.get("count", 1)
            cnt = int(cnt) if cnt is not None else 1
            for _ in range(max(0, cnt)):
                flat.append(g)
        n_total = len(flat)

        def sigma_g(i: int) -> float:
            if n_total <= 1:
                return round(sig_min, 6)
            return round(sig_min + (sig_max - sig_min) * i / (n_total - 1), 6)

        for i, g in enumerate(flat):
            gm = g.get("model")
            gm = gm.strip() if isinstance(gm, str) else gm
            g_temp = g.get("temperature")
            g_mtc = g.get("max_tool_calls")
            params = {"signal_sigma": sigma_g(i),
                      "temperature": float(g_temp) if g_temp is not None else temp,
                      "max_tool_calls": int(g_mtc) if g_mtc is not None else max_tc}
            if gm:
                params["model"] = str(gm)
            g_provider = g.get("provider") or provider
            if g_provider:
                params["provider"] = str(g_provider)
            if g.get("thinking"):
                params["thinking"] = True
            if g.get("reasoning_effort"):
                params["reasoning_effort"] = str(g["reasoning_effort"])
            agents.append(AgentConfig(id=f"llm{i + 1}", type="llm_agentic",
                                      initial_cash=llm_cash, params=params))
    else:
        def sigma(i: int) -> float:
            if n_llm <= 1:
                return round(sig_min, 6)
            return round(sig_min + (sig_max - sig_min) * i / (n_llm - 1), 6)

        for i in range(max(0, n_llm)):
            params = {"signal_sigma": sigma(i), "temperature": temp, "max_tool_calls": max_tc}
            a_model = str(models[i % len(models)]) if models else model
            if a_model:
                params["model"] = str(a_model)
            if provider:
                params["provider"] = str(provider)
            if thinking:
                params["thinking"] = True
            if reasoning_effort:
                params["reasoning_effort"] = str(reasoning_effort)
            agents.append(AgentConfig(id=f"llm{i + 1}", type="llm_agentic",
                                      initial_cash=llm_cash, params=params))

    if bool(_num(spec, "include_mm", True)) and int(_num(spec, "mm_count", 2)) > 0:
        agents.append(AgentConfig(
            id="mm", type="mm", count=int(_num(spec, "mm_count", 2)), initial_cash=500_000,
            params={"spread": int(_num(spec, "mm_spread", 3)), "size": int(_num(spec, "mm_size", 15))}))

    if bool(_num(spec, "include_noise", True)) and int(_num(spec, "noise_count", 1)) > 0:
        agents.append(AgentConfig(
            id="noise", type="noise", count=int(_num(spec, "noise_count", 1)), initial_cash=200_000,
            params={"q": float(_num(spec, "noise_q", 0.5)), "w": int(_num(spec, "noise_w", 8)),
                    "max_qty": int(_num(spec, "noise_max_qty", 10))}))

    n_markets = max(1, int(_num(spec, "markets", 3)))
    true_probs = spec.get("true_probs") or _DEFAULT_TRUE_PROBS
    resolve_round = int(_num(spec, "resolve_round", 999))
    markets: list[MarketConfig] = []
    for i in range(n_markets):
        letter = chr(ord("A") + i)
        p = float(true_probs[i % len(true_probs)])
        p = min(0.99, max(0.01, p))  # match the engine's signal clipping
        markets.append(MarketConfig(id=f"COIN-{letter}",
                                    question=f"Will coin {letter} land heads?",
                                    true_prob=p, resolve_round=resolve_round))

    signals_on = bool(_num(spec, "signals", True))
    if signals_on:
        news = NewsConfig(enabled=True, mode="prob",
                          sigma_decay=float(_num(spec, "sigma_decay", 0.8)),
                          disclose_sigma=bool(_num(spec, "disclose_sigma", True)))
    else:
        news = NewsConfig(enabled=False)

    caps_in = spec.get("capabilities") or {}
    caps = Capabilities(
        transfer=bool(caps_in.get("transfer", False)),
        create_account=bool(caps_in.get("create_account", False)),
        create_market=bool(caps_in.get("create_market", False)),
        advanced_orders=bool(caps_in.get("advanced_orders", False)),
    )

    return Config(
        seed=int(_num(spec, "seed", 42)),
        rounds=int(_num(spec, "rounds", 50)),
        max_actions_per_agent=int(_num(spec, "max_actions_per_agent", 12)),
        allow_self_trade=bool(_num(spec, "allow_self_trade", True)),
        depth_k=int(_num(spec, "depth_k", 8)),
        run_name=slugify(_num(spec, "name", "scenario")),
        markets=markets, agents=agents, news=news, capabilities=caps,
    )


def dump_config(config: Config, path: str | Path) -> Path:
    """Serialize a Config to a YAML template (round-trips through load_config)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(config.model_dump(), sort_keys=False, allow_unicode=True),
                 encoding="utf-8")
    return p
