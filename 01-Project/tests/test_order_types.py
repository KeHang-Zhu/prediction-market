"""Advanced order types: market orders (FOK/FAK), GTD expiry, post-only.

Engine-level paths assert conservation (`check_invariants`) after every branch; the GTD
expiry path is exercised through the Runner so the new `order_expired` event + the
round-based expiry phase are covered. A scripted run is checked to emit ZERO new events
(byte-exact replay guard), and the LLM tool gating is asserted.
"""

from __future__ import annotations

from market_sim.agents.base import PlaceOrder
from market_sim.commands.agent_api import run_agent_line
from market_sim.commands.session import Session
from market_sim.engine.exchange import Exchange
from market_sim.engine.models import Account, Market, Side, TimeInForce, Token
from market_sim.runner.config import AgentConfig, Capabilities, Config, MarketConfig
from market_sim.runner.events import read_events


def _ex() -> Exchange:
    accts = {"a": Account("a", 1_000_000), "b": Account("b", 1_000_000)}
    return Exchange({"M": Market("M", "q", 0.5, 9999)}, accts)


def _rest_ask(ex: Exchange, agent="a", price=60, qty=10):
    """Rest a YES ask at `price` via a buy-NO@(100-price) (cash only, no shares needed)."""
    res = ex.place_order(agent, "M", Token.NO, Side.BUY, 100 - price, qty, 1)
    assert res.status == "accepted" and res.resting_qty == qty
    return res


# ------------------------------------------------------------------- FOK / FAK

def test_fok_fully_fills():
    ex = _ex(); _rest_ask(ex, price=60, qty=10)
    res = ex.place_order("b", "M", Token.YES, Side.BUY, 60, 10, 2, tif=TimeInForce.FOK)
    assert res.status == "accepted" and res.filled_qty == 10 and res.resting_qty == 0
    assert ex.ledger.accounts["b"].position("M", Token.YES) == 10
    assert ex.ledger.accounts["b"].cash_locked == 0
    ex.check_invariants()


def test_fok_rejects_when_thin_locks_nothing():
    ex = _ex(); _rest_ask(ex, price=60, qty=10)
    before = ex.ledger.accounts["b"].cash_available
    res = ex.place_order("b", "M", Token.YES, Side.BUY, 60, 20, 2, tif=TimeInForce.FOK)
    assert res.status == "rejected" and res.reason == "fok_unfillable"
    assert ex.ledger.accounts["b"].cash_available == before  # nothing locked
    assert ex.ledger.accounts["b"].cash_locked == 0
    ex.check_invariants()


def test_fak_partial_kills_remainder():
    ex = _ex(); _rest_ask(ex, price=60, qty=10)
    res = ex.place_order("b", "M", Token.YES, Side.BUY, 60, 15, 2, tif=TimeInForce.FAK)
    assert res.status == "accepted" and res.filled_qty == 10 and res.resting_qty == 0
    assert ex.ledger.accounts["b"].cash_locked == 0  # remainder unlocked, nothing rests
    assert len(ex.books["M"]) == 0 or ex.books["M"].get(res.order_id) is None
    ex.check_invariants()


def test_fak_zero_fill_when_empty_book():
    ex = _ex()
    res = ex.place_order("b", "M", Token.YES, Side.BUY, 60, 10, 2, tif=TimeInForce.FAK)
    assert res.status == "accepted" and res.filled_qty == 0 and res.resting_qty == 0
    assert ex.ledger.accounts["b"].cash_locked == 0
    ex.check_invariants()


# -------------------------------------------------------------------- post-only

def test_post_only_rejects_on_cross():
    ex = _ex(); _rest_ask(ex, price=60, qty=10)
    before = ex.ledger.accounts["b"].cash_available
    res = ex.place_order("b", "M", Token.YES, Side.BUY, 60, 10, 2, post_only=True)
    assert res.status == "rejected" and res.reason == "post_only_would_cross"
    assert ex.ledger.accounts["b"].cash_available == before
    ex.check_invariants()


def test_post_only_rests_when_no_cross():
    ex = _ex(); _rest_ask(ex, price=60, qty=10)
    res = ex.place_order("b", "M", Token.YES, Side.BUY, 55, 10, 2, post_only=True)
    assert res.status == "accepted" and res.filled_qty == 0 and res.resting_qty == 10
    assert ex.books["M"].get(res.order_id) is not None  # it rested, never matched
    ex.check_invariants()


def test_post_only_incompatible_with_market_order():
    ex = _ex()
    res = ex.place_order("b", "M", Token.YES, Side.BUY, 60, 10, 2,
                         tif=TimeInForce.FOK, post_only=True)
    assert res.status == "rejected" and res.reason == "post_only_incompatible_tif"


# ----------------------------------------------------------------- GTD (runner)

def _human_session(tmp_path) -> Session:
    s = Session(runs_dir=tmp_path)
    s.init(Config(seed=1, rounds=10, max_actions_per_agent=8,
                  markets=[MarketConfig(id="M", true_prob=0.5, resolve_round=999)],
                  agents=[AgentConfig(id="me", type="human", initial_cash=100_000)]))
    return s


def test_gtd_expires_unlocks_and_emits(tmp_path):
    s = _human_session(tmp_path); r = s.runner
    res = r.execute_now("me", PlaceOrder("M", Token.YES, Side.BUY, 40, 10,
                                         tif="GTD", expire_round=2))
    assert res.status == "accepted"
    oid = res.order_id
    assert r.exchange.ledger.accounts["me"].cash_locked == 400
    r.step(); r.step()                       # rounds 1,2: still valid (2 < r is False)
    assert r.exchange.books["M"].get(oid) is not None
    r.step()                                 # round 3: 2 < 3 -> expired
    assert r.exchange.books["M"].get(oid) is None
    assert r.exchange.ledger.accounts["me"].cash_locked == 0   # lock released
    evs = read_events(str(s.log_path))
    assert any(e["type"] == "order_expired" and e["payload"]["order_id"] == oid for e in evs)
    r.exchange.check_invariants()


def test_gtd_rejects_past_or_missing_expiry(tmp_path):
    s = _human_session(tmp_path); r = s.runner
    r.step(); r.step()                       # round_no == 2
    bad = r.execute_now("me", PlaceOrder("M", Token.YES, Side.BUY, 40, 10,
                                         tif="GTD", expire_round=1))
    assert bad.status == "rejected" and bad.reason == "expire_round_in_past"
    none = r.execute_now("me", PlaceOrder("M", Token.YES, Side.BUY, 40, 10, tif="GTD"))
    assert none.status == "rejected" and none.reason == "expire_round_in_past"


# ---------------------------------------------- byte-exact guard + tool gating

def test_scripted_run_emits_no_new_order_fields(tmp_path):
    s = Session(runs_dir=tmp_path)
    s.init(Config(seed=5, rounds=5, max_actions_per_agent=12,
                  markets=[MarketConfig(id="M", true_prob=0.6, resolve_round=999)],
                  agents=[
                      AgentConfig(id="mm", type="mm", initial_cash=500_000,
                                  params={"spread": 3, "size": 15}),
                      AgentConfig(id="noise", type="noise", count=2, initial_cash=200_000,
                                  params={"q": 0.5, "w": 8, "max_qty": 10}),
                  ]))
    s.runner.run(5)
    evs = read_events(str(s.log_path))
    assert not any(e["type"] == "order_expired" for e in evs)        # no GTD orders
    assert all("tif" not in e["payload"] for e in evs if e["type"] == "place_order")
    s.runner.exchange.check_invariants()


def _place_params(caps) -> set:
    from market_sim.agents.llm_agent import build_agentic_tool_specs
    for t in build_agentic_tool_specs(caps):
        if t["function"]["name"] == "place_order":
            return set((t["function"]["parameters"]["properties"] or {}).keys())
    return set()


def test_advanced_orders_tool_gating():
    from market_sim.agents.llm_agent import _system_for
    assert _place_params(Capabilities()) == {"market", "token", "side", "price", "qty"}
    assert "ORDER TYPES" not in _system_for(Capabilities())
    on = _place_params(Capabilities(advanced_orders=True))
    assert {"order_type", "post_only", "expire_round"} <= on
    assert "ORDER TYPES" in _system_for(Capabilities(advanced_orders=True))


def test_console_market_and_post_only(tmp_path):
    s = Session(runs_dir=tmp_path)
    s.init(Config(seed=1, max_actions_per_agent=16,
                  markets=[MarketConfig(id="COIN-A", true_prob=0.6, resolve_round=10**9)],
                  agents=[
                      AgentConfig(id="mm", type="mm", initial_cash=500_000,
                                  params={"spread": 3, "size": 20}),
                      AgentConfig(id="me", type="human", initial_cash=100_000),
                  ]))
    s.runner.run(20)  # mm builds a book to fill against
    fak = run_agent_line(s, "place_order --market COIN-A --side buy --price 99 --qty 5 --tif FAK")
    assert fak.ok and fak.verb == "place_order"
    po = run_agent_line(s, "place_order --market COIN-A --side buy --price 10 --qty 3 --post-only")
    assert po.ok and po.verb == "place_order"
    s.runner.exchange.check_invariants()
