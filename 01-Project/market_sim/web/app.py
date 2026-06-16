"""FastAPI application: WebSocket transport + static hosting of the built UI.

A single global SimulationSession is the authoritative state shared by all
connected browsers. On startup the demo config is auto-initialized (paused at
round 0) so the page has something to show immediately.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from market_sim.agents.llm_agent import agentic_agent_meta
from market_sim.commands.agent_api import run_agent_line

from .protocol import command_result_msg, error_msg, event_batch_msg
from .session import EVENT_CHUNK, SimulationSession

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEMO_CONFIG = Path(os.environ.get("GMS_DEMO_CONFIG", "demo.yaml"))

session = SimulationSession()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if session.runner is None and DEMO_CONFIG.exists():
        session.load_config_file(DEMO_CONFIG.name)
    yield


app = FastAPI(title="Generative Market Simulation", lifespan=_lifespan)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "active": session.runner is not None,
            "round": session.max_round(), "mode": session.mode}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    session.clients.add(ws)
    # initial sync: playback state + full event history, chunked so no single frame
    # gets huge for long runs (frontend dedupes by event_id)
    await ws.send_json({"type": "hello", "playback": session.playback_state(),
                        "scenarios": session.scenarios(), "recordings": session.recordings(),
                        "agent_meta": agentic_agent_meta(session.current_capabilities())})
    log = session.event_log
    for i in range(0, len(log), EVENT_CHUNK):
        # event_log holds Event objects (live) or plain dicts (replay / resumed) —
        # normalize both, exactly like flush(), so reconnecting during a replay works.
        batch = [(e.to_dict() if hasattr(e, "to_dict") else e) for e in log[i:i + EVENT_CHUNK]]
        await ws.send_json(event_batch_msg(batch))
    try:
        while True:
            msg = await ws.receive_json()
            await _handle(msg, ws)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        session.clients.discard(ws)


async def _handle(msg: dict, ws: WebSocket) -> None:
    t = msg.get("type")
    if t == "play":
        await session.play()
    elif t == "pause":
        await session.pause()
    elif t == "step":
        await session.step_once()
    elif t == "speed":
        session.set_speed(msg.get("value", 4))
        await session.broadcast_playback()
    elif t == "seek":
        # scrubbing is client-side over the streamed event log; nothing to do here
        pass
    elif t == "reset_run":
        async with session.lock:
            ok = session.reset_run()
        if ok:
            await session.broadcast({"type": "reset"})
            await session.flush()
            await session.broadcast_playback()
        else:
            await ws.send_json(error_msg("nothing to reset — no run loaded"))
    elif t == "load":
        name = msg.get("config", "")
        async with session.lock:
            ok = session.load_recording(name) if name.endswith(".jsonl") else session.load_config_file(name)
        if ok:
            await session.broadcast({"type": "reset"})
            await session.flush()
            await session.broadcast_playback()
            await session.broadcast_library()
        else:
            await ws.send_json(error_msg(f"unknown config: {name}"))
    elif t == "save":
        async with session.lock:
            saved = session.save_run()
        if saved:
            await session.broadcast_library()
            await ws.send_json({"type": "saved", "file": saved})
        else:
            await ws.send_json(error_msg("nothing to save yet — run at least one round"))
    elif t == "save_scenario":
        # build a Config from the builder form's spec, write it as templates/<slug>.yaml,
        # then load it (reusing the load-success broadcast sequence so it appears in the
        # picker and becomes the active run).
        name = (msg.get("name") or "").strip()
        spec = msg.get("spec") or {}
        if not name:
            await ws.send_json(error_msg("a scenario needs a name"))
            return
        async with session.lock:
            try:
                rel = session.save_template(name, spec)
                ok = session.load_config_file(rel)
            except Exception as exc:  # ValidationError / ValueError / OSError -> user error
                await ws.send_json(error_msg(f"could not save scenario: {exc}"))
                return
        if not ok:
            await ws.send_json(error_msg("scenario saved but failed to load"))
            return
        await session.broadcast({"type": "reset"})
        await session.flush()
        await session.broadcast_playback()
        await session.broadcast_library()
        await ws.send_json({"type": "saved", "file": rel})
    elif t == "resume":
        name = msg.get("config", "")
        async with session.lock:
            ok = session.resume_recording(name)
        if ok:
            await session.broadcast({"type": "reset"})
            await session.flush()
            await session.broadcast_playback()
        else:
            await ws.send_json(error_msg("this run can't be continued (no saved state)"))
    elif t == "command":
        line = (msg.get("line") or "").strip()
        if not line:
            return
        # the browser console speaks the AGENT CLI (the proposal API); operator
        # control (init/run/step/reset) is via the transport buttons. Serialize with
        # in-flight LLM steps so a console order can't race the engine mutation.
        async with session.lock:
            result = run_agent_line(session, line)
        await session.flush()
        await ws.send_json(command_result_msg(msg.get("id"), result))
        await session.broadcast_playback()
    else:
        await ws.send_json(error_msg(f"unknown message type: {t}"))


# --- static hosting (registered last so /ws and /api take precedence) ---

if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    @app.get("/", response_class=HTMLResponse)
    async def _not_built() -> str:
        return (
            "<html><body style='font-family:system-ui;max-width:640px;margin:64px auto;color:#1e293b'>"
            "<h2>Frontend not built</h2>"
            "<p>The API and WebSocket are running, but the browser UI bundle is missing.</p>"
            "<pre style='background:#f1f5f9;padding:12px;border-radius:8px'>"
            "cd market_sim/web/frontend\nnpm install\nnpm run build</pre>"
            "<p>or run <code>make build-web</code> from the project root, then reload.</p>"
            "</body></html>"
        )
