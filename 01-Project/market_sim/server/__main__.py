"""Launch the web app: ``python -m market_sim.server`` (serves the built UI + WS).

Env: GMS_HOST (default 127.0.0.1), GMS_PORT (default 8000), GMS_DEMO_CONFIG.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("GMS_HOST", "127.0.0.1")
    port = int(os.environ.get("GMS_PORT", "8000"))
    uvicorn.run("market_sim.server.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
