"""Event sinks — the seam that decouples the runner from event consumers.

The runner emits to ONE sink. The CLI uses ``FanoutSink([JsonlEventSink, ...])``;
the web uses ``FanoutSink([JsonlEventSink, CallbackSink(broadcast)])``. The runner
never knows who is listening.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from .events import Event, event_line


class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...


class ListSink:
    """Collects events in memory (tests, replay, web in-memory log)."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)


class JsonlEventSink:
    """Append-only JSONL writer, one canonical event per line."""

    def __init__(self, path: str | Path, append: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a" if append else "w", encoding="utf-8")

    def emit(self, event: Event) -> None:
        self._fh.write(event_line(event))
        self._fh.write("\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


class CallbackSink:
    """Invokes a callback per event (web broadcast)."""

    def __init__(self, fn: Callable[[Event], None]) -> None:
        self.fn = fn

    def emit(self, event: Event) -> None:
        self.fn(event)


class FanoutSink:
    def __init__(self, children: list[EventSink]) -> None:
        self.children = children

    def emit(self, event: Event) -> None:
        for child in self.children:
            child.emit(event)

    def close(self) -> None:
        for child in self.children:
            if hasattr(child, "close"):
                child.close()  # type: ignore[attr-defined]
