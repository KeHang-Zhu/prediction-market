"""Web backend: WebSocket sync, stepping, and console commands via the same
shared dispatch the CLI uses."""

from __future__ import annotations

from starlette.testclient import TestClient

from market_sim.web.app import app, session


def _recv_until(ws, pred, limit=40):
    for _ in range(limit):
        m = ws.receive_json()
        if pred(m):
            return m
    raise AssertionError("expected message not received")


def test_ws_hello_and_initial_log():
    with TestClient(app) as client:               # triggers startup auto-init
        assert session.runner is not None
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            batch = _recv_until(ws, lambda m: m["type"] == "event_batch")
            types = {e["type"] for e in batch["events"]}
            assert "config" in types and "snapshot" in types


def test_ws_step_streams_events():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.send_json({"type": "step"})
            pb = _recv_until(ws, lambda m: m["type"] == "playback")
            assert pb["current_round"] >= 1


def test_ws_console_agent_get_orderbook():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "command", "id": "c1", "line": "get_orderbook --market COIN-A"})
            cr = _recv_until(ws, lambda m: m["type"] == "command_result" and m.get("id") == "c1")
            assert cr["ok"] is True and cr["verb"] == "get_orderbook"
            assert cr["data"]["market"] == "COIN-A" and "book" in cr["data"]


def test_ws_console_agent_place_order():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            for _ in range(5):  # advance via transport so a book exists
                ws.send_json({"type": "step"})
                _recv_until(ws, lambda m: m["type"] == "playback")
            ws.send_json({"type": "command", "id": "o1",
                          "line": "place_order --market COIN-A --side buy --price 50 --qty 5"})
            cr = _recv_until(ws, lambda m: m["type"] == "command_result" and m.get("id") == "o1")
            assert cr["ok"] is True and cr["verb"] == "place_order"


def test_ws_console_not_supported_stub():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "command", "id": "t1", "line": "transfer --to mm --amount 100"})
            cr = _recv_until(ws, lambda m: m["type"] == "command_result" and m.get("id") == "t1")
            assert cr["ok"] is False and cr["data"].get("status") == "not_supported"


def test_ws_reconnect_during_replay(tmp_path, monkeypatch):
    """Regression: reconnecting while a recording is loaded must sync the event log
    (which holds plain dicts in replay mode) without crashing on .to_dict()."""
    monkeypatch.setattr(session, "runs_dir", tmp_path)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()                                   # hello
            ws.send_json({"type": "load", "config": "demo.yaml"})
            _recv_until(ws, lambda m: m["type"] == "playback")
            for _ in range(2):
                ws.send_json({"type": "step"})
                _recv_until(ws, lambda m: m["type"] == "playback" and m["current_round"] >= 1)
            ws.send_json({"type": "save"})
            f = _recv_until(ws, lambda m: m["type"] == "saved")["file"]
            ws.send_json({"type": "load", "config": f})         # -> replay mode (dict events)
            _recv_until(ws, lambda m: m["type"] == "playback" and m.get("replay"))
        # a fresh connection (reconnect) must not crash syncing the dict-based log
        with client.websocket_connect("/ws") as ws2:
            assert ws2.receive_json()["type"] == "hello"
            batch = _recv_until(ws2, lambda m: m["type"] == "event_batch")
            assert any(e["type"] == "snapshot" for e in batch["events"])
