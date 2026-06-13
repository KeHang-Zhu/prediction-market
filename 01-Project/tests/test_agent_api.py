"""Agent CLI (proposal API) — the interface the browser console / agents use."""

from __future__ import annotations

from market_sim.commands.agent_api import run_agent_line
from market_sim.commands.session import Session
from market_sim.runner.config import AgentConfig, Config, MarketConfig


def make_session(tmp_path) -> Session:
    s = Session(runs_dir=tmp_path)
    cfg = Config(
        seed=1, max_actions_per_agent=16,
        markets=[MarketConfig(id="COIN-A", true_prob=0.6, resolve_round=10**9)],
        agents=[
            AgentConfig(id="mm", type="mm", initial_cash=500_000, params={"spread": 3, "size": 20}),
            AgentConfig(id="me", type="human", initial_cash=100_000),
        ],
    )
    s.init(cfg)
    s.runner.run(20)  # build a book
    return s


def test_get_markets(tmp_path):
    s = make_session(tmp_path)
    res = run_agent_line(s, "get_markets")
    assert res.ok and res.verb == "get_markets"
    ids = [m["id"] for m in res.data["markets"]]
    assert "COIN-A" in ids
    # ground-truth probability must NOT leak through the agent API
    assert all("true_prob" not in m for m in res.data["markets"])


def test_get_orderbook(tmp_path):
    s = make_session(tmp_path)
    res = run_agent_line(s, "get_orderbook --market COIN-A")
    assert res.ok and res.verb == "get_orderbook"
    assert res.data["market"] == "COIN-A" and "book" in res.data


def test_place_order_and_portfolio(tmp_path):
    s = make_session(tmp_path)
    # PDF-style: no token/agent -> defaults YES / me
    res = run_agent_line(s, "place_order --market COIN-A --side buy --price 40 --qty 5")
    assert res.ok and res.verb == "place_order"
    pf = run_agent_line(s, "get_portfolio")
    assert pf.ok and pf.verb == "get_portfolio" and pf.data["agent_id"] == "me"


def test_get_trade_history(tmp_path):
    s = make_session(tmp_path)
    res = run_agent_line(s, "get_trade_history --market COIN-A --last 5")
    assert res.ok and res.verb == "get_trade_history"
    assert res.data["market"] == "COIN-A" and "trades" in res.data


def test_not_supported_stubs(tmp_path):
    s = make_session(tmp_path)
    for line in ("create_account", "create_market --question X", "transfer --to mm --amount 100"):
        res = run_agent_line(s, line)
        assert res.ok is False
        assert res.data.get("status") == "not_supported", line


def test_unknown_endpoint(tmp_path):
    s = make_session(tmp_path)
    res = run_agent_line(s, "frobnicate")
    assert res.ok is False and "unknown agent endpoint" in (res.error or "")
