"""Behaviour added by the two-version refactor:

  - per-launch timestamped run files (no overwrite) + empty-stub discard
  - concurrent LLM decisions (no event-stream corruption; engine stays deterministic)
  - the web history listing (grouped, newest-first, stubs filtered)
  - cinematic, ts-paced replay (reveal / step / play-to-end / reset)
  - live LLM auto-run that stops at the configured horizon

LLM behaviour is exercised with a deterministic stub agent (no model calls), so
these run offline. Async session behaviour is driven via asyncio.run().
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from market_sim.agents.base import Agent, Hold, PlaceOrder
from market_sim.commands.session import Session
from market_sim.engine.models import Side, Token
from market_sim.runner.config import AgentConfig, Config, MarketConfig
from market_sim.runner.simulation import Runner
from market_sim.runner.sinks import ListSink
from market_sim.web.session import REPLAY_MAX_GAP, SimulationSession


# --------------------------------------------------------------------------- helpers

class StubLLM(Agent):
    """A deterministic stand-in for a tool-using LLM trader: it exercises the read
    (agent_query) path, queues an order, and records a belief — without any model call."""

    is_human = False

    def decide(self, ctx):
        if ctx.query:
            ctx.query("get_orderbook", {"market": "COIN-A"})
            ctx.query("get_portfolio", {})
        self.last_call = {"belief": {"COIN-A": 0.6}, "rationale": "stub",
                          "ok": True, "round": ctx.round}
        return [PlaceOrder("COIN-A", Token.YES, Side.BUY, 45, 1)]


class MemoryStubLLM(Agent):
    """A stub agent with persistent memory (like ToolLoopAgent.contents) — proves the
    conversation/memory survives a save -> resume -> continue round-trip."""

    is_human = False

    def __init__(self, agent_id, params=None):
        super().__init__(agent_id, params)
        self.contents: list = []

    def decide(self, ctx):
        self.contents.append(f"r{ctx.round}")
        self.last_call = {"belief": {}, "rationale": "", "ok": True, "round": ctx.round}
        return [Hold()]


def _scripted_cfg(run_name="demo", rounds=200):
    return Config(
        seed=20260607, rounds=rounds, max_actions_per_agent=16, run_name=run_name,
        markets=[MarketConfig(id="COIN-A", true_prob=0.65, resolve_round=10**9)],
        agents=[
            AgentConfig(id="noise", type="noise", count=2, initial_cash=250_000,
                        params={"q": 0.6, "w": 8, "max_qty": 12}),
            AgentConfig(id="mm", type="mm", count=1, initial_cash=600_000,
                        params={"spread": 3, "size": 20}),
        ],
    )


def _llm_cfg(run_name="llmt", rounds=6, count=4):
    return Config(
        seed=7, rounds=rounds, max_actions_per_agent=8, run_name=run_name,
        markets=[MarketConfig(id="COIN-A", true_prob=0.6, resolve_round=10**9)],
        agents=[
            AgentConfig(id="llm", type="llm_agentic", count=count, initial_cash=200_000,
                        params={"signal_sigma": 0.05}),
            AgentConfig(id="mm", type="mm", count=1, initial_cash=500_000,
                        params={"spread": 3, "size": 15}),
        ],
    )


# --------------------------------------------------------------------------- per-launch files

def test_per_launch_unique_files(tmp_path):
    s = Session(runs_dir=tmp_path)
    s.init(_scripted_cfg(), run_id="2026-01-01_000000"); s.runner.run(2)
    assert s.log_path == tmp_path / "demo" / "2026-01-01_000000.jsonl"
    s.init(_scripted_cfg(), run_id="2026-01-01_000001"); s.runner.run(1)
    files = sorted(p.name for p in (tmp_path / "demo").glob("*.jsonl"))
    assert files == ["2026-01-01_000000.jsonl", "2026-01-01_000001.jsonl"]  # nothing overwritten


def test_empty_stub_discarded(tmp_path):
    s = Session(runs_dir=tmp_path)
    s.init(_scripted_cfg(), run_id="a")        # never advanced -> config-only stub
    s.init(_scripted_cfg(), run_id="b")        # next init drops the empty 'a'
    files = sorted(p.stem for p in (tmp_path / "demo").glob("*.jsonl"))
    assert files == ["b"]


def test_make_run_id_disambiguates(tmp_path):
    s = Session(runs_dir=tmp_path)
    (tmp_path / "demo").mkdir(parents=True)
    base = s._make_run_id("demo")
    (tmp_path / "demo" / f"{base}.jsonl").write_text("{}")
    assert s._make_run_id("demo") != base   # collides within the same second -> suffixed


# --------------------------------------------------------------------------- parallel LLM

def test_parallel_llm_decisions(monkeypatch):
    import market_sim.agents.scripted as scripted
    monkeypatch.setitem(scripted.BOT_REGISTRY, "llm_agentic", StubLLM)

    r = Runner(_llm_cfg(rounds=6, count=4), ListSink())
    assert r.has_llm                      # -> takes the concurrent decision path
    r.run(6)
    evs = r.sink.events
    eids = [e.event_id for e in evs]
    assert eids == sorted(eids) and len(eids) == len(set(eids))   # no id race / no torn writes
    assert sum(1 for e in evs if e.type == "llm_call") == 4 * 6
    assert sum(1 for e in evs if e.type == "agent_query") == 4 * 6 * 2
    r.exchange.check_invariants()
    # execution order is real finish-time priority (wall-clock), so the LLM path is
    # intentionally non-reproducible — assert conservation instead of byte-equality.
    total = r.exchange.ledger.total_cash() + sum(m.collateral_pool for m in r.exchange.markets.values())
    assert total == r.exchange.ledger.total0


class DelayStub(Agent):
    """Stub LLM whose decision takes params['delay'] seconds — to exercise finish-time
    execution priority (the faster agent's order should match first)."""

    is_human = False

    def decide(self, ctx):
        import time as _t
        _t.sleep(float(self.params.get("delay", 0.0)))
        self.last_call = {"belief": {}, "rationale": "", "ok": True, "round": ctx.round}
        return [PlaceOrder("COIN-A", Token.YES, Side.BUY, 50, 1)]


def test_execution_order_by_finish_time(monkeypatch):
    import market_sim.agents.scripted as scripted
    monkeypatch.setitem(scripted.BOT_REGISTRY, "llm_agentic", DelayStub)
    cfg = Config(seed=1, rounds=1, max_actions_per_agent=8, run_name="t",
                 markets=[MarketConfig(id="COIN-A", true_prob=0.5, resolve_round=10**9)],
                 agents=[
                     AgentConfig(id="fast", type="llm_agentic", initial_cash=200_000, params={"delay": 0.0}),
                     AgentConfig(id="slow", type="llm_agentic", initial_cash=200_000, params={"delay": 0.08}),
                 ])
    r = Runner(cfg, ListSink())
    r.run(1)
    po = {e.agent_id: e.event_id for e in r.sink.events if e.type == "place_order"}
    assert "fast" in po and "slow" in po
    assert po["fast"] < po["slow"]   # faster decider's order executed (and emitted) first


# --------------------------------------------------------------------------- web history

def test_recordings_listing(tmp_path):
    Session(runs_dir=tmp_path).init(_scripted_cfg(), run_id="2026-01-01_100000")  # stub (filtered)
    s = Session(runs_dir=tmp_path)
    s.init(_scripted_cfg(), run_id="2026-01-01_100000"); s.runner.run(3)
    s.init(_scripted_cfg(), run_id="2026-01-01_110000"); s.runner.run(5)

    web = SimulationSession(runs_dir=tmp_path)
    assert [x["file"] for x in web.scenarios()] == ["demo.yaml", "demo5.yaml", "llm5_only.yaml"]
    recs = web.recordings()
    assert [r["file"] for r in recs] == [
        "demo/2026-01-01_110000.jsonl", "demo/2026-01-01_100000.jsonl",   # newest first
    ]
    assert recs[0]["rounds"] == 5 and recs[1]["rounds"] == 3
    assert all(r["scenario"] == "demo" for r in recs)


# --------------------------------------------------------------------------- cinematic replay

def test_cinematic_replay(tmp_path):
    s = Session(runs_dir=tmp_path)
    s.init(_scripted_cfg(), run_id="rec"); s.runner.run(4)
    rec = s.log_path.relative_to(tmp_path).as_posix()

    async def go():
        web = SimulationSession(runs_dir=tmp_path)
        assert web.load_recording(rec)
        assert web.max_round() == 0                 # setup (round 0) revealed at load
        await web.step_once()
        assert web.max_round() == 1                 # step reveals one whole round
        web.set_speed(60)
        await web.play()
        for _ in range(400):
            await asyncio.sleep(0.01)
            if web.mode != "playing":
                break
        assert web.mode == "paused" and web.max_round() == 4
        assert web.replay["cursor"] == len(web.replay["events"])
        web.reset_run()
        assert web.max_round() == 0                 # rewound to the setup snapshot

    asyncio.run(go())


def test_replay_gap_capped_and_speed_scaled(tmp_path):
    """Replay pacing is a ×speed multiplier on the recorded inter-event gaps, with each
    gap capped at REPLAY_MAX_GAP (so long idle/backoff dead time between rounds can't
    stall playback) and the speed divisor clamped at 0.5 so very low slider values can at
    most double the tempo. Gaps under the cap still replay faithfully at ×1."""
    web = SimulationSession(runs_dir=tmp_path)

    def gap(ts_next: str, speed: float) -> float:
        web.speed = speed
        web.replay = {"events": [{"round": 1, "ts": "2026-01-01T00:00:00+00:00"},
                                 {"round": 1, "ts": ts_next}], "cursor": 1}
        return web._replay_gap(web.replay["events"][0])

    assert gap("2026-01-01T00:00:04+00:00", 1.0) == 4.0    # under cap: ×1 is faithful real-time
    assert gap("2026-01-01T00:00:04+00:00", 2.0) == 2.0    # 4s / speed 2
    assert gap("2026-01-01T00:00:30+00:00", 1.0) == REPLAY_MAX_GAP  # 30s idle gap is capped
    assert gap("2026-01-01T00:00:01+00:00", 0.25) == 2.0   # divisor clamped at 0.5


def test_replay_zero_gap_not_floored(tmp_path):
    """Zero recorded gap -> zero wait: replay no longer pads instant events out to a
    floor (REPLAY_MIN_GAP is only the LIVE-stream drip beat). Pausability of a replay
    comes from real recorded gaps — see test_replay_pause_interrupts."""
    web = SimulationSession(runs_dir=tmp_path)
    web.speed = 1.0
    web.replay = {"events": [{"round": 1, "ts": "2026-01-01T00:00:00.000+00:00"},
                             {"round": 1, "ts": "2026-01-01T00:00:00.000+00:00"}], "cursor": 1}
    assert web._replay_gap(web.replay["events"][0]) == 0.0


def test_replay_pause_interrupts(tmp_path):
    s = Session(runs_dir=tmp_path)
    s.init(_scripted_cfg(run_name="demo", rounds=30))
    s.runner.run(30)
    # a scripted run records in ~0ms and would replay instantly (gaps replay 1:1, no
    # floor) — re-stamp the recording with 50ms inter-event gaps so ×1 playback paces out
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [json.loads(ln) for ln in s.log_path.read_text().splitlines() if ln.strip()]
    for i, e in enumerate(events):
        e["ts"] = (base + timedelta(milliseconds=50 * i)).isoformat()
    s.log_path.write_text("".join(json.dumps(e) + "\n" for e in events))
    rec = s.log_path.relative_to(tmp_path).as_posix()

    async def go():
        web = SimulationSession(runs_dir=tmp_path)
        web.load_recording(rec)
        web.set_speed(1.0)
        await web.play()
        await asyncio.sleep(0.2)                       # a few events stream...
        assert web.mode == "playing"
        assert 0 < web.replay["cursor"] < len(web.replay["events"])   # ...but not all (gaps pace it)
        await web.pause()
        await asyncio.sleep(0.1)                       # let any in-flight iteration finish
        frozen = web.replay["cursor"]
        await asyncio.sleep(0.3)
        assert web.mode == "paused"
        assert web.replay["cursor"] == frozen < len(web.replay["events"])   # pause froze it mid-replay

    asyncio.run(go())


# --------------------------------------------------------------------------- agent briefing

def test_briefing_is_trimmed_to_force_tool_use():
    from market_sim.agents.base import DecisionContext, MarketView, PortfolioView
    from market_sim.agents.llm_agent import ToolLoopAgent

    agent = ToolLoopAgent("llm1", {})
    mv = MarketView(id="COIN-A", question="", status="open", best_bid=60, best_ask=62,
                    last_trade=61, mid=61, true_prob=0.65, resolves_in=998, depth={})
    ctx = DecisionContext(round=3, agent_id="llm1", rng=None, markets={"COIN-A": mv},
                          portfolio=PortfolioView(1000, 0, {}, []),
                          signals=[{"market": "COIN-A", "prob_pct": 60, "sigma_pct": 4,
                                    "text": "r3: your read on COIN-A ≈ 60% YES (±4%)"}])
    b = agent._wake_briefing(ctx)
    assert "COIN-A" in b                                  # the market id is listed...
    assert "mid=61" not in b and "bid=" not in b          # ...but prices are NOT inlined
    assert "60% YES" not in b and "±4%" not in b          # nor the signal value -> must get_news
    assert "get_news" in b and "get_orderbook" in b       # told to fetch it itself


# --------------------------------------------------------------------------- manual save

def test_manual_save(tmp_path):
    from market_sim.runner.events import read_events
    web = SimulationSession(runs_dir=tmp_path)
    web.init(_scripted_cfg(run_name="demo", rounds=10))
    web.runner.run(3)                                   # advance in memory only
    assert web.recordings() == []                        # nothing persisted before Save
    assert not list(tmp_path.rglob("*.jsonl"))

    rel = web.save_run()                                 # the Save button
    assert rel and (tmp_path / rel).exists()
    recs = web.recordings()
    assert len(recs) == 1 and recs[0]["rounds"] == 3
    assert any(e["type"] == "round_end" for e in read_events(str(tmp_path / rel)))


def test_save_noop_before_first_round(tmp_path):
    web = SimulationSession(runs_dir=tmp_path)
    web.init(_scripted_cfg(run_name="demo"))
    assert web.save_run() is None                        # round 0 -> nothing worth saving
    assert not list(tmp_path.rglob("*.jsonl"))


# --------------------------------------------------------------------------- resume / continue

def test_save_writes_resume_snapshot_and_continues(tmp_path):
    web = SimulationSession(runs_dir=tmp_path)
    web.init(_scripted_cfg(run_name="demo", rounds=50))
    web.runner.run(4)
    rel = web.save_run()
    assert rel and SimulationSession._state_pkl(tmp_path / rel).exists()
    assert web.recordings()[0]["resumable"] is True

    # a fresh session loads it as a replay, then continues it live from round 4
    web2 = SimulationSession(runs_dir=tmp_path)
    assert web2.load_recording(rel) and web2.replay is not None and web2.runner is None
    assert web2.resume_recording(rel)
    assert web2.replay is None and web2.runner is not None and web2.runner.round_no == 4
    assert len(web2.event_log) > 0                       # prior history kept (scrubbable)
    web2.runner.run(3)                                   # continue live
    assert web2.runner.round_no == 7
    web2.runner.exchange.check_invariants()


def test_resume_keeps_agent_memory(tmp_path, monkeypatch):
    import market_sim.agents.scripted as scripted
    monkeypatch.setitem(scripted.BOT_REGISTRY, "llm_agentic", MemoryStubLLM)

    web = SimulationSession(runs_dir=tmp_path)
    web.init(_llm_cfg(run_name="demo5", rounds=20, count=2))
    web.runner.run(3)
    mem = lambda s: [len(a.contents) for a in s.runner.agents.values() if hasattr(a, "contents")]
    assert mem(web) == [3, 3]
    rel = web.save_run()

    web2 = SimulationSession(runs_dir=tmp_path)
    assert web2.resume_recording(rel)
    assert mem(web2) == [3, 3]                           # memory survived save -> resume
    web2.runner.run(2)
    assert mem(web2) == [5, 5]                           # and keeps accumulating on continue


def test_history_scoped_to_active_scenario(tmp_path):
    web = SimulationSession(runs_dir=tmp_path)
    assert web.load_config_file("demo.yaml")
    assert web.current_scenario == "demo"
    web.runner.run(2)
    web.save_run()
    assert web.load_config_file("demo5.yaml")            # switch scenario
    assert web.current_scenario == "demo5"
    # the demo recording exists but belongs to a different scenario than the active one
    demo_recs = [r for r in web.recordings() if r["scenario"] == "demo"]
    assert demo_recs and all(r["scenario"] != web.current_scenario for r in demo_recs)


# --------------------------------------------------------------------------- live auto-run

def test_llm_autorun_stops_at_horizon(tmp_path, monkeypatch):
    import market_sim.agents.scripted as scripted
    monkeypatch.setitem(scripted.BOT_REGISTRY, "llm_agentic", StubLLM)
    # live LLM rounds reveal cinematically (one event at a time); drop the per-event
    # floor so the stub run drips instantly here instead of pacing at ~0.15s/event.
    monkeypatch.setattr("market_sim.web.session.REPLAY_MIN_GAP", 0.0)

    async def go():
        web = SimulationSession(runs_dir=tmp_path)
        web.init(_llm_cfg(rounds=5, count=2))
        assert web.runner.has_llm
        await web.play()                            # auto-run drips each round's events
        for _ in range(500):
            await asyncio.sleep(0.01)
            if web.mode != "playing":
                break
        assert web.mode == "paused" and web.runner.round_no == 5   # stops at config.rounds

    asyncio.run(go())


def test_live_llm_reveals_events_one_at_a_time(tmp_path, monkeypatch):
    """Live LLM auto-run streams a round's events ONE BY ONE (cinematic drip), not the
    whole round in a single batch — so each agent's tool calls appear individually."""
    import market_sim.agents.scripted as scripted
    monkeypatch.setitem(scripted.BOT_REGISTRY, "llm_agentic", StubLLM)
    monkeypatch.setattr("market_sim.web.session.REPLAY_MIN_GAP", 0.0)  # instant in test

    class FakeWS:
        def __init__(self):
            self.batch_sizes: list[int] = []

        async def send_json(self, msg):
            if msg.get("type") == "event_batch":
                self.batch_sizes.append(len(msg["events"]))

    async def go():
        web = SimulationSession(runs_dir=tmp_path)
        web.init(_llm_cfg(rounds=3, count=2))
        ws = FakeWS()
        web.clients.add(ws)
        await web.play()
        for _ in range(500):
            await asyncio.sleep(0.01)
            if web.mode != "playing":
                break
        assert web.mode == "paused" and web.runner.round_no == 3
        # every streamed batch carries exactly one event (drip), and a full run's worth
        # of events went out — proving the round did NOT arrive as one fat batch.
        assert ws.batch_sizes, "no events were streamed"
        assert all(n == 1 for n in ws.batch_sizes)
        assert len(ws.batch_sizes) >= 3 * 5   # >= a handful of events per round, 3 rounds

    asyncio.run(go())
