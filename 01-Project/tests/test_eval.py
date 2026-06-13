"""Eval correctness: probe oracles/anti-oracles, action validation, and metrics.

No LLM calls — every AgentResponse is hand-built, so this exercises the L1/L2
contracts (probes.py, base.py, metrics.py) deterministically and offline.
"""

from __future__ import annotations

import math

import pytest

from market_sim.eval.base import Trial, validate_action
from market_sim.eval.metrics import summarize, wilson_ci
from market_sim.eval.probes import ALL_PROBES, PROBE_BY_ID
from market_sim.eval.schema import ActionItem, AgentResponse, Belief


# --------------------------------------------------------------------------- helpers

def po(market, token, side, price, qty):
    return ActionItem(type="place_order", market=market, token=token,
                      side=side, price=price, qty=qty)


HOLD = ActionItem(type="hold")


def mk_trial(probe_id, passed, *, parse_ok=True, valid=True,
             n_actions=1, n_valid=1, n_invalid=0, n_hall=0, repeat=0):
    return Trial(
        probe_id=probe_id, repeat=repeat, parse_ok=parse_ok, valid=valid,
        n_actions=n_actions, n_valid_actions=n_valid, n_invalid_actions=n_invalid,
        n_hallucinated=n_hall, passed=passed, reason="", rationale="", attempts=1,
    )


# verified-correct oracle responses, one per probe (must pass its judge)
ORACLES = {
    "P1": AgentResponse(actions=[po("COIN-A", "YES", "buy", 10, 20)]),
    "P2": AgentResponse(actions=[po("COIN-A", "YES", "sell", 90, 60)]),
    "P3": AgentResponse(actions=[po("COIN-A", "YES", "buy", 55, 10),
                                 po("COIN-A", "NO", "buy", 35, 10)]),
    "P4": AgentResponse(actions=[po("RAIN", "YES", "buy", 40, 10),
                                 po("DRY", "YES", "buy", 45, 10)]),
    "P5": AgentResponse(actions=[HOLD]),
    "P6": AgentResponse(actions=[po("COIN-A", "YES", "buy", 60, 8)]),
    "P7": AgentResponse(beliefs=[Belief(market="COIN-A", prob=0.2)], actions=[HOLD]),
    "P8": AgentResponse(actions=[HOLD]),
}


# --------------------------------------------------------------------------- probe oracles

def test_all_probes_oracle_pass():
    assert set(ORACLES) == {p.id for p in ALL_PROBES}  # an oracle for every probe
    for probe in ALL_PROBES:
        setup = probe.build()
        result = probe.judge(ORACLES[probe.id], setup)
        assert result.passed, f"{probe.id} oracle should pass but: {result.reason}"


def test_anti_oracles_fail():
    anti = {
        "P1": AgentResponse(actions=[HOLD]),
        "P2": AgentResponse(actions=[HOLD]),
        "P5": AgentResponse(actions=[po("COIN-A", "YES", "buy", 61, 10)]),
        "P6": AgentResponse(actions=[po("COIN-A", "YES", "buy", 60, 10)]),
        "P8": AgentResponse(actions=[po("COIN-A", "YES", "buy", 55, 10)]),
    }
    for pid, resp in anti.items():
        probe = PROBE_BY_ID[pid]
        result = probe.judge(resp, probe.build())
        assert not result.passed, f"{pid} anti-oracle should fail but passed: {result.reason}"


# --------------------------------------------------------------------------- action validation

def test_validate_action():
    setup = PROBE_BY_ID["P1"].build()   # cash 50000, market COIN-A, agent under test
    ex, agent = setup.exchange, setup.agent_id
    assert ex.ledger.accounts[agent].cash_available == 50_000
    assert "COIN-A" in ex.markets

    ok = validate_action(ex, agent, po("COIN-A", "YES", "buy", 10, 10))
    assert ok.valid and not ok.hallucinated

    over = validate_action(ex, agent, po("COIN-A", "YES", "buy", 10, 99999))
    assert not over.valid and not over.hallucinated      # insufficient cash

    sell = validate_action(ex, agent, po("COIN-A", "YES", "sell", 50, 5))
    assert not sell.valid and not sell.hallucinated      # no shares held

    transfer = validate_action(ex, agent, ActionItem(type="transfer"))
    assert not transfer.valid and transfer.hallucinated  # unknown action type

    bad_price = validate_action(ex, agent, po("COIN-A", "YES", "buy", 0, 10))
    assert not bad_price.valid                            # price out of range

    held = validate_action(ex, agent, HOLD)
    assert held.valid and not held.hallucinated


# --------------------------------------------------------------------------- metrics

def test_wilson_ci():
    assert wilson_ci(0, 0) == (0.0, 0.0)

    lo, hi = wilson_ci(20, 20)
    assert hi == 1.0 or hi > 0.99
    assert lo > 0.8

    lo, hi = wilson_ci(18, 20)
    assert 0.6 < lo < hi <= 1.0

    lo, hi = wilson_ci(0, 5)
    assert lo >= 0.0 and hi < 0.6


def test_metrics_synthetic():
    # 2 probes, mixed passed / parse_ok / valid-action counts
    trials = [
        mk_trial("P1", True,  parse_ok=True,  n_actions=2, n_valid=2),
        mk_trial("P1", False, parse_ok=False, n_actions=1, n_valid=0, n_invalid=1, repeat=1),
        mk_trial("P2", True,  parse_ok=True,  n_actions=1, n_valid=1),
        mk_trial("P2", True,  parse_ok=True,  n_actions=3, n_valid=2, n_invalid=1, repeat=1),
    ]
    summary = summarize(trials)

    assert summary["n_trials"] == 4

    l1 = summary["l1"]
    assert l1["parse_success_rate"] == pytest.approx(3 / 4)           # 3 of 4 parse_ok
    assert l1["action_valid_rate"] == pytest.approx(5 / 7)            # 5 valid of 7 actions

    probes = summary["probes"]
    assert set(probes) == {"P1", "P2"}

    assert probes["P1"]["n"] == 2 and probes["P1"]["passed"] == 1
    assert probes["P1"]["rate"] == pytest.approx(0.5)

    assert probes["P2"]["n"] == 2 and probes["P2"]["passed"] == 2
    assert probes["P2"]["rate"] == pytest.approx(1.0)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
