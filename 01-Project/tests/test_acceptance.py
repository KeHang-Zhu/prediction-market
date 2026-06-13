"""M4 acceptance: demo runs clean + converges, MM profits in pure noise, replay
is byte-exact."""

from __future__ import annotations

from pathlib import Path

from market_sim.runner.config import AgentConfig, Config, MarketConfig, NewsConfig
from market_sim.runner.replay import verify_replay
from market_sim.runner.simulation import Runner
from market_sim.runner.sinks import FanoutSink, JsonlEventSink, ListSink

DEMO = Path(__file__).resolve().parent.parent / "demo.yaml"


def _pnl(snap, aid):
    return next(a["pnl"] for a in snap["agents"] if a["agent_id"] == aid)


def _market(snap, mid):
    return next(m for m in snap["markets"] if m["id"] == mid)


def test_mm_profits_in_pure_noise():
    """Across seeds, the market maker captures the spread (P&L > 0) and noise
    traders pay it on average."""
    wins = 0
    for seed in range(5):
        cfg = Config(
            seed=seed, rounds=250, max_actions_per_agent=16, depth_k=8,
            markets=[MarketConfig(id="M", true_prob=0.5, resolve_round=10**9)],
            agents=[
                AgentConfig(id="noise", type="noise", count=4, initial_cash=400_000,
                            params={"q": 0.7, "w": 10, "max_qty": 12}),
                AgentConfig(id="mm", type="mm", count=1, initial_cash=600_000,
                            params={"spread": 4, "size": 15, "skew_unit": 25}),
            ],
            news=NewsConfig(enabled=False),
        )
        r = Runner(cfg, ListSink())
        r.run(250)
        snap = r.latest_snapshot()
        if _pnl(snap, "mm") > 0:
            wins += 1
    assert wins == 5, f"MM should profit on every seed in pure noise (won {wins}/5)"


def test_demo_converges_and_conserves():
    from market_sim.runner.config import load_config

    cfg = load_config(DEMO)
    runner = Runner(cfg, ListSink())  # ListSink: no file writes in the test
    total0 = runner.exchange.ledger.total0
    runner.run(200)  # invariants asserted every round internally
    snap = runner.latest_snapshot()

    # conservation
    pools = sum(m.collateral_pool for m in runner.exchange.markets.values())
    assert runner.exchange.ledger.total_cash() + pools == total0

    # fundamentalist markets converge to true_prob ± 5¢
    a = _market(snap, "COIN-A")
    c = _market(snap, "COIN-C")
    assert a["status"] == "open" and abs(a["mid"] - 65) <= 5, a
    assert c["status"] == "open" and abs(c["mid"] - 55) <= 5, c

    # COIN-B resolved mid-run
    b = _market(snap, "COIN-B")
    assert b["status"] == "resolved" and b["outcome"] in (0, 1)


def test_demo_replay_byte_exact(tmp_path: Path):
    from market_sim.runner.config import load_config

    cfg = load_config(DEMO)
    log = tmp_path / "demo.jsonl"
    sink = FanoutSink([JsonlEventSink(log)])
    runner = Runner(cfg, sink)
    runner.run(200)
    sink.close()
    matched, idx, detail = verify_replay(str(log))
    assert matched, detail
