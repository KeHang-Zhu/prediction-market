"""SimulationSession — authoritative server-side state + live playback.

Extends the shared Session with: an in-memory event log (the substrate the
browser scrubs over), a set of connected WebSocket clients, and an async playback
loop that advances the runner and streams new events. Orders typed in the console
inject into the next round while playing (preserving blind-submit), or execute
immediately while paused.
"""

from __future__ import annotations

import asyncio
import json
import pickle
from datetime import datetime
from pathlib import Path

from market_sim.agents.base import Action
from market_sim.commands.session import Session
from market_sim.runner.config import load_config
from market_sim.runner.sinks import CallbackSink, EventSink, FanoutSink

from .protocol import event_batch_msg, playback_msg

# max events per WebSocket frame (keeps frames well under typical 1 MB limits)
EVENT_CHUNK = 200
# Live-stream drip beat: when a LIVE round's events are pushed to the browser they're
# revealed one at a time with this small beat, so an agent's tool calls appear one-by-one
# instead of the whole round dumping at once. (Recorded REPLAY no longer uses any floor or
# cap — its pacing is a pure ×speed multiplier on the recorded inter-event gaps, so ×1 is
# faithful real-time and higher values compress linearly; see _paced_gap.)
REPLAY_MIN_GAP = 0.15
# Upper bound on a single recorded inter-event gap (before ÷speed). Live recordings can
# carry minutes of dead time between rounds (rate-limit backoffs, or the operator pausing
# between rounds); without a cap, replay stalls there and looks like it "only played one
# round". Genuine model-thinking pauses (a few seconds) are well under this and pass through.
REPLAY_MAX_GAP = 8.0
# project root holds the *.yaml scenario configs the UI can switch between
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The canonical scenarios the UI offers, in display order: a human-interactive
# scripted demo, the 5-agent agentic-LLM showcase (with mm + noise for liquidity),
# and a pure-LLM variant where the five agents are the only market participants.
# Other *.yaml in the project root (e.g. archived experiments) are intentionally
# NOT offered in the picker.
SCENARIO_FILES = ("demo.yaml", "demo5.yaml", "llm5_only.yaml")


def _last_round(path: Path) -> int:
    """The final round number of a recording, read cheaply from the file's tail
    (the last event is a round_end / snapshot). 0 for a config-only stub."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", "ignore")
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        return int(json.loads(lines[-1]).get("round", 0)) if lines else 0
    except (OSError, ValueError, KeyError):
        return 0


class SimulationSession(Session):
    def __init__(self, runs_dir="runs") -> None:
        super().__init__(runs_dir)
        self.event_log: list = []
        self.clients: set = set()
        self.mode: str = "paused"
        self.speed: float = 4.0
        self.busy: bool = False                      # an LLM step is in progress
        self.lock = asyncio.Lock()                   # serializes step vs console mutations
        self.current_config: str | None = None       # filename of the loaded scenario / recording
        self.current_scenario: str | None = None      # run_name of the active scenario (history scoping)
        self._sent: int = 0
        self._play_task: asyncio.Task | None = None
        # replay mode: a recorded run streamed from a *.jsonl log instead of a live engine
        self.replay: dict | None = None

    # --- available scenario configs + recordings (UI dropdown) ---

    def scenarios(self) -> list[dict]:
        """The whitelisted scenarios offered in the picker, in display order."""
        return [{"file": f} for f in SCENARIO_FILES if (_PROJECT_ROOT / f).exists()]

    def recordings(self) -> list[dict]:
        """Saved runs for the history picker — one per launch, grouped under their
        scenario as runs/<scenario>/<timestamp>.jsonl. Newest first; config-only
        stubs (never advanced past round 0) are skipped."""
        out = []
        for p in self.runs_dir.rglob("*.jsonl"):
            rounds = _last_round(p)
            if rounds < 1:
                continue
            rel = p.relative_to(self.runs_dir)
            out.append({"file": rel.as_posix(),
                        "scenario": rel.parts[0] if len(rel.parts) > 1 else "—",
                        "ts": p.stem, "rounds": rounds,
                        "resumable": self._state_pkl(p).exists()})
        out.sort(key=lambda r: r["ts"], reverse=True)
        return out

    def load_config_file(self, name: str) -> bool:
        """Switch to a named scenario config from the project root (whitelisted)."""
        if name not in SCENARIO_FILES or not (_PROJECT_ROOT / name).exists():
            return False
        self.replay = None
        self.init(load_config(_PROJECT_ROOT / name))
        self.current_config = name
        self.current_scenario = self.config.run_name
        return True

    def load_recording(self, name: str) -> bool:
        """Load a recorded run (runs/*.jsonl) for replay — no live engine. Events are
        grouped by round and revealed progressively by play/step (fast, free)."""
        from market_sim.runner.events import read_events
        if not name.endswith(".jsonl"):
            return False
        path = self.runs_dir / name
        try:                                  # keep replays confined to runs/
            path.resolve().relative_to(self.runs_dir.resolve())
        except ValueError:
            return False
        if not path.exists():
            return False
        events = read_events(str(path))
        has_steps = any(e["type"] in ("llm_call", "agent_query", "signal") for e in events)
        full_max = max((int(e["round"]) for e in events), default=0)
        self._stop_play_task()
        self.runner = None
        self.config = None
        self.event_log = []
        self._sent = 0
        self.busy = False
        self.mode = "paused"
        # cursor-based replay: events are revealed one at a time, paced by their
        # recorded ts, so playback unfolds at the original tempo (cinematic). `round`
        # tracks the max round revealed so far; `max_round` is the recording's total.
        self.replay = {"events": events, "cursor": 0, "round": 0,
                       "max_round": full_max, "has_steps": has_steps}
        self.current_config = name
        parts = Path(name).parts
        self.current_scenario = parts[0] if len(parts) > 1 else "—"
        self._reveal_round_zero()   # show config + initial snapshot immediately
        return True

    def resume_recording(self, name: str) -> bool:
        """Turn a loaded recording into a LIVE run continued from its end state by
        restoring the saved engine + agent (memory) snapshot. Needs the sibling
        .session.pkl that Save writes. Works for scripted (deterministic) and LLM
        (each agent keeps its conversation memory) runs alike."""
        if not name.endswith(".jsonl"):
            return False
        rec = self.runs_dir / name
        try:
            rec.resolve().relative_to(self.runs_dir.resolve())
        except ValueError:
            return False
        pkl = self._state_pkl(rec)
        if not rec.exists() or not pkl.exists():
            return False
        from market_sim.runner.events import read_events
        events = read_events(str(rec))
        with pkl.open("rb") as fh:
            data = pickle.load(fh)
        self._stop_play_task()
        self.replay = None
        self.runner = data["runner"]            # round_no = N, _eid continues, RNG + agents restored
        self.config = data["config"]
        self.run_id = data.get("run_id") or rec.stem
        self.log_path = rec                     # a later Save extends this same run file
        self.event_log = list(events)           # full prior history stays visible + scrubbable
        self._sent = 0
        self.busy = False
        self.mode = "paused"
        if self._sink is not None:
            self._sink.close()
        # memory-only sink (disk deferred to Save); new rounds stream + append here
        self._sink = FanoutSink([*self._live_disk_sinks(), *self.extra_sinks()])
        self.runner.sink = self._sink
        self.current_config = name
        parts = Path(name).parts
        self.current_scenario = parts[0] if len(parts) > 1 else "—"
        return True

    @staticmethod
    def _ts_seconds(ev: dict) -> float | None:
        ts = ev.get("ts")
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts).timestamp()
        except ValueError:
            return None

    def _reveal_round_zero(self) -> None:
        """Reveal the setup events (round 0: config + initial snapshot) at once."""
        rp = self.replay
        if rp is None:
            return
        rp["round"] = 0
        while rp["cursor"] < len(rp["events"]) and int(rp["events"][rp["cursor"]]["round"]) == 0:
            self.event_log.append(rp["events"][rp["cursor"]])
            rp["cursor"] += 1

    def _reveal_next_event(self) -> dict | None:
        """Reveal exactly one recorded event (cinematic playback). None at the end."""
        rp = self.replay
        if rp is None or rp["cursor"] >= len(rp["events"]):
            return None
        ev = rp["events"][rp["cursor"]]
        self.event_log.append(ev)
        rp["cursor"] += 1
        r = int(ev["round"])
        if r > rp["round"]:
            rp["round"] = r
        return ev

    def _reveal_next_round(self) -> bool:
        """Reveal every event of the next round at once (the step button on a
        recording). Returns False at the end of the recording."""
        rp = self.replay
        if rp is None or rp["cursor"] >= len(rp["events"]):
            return False
        target = int(rp["events"][rp["cursor"]]["round"])
        while rp["cursor"] < len(rp["events"]) and int(rp["events"][rp["cursor"]]["round"]) == target:
            self._reveal_next_event()
        return True

    def _paced_gap(self, cur: dict, nxt: dict | None) -> float:
        """Seconds to wait between revealing event ``cur`` and the next event ``nxt`` during
        cinematic REPLAY: the recorded inter-event gap (capped at ``REPLAY_MAX_GAP`` so long
        idle/backoff dead time between rounds can't stall playback) divided by the speed
        slider. Model-thinking pauses are under the cap, so ×1 still reproduces the real-time
        tempo; higher values compress it linearly."""
        if nxt is None:
            return 0.0
        a = self._ts_seconds(cur)
        b = self._ts_seconds(nxt)
        real = (b - a) if (a is not None and b is not None and b > a) else 0.0
        return min(real, REPLAY_MAX_GAP) / max(0.5, self.speed)

    def _replay_gap(self, revealed: dict) -> float:
        """Wait before the NEXT recorded event during cinematic replay."""
        rp = self.replay
        nxt = rp["events"][rp["cursor"]] if (rp and rp["cursor"] < len(rp["events"])) else None
        return self._paced_gap(revealed, nxt)

    # --- sink seam: capture every emitted event into the in-memory log ---

    def extra_sinks(self) -> list[EventSink]:
        return [CallbackSink(self.event_log.append)]

    def _live_disk_sinks(self) -> list[EventSink]:
        # the web does NOT write to disk while running — a run is kept in memory and
        # only persisted when the user clicks Save (see save_run).
        return []

    @staticmethod
    def _state_pkl(log_path: Path) -> Path:
        """The engine-state snapshot saved next to a recording (for Continue)."""
        return log_path.with_name(log_path.stem + ".session.pkl")

    def save_run(self) -> str | None:
        """Persist the current LIVE run to runs/<scenario>/<run_id>.jsonl (the manual
        Save button), PLUS a sibling .session.pkl snapshot of the full engine + agent
        state so the run can later be RESUMED. Returns the saved path relative to
        runs/, or None when there's nothing worth saving (no live run / still round 0)."""
        if self.runner is None or self.log_path is None or self.runner.round_no < 1:
            return None
        from market_sim.runner.events import canonical_json, event_line
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w", encoding="utf-8") as fh:
            for ev in self.event_log:        # event_log mixes Event objects (live) and dicts (resumed)
                fh.write((event_line(ev) if hasattr(ev, "to_dict") else canonical_json(ev)) + "\n")
        try:                                  # resume snapshot is best-effort; replay always works
            with self._state_pkl(self.log_path).open("wb") as fh:
                pickle.dump({"runner": self.runner, "config": self.config, "run_id": self.run_id}, fh)
        except Exception:                     # noqa: BLE001
            pass
        return self.log_path.relative_to(self.runs_dir).as_posix()

    def before_init(self) -> None:
        self._stop_play_task()
        self.replay = None
        self.event_log = []
        self._sent = 0
        self.mode = "paused"

    def reset_run(self) -> bool:
        """Rebuild the run from the currently loaded config (same seed -> a clean,
        reproducible restart at round 0). For a recording, rewind to round 0."""
        if self.replay is not None:
            self._stop_play_task()
            self.event_log = []
            self._sent = 0
            self.busy = False
            self.mode = "paused"
            self.replay["cursor"] = 0
            self.replay["round"] = 0
            self._reveal_round_zero()
            return True
        if self.config is None:
            return False
        self.init(self.config)
        return True

    # --- order policy: inject while playing, immediate while paused ---

    def submit_order(self, agent_id: str, action: Action):
        if self.mode == "playing":
            self.require_runner().inject_action(agent_id, action)
            return None
        return self.require_runner().execute_now(agent_id, action)

    # --- playback state ---

    def max_round(self) -> int:
        if self.replay is not None:
            return max(0, self.replay["round"])
        return self.runner.round_no if self.runner else 0

    def playback_state(self) -> dict:
        msg = playback_msg(self.mode, self.speed, self.max_round(), self.max_round())
        # the steps panel shows for live LLM runs AND recordings that contain agent steps
        msg["has_llm"] = bool((self.runner and self.runner.has_llm)
                              or (self.replay and self.replay["has_steps"]))
        msg["replay"] = self.replay is not None
        msg["busy"] = self.busy
        msg["config_name"] = self.current_config
        msg["scenario"] = self.current_scenario       # scopes the history picker
        # a loaded recording can be CONTINUED only if its engine-state snapshot exists
        msg["resumable"] = bool(self.replay is not None and self.current_config
                                and self._state_pkl(self.runs_dir / self.current_config).exists())
        return msg

    # --- broadcasting ---

    async def broadcast(self, msg: dict) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def flush(self) -> None:
        """Send events appended since the last flush to all clients, chunked so no
        single WebSocket frame gets huge (e.g. after a long `run`)."""
        if self._sent >= len(self.event_log):
            return
        new = self.event_log[self._sent:]
        self._sent = len(self.event_log)
        for i in range(0, len(new), EVENT_CHUNK):
            batch = [(e.to_dict() if hasattr(e, "to_dict") else e) for e in new[i:i + EVENT_CHUNK]]
            await self.broadcast(event_batch_msg(batch))

    async def broadcast_playback(self) -> None:
        await self.broadcast(self.playback_state())

    async def broadcast_library(self) -> None:
        """Push the scenario + recordings lists (the history picker refreshes so a
        just-finished run appears without a page reload)."""
        await self.broadcast({"type": "library",
                              "scenarios": self.scenarios(), "recordings": self.recordings()})

    # --- transport controls ---

    async def play(self) -> None:
        if self.mode == "playing":
            return
        if self.replay is None and self.runner is None:
            return
        # LLM scenarios now auto-run too: each round is several model calls, so the
        # loop runs them off the event loop and paces by model latency (see _play_loop).
        self.mode = "playing"
        await self.broadcast_playback()
        if self._play_task is None or self._play_task.done():
            self._play_task = asyncio.create_task(self._play_loop())

    async def pause(self) -> None:
        self.mode = "paused"
        await self.broadcast_playback()

    async def step_once(self) -> None:
        if self.busy or self.mode == "playing":   # don't manual-step over an auto-run
            return
        if self.replay is not None:
            self._reveal_next_round()
            await self.flush()
            await self.broadcast_playback()
            return
        if self.runner is None:
            return
        async with self.lock:
            if self.runner.has_llm:
                # An LLM round is tens of seconds of model calls — stream its events out as
                # they're emitted (tool calls appear in real time, "thinking" shows while it
                # computes) instead of a long blank wait then the whole round at once.
                self.busy = True
                await self.broadcast_playback()
                try:
                    await self._step_and_stream()
                except Exception as exc:  # invariant or engine error -> surface, halt
                    self.busy = False
                    self.mode = "paused"
                    await self.broadcast({"type": "error", "message": f"engine error: {exc}"})
                    await self.broadcast_playback()
                    return
                self.busy = False
                await self.broadcast_playback()
                return
            # scripted round is instant -> just run it and flush
            try:
                await asyncio.to_thread(self.runner.step)
            except Exception as exc:  # invariant or engine error -> surface, halt
                self.mode = "paused"
                await self.broadcast({"type": "error", "message": f"engine error: {exc}"})
                await self.broadcast_playback()
                return
            await self.flush()
            await self.broadcast_playback()

    def set_speed(self, value: float) -> None:
        self.speed = max(0.5, min(60.0, float(value)))

    async def _drip_pending(self, beat: float) -> None:
        """Broadcast events appended since the last send ONE AT A TIME, with a small beat
        between them so each is individually visible. Reveals only what's already been
        emitted — callers re-invoke as more arrives (the engine emits over the round)."""
        while self._sent < len(self.event_log):
            cur = self.event_log[self._sent]
            cur_d = cur.to_dict() if hasattr(cur, "to_dict") else cur
            self._sent += 1
            await self.broadcast(event_batch_msg([cur_d]))
            await self.broadcast_playback()
            if beat:
                await asyncio.sleep(beat)

    async def _step_and_stream(self) -> None:
        """Advance ONE live round in a worker thread while streaming its events to clients
        AS THEY ARE EMITTED. A live LLM round is tens of seconds of model calls that the
        engine emits progressively (each agent's signals -> tool reads -> decision -> the
        orders), so this surfaces every tool call in real time instead of a blank wait
        followed by the whole round dumping at once. Propagates engine errors to the
        caller. Used by both auto-run (_play_loop) and manual Step."""
        step_task = asyncio.create_task(asyncio.to_thread(self.runner.step))
        try:
            while not step_task.done():
                await self._drip_pending(REPLAY_MIN_GAP)   # stream what's emitted so far
                if not step_task.done():
                    await asyncio.sleep(0.05)               # let more events accumulate
        finally:
            await step_task                                # surface errors; never orphan it
        await self._drip_pending(REPLAY_MIN_GAP)           # tail emitted right at round end

    async def _play_loop(self) -> None:
        while self.mode == "playing":
            if self.replay is not None:
                # cinematic: reveal one event, then wait the recorded gap to the next
                ev = self._reveal_next_event()
                if ev is None:
                    self.mode = "paused"     # reached the end of the recording
                    await self.broadcast_playback()
                    break
                await self.flush()
                await self.broadcast_playback()
                gap = self._replay_gap(ev)
                if gap:
                    await asyncio.sleep(gap)
                continue
            if self.runner is None:
                break
            if self.runner.has_llm:
                # Live LLM auto-run. A round is tens of seconds of model calls; stream its
                # events out AS THEY ARE EMITTED (see _step_and_stream) so each agent's tool
                # calls appear in real time. "thinking" shows while the round computes; a
                # pause takes effect at the round boundary (a round can't be cancelled mid
                # model call). The step is serialized with console mutations via the lock.
                if self.runner.round_no >= self.runner.config.rounds:
                    self.mode = "paused"          # reached the configured horizon -> stop
                    await self.broadcast_playback()
                    break
                self.busy = True
                await self.broadcast_playback()
                async with self.lock:
                    try:
                        await self._step_and_stream()
                    except Exception as exc:  # noqa: BLE001 — invariant/engine error: halt + surface
                        self.busy = False
                        self.mode = "paused"
                        await self.broadcast({"type": "error", "message": f"engine error: {exc}"})
                        await self.broadcast_playback()
                        break
                self.busy = False
                await self.broadcast_playback()
                continue
            # Scripted live: a round is instant, so advance a whole round per speed tick
            # (kept snappy for the 200-round human demo; there are no tool calls to reveal).
            async with self.lock:
                try:
                    await asyncio.to_thread(self.runner.step)
                except Exception as exc:  # noqa: BLE001 — invariant/engine error: halt + surface
                    self.mode = "paused"
                    await self.broadcast({"type": "error", "message": f"engine error: {exc}"})
                    await self.broadcast_playback()
                    break
            await self.flush()
            await self.broadcast_playback()
            if self.runner.round_no >= self.runner.config.rounds:
                self.mode = "paused"          # reached the configured horizon -> stop
                await self.broadcast_playback()
                break
            await asyncio.sleep(1.0 / self.speed)

    def _stop_play_task(self) -> None:
        self.mode = "paused"
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
        self._play_task = None
