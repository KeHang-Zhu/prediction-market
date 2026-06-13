"""Core engine data models (enums + mutable dataclasses).

Design note: internal hot-path engine state (Order, Account, Market, Trade) uses
plain dataclasses for speed and easy mutation. Boundary schemas that need
validation/serialization (Config, Event payloads, WS protocol) use pydantic and
live in their own modules. Everything here is integer-valued (cents / shares).

The single internal order book lives in YES-price coordinates. Every order stores
both its TRUE intent (``token``/``side``/``limit_price`` in the token's own
coordinates — NO-price for NO orders) and its derived BOOK coordinates
(``book_side``/``book_price`` in YES space). The ledger settles on TRUE coords;
the book matches on BOOK coords.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Token(str, Enum):
    YES = "YES"
    NO = "NO"

    @property
    def other(self) -> "Token":
        return Token.NO if self is Token.YES else Token.YES


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class BookSide(str, Enum):
    BID = "bid"  # someone willing to acquire YES exposure at <= price
    ASK = "ask"  # someone willing to shed YES exposure at >= price


class OrderStatus(str, Enum):
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"


class SettleType(str, Enum):
    TRANSFER_YES = "transfer_yes"
    TRANSFER_NO = "transfer_no"
    MINT = "mint"
    MERGE = "merge"


class MarketStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


# --- coordinate transforms (the heart of the single-book mirror trick) ---

def derive_book_side(token: Token, side: Side) -> BookSide:
    """buy YES / sell NO -> BID ; sell YES / buy NO -> ASK."""
    if (token is Token.YES and side is Side.BUY) or (token is Token.NO and side is Side.SELL):
        return BookSide.BID
    return BookSide.ASK


def derive_book_price(token: Token, limit_price: int) -> int:
    """YES order keeps its price; NO order mirrors to ``100 - n``."""
    return limit_price if token is Token.YES else 100 - limit_price


@dataclass
class Order:
    order_id: int
    agent_id: str
    market_id: str
    token: Token            # TRUE token
    side: Side              # TRUE side
    limit_price: int        # TRUE coords (NO-price for NO orders), 1..99
    qty: int                # original quantity
    filled_qty: int = 0
    status: OrderStatus = OrderStatus.OPEN
    seq_id: int = 0         # monotonic acceptance counter -> time priority
    round_placed: int = 0

    @property
    def remaining(self) -> int:
        return self.qty - self.filled_qty

    @property
    def book_side(self) -> BookSide:
        return derive_book_side(self.token, self.side)

    @property
    def book_price(self) -> int:
        return derive_book_price(self.token, self.limit_price)


@dataclass
class Account:
    agent_id: str
    cash_available: int
    cash_locked: int = 0
    # market_id -> {"YES": q, "NO": q}; always >= 0
    positions: dict[str, dict[str, int]] = field(default_factory=dict)
    # market_id -> {"YES": q, "NO": q}; shares reserved by resting sell orders
    shares_locked: dict[str, dict[str, int]] = field(default_factory=dict)

    def _pos_row(self, market_id: str) -> dict[str, int]:
        return self.positions.setdefault(market_id, {"YES": 0, "NO": 0})

    def _lock_row(self, market_id: str) -> dict[str, int]:
        return self.shares_locked.setdefault(market_id, {"YES": 0, "NO": 0})

    def position(self, market_id: str, token: Token) -> int:
        return self.positions.get(market_id, {}).get(token.value, 0)

    def add_position(self, market_id: str, token: Token, delta: int) -> None:
        row = self._pos_row(market_id)
        row[token.value] += delta

    def set_position(self, market_id: str, token: Token, value: int) -> None:
        self._pos_row(market_id)[token.value] = value

    def locked_shares(self, market_id: str, token: Token) -> int:
        return self.shares_locked.get(market_id, {}).get(token.value, 0)

    def add_locked_shares(self, market_id: str, token: Token, delta: int) -> None:
        self._lock_row(market_id)[token.value] += delta

    def available_shares(self, market_id: str, token: Token) -> int:
        return self.position(market_id, token) - self.locked_shares(market_id, token)


@dataclass
class Market:
    id: str
    question: str
    true_prob: float        # config metadata; consumed only by runner rng / fundamentalist
    resolve_round: int
    status: MarketStatus = MarketStatus.OPEN
    collateral_pool: int = 0
    outcome: int | None = None       # latent, pre-sampled at init; revealed at resolution
    fixed_outcome: int | None = None  # if set, outcome is not sampled


@dataclass
class Trade:
    trade_id: int
    market_id: str
    price: int              # YES coords (maker price)
    qty: int
    settle_type: SettleType
    taker_id: str
    maker_id: str
    round: int
    seq: int


@dataclass
class Fill:
    """Result of one match, carrying everything needed to emit an event."""
    price: int
    qty: int
    settle: SettleType
    market_id: str
    taker_id: str
    maker_id: str
    maker_order_id: int
    pool_delta: int
    # semantic party roles for event payloads:
    #   transfer_*: {"buyer", "seller", "token"}
    #   mint:       {"yes_buyer", "no_buyer"}
    #   merge:      {"yes_seller", "no_seller"}
    roles: dict[str, str] = field(default_factory=dict)


@dataclass
class PlaceResult:
    status: str             # "accepted" | "rejected"
    order_id: int | None
    fills: list[Fill] = field(default_factory=list)
    reason: str | None = None
    filled_qty: int = 0
    resting_qty: int = 0


@dataclass
class CancelResult:
    status: str             # "cancelled" | "not_found" | "not_owner"
    order_id: int
    reason: str | None = None
