"""Frozen-state builders for probes.

Books are shaped by placing resting orders from a deep-pocketed maker; YES asks are
created via maker buy-NO (mirror), so no share inventory is needed. Positions are
granted via a mint-style seed that preserves the engine invariants.
"""

from __future__ import annotations

from market_sim.engine.exchange import Exchange
from market_sim.engine.models import Account, Market, Side, Token

MAKER = "mm"
SELF = "me"


def make_exchange(markets_spec: list[dict], *, cash: int = 50_000,
                  maker_cash: int = 10_000_000) -> Exchange:
    """markets_spec: list of {id, question?, resolve_round?}."""
    markets = {}
    for ms in markets_spec:
        markets[ms["id"]] = Market(
            id=ms["id"], question=ms.get("question", ""),
            true_prob=ms.get("true_prob", 0.5), resolve_round=ms.get("resolve_round", 10**9),
        )
    accounts = {SELF: Account(SELF, cash_available=cash), MAKER: Account(MAKER, cash_available=maker_cash)}
    return Exchange(markets, accounts, allow_self_trade=True)


def add_yes_ask(ex: Exchange, market: str, price: int, qty: int) -> None:
    """Create a resting YES ask at `price` (maker buys NO at 100-price; no inventory needed)."""
    ex.place_order(MAKER, market, Token.NO, Side.BUY, 100 - price, qty, 0)


def add_yes_bid(ex: Exchange, market: str, price: int, qty: int) -> None:
    """Create a resting YES bid at `price` (maker buys YES)."""
    ex.place_order(MAKER, market, Token.YES, Side.BUY, price, qty, 0)


def give_shares(ex: Exchange, agent: str, market: str, token: Token, qty: int, bank: str = MAKER) -> None:
    """Grant `qty` shares of `token` to `agent` (mint-style; preserves all invariants:
    bank funds 100*qty into the pool and takes the complementary side)."""
    ex.ledger.accounts[bank].cash_available -= 100 * qty
    ex.markets[market].collateral_pool += 100 * qty
    ex.ledger.accounts[agent].add_position(market, token, qty)
    ex.ledger.accounts[bank].add_position(market, token.other, qty)


def place_self_order(ex: Exchange, market: str, token: Token, side: Side, price: int, qty: int,
                     agent: str = SELF):
    """Place a resting order owned by the agent-under-test (for memory/update probes)."""
    return ex.place_order(agent, market, token, side, price, qty, 0)
