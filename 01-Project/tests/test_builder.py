"""Scenario builder: spec -> Config expansion, YAML round-trip, and web save_template."""

from __future__ import annotations

import pytest

from market_sim.runner.builder import (
    BUILTIN_RUN_NAMES,
    build_config,
    dump_config,
    slugify,
)
from market_sim.runner.config import load_config
from market_sim.runner.simulation import Runner
from market_sim.runner.sinks import ListSink


def _llm_sigmas(cfg):
    return [a.params["signal_sigma"] for a in cfg.agents if a.type == "llm_agentic"]


def test_build_config_shape_matches_demo5():
    cfg = build_config({"name": "x", "llm_agentic": 5})
    ids = [a.id for a in cfg.agents]
    assert ids == ["llm1", "llm2", "llm3", "llm4", "llm5", "mm", "noise"]
    assert _llm_sigmas(cfg) == [0.04, 0.06, 0.08, 0.10, 0.12]
    assert cfg.news.enabled and cfg.news.mode == "prob"
    assert cfg.run_name == "x"
    mm = next(a for a in cfg.agents if a.id == "mm")
    assert mm.count == 2 and mm.type == "mm"
    assert [m.id for m in cfg.markets] == ["COIN-A", "COIN-B", "COIN-C"]


def test_capabilities_from_spec():
    cfg = build_config({"name": "caps", "capabilities": {"transfer": True, "advanced_orders": True}})
    assert cfg.capabilities.transfer and cfg.capabilities.advanced_orders
    assert not cfg.capabilities.create_account and not cfg.capabilities.create_market


def test_dump_config_round_trips(tmp_path):
    cfg = build_config({"name": "rt", "llm_agentic": 3, "capabilities": {"create_market": True}})
    p = dump_config(cfg, tmp_path / "rt.yaml")
    loaded = load_config(p)
    assert loaded.model_dump() == cfg.model_dump()


def test_sigma_spread_deterministic_and_single():
    a = _llm_sigmas(build_config({"name": "a", "llm_agentic": 4, "sigma_min": 0.05, "sigma_max": 0.11}))
    b = _llm_sigmas(build_config({"name": "b", "llm_agentic": 4, "sigma_min": 0.05, "sigma_max": 0.11}))
    assert a == b
    one = _llm_sigmas(build_config({"name": "one", "llm_agentic": 1, "sigma_min": 0.07, "sigma_max": 0.2}))
    assert one == [0.07]
    # descending min/max is clamped, not an error
    asc = _llm_sigmas(build_config({"name": "c", "llm_agentic": 3, "sigma_min": 0.1, "sigma_max": 0.02}))
    assert asc == [0.1, 0.1, 0.1]


def test_signals_off_disables_news():
    cfg = build_config({"name": "ns", "signals": False})
    assert cfg.news.enabled is False


def test_mm_and_noise_can_be_omitted():
    cfg = build_config({"name": "lean", "llm_agentic": 2, "include_mm": False, "noise_count": 0})
    types = {a.type for a in cfg.agents}
    assert types == {"llm_agentic"}
    assert len(cfg.agents) == 2


def test_slugify_and_reserved_names():
    assert slugify("My Test!!") == "my-test"
    assert slugify("../../etc/passwd") == "etc-passwd"   # traversal collapsed to a safe slug
    assert slugify("   ") == "scenario"
    assert "demo5" in BUILTIN_RUN_NAMES


def test_no_human_scripted_config_runs():
    # llm_agentic=0 -> bots only -> fast deterministic path, no network, no human seat
    cfg = build_config({"name": "bots", "llm_agentic": 0, "rounds": 5, "mm_count": 2, "noise_count": 1})
    assert all(a.type != "human" for a in cfg.agents)
    r = Runner(cfg, ListSink())
    r.run(2)
    assert r.round_no == 2
    r.exchange.check_invariants()


def test_session_save_template(tmp_path, monkeypatch):
    import market_sim.server.session as sess_mod
    monkeypatch.setattr(sess_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sess_mod, "_TEMPLATES_DIR", tmp_path / "templates")
    s = sess_mod.SimulationSession(runs_dir=tmp_path / "runs")

    rel = s.save_template("Smoke Test", {"llm_agentic": 0, "rounds": 5,
                                         "mm_count": 2, "noise_count": 1})
    assert rel == "templates/smoke-test.yaml"
    assert (tmp_path / "templates" / "smoke-test.yaml").exists()
    assert {"file": "templates/smoke-test.yaml", "builtin": False} in s.scenarios()

    assert s.load_config_file(rel) is True
    assert s.runner is not None
    s.runner.step()
    assert s.runner.round_no == 1

    with pytest.raises(ValueError):       # reserved built-in name rejected
        s.save_template("demo5", {})
    # path traversal can't escape templates/
    assert s.load_config_file("templates/../../etc/passwd.yaml") is False
