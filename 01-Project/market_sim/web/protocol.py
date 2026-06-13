"""WebSocket message helpers.

Client -> server (JSON, discriminated on ``type``):
  {"type": "play"} | {"type": "pause"} | {"type": "step"}
  {"type": "speed", "value": <rounds/sec>}
  {"type": "load", "config": "<scenario.yaml | recording.jsonl>"}
  {"type": "save"}                     # persist the current live run to disk
  {"type": "reset_run"}
  {"type": "command", "id": <str>, "line": "<raw CLI line>"}
  {"type": "seek", "round": <int>}     # advisory; scrubbing is client-side

Server -> client:
  {"type": "hello", "playback": {...}, "scenarios": [...], "recordings": [...]}
  {"type": "library", "scenarios": [...], "recordings": [...]}   # picker refresh
  {"type": "reset"}                                  # a fresh run started
  {"type": "event_batch", "events": [<event dict>...]}
  {"type": "command_result", "id", "ok", "text", "data", "error"}
  {"type": "playback", "mode", "speed", "current_round", "max_round"}
  {"type": "saved", "file": "<relative path>"}       # a manual Save completed
  {"type": "error", "message"}
"""

from __future__ import annotations


def playback_msg(mode: str, speed: float, current_round: int, max_round: int) -> dict:
    return {"type": "playback", "mode": mode, "speed": speed,
            "current_round": current_round, "max_round": max_round}


def event_batch_msg(events: list[dict]) -> dict:
    return {"type": "event_batch", "events": events}


def command_result_msg(msg_id, result) -> dict:
    return {"type": "command_result", "id": msg_id, "ok": result.ok, "verb": result.verb,
            "text": result.text, "data": result.data, "error": result.error}


def error_msg(message: str) -> dict:
    return {"type": "error", "message": message}
