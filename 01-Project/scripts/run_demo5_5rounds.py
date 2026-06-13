"""Run demo5 for 5 rounds and save BOTH the JSONL recording and a resume snapshot
(.session.pkl), mirroring what the web 'Save' button writes — so the recording shows
up in the history picker AND is resumable (⏯) for a real live continuation later.
"""
from __future__ import annotations

import pickle
import time
from datetime import datetime
from pathlib import Path

from market_sim.runner.config import load_config
from market_sim.runner.simulation import Runner
from market_sim.runner.sinks import JsonlEventSink

ROUNDS = 5
cfg = load_config("demo5.yaml")
run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
jsonl = Path("runs/demo5") / f"{run_id}.jsonl"
pkl = jsonl.with_name(jsonl.stem + ".session.pkl")

sink = JsonlEventSink(str(jsonl))
runner = Runner(cfg, sink)
print(f"START {run_id}: demo5 x{ROUNDS} rounds (cap={cfg.max_actions_per_agent}) -> {jsonl}", flush=True)

t0 = time.monotonic()
for r in range(1, ROUNDS + 1):
    runner.step()
    print(f"  round {r}/{ROUNDS} done @ {time.monotonic() - t0:.0f}s", flush=True)
sink.close()

# resume snapshot (runner.__getstate__ drops the unpicklable sink; agents keep memory)
with pkl.open("wb") as fh:
    pickle.dump({"runner": runner, "config": cfg, "run_id": run_id}, fh)

print(f"DONE jsonl={jsonl} pkl={pkl} elapsed={time.monotonic() - t0:.0f}s", flush=True)
