"""Minimal offline example — run the built-in scripted demo (no LLM, no API key).

    python experiments/quickstart.py

Loads scenarios/demo.yaml, advances 200 rounds with the scripted bots, prints the final
per-market prices, and checks the engine's conservation invariants.
"""

from __future__ import annotations

from pathlib import Path

from market_sim.runner.config import load_config
from market_sim.runner.simulation import Runner
from market_sim.runner.sinks import ListSink

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    cfg = load_config(ROOT / "scenarios" / "demo.yaml")
    runner = Runner(cfg, ListSink())
    runner.run(200)

    print(f"ran {runner.round_no} rounds of '{cfg.run_name}'")
    for mid, mv in runner._market_views().items():
        true_pct = next((int(m.true_prob * 100) for m in cfg.markets if m.id == mid), None)
        print(f"  {mid}: bid={mv.best_bid} ask={mv.best_ask} last={mv.last_trade} (true_prob={true_pct})")

    runner.exchange.check_invariants()
    print("engine invariants hold")


if __name__ == "__main__":
    main()
