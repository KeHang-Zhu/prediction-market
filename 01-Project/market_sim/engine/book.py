"""Single order book in YES-price coordinates — dumb storage + priority iteration.

No matching logic and no money here: that lives in the exchange (so it can call
the ledger). The book only stores resting orders and answers "what's the best
crossing maker for this taker?" respecting price-time priority.
"""

from __future__ import annotations

from collections import deque

from sortedcontainers import SortedDict

from .models import BookSide, Order


class OwnerBook:
    """One book per market. Keyed by ``book_price`` (YES coords)."""

    def __init__(self, market_id: str) -> None:
        self.market_id = market_id
        self._bids: SortedDict[int, deque[Order]] = SortedDict()  # price -> FIFO
        self._asks: SortedDict[int, deque[Order]] = SortedDict()
        self._by_id: dict[int, Order] = {}

    # --- mutation ---

    def add_resting(self, order: Order) -> None:
        side_map = self._bids if order.book_side is BookSide.BID else self._asks
        level = side_map.get(order.book_price)
        if level is None:
            level = deque()
            side_map[order.book_price] = level
        level.append(order)
        self._by_id[order.order_id] = order

    def remove(self, order_id: int) -> Order | None:
        order = self._by_id.pop(order_id, None)
        if order is None:
            return None
        side_map = self._bids if order.book_side is BookSide.BID else self._asks
        level = side_map.get(order.book_price)
        if level is not None:
            try:
                level.remove(order)
            except ValueError:
                pass
            if not level:
                del side_map[order.book_price]
        return order

    # --- queries ---

    def get(self, order_id: int) -> Order | None:
        return self._by_id.get(order_id)

    def best_bid(self) -> int | None:
        return self._bids.peekitem(-1)[0] if self._bids else None

    def best_ask(self) -> int | None:
        return self._asks.peekitem(0)[0] if self._asks else None

    def best_opposite_crossing(self, taker: Order, skip_agent: str | None = None) -> Order | None:
        """Return the highest-priority resting maker that crosses ``taker``.

        A taker BID crosses asks with ``ask_price <= taker.book_price`` (lowest
        ask first); a taker ASK crosses bids with ``bid_price >= taker.book_price``
        (highest bid first). Within a price level, FIFO (== seq_id order). If
        ``skip_agent`` is set, the taker's own resting orders are skipped
        (self-trade prevention).
        """
        if taker.book_side is BookSide.BID:
            if not self._asks:
                return None
            best_price, _ = self._asks.peekitem(0)
            if best_price > taker.book_price:
                return None
            # iterate ascending ask prices up to the taker limit
            for price in self._asks.irange(maximum=taker.book_price):
                level = self._asks[price]
                for maker in level:
                    if skip_agent is not None and maker.agent_id == skip_agent:
                        continue
                    return maker
            return None
        else:  # taker is ASK
            if not self._bids:
                return None
            best_price, _ = self._bids.peekitem(-1)
            if best_price < taker.book_price:
                return None
            for price in self._bids.irange(minimum=taker.book_price, reverse=True):
                level = self._bids[price]
                for maker in level:
                    if skip_agent is not None and maker.agent_id == skip_agent:
                        continue
                    return maker
            return None

    def crossing_qty(self, taker: Order, skip_agent: str | None = None) -> int:
        """Total resting maker quantity that crosses ``taker`` at/within its limit.

        Same iteration as ``best_opposite_crossing`` but SUMS ``remaining`` instead of
        returning the first maker — used for the FOK fill-or-kill pre-check. ``skip_agent``
        MUST match the one ``_match`` uses (self-trade prevention), or the count and the
        actual fill disagree.
        """
        total = 0
        if taker.book_side is BookSide.BID:
            for price in self._asks.irange(maximum=taker.book_price):
                for maker in self._asks[price]:
                    if skip_agent is not None and maker.agent_id == skip_agent:
                        continue
                    total += maker.remaining
        else:
            for price in self._bids.irange(minimum=taker.book_price, reverse=True):
                for maker in self._bids[price]:
                    if skip_agent is not None and maker.agent_id == skip_agent:
                        continue
                    total += maker.remaining
        return total

    def aggregated(self, depth: int | None = None) -> dict[str, list[list[int]]]:
        """Top-of-book ladder aggregated by price level (best-first).

        Returns YES-coords levels: ``bids`` descending, ``asks`` ascending,
        each ``[price, total_qty]``.
        """
        bids: list[list[int]] = []
        for price in reversed(self._bids):
            qty = sum(o.remaining for o in self._bids[price])
            if qty > 0:
                bids.append([price, qty])
            if depth is not None and len(bids) >= depth:
                break
        asks: list[list[int]] = []
        for price in self._asks:
            qty = sum(o.remaining for o in self._asks[price])
            if qty > 0:
                asks.append([price, qty])
            if depth is not None and len(asks) >= depth:
                break
        return {"bids": bids, "asks": asks}

    def all_orders(self) -> list[Order]:
        return list(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)
