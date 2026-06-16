"""Exchange — the API facade. Owns books + ledger + markets.

Responsibilities: validate & place orders (locking in TRUE coords), run the match
loop (classify each cross by the pair of TRUE intents, settle via the ledger,
release locks), cancel orders, answer read-only queries, and assert the three
conservation invariants.
"""

from __future__ import annotations

from .book import OwnerBook
from .ledger import Ledger
from .models import (
    Account,
    BookSide,
    CancelResult,
    Fill,
    Market,
    MarketStatus,
    Order,
    OrderStatus,
    PlaceResult,
    SettleType,
    Side,
    TimeInForce,
    Token,
    Trade,
)


class InvariantError(AssertionError):
    pass


class Exchange:
    def __init__(
        self,
        markets: dict[str, Market],
        accounts: dict[str, Account],
        *,
        allow_self_trade: bool = True,
    ) -> None:
        self.markets = markets
        self.ledger = Ledger(accounts, total0=sum(a.cash_available + a.cash_locked for a in accounts.values()))
        self.books: dict[str, OwnerBook] = {m: OwnerBook(m) for m in markets}
        self.allow_self_trade = allow_self_trade
        self._seq = 0
        self._oid = 0
        self._tid = 0
        self.last_price: dict[str, int | None] = {m: None for m in markets}
        self.volume: dict[str, int] = {m: 0 for m in markets}
        self.trades: dict[str, list[Trade]] = {m: [] for m in markets}

    # ------------------------------------------------------------------ place

    def place_order(
        self, agent_id: str, market_id: str, token: Token, side: Side, limit_price: int, qty: int, round_no: int,
        *, tif: TimeInForce = TimeInForce.GTC, post_only: bool = False, expire_round: int | None = None,
    ) -> PlaceResult:
        market = self.markets.get(market_id)
        if market is None:
            return PlaceResult("rejected", None, reason="unknown_market")
        if market.status is not MarketStatus.OPEN:
            return PlaceResult("rejected", None, reason="market_closed")
        if not (1 <= limit_price <= 99):
            return PlaceResult("rejected", None, reason="price_out_of_range")
        if qty < 1:
            return PlaceResult("rejected", None, reason="bad_qty")
        acct = self.ledger.accounts.get(agent_id)
        if acct is None:
            return PlaceResult("rejected", None, reason="unknown_agent")

        # advanced-type validation (reject before locking)
        if post_only and tif in (TimeInForce.FOK, TimeInForce.FAK):
            return PlaceResult("rejected", None, reason="post_only_incompatible_tif")
        if tif is TimeInForce.GTD and (expire_round is None or expire_round < round_no):
            return PlaceResult("rejected", None, reason="expire_round_in_past")

        # budget / inventory check — reject with NOTHING locked (clean D3 condition)
        if side is Side.BUY:
            need = limit_price * qty
            if need > acct.cash_available:
                return PlaceResult("rejected", None, reason="insufficient_cash")
        else:
            if qty > acct.available_shares(market_id, token):
                return PlaceResult("rejected", None, reason="insufficient_shares")

        skip = None if self.allow_self_trade else agent_id  # SAME skip _match uses
        book = self.books[market_id]
        # post-only + FOK pre-checks use a probe order (book coords only) so a rejection
        # NEVER consumes an order id or lock — keeping the plain GTC path byte-identical.
        # crossing_qty/best_opposite_crossing MUST use this same `skip` as _match, else a
        # self-order-heavy book would pass the FOK check then under-fill and silently rest.
        if post_only or tif is TimeInForce.FOK:
            probe = Order(order_id=0, agent_id=agent_id, market_id=market_id, token=token,
                          side=side, limit_price=limit_price, qty=qty)
            if post_only and book.best_opposite_crossing(probe, skip_agent=skip) is not None:
                return PlaceResult("rejected", None, reason="post_only_would_cross")
            if tif is TimeInForce.FOK and book.crossing_qty(probe, skip_agent=skip) < qty:
                return PlaceResult("rejected", None, reason="fok_unfillable")

        # accept: assign ids, lock, then match
        self._oid += 1
        self._seq += 1
        order = Order(
            order_id=self._oid,
            agent_id=agent_id,
            market_id=market_id,
            token=token,
            side=side,
            limit_price=limit_price,
            qty=qty,
            seq_id=self._seq,
            round_placed=round_no,
            tif=tif,
            post_only=post_only,
            expire_round=expire_round if tif is TimeInForce.GTD else None,
        )
        if side is Side.BUY:
            self.ledger.lock_buy(agent_id, limit_price * qty)
        else:
            self.ledger.lock_sell(agent_id, market_id, token, qty)

        # post-only: verified above it won't cross -> rest without matching.
        if post_only:
            order.status = OrderStatus.OPEN
            book.add_resting(order)
            return PlaceResult("accepted", order.order_id, fills=[], filled_qty=0,
                               resting_qty=order.remaining)

        fills = self._match(order, market)

        # FAK (IOC) / FOK-already-full: kill any remainder instead of resting. FOK is
        # guaranteed full by the pre-check, so its remainder is 0; FAK unlocks the rest
        # exactly like cancel_order.
        if tif in (TimeInForce.FOK, TimeInForce.FAK):
            rem = order.remaining
            if rem > 0:
                if side is Side.BUY:
                    self.ledger.unlock_buy(agent_id, limit_price * rem)
                else:
                    self.ledger.unlock_sell(agent_id, market_id, token, rem)
            order.status = OrderStatus.FILLED if order.filled_qty == qty else OrderStatus.CANCELLED
            return PlaceResult("accepted", order.order_id, fills=fills,
                               filled_qty=order.filled_qty, resting_qty=0)

        # GTC / GTD: rest any remainder (GTD additionally carries expire_round).
        if order.remaining > 0:
            order.status = OrderStatus.PARTIAL if order.filled_qty > 0 else OrderStatus.OPEN
            book.add_resting(order)
        else:
            order.status = OrderStatus.FILLED

        return PlaceResult(
            status="accepted",
            order_id=order.order_id,
            fills=fills,
            filled_qty=order.filled_qty,
            resting_qty=order.remaining,
        )

    def _match(self, taker: Order, market: Market) -> list[Fill]:
        book = self.books[market.id]
        skip = None if self.allow_self_trade else taker.agent_id
        fills: list[Fill] = []
        while taker.remaining > 0:
            maker = book.best_opposite_crossing(taker, skip_agent=skip)
            if maker is None:
                break
            q = min(taker.remaining, maker.remaining)
            p = maker.book_price
            fill = self._settle(taker, maker, p, q, market)
            taker.filled_qty += q
            maker.filled_qty += q
            self._tid += 1
            self.trades[market.id].append(
                Trade(self._tid, market.id, p, q, fill.settle, taker.agent_id, maker.agent_id,
                      taker.round_placed, self._seq)
            )
            self.last_price[market.id] = p
            self.volume[market.id] += q
            fills.append(fill)
            if maker.remaining == 0:
                maker.status = OrderStatus.FILLED
                book.remove(maker.order_id)
        return fills

    def _settle(self, taker: Order, maker: Order, p: int, q: int, market: Market) -> Fill:
        """Classify the cross by TRUE intents and apply the matching settle.

        In book coords one party is the BID and the other the ASK. A BID is
        always buy-YES or sell-NO; an ASK is always sell-YES or buy-NO.
        """
        if taker.book_side is BookSide.BID:
            bid_order, ask_order = taker, maker
        else:
            bid_order, ask_order = maker, taker

        bid_is_buy_yes = bid_order.token is Token.YES and bid_order.side is Side.BUY
        ask_is_sell_yes = ask_order.token is Token.YES and ask_order.side is Side.SELL

        if bid_is_buy_yes and ask_is_sell_yes:
            self.ledger.settle_transfer(market, Token.YES, bid_order, ask_order, p, q)
            settle, roles, pool_delta = SettleType.TRANSFER_YES, {
                "buyer": bid_order.agent_id, "seller": ask_order.agent_id, "token": "YES"}, 0
        elif bid_is_buy_yes and not ask_is_sell_yes:  # ask is buy NO -> mint
            pool_delta = self.ledger.settle_mint(market, bid_order, ask_order, p, q)
            settle, roles = SettleType.MINT, {
                "yes_buyer": bid_order.agent_id, "no_buyer": ask_order.agent_id}
        elif (not bid_is_buy_yes) and ask_is_sell_yes:  # bid is sell NO -> merge
            pool_delta = self.ledger.settle_merge(market, ask_order, bid_order, p, q)
            settle, roles = SettleType.MERGE, {
                "yes_seller": ask_order.agent_id, "no_seller": bid_order.agent_id}
        else:  # bid is sell NO, ask is buy NO -> transfer NO
            self.ledger.settle_transfer(market, Token.NO, ask_order, bid_order, p, q)
            settle, roles, pool_delta = SettleType.TRANSFER_NO, {
                "buyer": ask_order.agent_id, "seller": bid_order.agent_id, "token": "NO"}, 0

        return Fill(
            price=p, qty=q, settle=settle, market_id=market.id,
            taker_id=taker.agent_id, maker_id=maker.agent_id, maker_order_id=maker.order_id,
            pool_delta=pool_delta, roles=roles,
        )

    # ----------------------------------------------------------------- cancel

    def cancel_order(self, agent_id: str, order_id: int) -> CancelResult:
        for book in self.books.values():
            order = book.get(order_id)
            if order is None:
                continue
            if order.agent_id != agent_id:
                return CancelResult("not_owner", order_id, reason="not_owner")
            rem = order.remaining
            if order.side is Side.BUY:
                self.ledger.unlock_buy(agent_id, order.limit_price * rem)
            else:
                self.ledger.unlock_sell(agent_id, order.market_id, order.token, rem)
            order.status = OrderStatus.CANCELLED
            book.remove(order_id)
            return CancelResult("cancelled", order_id)
        return CancelResult("not_found", order_id, reason="not_found")

    def expire_due(self, round_no: int) -> list[tuple[str, int]]:
        """Cancel resting GTD orders whose validity has passed (``expire_round < round_no``),
        unlocking exactly like ``cancel_order``. Returns ``(market_id, order_id)`` pairs in
        deterministic (market, order_id) order. No GTD orders -> empty -> a pure no-op, so
        scripted runs (which never set ``expire_round``) emit nothing and stay byte-exact."""
        expired: list[tuple[str, int]] = []
        for mid in sorted(self.books):
            book = self.books[mid]
            due = [o for o in sorted(book.all_orders(), key=lambda o: o.order_id)
                   if o.expire_round is not None and o.expire_round < round_no]
            for order in due:
                rem = order.remaining
                if order.side is Side.BUY:
                    self.ledger.unlock_buy(order.agent_id, order.limit_price * rem)
                else:
                    self.ledger.unlock_sell(order.agent_id, mid, order.token, rem)
                order.status = OrderStatus.CANCELLED
                book.remove(order.order_id)
                expired.append((mid, order.order_id))
        return expired

    # ------------------------------------------------- accounts & markets (open scenario)

    def create_account(self, account_id: str, funder_id: str, initial_cash: int) -> Account:
        """Create a new PASSIVE wallet ``account_id`` funded FROM ``funder_id``'s available
        cash. Money is moved, never created, so INV-A and ``ledger.total0`` are unchanged.
        The caller validates: ``account_id`` is new, ``funder_id`` exists, ``initial_cash``
        is in [0, funder.cash_available]. The wallet starts empty (no positions/orders);
        it only holds and forwards cash (it does not trade on its own)."""
        acct = Account(account_id, cash_available=0)
        self.ledger.accounts[account_id] = acct
        if initial_cash:
            self.ledger.transfer(funder_id, account_id, initial_cash)
        return acct

    def create_market(
        self, market_id: str, question: str, resolve_round: int, true_prob: float, outcome: int
    ) -> Market:
        """Open a brand-new market mid-run. It starts empty (pool=0, no shares, empty book),
        so it preserves every invariant. The latent ``true_prob``/``outcome`` are sampled by
        the runner (so the creator never sees them) and passed in here. The market joins the
        normal machinery — signals, snapshots, resolution — from the next round automatically."""
        m = Market(id=market_id, question=question, true_prob=true_prob, resolve_round=resolve_round)
        m.outcome = outcome
        self.markets[market_id] = m
        self.books[market_id] = OwnerBook(market_id)
        self.last_price[market_id] = None
        self.volume[market_id] = 0
        self.trades[market_id] = []
        return m

    # ------------------------------------------------------------------ reads

    def get_book(self, market_id: str, depth: int | None = None) -> dict[str, list[list[int]]]:
        return self.books[market_id].aggregated(depth)

    def best_bid(self, market_id: str) -> int | None:
        return self.books[market_id].best_bid()

    def best_ask(self, market_id: str) -> int | None:
        return self.books[market_id].best_ask()

    def mid(self, market_id: str) -> int:
        bb, ba = self.best_bid(market_id), self.best_ask(market_id)
        if bb is not None and ba is not None:
            return (bb + ba) // 2
        if self.last_price[market_id] is not None:
            return self.last_price[market_id]
        return 50

    def get_portfolio(self, agent_id: str) -> dict:
        a = self.ledger.accounts[agent_id]
        open_orders = []
        for book in self.books.values():
            for o in book.all_orders():
                if o.agent_id == agent_id:
                    open_orders.append({
                        "order_id": o.order_id, "market": o.market_id, "token": o.token.value,
                        "side": o.side.value, "price": o.limit_price, "qty": o.remaining,
                    })
        open_orders.sort(key=lambda d: d["order_id"])
        return {
            "agent_id": agent_id,
            "cash_available": a.cash_available,
            "cash_locked": a.cash_locked,
            "positions": {m: dict(row) for m, row in sorted(a.positions.items())},
            "open_orders": open_orders,
        }

    def get_tape(self, market_id: str, last: int = 20) -> list[dict]:
        trades = self.trades[market_id][-last:]
        return [{
            "trade_id": t.trade_id, "price": t.price, "qty": t.qty,
            "settle": t.settle_type.value, "taker": t.taker_id, "maker": t.maker_id,
            "round": t.round,
        } for t in trades]

    # -------------------------------------------------------------- invariants

    def check_invariants(self) -> None:
        # INV-A: total cash + all pools == initial total
        pools = sum(m.collateral_pool for m in self.markets.values())
        total = self.ledger.total_cash() + pools
        if total != self.ledger.total0:
            raise InvariantError(
                f"INV-A cash conservation: total={total} expected={self.ledger.total0} (pools={pools})"
            )

        for mid, market in self.markets.items():
            yes_out = sum(a.position(mid, Token.YES) for a in self.ledger.accounts.values())
            no_out = sum(a.position(mid, Token.NO) for a in self.ledger.accounts.values())
            if market.collateral_pool % 100 != 0:
                raise InvariantError(f"INV-B pool not multiple of 100: {mid} pool={market.collateral_pool}")
            if not (yes_out == no_out == market.collateral_pool // 100):
                raise InvariantError(
                    f"INV-B shares/pool: {mid} yes={yes_out} no={no_out} pool/100={market.collateral_pool // 100}"
                )

        # INV-C: locked cash == sum of resting buys' true_limit*remaining;
        #        locked shares == sum of resting sells' remaining (per market/token)
        exp_cash_locked = 0
        exp_shares: dict[tuple[str, str], int] = {}
        for book in self.books.values():
            for o in book.all_orders():
                if o.side is Side.BUY:
                    exp_cash_locked += o.limit_price * o.remaining
                else:
                    exp_shares[(o.market_id, o.token.value)] = (
                        exp_shares.get((o.market_id, o.token.value), 0) + o.remaining
                    )
        act_cash_locked = sum(a.cash_locked for a in self.ledger.accounts.values())
        if act_cash_locked != exp_cash_locked:
            raise InvariantError(
                f"INV-C cash_locked: actual={act_cash_locked} expected={exp_cash_locked}"
            )
        for a in self.ledger.accounts.values():
            for mid, row in a.shares_locked.items():
                for tok, qty in row.items():
                    if qty < 0:
                        raise InvariantError(f"INV-C negative shares_locked: {a.agent_id} {mid} {tok}={qty}")
        # cross-check aggregate locked shares
        agg_shares: dict[tuple[str, str], int] = {}
        for a in self.ledger.accounts.values():
            for mid, row in a.shares_locked.items():
                for tok, qty in row.items():
                    if qty:
                        agg_shares[(mid, tok)] = agg_shares.get((mid, tok), 0) + qty
        if agg_shares != exp_shares:
            raise InvariantError(f"INV-C shares_locked: actual={agg_shares} expected={exp_shares}")
