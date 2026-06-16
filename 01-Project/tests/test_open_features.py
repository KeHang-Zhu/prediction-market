"""Open-scenario agent actions: transfer / create_account / create_market.

These exercise the new capability-gated actions end-to-end through the same engine
seams everything else uses (Runner._execute / Exchange / Ledger), asserting the three
conservation invariants hold after every mutation, that a created wallet is passive,
that a created market's latent truth is system-fixed, deterministic, and never leaked,
and that capability gating + pickling/resume behave.
"""

from __future__ import annotations

import pickle

from market_sim.agents.base import CreateAccount, CreateMarket, Transfer
from market_sim.commands.agent_api import run_agent_line
from market_sim.commands.session import Session
from market_sim.engine.exchange import Exchange
from market_sim.engine.models import Account, Market
from market_sim.runner.config import AgentConfig, Capabilities, Config, MarketConfig
from market_sim.runner.events import read_events
from market_sim.runner.simulation import Runner
from market_sim.runner.sinks import ListSink


def _open_cfg(**over) -> Config:
    base = dict(
        seed=7, rounds=50, max_actions_per_agent=16,
        markets=[MarketConfig(id="COIN-A", true_prob=0.6, resolve_round=10**9)],
        agents=[
            AgentConfig(id="mm", type="mm", initial_cash=500_000, params={"spread": 3, "size": 20}),
            AgentConfig(id="me", type="human", initial_cash=100_000),
        ],
        capabilities=Capabilities(transfer=True, create_account=True, create_market=True),
    )
    base.update(over)
    return Config(**base)


# --------------------------------------------------------------- engine primitives

def test_engine_primitives_preserve_invariants():
    accts = {"a": Account("a", 1000), "b": Account("b", 500)}
    markets = {"M": Market("M", "q", 0.5, 9999)}
    ex = Exchange(markets, accts)
    total0 = ex.ledger.total0

    ex.ledger.transfer("a", "b", 300)
    assert accts["a"].cash_available == 700 and accts["b"].cash_available == 800
    ex.check_invariants()

    ex.create_account("c", "a", 200)
    assert accts["a"].cash_available == 500 and accts["c"].cash_available == 200
    assert ex.ledger.total0 == total0  # money moved, never created
    ex.check_invariants()

    m = ex.create_market("M2", "q2", 9999, 0.7, 1)
    assert m.outcome == 1 and ex.markets["M2"].collateral_pool == 0
    assert "M2" in ex.books and ex.last_price["M2"] is None and ex.volume["M2"] == 0
    ex.check_invariants()


# ---------------------------------------------------------------------- transfer

def test_transfer_moves_cash_and_rejects(tmp_path):
    s = Session(runs_dir=tmp_path); s.init(_open_cfg())
    r = s.runner
    me0 = r.exchange.ledger.accounts["me"].cash_available
    mm0 = r.exchange.ledger.accounts["mm"].cash_available

    assert r.execute_now("me", Transfer("mm", 1000)) == {"status": "ok"}
    assert r.exchange.ledger.accounts["me"].cash_available == me0 - 1000
    assert r.exchange.ledger.accounts["mm"].cash_available == mm0 + 1000
    r.exchange.check_invariants()

    assert r.execute_now("me", Transfer("nope", 100))["reason"] == "unknown_recipient"
    assert r.execute_now("me", Transfer("me", 100))["reason"] == "self_transfer"
    assert r.execute_now("me", Transfer("mm", 0))["reason"] == "bad_amount"
    assert r.execute_now("me", Transfer("mm", 10**9))["reason"] == "insufficient_cash"
    r.exchange.check_invariants()


# ------------------------------------------------------------------ create_account

def test_create_account_is_passive_and_conserves(tmp_path):
    s = Session(runs_dir=tmp_path); s.init(_open_cfg())
    r = s.runner
    total0 = r.exchange.ledger.total0

    assert r.execute_now("me", CreateAccount("wallet1", 5000)) == {"status": "ok"}
    accts = r.exchange.ledger.accounts
    assert accts["wallet1"].cash_available == 5000
    assert accts["me"].cash_available == 100_000 - 5000
    assert r.agent_types["wallet1"] == "wallet"
    assert r.initial_cash["wallet1"] == 5000
    # passive: no agent, no decision slot, no signal substream
    assert "wallet1" not in r.agents and "wallet1" not in r.agent_ids_sorted
    assert r.exchange.ledger.total0 == total0
    r.exchange.check_invariants()

    # shows up in the snapshot as a wallet with zero PnL (baseline = funded amount)
    w = next(a for a in r.latest_snapshot()["agents"] if a["agent_id"] == "wallet1")
    assert w["type"] == "wallet" and w["pnl"] == 0

    # it can RECEIVE a transfer
    r.execute_now("me", Transfer("wallet1", 1000))
    assert accts["wallet1"].cash_available == 6000
    r.exchange.check_invariants()

    # reject: duplicate id, overdraw
    assert r.execute_now("me", CreateAccount("wallet1", 10))["reason"] == "account_exists"
    assert r.execute_now("me", CreateAccount("w2", 10**9))["reason"] == "insufficient_cash"


# ------------------------------------------------------------------- create_market

def test_create_market_lifecycle(tmp_path):
    s = Session(runs_dir=tmp_path); s.init(_open_cfg())
    r = s.runner

    assert r.execute_now("me", CreateMarket("NEW", "Will it rain?", r.round_no + 5)) == {"status": "ok"}
    m = r.exchange.markets["NEW"]
    assert m.status.value == "open" and m.outcome in (0, 1)
    assert "NEW" in r.exchange.books
    r.exchange.check_invariants()
    assert any(mk["id"] == "NEW" for mk in r.latest_snapshot()["markets"])

    assert r.execute_now("me", CreateMarket("NEW", "q", r.round_no + 5))["reason"] == "market_exists"
    assert r.execute_now("me", CreateMarket("X", "q", r.round_no))["reason"] == "resolve_round_in_past"
    assert r.execute_now("me", CreateMarket("Y", "   ", r.round_no + 5))["reason"] == "empty_question"


def test_market_created_event_hides_truth(tmp_path):
    s = Session(runs_dir=tmp_path); s.init(_open_cfg())
    s.runner.execute_now("me", CreateMarket("NEW", "Will it rain?", 9999))
    evs = [e for e in read_events(str(s.log_path)) if e["type"] == "market_created"]
    assert len(evs) == 1
    p = evs[0]["payload"]
    assert "true_prob" not in p and "outcome" not in p   # creator stays blind to the truth
    assert p["market_id"] == "NEW" and p["resolve_round"] == 9999


def test_created_market_resolves(tmp_path):
    s = Session(runs_dir=tmp_path); s.init(_open_cfg())
    r = s.runner
    r.execute_now("me", CreateMarket("NEW", "Q?", r.round_no + 2))
    r.run(2)   # advance to the resolve round
    evs = read_events(str(s.log_path))
    assert any(e["type"] == "resolution" and e["payload"]["market"] == "NEW" for e in evs)
    r.exchange.check_invariants()


# ------------------------------------------------------- determinism / isolation

def test_created_market_truth_is_deterministic():
    r1 = Runner(_open_cfg(), ListSink())
    r1.execute_now("me", CreateMarket("NEW", "q", r1.round_no + 5))

    r2 = Runner(_open_cfg(), ListSink())
    r2.run(3)   # different round / rng position
    r2.execute_now("me", CreateMarket("NEW", "q", r2.round_no + 5))

    assert r1.exchange.markets["NEW"].true_prob == r2.exchange.markets["NEW"].true_prob
    assert r1.exchange.markets["NEW"].outcome == r2.exchange.markets["NEW"].outcome


def test_create_market_does_not_consume_main_rng():
    r = Runner(_open_cfg(), ListSink())
    r.run(2)
    before = r.rng.bit_generator.state
    r.execute_now("me", CreateMarket("NEW", "q", r.round_no + 5))
    assert r.rng.bit_generator.state == before   # main draw sequence untouched


# ------------------------------------------------------------- capability gating

def test_agent_api_capability_gating(tmp_path):
    # OFF -> the exact not_supported contract (existing scenarios unchanged)
    s_off = Session(runs_dir=tmp_path)
    s_off.init(_open_cfg(run_name="off", capabilities=Capabilities()))
    for line in ("create_account --account-id w1 --initial-cash 10",
                 "create_market --market-id M --question Q --resolve-round 99",
                 "transfer --to mm --amount 100"):
        res = run_agent_line(s_off, line)
        assert res.ok is False and res.data.get("status") == "not_supported", line

    # ON -> real actions
    s_on = Session(runs_dir=tmp_path)
    s_on.init(_open_cfg(run_name="on"))
    assert run_agent_line(s_on, "transfer --to mm --amount 100").ok is True
    assert run_agent_line(s_on, "create_account --account-id w1 --initial-cash 5000").ok is True
    assert run_agent_line(s_on, 'create_market --market-id NEWM --question "Q?" --resolve-round 9999').ok is True
    assert "w1" in s_on.runner.exchange.ledger.accounts
    assert "NEWM" in s_on.runner.exchange.markets
    s_on.runner.exchange.check_invariants()


# -------------------------------------------------------------- pickling / resume

def test_pickle_roundtrip_after_create(tmp_path):
    s = Session(runs_dir=tmp_path); s.init(_open_cfg())
    r = s.runner
    r.execute_now("me", CreateAccount("w1", 5000))
    r.execute_now("me", CreateMarket("NEW", "Q?", 9999))

    r2 = pickle.loads(pickle.dumps(r))
    assert "w1" in r2.exchange.ledger.accounts
    assert "NEW" in r2.exchange.markets and "NEW" in r2.exchange.books
    assert r2.agent_types["w1"] == "wallet"

    r2.sink = ListSink()   # __getstate__ drops the sink; reattach to resume
    r2.step()
    r2.exchange.check_invariants()


# ---------------------------------- LLM tool-loop -> queue -> settle (no live model)

def test_llm_tool_loop_queues_and_settles_new_actions(tmp_path):
    """Drive a real ToolLoopAgent with a fake provider that calls the new tools, then
    step the runner — proving the blind-submit queue -> execution-phase settle -> event
    path fires end-to-end, deterministically and with no Gemini call."""
    from google.genai import types

    cfg = Config(
        seed=7, rounds=10, max_actions_per_agent=16,
        markets=[MarketConfig(id="COIN-A", true_prob=0.6, resolve_round=10**9)],
        agents=[
            AgentConfig(id="llm1", type="llm_agentic", initial_cash=100_000,
                        params={"max_tool_calls": 4}),
            AgentConfig(id="me", type="human", initial_cash=100_000),
        ],
        capabilities=Capabilities(transfer=True, create_account=True, create_market=True),
    )
    s = Session(runs_dir=tmp_path); s.init(cfg)
    r = s.runner
    assert r.agents["llm1"].caps.transfer   # runner handed the agent its capabilities

    class FakeProvider:
        def tool_turn(self, contents, tools, system, temperature):
            return {
                "error": None, "api_error": None,
                "content": types.Content(role="model", parts=[types.Part(text="ok")]),
                "text": "",
                "function_calls": [
                    {"name": "commit_view",
                     "args": {"beliefs": [{"market": "COIN-A", "prob": 0.6}], "plan": "p"}},
                    {"name": "transfer", "args": {"to": "me", "amount": 1000}},
                    {"name": "create_account", "args": {"account_id": "w1", "initial_cash": 2000}},
                    {"name": "create_market",
                     "args": {"market_id": "AGENT-M", "question": "Q?", "resolve_round": 50}},
                    {"name": "finish", "args": {"lessons": "done"}},
                ],
                "retries": 0, "backoff_s": 0.0,
            }

    r.agents["llm1"]._provider = FakeProvider()
    r.step()

    types_seen = {e["type"] for e in read_events(str(s.log_path))}
    assert {"transfer", "account_created", "market_created"} <= types_seen

    accts = r.exchange.ledger.accounts
    assert accts["me"].cash_available == 100_000 + 1000           # received transfer
    assert accts["llm1"].cash_available == 100_000 - 1000 - 2000  # sent + funded wallet
    assert accts["w1"].cash_available == 2000
    assert "AGENT-M" in r.exchange.markets
    r.exchange.check_invariants()
