"""Ledger — the sole writer of cash and shares.

All four settlement types, the two locking primitives, and the per-fill
residual-refund rule live here. Every method mutates accounts / market pools in
place and is integer-exact. See the design doc for the per-party derivations and
invariant proofs; the worked numbers in the docstrings below are the unit-test
oracles.
"""

from __future__ import annotations

from .models import Account, Market, Order, SettleType, Token


class Ledger:
    def __init__(self, accounts: dict[str, Account], total0: int) -> None:
        self.accounts = accounts
        self.total0 = total0  # initial total cash across all accounts (pools start at 0)

    # --- locking primitives (TRUE coords) ---

    def lock_buy(self, agent_id: str, cents: int) -> None:
        a = self.accounts[agent_id]
        a.cash_available -= cents
        a.cash_locked += cents

    def unlock_buy(self, agent_id: str, cents: int) -> None:
        a = self.accounts[agent_id]
        a.cash_locked -= cents
        a.cash_available += cents

    def lock_sell(self, agent_id: str, market_id: str, token: Token, qty: int) -> None:
        self.accounts[agent_id].add_locked_shares(market_id, token, qty)

    def unlock_sell(self, agent_id: str, market_id: str, token: Token, qty: int) -> None:
        self.accounts[agent_id].add_locked_shares(market_id, token, -qty)

    # --- settlement (p is always the YES-coords maker price) ---

    def settle_transfer(
        self, market: Market, token: Token, buy_order: Order, sell_order: Order, p: int, q: int
    ) -> int:
        """Normal transfer of ``token`` from seller to buyer.

        Buyer pays ``pay`` per unit (YES: p, NO: 100-p); releases its full lock
        ``true_limit*q`` and gets the overpay refunded to available. Seller
        releases ``q`` locked shares and receives ``pay*q``. Pool unchanged.

        Oracle: maker sell YES@58, taker buy YES@62, q=12, p=58 ->
        taker.cash_locked -744, taker.cash_available +48, taker.YES +12;
        seller.YES -12, seller.cash_available +696. Pool delta 0.
        """
        buyer = self.accounts[buy_order.agent_id]
        seller = self.accounts[sell_order.agent_id]
        pay = p if token is Token.YES else 100 - p
        lb = buy_order.limit_price  # TRUE-coords limit
        buyer.cash_locked -= lb * q
        buyer.cash_available += (lb - pay) * q
        buyer.add_position(market.id, token, q)
        seller.add_locked_shares(market.id, token, -q)
        seller.add_position(market.id, token, -q)
        seller.cash_available += pay * q
        return 0  # pool delta

    def settle_mint(
        self, market: Market, yes_buy: Order, no_buy: Order, p: int, q: int
    ) -> int:
        """buy YES x buy NO -> mint. 100*q cash enters the pool; 1 YES + 1 NO
        minted per unit. YES-buyer pays p, NO-buyer pays 100-p.

        Oracle: maker buy NO@NO-60 (YES-ask 40), taker buy YES@45, p=40, q=10 ->
        pool +1000; yb spends 400 (locked -450, avail +50, YES +10);
        nb spends 600 (locked -600, avail 0, NO +10).
        """
        yb = self.accounts[yes_buy.agent_id]
        nb = self.accounts[no_buy.agent_id]
        ly = yes_buy.limit_price          # YES coords
        ln = no_buy.limit_price           # NO coords
        yb.cash_locked -= ly * q
        yb.cash_available += (ly - p) * q
        yb.add_position(market.id, Token.YES, q)
        nb.cash_locked -= ln * q
        nb.cash_available += (ln - (100 - p)) * q
        nb.add_position(market.id, Token.NO, q)
        market.collateral_pool += 100 * q
        return 100 * q

    def settle_merge(
        self, market: Market, yes_sell: Order, no_sell: Order, p: int, q: int
    ) -> int:
        """sell YES x sell NO -> merge. Pool releases 100*q, split (p, 100-p) —
        integer-exact, never rounded. Both shares destroyed.

        Oracle: maker sell YES@58, taker sell NO@NO-38 (YES-bid 62), p=58, q=10 ->
        pool -1000; ys.YES -10, ys.cash_available +580;
        ns.NO -10, ns.cash_available +420.
        """
        ys = self.accounts[yes_sell.agent_id]
        ns = self.accounts[no_sell.agent_id]
        ys.add_locked_shares(market.id, Token.YES, -q)
        ys.add_position(market.id, Token.YES, -q)
        ys.cash_available += p * q
        ns.add_locked_shares(market.id, Token.NO, -q)
        ns.add_position(market.id, Token.NO, -q)
        ns.cash_available += (100 - p) * q
        market.collateral_pool -= 100 * q
        return -100 * q

    # --- helpers ---

    def total_cash(self) -> int:
        return sum(a.cash_available + a.cash_locked for a in self.accounts.values())
