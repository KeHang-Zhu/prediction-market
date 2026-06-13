"""Engine correctness: ledger oracles, scenario invariants, and property tests."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from market_sim.engine.exchange import Exchange
from market_sim.engine.ledger import Ledger
from market_sim.engine.models import (
    Account,
    Market,
    Order,
    Side,
    Token,
    derive_book_price,
    derive_book_side,
    BookSide,
)
from market_sim.engine.settlement import resolve_market


# --------------------------------------------------------------------------- helpers

def mk_market(mid="M", true_prob=0.5, resolve_round=1000):
    return Market(id=mid, question="q?", true_prob=true_prob, resolve_round=resolve_round)


def mk_order(oid, agent, token, side, limit, qty, filled=0):
    return Order(order_id=oid, agent_id=agent, market_id="M", token=token, side=side,
                 limit_price=limit, qty=qty, filled_qty=filled)


def two_accounts(cash=10_000):
    return {
        "A": Account("A", cash_available=cash),
        "B": Account("B", cash_available=cash),
    }


# --------------------------------------------------------------------------- ledger oracles

def test_settle_transfer_yes_overpay_refund():
    accts = two_accounts(0)
    # buyer (taker) bid 62, locked 744; seller (maker) holds 12 YES, locked
    accts["B"].cash_locked = 744
    accts["A"].add_position("M", Token.YES, 12)
    accts["A"].add_locked_shares("M", Token.YES, 12)
    led = Ledger(accts, total0=744)
    m = mk_market()
    buy = mk_order(1, "B", Token.YES, Side.BUY, 62, 12)
    sell = mk_order(2, "A", Token.YES, Side.SELL, 58, 12)
    led.settle_transfer(m, Token.YES, buy, sell, p=58, q=12)
    assert accts["B"].cash_locked == 0
    assert accts["B"].cash_available == 48          # (62-58)*12 refunded
    assert accts["B"].position("M", Token.YES) == 12
    assert accts["A"].cash_available == 696          # 58*12
    assert accts["A"].position("M", Token.YES) == 0
    assert accts["A"].locked_shares("M", Token.YES) == 0


def test_settle_transfer_no():
    accts = two_accounts(0)
    accts["B"].cash_locked = 450                     # buy NO @45 x10
    accts["A"].add_position("M", Token.NO, 10)
    accts["A"].add_locked_shares("M", Token.NO, 10)
    led = Ledger(accts, total0=450)
    m = mk_market()
    buy = mk_order(1, "B", Token.NO, Side.BUY, 45, 10)
    sell = mk_order(2, "A", Token.NO, Side.SELL, 40, 10)
    led.settle_transfer(m, Token.NO, buy, sell, p=55, q=10)  # maker YES-ask price 55
    assert accts["B"].cash_locked == 0
    assert accts["B"].cash_available == 0            # NO-price paid 45 == limit
    assert accts["B"].position("M", Token.NO) == 10
    assert accts["A"].cash_available == 450
    assert accts["A"].position("M", Token.NO) == 0


def test_settle_mint():
    accts = two_accounts(0)
    accts["A"].cash_locked = 450                     # buy YES @45 x10
    accts["B"].cash_locked = 600                     # buy NO  @60 x10
    led = Ledger(accts, total0=1050)
    m = mk_market()
    yes_buy = mk_order(1, "A", Token.YES, Side.BUY, 45, 10)
    no_buy = mk_order(2, "B", Token.NO, Side.BUY, 60, 10)
    delta = led.settle_mint(m, yes_buy, no_buy, p=40, q=10)
    assert delta == 1000 and m.collateral_pool == 1000
    assert accts["A"].cash_locked == 0 and accts["A"].cash_available == 50  # (45-40)*10
    assert accts["A"].position("M", Token.YES) == 10
    assert accts["B"].cash_locked == 0 and accts["B"].cash_available == 0   # (60-60)*10
    assert accts["B"].position("M", Token.NO) == 10
    # cash + pool conserved
    assert led.total_cash() + m.collateral_pool == 1050


def test_settle_merge_split_is_exact():
    accts = two_accounts(0)
    accts["A"].add_position("M", Token.YES, 10)
    accts["A"].add_locked_shares("M", Token.YES, 10)
    accts["B"].add_position("M", Token.NO, 10)
    accts["B"].add_locked_shares("M", Token.NO, 10)
    m = mk_market()
    m.collateral_pool = 1000
    led = Ledger(accts, total0=0)
    yes_sell = mk_order(1, "A", Token.YES, Side.SELL, 58, 10)
    no_sell = mk_order(2, "B", Token.NO, Side.SELL, 38, 10)
    delta = led.settle_merge(m, yes_sell, no_sell, p=58, q=10)
    assert delta == -1000 and m.collateral_pool == 0
    assert accts["A"].cash_available == 580          # p * q
    assert accts["B"].cash_available == 420          # (100-p) * q
    assert accts["A"].cash_available + accts["B"].cash_available == 1000  # exact split


# --------------------------------------------------------------------------- scenarios

def build_exchange(cash=100_000, **kw):
    markets = {"M": mk_market()}
    accts = {a: Account(a, cash_available=cash) for a in ("alice", "bob", "carol")}
    return Exchange(markets, accts, **kw)


def test_mint_then_transfer_then_merge_keeps_invariants():
    ex = build_exchange()
    ex.check_invariants()
    # alice buy YES @60 rests; bob buy NO @45 takes -> mint at p=60
    ex.place_order("alice", "M", Token.YES, Side.BUY, 60, 10, 1)
    r = ex.place_order("bob", "M", Token.NO, Side.BUY, 45, 10, 1)
    assert any(f.settle.value == "mint" for f in r.fills)
    ex.check_invariants()
    assert ex.markets["M"].collateral_pool == 1000
    # alice now sells 4 YES to carol (transfer_yes)
    ex.place_order("carol", "M", Token.YES, Side.BUY, 70, 4, 2)  # rests as bid 70
    ex.place_order("alice", "M", Token.YES, Side.SELL, 55, 4, 2)  # taker ask hits bid 70 -> p=70
    ex.check_invariants()
    assert ex.ledger.accounts["carol"].position("M", Token.YES) == 4
    assert ex.ledger.accounts["alice"].position("M", Token.YES) == 6
    # alice (6 YES) merges with bob (10 NO): alice sell YES @40 rests, bob sell NO @55 takes
    ex.place_order("alice", "M", Token.YES, Side.SELL, 40, 6, 3)
    rr = ex.place_order("bob", "M", Token.NO, Side.SELL, 55, 6, 3)
    assert any(f.settle.value == "merge" for f in rr.fills)
    ex.check_invariants()


def test_over_budget_rejected_nothing_locked():
    ex = build_exchange(cash=500)
    before = ex.ledger.accounts["alice"].cash_available
    r = ex.place_order("alice", "M", Token.YES, Side.BUY, 60, 100, 1)  # needs 6000 > 500
    assert r.status == "rejected" and r.reason == "insufficient_cash"
    assert ex.ledger.accounts["alice"].cash_available == before
    assert ex.ledger.accounts["alice"].cash_locked == 0
    ex.check_invariants()


def test_oversell_rejected():
    ex = build_exchange()
    r = ex.place_order("alice", "M", Token.YES, Side.SELL, 40, 5, 1)  # no shares
    assert r.status == "rejected" and r.reason == "insufficient_shares"
    ex.check_invariants()


def test_cancel_unlocks_fully():
    ex = build_exchange()
    r = ex.place_order("alice", "M", Token.YES, Side.BUY, 55, 10, 1)
    assert ex.ledger.accounts["alice"].cash_locked == 550
    c = ex.cancel_order("alice", r.order_id)
    assert c.status == "cancelled"
    assert ex.ledger.accounts["alice"].cash_locked == 0
    assert ex.ledger.accounts["alice"].cash_available == 100_000
    ex.check_invariants()


def test_resolution_unlocks_all_and_pays_winner():
    ex = build_exchange()
    # mint 10 YES to alice / 10 NO to bob
    ex.place_order("alice", "M", Token.YES, Side.BUY, 60, 10, 1)
    ex.place_order("bob", "M", Token.NO, Side.BUY, 45, 10, 1)
    # leave some resting orders that must be unlocked on resolution
    ex.place_order("carol", "M", Token.YES, Side.BUY, 30, 5, 2)
    ex.place_order("alice", "M", Token.YES, Side.SELL, 80, 4, 2)
    ex.check_invariants()
    info = resolve_market(ex, "M", outcome=1, round_no=3)
    assert info["outcome"] == 1
    # YES wins: alice's 10 YES -> 1000
    assert any(p["agent"] == "alice" and p["amount"] == 1000 for p in info["payouts"])
    # everything unlocked, pool drained
    assert ex.markets["M"].collateral_pool == 0
    assert ex.ledger.accounts["carol"].cash_locked == 0
    assert ex.ledger.accounts["alice"].cash_locked == 0
    ex.check_invariants()
    # total cash back to initial 300k (no cash destroyed)
    assert ex.ledger.total_cash() == 300_000


# --------------------------------------------------------------------------- properties

@given(n=st.integers(min_value=1, max_value=99))
def test_mirror_round_trip(n):
    # NO order at NO-price n -> YES book price 100-n -> invert back to n
    bp = derive_book_price(Token.NO, n)
    assert bp == 100 - n
    assert 100 - bp == n                       # inverse transform recovers n
    assert derive_book_price(Token.YES, n) == n  # YES is identity
    # a NO buy is a YES ASK; a NO sell is a YES BID
    assert derive_book_side(Token.NO, Side.BUY) is BookSide.ASK
    assert derive_book_side(Token.NO, Side.SELL) is BookSide.BID


order_action = st.fixed_dictionaries({
    "agent": st.sampled_from(["alice", "bob", "carol"]),
    "token": st.sampled_from([Token.YES, Token.NO]),
    "side": st.sampled_from([Side.BUY, Side.SELL]),
    "price": st.integers(min_value=1, max_value=99),
    "qty": st.integers(min_value=1, max_value=30),
})


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(actions=st.lists(order_action, min_size=1, max_size=60),
       self_trade=st.booleans())
def test_random_sequence_preserves_invariants(actions, self_trade):
    """Place a random stream of (possibly invalid) orders; the exchange must
    reject the invalid ones cleanly and keep all three invariants after every
    accepted action. Catches lock leakage, refund bugs, mirror errors."""
    ex = build_exchange(cash=1_000_000, allow_self_trade=self_trade)
    placed_ids: list[tuple[str, int]] = []
    for i, a in enumerate(actions):
        r = ex.place_order(a["agent"], "M", a["token"], a["side"], a["price"], a["qty"], i)
        if r.status == "accepted" and r.resting_qty > 0:
            placed_ids.append((a["agent"], r.order_id))
        ex.check_invariants()
        # no negative balances ever
        for acc in ex.ledger.accounts.values():
            assert acc.cash_available >= 0 and acc.cash_locked >= 0
            for row in acc.positions.values():
                assert all(v >= 0 for v in row.values())
    # cancel everything; all locks must return to zero
    for agent, oid in placed_ids:
        ex.cancel_order(agent, oid)
        ex.check_invariants()
    assert sum(acc.cash_locked for acc in ex.ledger.accounts.values()) == 0


@settings(max_examples=100, deadline=None)
@given(p=st.integers(2, 98), q=st.integers(1, 20), p2=st.integers(2, 98))
def test_mint_then_merge_round_trip_conserves(p, q, p2):
    """Mint a YES/NO pair into the pool, then merge it back out. Total cash and
    pool return to start regardless of the two prices (the difference is P&L
    between the parties)."""
    ex = build_exchange(cash=1_000_000)
    total0 = ex.ledger.total_cash()
    # mint: alice buy YES @ p (rests), bob buy NO @ (100-p) takes -> p
    ex.place_order("alice", "M", Token.YES, Side.BUY, p, q, 1)
    ex.place_order("bob", "M", Token.NO, Side.BUY, 100 - p, q, 1)
    ex.check_invariants()
    assert ex.markets["M"].collateral_pool == 100 * q
    # merge: alice sell YES @ p2 (rests as ask), bob sell NO @ (100-p2) takes -> p2
    ex.place_order("alice", "M", Token.YES, Side.SELL, p2, q, 2)
    ex.place_order("bob", "M", Token.NO, Side.SELL, 100 - p2, q, 2)
    ex.check_invariants()
    assert ex.markets["M"].collateral_pool == 0
    assert ex.ledger.total_cash() == total0
