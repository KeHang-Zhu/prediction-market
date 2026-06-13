"""Runner: conservation across rounds, determinism, and byte-exact replay."""

from __future__ import annotations

from pathlib import Path

from market_sim.runner.config import AgentConfig, Config, MarketConfig, NewsConfig
from market_sim.runner.events import compare_streams
from market_sim.runner.replay import verify_replay
from market_sim.runner.simulation import Runner
from market_sim.runner.sinks import FanoutSink, JsonlEventSink, ListSink


def demo_config(seed=7, rounds=60) -> Config:
    return Config(
        seed=seed,
        rounds=rounds,
        max_actions_per_agent=16,
        depth_k=10,
        markets=[
            MarketConfig(id="COIN-A", question="Coin A heads?", true_prob=0.65, resolve_round=50),
            MarketConfig(id="COIN-B", question="Coin B heads?", true_prob=0.40, resolve_round=80),
        ],
        agents=[
            AgentConfig(id="noise", type="noise", count=3, initial_cash=200_000,
                        params={"q": 0.6, "w": 8, "max_qty": 12}),
            AgentConfig(id="mm", type="mm", count=1, initial_cash=400_000,
                        params={"spread": 3, "size": 20}),
            AgentConfig(id="fund", type="fundamentalist", count=1, initial_cash=400_000,
                        params={"threshold": 4, "size": 15}),
            AgentConfig(id="me", type="human", count=1, initial_cash=100_000),
        ],
        news=NewsConfig(enabled=True, every_rounds=12, epsilon=0.2),
    )


def test_conservation_across_rounds():
    runner = Runner(demo_config(), ListSink())
    total0 = runner.exchange.ledger.total0
    runner.run(60)  # invariants asserted inside every step
    pools = sum(m.collateral_pool for m in runner.exchange.markets.values())
    assert runner.exchange.ledger.total_cash() + pools == total0


def test_determinism_same_seed_identical_streams():
    a = Runner(demo_config(seed=123), ListSink())
    a.run(40)
    b = Runner(demo_config(seed=123), ListSink())
    b.run(40)
    sa = [e.to_dict() for e in a.sink.events]
    sb = [e.to_dict() for e in b.sink.events]
    matched, idx, detail = compare_streams(sa, sb)
    assert matched, detail


def test_different_seed_differs():
    a = Runner(demo_config(seed=1), ListSink())
    a.run(40)
    b = Runner(demo_config(seed=2), ListSink())
    b.run(40)
    sa = [e.to_dict() for e in a.sink.events]
    sb = [e.to_dict() for e in b.sink.events]
    matched, _, _ = compare_streams(sa, sb)
    assert not matched


def test_byte_exact_replay_from_log(tmp_path: Path):
    log = tmp_path / "run.jsonl"
    sink = FanoutSink([JsonlEventSink(log), ListSink()])
    runner = Runner(demo_config(seed=42), sink)
    runner.run(60)
    sink.close()
    matched, idx, detail = verify_replay(str(log))
    assert matched, detail
