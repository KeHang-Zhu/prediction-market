"""Session — holds the live Runner and decides how console orders are submitted.

The CLI uses the base Session (orders execute immediately against the current
book). The web's SimulationSession subclasses this to add a broadcast sink and to
inject orders into the next round during live playback.
"""

from __future__ import annotations

import pickle
from datetime import datetime
from pathlib import Path

from market_sim.agents.base import Action
from market_sim.runner.config import Config
from market_sim.runner.simulation import Runner
from market_sim.runner.sinks import EventSink, FanoutSink, JsonlEventSink


class Session:
    def __init__(self, runs_dir: str | Path = "runs") -> None:
        self.runs_dir = Path(runs_dir)
        self.runner: Runner | None = None
        self.config: Config | None = None
        self.log_path: Path | None = None
        self.run_id: str | None = None       # per-launch instance id (timestamp)
        self._sink: FanoutSink | None = None

    # --- hooks the web overrides ---

    def extra_sinks(self) -> list[EventSink]:
        """Additional sinks (e.g. WS broadcast). Base session adds none."""
        return []

    def _live_disk_sinks(self) -> list[EventSink]:
        """Sinks that persist events to disk AS THEY HAPPEN. The CLI writes the run
        log live; the web overrides this to defer persistence to an explicit Save."""
        return [JsonlEventSink(self.log_path)]

    # --- lifecycle ---

    def before_init(self) -> None:
        """Called at the very start of init (web clears its in-memory log here)."""

    def init(self, config: Config, run_id: str | None = None) -> None:
        self.before_init()
        # If the previous run never advanced past round 0, drop its config-only stub
        # so idle scenario-loads / resets don't pile up empty recordings.
        self._discard_if_empty()
        if self._sink is not None:
            self._sink.close()
        self.config = config
        self.run_id = run_id or self._make_run_id(config.run_name)
        # One file per launch: runs/<scenario>/<timestamp>.jsonl — never overwritten,
        # so every live run is saved and can be replayed afterwards.
        self.log_path = self.runs_dir / config.run_name / f"{self.run_id}.jsonl"
        self._sink = FanoutSink([*self._live_disk_sinks(), *self.extra_sinks()])
        self.runner = Runner(config, self._sink)
        self.on_init()

    def _make_run_id(self, run_name: str) -> str:
        """A filesystem-safe per-launch id; disambiguates within the same second."""
        base = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        scen_dir = self.runs_dir / run_name
        rid, n = base, 1
        while (scen_dir / f"{rid}.jsonl").exists():
            n += 1
            rid = f"{base}-{n}"
        return rid

    def _discard_if_empty(self) -> None:
        """Remove the current run's log if it never advanced past round 0 (only the
        config + initial snapshot were written). Keeps runs/ free of idle stubs."""
        if (self.runner is not None and self.runner.round_no == 0
                and self.log_path is not None and self.log_path.exists()):
            if self._sink is not None:
                self._sink.close()
                self._sink = None
            try:
                self.log_path.unlink()
                parent = self.log_path.parent
                if parent != self.runs_dir and parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass

    def on_init(self) -> None:
        """Called after a fresh runner is built (web resets playback here)."""

    def require_runner(self) -> Runner:
        if self.runner is None:
            raise RuntimeError("no active run — use `init` first")
        return self.runner

    # --- order submission policy (overridden by web for live playback) ---

    def submit_order(self, agent_id: str, action: Action):
        """Immediate execution against the current book (CLI default)."""
        return self.require_runner().execute_now(agent_id, action)

    # --- persistence across CLI invocations (each command is a fresh process) ---

    def _state_path(self, run_name: str) -> Path:
        return self.runs_dir / f"{run_name}.session.pkl"

    def _current_pointer(self) -> Path:
        return self.runs_dir / ".current"

    def save(self) -> None:
        if self.runner is None or self.config is None:
            return
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with self._state_path(self.config.run_name).open("wb") as fh:
            pickle.dump({"runner": self.runner, "config": self.config,
                         "log_path": str(self.log_path)}, fh)
        self._current_pointer().write_text(self.config.run_name, encoding="utf-8")

    def load(self, run_name: str | None = None) -> bool:
        if run_name is None:
            ptr = self._current_pointer()
            if not ptr.exists():
                return False
            run_name = ptr.read_text(encoding="utf-8").strip()
        path = self._state_path(run_name)
        if not path.exists():
            return False
        with path.open("rb") as fh:
            data = pickle.load(fh)
        self.runner = data["runner"]
        self.config = data["config"]
        self.log_path = Path(data["log_path"])
        # reattach a fresh APPEND sink so new events extend the existing log
        self._sink = FanoutSink([JsonlEventSink(self.log_path, append=True), *self.extra_sinks()])
        self.runner.sink = self._sink
        return True
