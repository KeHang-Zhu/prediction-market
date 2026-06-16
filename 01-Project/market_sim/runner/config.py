"""Experiment configuration (pydantic) + YAML loader.

Switches reserved for institutional variation (fees, market orders, info
disclosure) are pre-wired here and default to off, per the design brief.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class MarketConfig(BaseModel):
    id: str
    question: str = ""
    true_prob: float = 0.5
    resolve_round: int = 10_000
    fixed_outcome: int | None = None  # 0/1 to pin the outcome; else sampled from true_prob


class AgentConfig(BaseModel):
    id: str
    type: str                          # noise | mm | zic | fundamentalist | human
    count: int = 1
    initial_cash: int = 100_000
    params: dict = Field(default_factory=dict)


class Capabilities(BaseModel):
    """Agent-action capabilities, gated per scenario (default OFF). When enabled, the
    matching agent-API endpoint + LLM tool become available; existing scenarios that
    leave these off behave exactly as before (the endpoints return ``not_supported``)."""

    transfer: bool = False         # move cash to another existing account
    create_account: bool = False   # create a passive sub-wallet funded from your own cash
    create_market: bool = False    # open a new market (system fixes its hidden truth)
    advanced_orders: bool = False  # market orders (FOK/FAK), GTD expiry, post-only for LLMs


class NewsConfig(BaseModel):
    enabled: bool = False
    mode: str = "lean"                 # "lean": legacy binary public signal every N rounds
    #                                    "prob": per-agent PRIVATE noisy probability each round
    every_rounds: int = 10             # lean: publish cadence
    epsilon: float = 0.2               # lean: flip probability of the noisy public signal
    # prob mode: each agent with params.signal_sigma gets a private estimate
    #   s = clip(true_prob + N(0, sigma_t)), sigma_t = signal_sigma * (1 - sigma_decay*(t-1)/(T-1))
    sigma_decay: float = 0.8           # fraction the noise shrinks by over the horizon
    disclose_sigma: bool = True        # tell each agent its current reliability (±sigma)


class Config(BaseModel):
    seed: int = 0
    rounds: int = 200                  # default horizon for `run`
    max_actions_per_agent: int = 5     # K (raise for scripted bots; LLM cost knob)
    allow_self_trade: bool = True
    depth_k: int = 12                  # order-book depth returned by queries
    run_name: str = "run"

    markets: list[MarketConfig] = Field(default_factory=list)
    agents: list[AgentConfig] = Field(default_factory=list)
    news: NewsConfig = Field(default_factory=NewsConfig)
    capabilities: Capabilities = Field(default_factory=Capabilities)

    # reserved institutional-variation switches (V1; default off)
    fees_bps: int = 0
    enable_market_orders: bool = False
    enable_maker_rewards: bool = False

    def expand_agent_ids(self, agent: AgentConfig) -> list[str]:
        if agent.count <= 1:
            return [agent.id]
        return [f"{agent.id}_{i}" for i in range(agent.count)]


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Config(**(data or {}))
