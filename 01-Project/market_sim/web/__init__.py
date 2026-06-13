"""Web layer: FastAPI + WebSocket wrapper over the engine.

The Python engine remains the single source of truth. The browser holds no
authoritative state — it renders the event-sourced stream and scrubs over it
client-side.
"""
