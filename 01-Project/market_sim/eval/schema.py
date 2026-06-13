"""Observation + forced-JSON action schema, and the neutral system prompt.

The system prompt is the API documentation (the same rules a human reads) plus the
output-format contract and one neutral goal sentence — no strategy hints, no example
trades (the "Arm 1" spirit: measure the bare model). The observation deliberately
omits ground-truth probabilities.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from market_sim.engine.exchange import Exchange
from market_sim.engine.models import MarketStatus


# --- forced action schema (the model must return exactly this) ---

class Belief(BaseModel):
    market: str
    prob: float = Field(description="probability the market resolves YES, in [0,1]")


class ActionItem(BaseModel):
    type: str = Field(description="one of: place_order, cancel_order, hold")
    market: Optional[str] = None
    token: Optional[str] = Field(default=None, description="YES or NO (for place_order)")
    side: Optional[str] = Field(default=None, description="buy or sell (for place_order)")
    price: Optional[int] = Field(default=None, description="integer cents 1..99 (for place_order)")
    qty: Optional[int] = Field(default=None, description="positive integer (for place_order)")
    order_id: Optional[int] = Field(default=None, description="for cancel_order")


class AgentResponse(BaseModel):
    beliefs: list[Belief] = Field(default_factory=list)
    rationale: str = ""
    actions: list[ActionItem] = Field(default_factory=list)


SYSTEM_PROMPT = """\
You are a trader in a binary prediction market. Your only goal is to maximize your final wealth.

MARKET RULES
- Each market has two share types, YES and NO. At resolution the winning side pays 100 cents
  per share; the losing side pays 0. So one YES + one NO is always worth exactly 100 cents.
- Prices are INTEGER CENTS from 1 to 99. A YES price of 60 means 60 cents per YES share
  (i.e. the market implies a ~60% chance of YES). Quantities are positive integers.
- There is no short selling. To bet against YES, buy NO. Buying YES at price p and NO at
  price (100-p) for the same quantity costs 100 per pair and is risk-free at resolution.
- Cash is in integer cents. Placing a BUY locks price*qty cash; placing a SELL locks qty
  shares. You cannot spend more cash than `cash_available` or sell more shares than you hold.

ACTIONS YOU MAY TAKE (return a list)
- place_order: {type:"place_order", market, token:"YES"|"NO", side:"buy"|"sell", price, qty}
- cancel_order: {type:"cancel_order", order_id}
- hold: {type:"hold"}            # take no action this round — use it when nothing is worth doing

OUTPUT FORMAT (return ONLY this JSON object)
{
  "beliefs": [{"market": "<id>", "prob": <0..1>}],   // your probability each market resolves YES
  "rationale": "<one short sentence>",
  "actions": [ <action objects as above> ]
}

Do not invent endpoints or fields. If you do not want to trade, return a single hold action.
Think about prices as cents, not probabilities, when you place orders.
"""


def build_observation(
    exchange: Exchange,
    agent_id: str,
    round_no: int,
    *,
    depth: int = 6,
    news: list[str] | None = None,
    recent_actions: list[str] | None = None,
) -> dict:
    """Build the observation dict for one agent from a frozen exchange state.

    Ground-truth probabilities are never included.
    """
    pf = exchange.get_portfolio(agent_id)
    markets = []
    for mid in sorted(exchange.markets):
        m = exchange.markets[mid]
        if m.status is not MarketStatus.OPEN:
            continue
        markets.append({
            "id": mid,
            "question": m.question,
            "best_bid": exchange.best_bid(mid),
            "best_ask": exchange.best_ask(mid),
            "last_trade": exchange.last_price[mid],
            "depth": exchange.get_book(mid, depth),
            "resolves_in_rounds": max(0, m.resolve_round - round_no),
        })
    return {
        "round": round_no,
        "you": {
            "cash_available": pf["cash_available"],
            "cash_locked": pf["cash_locked"],
            "positions": {k: v for k, v in pf["positions"].items() if any(v.values())},
            "open_orders": pf["open_orders"],
        },
        "markets": markets,
        "news": news or [],
        "your_recent_actions": recent_actions or [],
    }
