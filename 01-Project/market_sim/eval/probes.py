"""The 8 L2 probe scenarios (plan §5.4), each a frozen single-step test with an
executable pass criterion.

Engine-faithful adaptations from the proposal (single combined book):
- P4 (complementary arbitrage): a resting two-sided arb (YES ask + NO ask summing
  to <100) is IMPOSSIBLE in a single CDA book — it would imply best_bid > best_ask
  and auto-match. So P4 is posed as two explicitly-complementary markets whose YES
  shares are jointly underpriced (40 + 45 < 100); the skill tested (spot risk-free
  cross-contract profit) is identical.
"""

from __future__ import annotations

from market_sim.engine.models import Side, Token

from .base import (
    JudgeResult,
    Probe,
    ProbeSetup,
    belief_for,
    implied_yes_quotes,
    would_cross,
)
from .scenarios import SELF, add_yes_ask, add_yes_bid, give_shares, make_exchange, place_self_order
from .schema import build_observation

ROUND = 17


def _buys(resp, market, token):
    return [a for a in resp.actions
            if a.type == "place_order" and a.market == market and a.token == token
            and a.side == "buy" and a.price is not None and a.qty and a.qty >= 1]


def _sells(resp, market, token):
    return [a for a in resp.actions
            if a.type == "place_order" and a.market == market and a.token == token
            and a.side == "sell" and a.price is not None and a.qty and a.qty >= 1]


# --------------------------------------------------------------------- P1 free lunch

def _p1_build() -> ProbeSetup:
    ex = make_exchange([{"id": "COIN-A", "question": "Will event A occur?", "resolve_round": 50}])
    add_yes_ask(ex, "COIN-A", 5, 50)
    news = ["Market notice: event A is estimated to occur with about 90% probability."]
    return ProbeSetup(ex, SELF, ROUND, build_observation(ex, SELF, ROUND, news=news), {"market": "COIN-A"})


def _p1_judge(resp, s) -> JudgeResult:
    for a in _buys(resp, "COIN-A", "YES"):
        if a.price >= 5:
            return JudgeResult(True, f"bought underpriced YES @{a.price} x{a.qty}")
    return JudgeResult(False, "did not buy the 5¢ YES despite ~90% news")


# --------------------------------------------------------------------- P2 take profit on overpriced

def _p2_build() -> ProbeSetup:
    ex = make_exchange([{"id": "COIN-A", "question": "Will event A occur?", "resolve_round": 50}])
    give_shares(ex, SELF, "COIN-A", Token.YES, 60)
    add_yes_bid(ex, "COIN-A", 95, 50)
    news = ["Market notice: event A is estimated to occur with only about 30% probability."]
    recent = ["r16: (you hold 60 YES from earlier)"]
    return ProbeSetup(ex, SELF, ROUND,
                      build_observation(ex, SELF, ROUND, news=news, recent_actions=recent),
                      {"market": "COIN-A"})


def _p2_judge(resp, s) -> JudgeResult:
    for a in _sells(resp, "COIN-A", "YES"):
        if a.price <= 95:
            return JudgeResult(True, f"sold overvalued YES @{a.price} x{a.qty}")
    return JudgeResult(False, "held overvalued YES instead of selling into the 95¢ bid")


# --------------------------------------------------------------------- P3 empty-book quoting

def _p3_build() -> ProbeSetup:
    ex = make_exchange([{"id": "COIN-A", "question": "Will event A occur?", "resolve_round": 50}])
    news = ["Market notice: event A is estimated to occur with about 60% probability."]
    return ProbeSetup(ex, SELF, ROUND, build_observation(ex, SELF, ROUND, news=news), {"market": "COIN-A"})


def _p3_judge(resp, s) -> JudgeResult:
    bids, asks = implied_yes_quotes([a for a in resp.actions if a.market == "COIN-A"])
    if not bids or not asks:
        return JudgeResult(False, "did not post a two-sided quote")
    b, a = max(bids), min(asks)
    if b < a and 40 <= b <= 75 and 45 <= a <= 90:
        return JudgeResult(True, f"posted two-sided quote bid {b} / ask {a} around 60")
    return JudgeResult(False, f"quote not sensible (inside bid {b}, ask {a})")


# --------------------------------------------------------------------- P4 complementary arbitrage

def _p4_build() -> ProbeSetup:
    ex = make_exchange([
        {"id": "RAIN", "question": "Will it RAIN in the city tomorrow?", "resolve_round": 50},
        {"id": "DRY", "question": "Will it stay DRY (no rain) tomorrow? This is the exact complement of RAIN — exactly one of RAIN/DRY resolves YES.", "resolve_round": 50},
    ])
    add_yes_ask(ex, "RAIN", 40, 50)
    add_yes_ask(ex, "DRY", 45, 50)
    return ProbeSetup(ex, SELF, ROUND, build_observation(ex, SELF, ROUND),
                      {"markets": ["RAIN", "DRY"]})


def _p4_judge(resp, s) -> JudgeResult:
    rain = [a for a in _buys(resp, "RAIN", "YES") if a.price >= 40]
    dry = [a for a in _buys(resp, "DRY", "YES") if a.price >= 45]
    if rain and dry:
        return JudgeResult(True, "bought YES in both complementary markets (locked risk-free profit)")
    return JudgeResult(False, "missed the complementary arb (did not buy both YES legs)")


# --------------------------------------------------------------------- P5 manage your hand (don't overtrade)

def _p5_build() -> ProbeSetup:
    ex = make_exchange([{"id": "COIN-A", "question": "Will event A occur?", "resolve_round": 50}])
    add_yes_bid(ex, "COIN-A", 59, 40)
    add_yes_ask(ex, "COIN-A", 61, 40)
    news = ["Market notice: event A is estimated to occur with about 60% probability (consistent with the current price)."]
    return ProbeSetup(ex, SELF, ROUND, build_observation(ex, SELF, ROUND, news=news), {"market": "COIN-A"})


def _p5_judge(resp, s) -> JudgeResult:
    marketable = [a for a in resp.actions if would_cross(s.exchange, "COIN-A", a)]
    if not marketable:
        return JudgeResult(True, "did not cross a fair spread (held or quoted passively)")
    return JudgeResult(False, f"overtraded: {len(marketable)} order(s) cross the fair 59/61 spread")


# --------------------------------------------------------------------- P6 budget constraint

def _p6_build() -> ProbeSetup:
    ex = make_exchange([{"id": "COIN-A", "question": "Will event A occur?", "resolve_round": 50}], cash=500)
    add_yes_ask(ex, "COIN-A", 60, 50)
    news = ["Market notice: event A is estimated to occur with about 70% probability."]
    return ProbeSetup(ex, SELF, ROUND, build_observation(ex, SELF, ROUND, news=news),
                      {"market": "COIN-A", "budget": 500})


def _p6_judge(resp, s) -> JudgeResult:
    lock = sum(a.price * a.qty for a in resp.actions
               if a.type == "place_order" and a.side == "buy" and a.price and a.qty)
    if lock <= s.meta["budget"]:
        return JudgeResult(True, f"order set locks {lock}¢ ≤ budget {s.meta['budget']}¢")
    return JudgeResult(False, f"over budget: locks {lock}¢ > {s.meta['budget']}¢")


# --------------------------------------------------------------------- P7 information update

def _p7_build() -> ProbeSetup:
    ex = make_exchange([{"id": "COIN-A", "question": "Will event A occur?", "resolve_round": 50}])
    res = place_self_order(ex, "COIN-A", Token.YES, Side.BUY, 60, 10)
    news = ["Market notice: a STRONG NEGATIVE signal just arrived — event A is now estimated at about 20% probability (signal reliability ~80%)."]
    recent = ["r16: place buy YES@60 x10 (resting, based on the old ~60% price)"]
    obs = build_observation(ex, SELF, ROUND, news=news, recent_actions=recent)
    return ProbeSetup(ex, SELF, ROUND, obs, {"market": "COIN-A", "order_id": res.order_id})


def _p7_judge(resp, s) -> JudgeResult:
    mkt = s.meta["market"]
    if any(a.type == "cancel_order" and a.order_id == s.meta["order_id"] for a in resp.actions):
        return JudgeResult(True, "cancelled the stale buy order")
    bel = belief_for(resp, mkt)
    if bel is not None and bel <= 0.40:
        return JudgeResult(True, f"downgraded belief to {bel}")
    if _sells(resp, mkt, "YES"):
        return JudgeResult(True, "sold YES on the negative signal")
    if _buys(resp, mkt, "NO"):
        return JudgeResult(True, "bought NO on the negative signal")
    return JudgeResult(False, "ignored the strong negative signal (no cancel / downgrade / sell / NO)")


# --------------------------------------------------------------------- P8 order memory

def _p8_build() -> ProbeSetup:
    ex = make_exchange([{"id": "COIN-A", "question": "Will event A occur?", "resolve_round": 50}])
    place_self_order(ex, "COIN-A", Token.YES, Side.BUY, 55, 10)
    news = ["Note: you want to MAINTAIN your current exposure this round (you already have a resting buy YES@55 x10)."]
    recent = ["r16: place buy YES@55 x10 (resting)"]
    obs = build_observation(ex, SELF, ROUND, news=news, recent_actions=recent)
    existing = {(o["market"], o["token"], o["side"], o["price"]) for o in obs["you"]["open_orders"]}
    return ProbeSetup(ex, SELF, ROUND, obs, {"existing": existing})


def _p8_judge(resp, s) -> JudgeResult:
    existing = s.meta["existing"]
    for a in resp.actions:
        if a.type == "place_order" and (a.market, a.token, a.side, a.price) in existing:
            return JudgeResult(False, f"re-submitted a duplicate of an existing order ({a.market} {a.side} {a.token}@{a.price})")
    return JudgeResult(True, "did not duplicate the existing resting order")


ALL_PROBES: list[Probe] = [
    Probe("P1", "free lunch", "won't grab obvious mispricing", _p1_build, _p1_judge),
    Probe("P2", "take profit on overpriced", "disposition effect / won't sell", _p2_build, _p2_judge),
    Probe("P3", "empty-book quoting", "frozen on an empty book", _p3_build, _p3_judge),
    Probe("P4", "complementary arbitrage", "blind to cross-contract arb", _p4_build, _p4_judge),
    Probe("P5", "manage your hand", "overtrading", _p5_build, _p5_judge),
    Probe("P6", "budget constraint", "over-budget / ignores cash", _p6_build, _p6_judge),
    Probe("P7", "information update", "anchoring / no update", _p7_build, _p7_judge),
    Probe("P8", "order memory", "forgets its own resting orders", _p8_build, _p8_judge),
]

PROBE_BY_ID = {p.id: p for p in ALL_PROBES}
