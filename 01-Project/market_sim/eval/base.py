"""Shared eval contracts: probe types, action validation, and helpers.

These are the interfaces every other eval module depends on, so they are defined
once here and imported everywhere (keeps parallel-authored modules aligned).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from market_sim.engine.exchange import Exchange
from market_sim.engine.models import Side, Token

from .schema import ActionItem, AgentResponse


@dataclass
class ProbeSetup:
    exchange: Exchange
    agent_id: str
    round_no: int
    observation: dict
    meta: dict = field(default_factory=dict)


@dataclass
class JudgeResult:
    passed: bool
    reason: str


@dataclass
class Probe:
    id: str
    name: str
    failure_mode: str
    build: Callable[[], ProbeSetup]
    judge: Callable[[AgentResponse, ProbeSetup], JudgeResult]


@dataclass
class ActionCheck:
    valid: bool          # passes engine validation (could be executed)
    hallucinated: bool   # unknown action type / non-existent endpoint
    reason: str


@dataclass
class Trial:
    """One probe run (one model call). Consumed by metrics/scorecard."""
    probe_id: str
    repeat: int
    parse_ok: bool          # first attempt was schema-valid JSON
    valid: bool             # a usable response was obtained (within retries)
    n_actions: int
    n_valid_actions: int
    n_invalid_actions: int
    n_hallucinated: int
    passed: bool            # probe pass criterion met
    reason: str
    rationale: str
    attempts: int
    errored: bool = False   # infra/API failure (e.g. 429), excluded from rationality metrics


VALID_TYPES = {"place_order", "cancel_order", "hold"}


def validate_action(exchange: Exchange, agent_id: str, a: ActionItem) -> ActionCheck:
    """Validate one action against the frozen engine state (the L1 oracle)."""
    t = (a.type or "").strip()
    if t not in VALID_TYPES:
        return ActionCheck(False, True, f"unknown action type '{t}'")
    if t == "hold":
        return ActionCheck(True, False, "hold")
    if t == "cancel_order":
        if a.order_id is None:
            return ActionCheck(False, False, "missing order_id")
        for book in exchange.books.values():
            o = book.get(a.order_id)
            if o is not None:
                owned = o.agent_id == agent_id
                return ActionCheck(owned, False, "ok" if owned else "not owner")
        return ActionCheck(False, False, "order not found")
    # place_order
    if a.market not in exchange.markets:
        return ActionCheck(False, False, "unknown market")
    if a.token not in ("YES", "NO"):
        return ActionCheck(False, False, "bad token")
    if a.side not in ("buy", "sell"):
        return ActionCheck(False, False, "bad side")
    if a.price is None or not (1 <= a.price <= 99):
        return ActionCheck(False, False, "price out of range")
    if a.qty is None or a.qty < 1:
        return ActionCheck(False, False, "qty < 1")
    acct = exchange.ledger.accounts.get(agent_id)
    if acct is None:
        return ActionCheck(False, False, "unknown agent")
    tok = Token(a.token)
    if Side(a.side) is Side.BUY:
        if a.price * a.qty > acct.cash_available:
            return ActionCheck(False, False, "insufficient cash")
    else:
        if a.qty > acct.available_shares(a.market, tok):
            return ActionCheck(False, False, "insufficient shares")
    return ActionCheck(True, False, "ok")


def would_cross(exchange: Exchange, market: str, a: ActionItem) -> bool:
    """Would this place_order immediately trade against the current book?"""
    if a.type != "place_order" or a.price is None or market not in exchange.markets:
        return False
    bb, ba, p = exchange.best_bid(market), exchange.best_ask(market), a.price
    if a.token == "YES" and a.side == "buy":
        return ba is not None and p >= ba
    if a.token == "YES" and a.side == "sell":
        return bb is not None and p <= bb
    if a.token == "NO" and a.side == "buy":     # rests as YES ask at 100-p
        return bb is not None and bb >= 100 - p
    if a.token == "NO" and a.side == "sell":    # rests as YES bid at 100-p
        return ba is not None and ba <= 100 - p
    return False


def implied_yes_quotes(actions: list[ActionItem]) -> tuple[list[int], list[int]]:
    """Map a set of place_orders to implied YES-coordinate bids and asks."""
    bids: list[int] = []
    asks: list[int] = []
    for a in actions:
        if a.type != "place_order" or a.price is None:
            continue
        if a.token == "YES" and a.side == "buy":
            bids.append(a.price)
        elif a.token == "YES" and a.side == "sell":
            asks.append(a.price)
        elif a.token == "NO" and a.side == "buy":
            asks.append(100 - a.price)
        elif a.token == "NO" and a.side == "sell":
            bids.append(100 - a.price)
    return bids, asks


def belief_for(resp: AgentResponse, market: str) -> float | None:
    for b in resp.beliefs:
        if b.market == market:
            return b.prob
    return None
