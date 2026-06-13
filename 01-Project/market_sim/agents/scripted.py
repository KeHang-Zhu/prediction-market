"""Scripted bots: NoiseTrader, NaiveMM, ZIC, Fundamentalist.

Each draws only from ``ctx.rng`` (its own spawned substream). Feasibility is kept
light — the exchange rejects anything infeasible cleanly — but obviously-doomed
actions (e.g. selling shares you don't hold) are avoided to keep logs readable.
"""

from __future__ import annotations

from market_sim.engine.models import Side, Token

from .base import Action, Agent, Cancel, DecisionContext, Hold, PlaceOrder
from .llm_agent import LLMAgent, ToolLoopAgent  # lazy-import the Gemini provider only when used


def _clamp(x: int, lo: int = 1, hi: int = 99) -> int:
    return max(lo, min(hi, x))


class NoiseTrader(Agent):
    """Random limit orders within mid ± w, with activity probability q."""

    def decide(self, ctx: DecisionContext) -> list[Action]:
        rng = ctx.rng
        q = self.params.get("q", 0.6)
        w = self.params.get("w", 8)
        max_qty = self.params.get("max_qty", 15)
        acts: list[Action] = []
        for mid, mv in ctx.markets.items():
            if mv.status != "open":
                continue
            if rng.random() > q:
                continue
            token = Token.YES if rng.random() < 0.5 else Token.NO
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            ref = mv.mid if token is Token.YES else 100 - mv.mid
            price = _clamp(ref + int(rng.integers(-w, w + 1)))
            qty = int(rng.integers(1, max_qty + 1))
            if side is Side.SELL:
                held = ctx.portfolio.positions.get(mid, {}).get(token.value, 0)
                if held < 1:
                    side = Side.BUY  # nothing to sell -> buy instead
                else:
                    qty = min(qty, held)
            acts.append(PlaceOrder(mid, token, side, price, qty))
        return acts or [Hold()]


class NaiveMM(Agent):
    """Polymarket-faithful market maker: quotes a YES bid and a YES ask, where the
    ask is posted as a *buy NO* at ``100 - ask``. When both legs fill it mints,
    locking in the spread. Cancels and re-quotes each round; skews the center down
    when long net YES inventory.
    """

    def decide(self, ctx: DecisionContext) -> list[Action]:
        spread = self.params.get("spread", 3)
        size = self.params.get("size", 20)
        skew_unit = self.params.get("skew_unit", 30)  # shift center 1¢ per `skew_unit` net shares
        acts: list[Action] = []
        for mid, mv in ctx.markets.items():
            if mv.status != "open":
                continue
            # cancel my stale quotes in this market
            for o in ctx.portfolio.open_orders:
                if o["market"] == mid:
                    acts.append(Cancel(o["order_id"]))
            pos = ctx.portfolio.positions.get(mid, {"YES": 0, "NO": 0})
            net = pos.get("YES", 0) - pos.get("NO", 0)
            center = _clamp(mv.mid - net // skew_unit, 1 + spread, 99 - spread)
            bid = _clamp(center - spread)
            ask = _clamp(center + spread)
            acts.append(PlaceOrder(mid, Token.YES, Side.BUY, bid, size))         # YES bid
            acts.append(PlaceOrder(mid, Token.NO, Side.BUY, 100 - ask, size))    # YES ask via buy-NO
        return acts or [Hold()]


class ZIC(Agent):
    """Gode-Sunder zero-intelligence-constrained: random prices on the profitable
    side of a fixed private valuation, budget-constrained. The Arm-1 null model."""

    def decide(self, ctx: DecisionContext) -> list[Action]:
        rng = ctx.rng
        q = self.params.get("q", 0.5)
        max_qty = self.params.get("max_qty", 15)
        value = int(self.params.get("value", 50))  # private YES valuation (cents)
        value = max(2, min(98, value))
        acts: list[Action] = []
        for mid, mv in ctx.markets.items():
            if mv.status != "open":
                continue
            if rng.random() > q:
                continue
            if rng.random() < 0.5:
                # buy YES at a random price strictly below valuation (profitable)
                price = int(rng.integers(1, value))
                qty = int(rng.integers(1, max_qty + 1))
                acts.append(PlaceOrder(mid, Token.YES, Side.BUY, price, qty))
            else:
                held = ctx.portfolio.positions.get(mid, {}).get("YES", 0)
                if held > 0:
                    price = int(rng.integers(value + 1, 100))
                    qty = min(held, int(rng.integers(1, max_qty + 1)))
                    acts.append(PlaceOrder(mid, Token.YES, Side.SELL, price, qty))
        return acts or [Hold()]


class Fundamentalist(Agent):
    """Knows true_prob; nudges price toward the target ``t = round(true_prob*100)``
    when it deviates beyond a threshold. Buys YES @ t when underpriced (lifting
    asks up to t) and buys NO @ 100-t when overpriced (capping price at t)."""

    def decide(self, ctx: DecisionContext) -> list[Action]:
        threshold = self.params.get("threshold", 4)
        size = self.params.get("size", 15)
        acts: list[Action] = []
        for mid, mv in ctx.markets.items():
            if mv.status != "open":
                continue
            t = _clamp(round(mv.true_prob * 100))
            if mv.mid < t - threshold:
                acts.append(PlaceOrder(mid, Token.YES, Side.BUY, t, size))
            elif mv.mid > t + threshold:
                acts.append(PlaceOrder(mid, Token.NO, Side.BUY, _clamp(100 - t), size))
        return acts or [Hold()]


BOT_REGISTRY = {
    "noise": NoiseTrader,
    "mm": NaiveMM,
    "zic": ZIC,
    "fundamentalist": Fundamentalist,
    "llm": LLMAgent,
    "llm_agentic": ToolLoopAgent,
}
