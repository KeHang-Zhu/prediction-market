"""Strict replay: re-run a logged config with the same seed and byte-compare.

V0 strict mode reproduces all-scripted runs exactly (bots are deterministic given
their seeded substreams). Runs that contain human/console-injected actions need
action-replay (feed the logged actions) — a documented extension; strict mode
covers the M4 acceptance (pure-bot run).
"""

from __future__ import annotations

from .config import Config
from .events import compare_streams, read_events
from .simulation import Runner
from .sinks import ListSink


def rerun_from_log(log_path: str) -> list[dict]:
    events = read_events(log_path)
    cfg_event = next(e for e in events if e["type"] == "config")
    config = Config(**cfg_event["payload"]["config"])
    rounds = max((e["round"] for e in events if e["type"] == "round_end"), default=0)
    sink = ListSink()
    runner = Runner(config, sink)
    runner.run(rounds)
    return [ev.to_dict() for ev in sink.events]


def verify_replay(log_path: str) -> tuple[bool, int | None, str]:
    original = read_events(log_path)
    replayed = rerun_from_log(log_path)
    return compare_streams(original, replayed)
