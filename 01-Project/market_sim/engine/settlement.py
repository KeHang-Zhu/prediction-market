"""Market resolution and payout.

On expiry: cancel + unlock ALL resting orders in the market (refunding every
lock BEFORE payout — else cash is stranded and INV-C fails), then pay the winning
token 100 cents/share, zero the losing token, and drain the collateral pool. The
pool exactly equals winning_outstanding * 100, so the drain conserves cash.
"""

from __future__ import annotations

from .exchange import Exchange
from .models import MarketStatus, OrderStatus, Side, Token


def resolve_market(exchange: Exchange, market_id: str, outcome: int, round_no: int) -> dict:
    market = exchange.markets[market_id]
    book = exchange.books[market_id]

    # 1. cancel + unlock every resting order
    cancelled: list[int] = []
    for order in sorted(book.all_orders(), key=lambda o: o.order_id):
        rem = order.remaining
        if order.side is Side.BUY:
            exchange.ledger.unlock_buy(order.agent_id, order.limit_price * rem)
        else:
            exchange.ledger.unlock_sell(order.agent_id, market_id, order.token, rem)
        order.status = OrderStatus.CANCELLED
        book.remove(order.order_id)
        cancelled.append(order.order_id)

    # 2. payout
    winning = Token.YES if outcome == 1 else Token.NO
    losing = winning.other
    payouts: list[dict] = []
    paid_total = 0
    for agent_id in sorted(exchange.ledger.accounts):
        acct = exchange.ledger.accounts[agent_id]
        wq = acct.position(market_id, winning)
        if wq > 0:
            amount = wq * 100
            acct.cash_available += amount
            paid_total += amount
            payouts.append({
                "agent": agent_id, "winning_token": winning.value, "qty": wq, "amount": amount,
            })
        acct.set_position(market_id, winning, 0)
        acct.set_position(market_id, losing, 0)

    # 3. drain pool (it equals paid_total by construction)
    pool_before = market.collateral_pool
    market.collateral_pool = 0
    market.status = MarketStatus.RESOLVED
    market.outcome = outcome

    return {
        "market": market_id,
        "outcome": outcome,
        "winning_token": winning.value,
        "cancelled_orders": cancelled,
        "payouts": payouts,
        "pool_drained": pool_before,
        "paid_total": paid_total,
    }
