"""Agent protocol, decision context, and action types.

An agent observes a frozen snapshot (end of the previous round) and returns a list
of actions. Scripted bots draw randomness from their OWN spawned substream
(assigned by the runner) so they never perturb the runner's draw sequence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from market_sim.engine.models import Side, Token


# --- actions ---

@dataclass
class PlaceOrder:
    market: str
    token: Token
    side: Side
    price: int
    qty: int
    type: str = "place_order"
    # Set by tool-using agents that announced this order at call time (via
    # DecisionContext.on_queue -> an `order_queued` event). The runner echoes it on the
    # round-end place_order/invalid_action event so the UI can correlate the queued order
    # with its fill/reject result. None for scripted bots -> not written to the event.
    client_id: str | None = None
    # time-in-force + modifiers (string for a JSON-trivial action; the runner maps tif to
    # the engine TimeInForce enum). Defaults = plain GTC limit order, so scripted bots that
    # build PlaceOrder(market, token, side, price, qty) are unchanged.
    tif: str = "GTC"                  # "GTC" | "GTD" | "FOK" | "FAK"
    post_only: bool = False           # GTC/GTD only: reject if it would cross on entry
    expire_round: int | None = None   # GTD only: last round valid


@dataclass
class Cancel:
    order_id: int
    type: str = "cancel_order"
    client_id: str | None = None


@dataclass
class Hold:
    type: str = "hold"


# --- open-scenario actions (gated by Config.capabilities; only LLM/console paths
#     produce them, so scripted byte-exact runs are unaffected). Like PlaceOrder they
#     carry a client_id set by tool-using agents to correlate the queued announcement
#     with its round-end settle/reject result; None for console/CLI use. ---

@dataclass
class Transfer:
    to: str                 # recipient account id (must already exist)
    amount: int             # cents of the caller's available cash to move
    type: str = "transfer"
    client_id: str | None = None


@dataclass
class CreateAccount:
    account_id: str         # id of the new passive wallet
    initial_cash: int       # funded FROM the creator's available cash
    type: str = "create_account"
    client_id: str | None = None


@dataclass
class CreateMarket:
    market_id: str
    question: str
    resolve_round: int      # must be > the current round; system fixes the hidden truth
    type: str = "create_market"
    client_id: str | None = None


Action = PlaceOrder | Cancel | Hold | Transfer | CreateAccount | CreateMarket


# --- observation views (built from the frozen snapshot) ---

@dataclass
class MarketView:
    id: str
    question: str
    status: str
    best_bid: int | None
    best_ask: int | None
    last_trade: int | None
    mid: int
    true_prob: float
    resolves_in: int
    depth: dict


@dataclass
class PortfolioView:
    cash_available: int
    cash_locked: int
    positions: dict[str, dict[str, int]]
    open_orders: list[dict]


@dataclass
class DecisionContext:
    round: int
    agent_id: str
    rng: np.random.Generator
    markets: dict[str, MarketView]
    portfolio: PortfolioView
    news: list[dict] = field(default_factory=list)
    # prob-mode PRIVATE signals for THIS agent THIS round (one per market it has a
    # signal on): {market, prob_pct, sigma_pct, text}. Empty in lean mode.
    signals: list[dict] = field(default_factory=list)
    # read-only query into the FROZEN round-start state, bound to this agent.
    # query(verb, args) -> json-able dict. Provided by the runner for tool-using
    # agents; None for scripted bots (they read everything from the views above).
    # verbs: get_markets, get_orderbook, get_trade_history, get_portfolio,
    #        get_news, get_news_detail. Reads never mutate the engine.
    query: Optional[Callable[[str, dict], dict]] = None
    # announce an order/cancel at the MOMENT the agent decides it (an `order_queued`
    # event), so the UI shows it in its true model-call position — before the round-end
    # fill is known (blind submit). on_queue(payload_dict). Provided by the runner for
    # tool-using agents; None for scripted bots.
    on_queue: Optional[Callable[[dict], None]] = None
    # announce the agent's committed view (belief + plan) the moment it calls commit_view,
    # BEFORE trading (an `agent_view` event). on_view({"belief": {...}, "plan": "..."}).
    # Provided by the runner for tool-using agents; None for scripted bots.
    on_view: Optional[Callable[[dict], None]] = None
    # announce the literal per-round wake-up briefing the agent was fed (a `briefing`
    # event), at the very start of the turn — so the "Agent single-round walkthrough"
    # demo can show EXACTLY what the system put in the model's mouth. on_briefing(text).
    on_briefing: Optional[Callable[[str], None]] = None
    # announce one model turn the instant it returns (a `model_turn` event): the raw
    # text the model produced and the function calls it asked for, BEFORE those calls'
    # results come back — the verbatim "what the LLM output" side of the dialogue.
    # on_model_turn({"turn": i, "text": ..., "calls": [{"name","args"}...], "error": ...}).
    on_model_turn: Optional[Callable[[dict], None]] = None


# --- base agent ---

class Agent:
    is_human: bool = False

    def __init__(self, agent_id: str, params: dict | None = None) -> None:
        self.agent_id = agent_id
        self.params = params or {}
        self.rng: Optional[np.random.Generator] = None  # assigned by the runner
        # the scenario's capability flags (Config.capabilities), assigned by the runner
        # like rng; None means "all off" (existing scenarios). Tool-using agents read it
        # to decide which extra tools to declare.
        self.caps = None
        # set by LLM agents to a {belief, rationale, ok, ...} dict each round; the
        # runner emits it as an `llm_call` event and resets it to None.
        self.last_call: dict | None = None

    def decide(self, ctx: DecisionContext) -> list[Action]:
        return []


class HumanAgent(Agent):
    """Placeholder for a human/console-driven trader; actions are injected."""

    is_human = True

    def decide(self, ctx: DecisionContext) -> list[Action]:
        return []
