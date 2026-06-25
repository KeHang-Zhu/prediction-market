# Generative Market Simulation

A reproducible, event-sourced **binary prediction-market engine** with three faces over one
engine:

1. a **terminal CLI** for running, inspecting, and replaying simulations;
2. a **browser visualizer** that renders the trading process live (order book, price chart,
   trade tape, P&L) and accepts the same agent commands in an in-page console;
3. a **Python library** (`market_sim`) you can script directly.

The engine uses a single order book in YES-price coordinates with **mint/merge** (buying YES
vs. buying NO mints a YES+NO share pair into a collateral pool; selling YES vs. selling NO
merges it back out), integer-cent pricing, round-based **blind submission**, and append-only
JSONL event sourcing with **byte-exact replay**. See [`docs/architecture.md`](docs/architecture.md).

## Install

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"     # add ".[dev,llm]" for the LLM agents
cd web && npm install && npm run build && cd -     # build the browser UI (optional)
```

Or use the Makefile: `make setup` (venv + Python + frontend deps), `make test`, `make web`.

> The project path may contain a space, which breaks the generated console script's shebang.
> Use `./market <cmd>` (the provided wrapper) or `./.venv/bin/python -m market_sim.cli.main <cmd>`.

## Quick start

```bash
# offline scripted example — no API key needed
./.venv/bin/python experiments/quickstart.py

# the test suite (engine invariants, determinism, replay, acceptance, web)
./.venv/bin/python -m pytest

# build + serve the browser app on http://127.0.0.1:8000
make web        # or: ./.venv/bin/python -m market_sim.server
```

## Terminal CLI

State persists between invocations (each command is its own process).

```bash
./market init --config scenarios/demo.yaml          # initialize a run
./market run --rounds 200                            # advance 200 rounds
./market step                                        # advance one round
./market status                                      # state of all markets
./market book COIN-A                                 # order-book ladder
./market portfolio mm                                # an account (cash / positions)
./market tape COIN-A --last 20                       # recent trades
./market replay --log runs/demo/<ts>.jsonl           # verify byte-exact replay
./market plot  --log runs/demo/<ts>.jsonl --out runs/demo.png   # price/volume figure
```

`make demo` runs the whole sequence end-to-end.

## Browser app

`make web` builds the React bundle (into `market_sim/server/static/`) and serves it; the
engine is the single source of truth and the browser renders the event stream. It auto-loads
`scenarios/demo.yaml`, paused at round 0 — press **play**.

- **Order book / price & volume / trade tape / portfolios** panels.
- **Transport** (play / pause / step / speed / reset) and a **scrubber** to time-travel
  through a recorded run.
- **Scenario** picker (built-ins from `scenarios/`) and a **New scenario** builder that
  defines LLM agents in groups (model + count + thinking) plus market-maker / noise bots.
- **History** picker for cinematic, timestamp-paced replay of saved runs
  (`runs/<scenario>/<ts>.jsonl`).
- An in-page **Agent CLI** / **Visual ops** that emit the same action API an agent uses.

Frontend dev mode with hot reload:

```bash
make dev-api    # backend on :8000  (terminal 1)
make dev-ui     # Vite dev server on :5173, proxies /ws + /api  (terminal 2)
```

## LLM agents

The agentic trader (`agents/llm_agent.py`) is a persistent, tool-using agent with a private,
heterogeneous, decaying probability signal. Models are pluggable through a provider factory
(`market_sim/llm/`):

- **Gemini (Vertex AI)** — set `GOOGLE_CLOUD_PROJECT` / `GEMINI_MODEL`, auth via ADC.
- **OpenAI-compatible** (DeepSeek, OpenAI, OpenRouter, local vLLM) — set the matching
  `*_API_KEY` / `*_MODEL`; thinking mode is supported for models that have it.

Copy `.env.example` to `.env` and fill in what you use. A run can mix models per agent. LLM
runs are not byte-exact reproducible (model nondeterminism), so each launch is recorded and
replayed rather than re-run.

## What it guarantees (tested)

- **Conservation, every round**: total cash + all collateral pools == initial total; per
  market YES outstanding == NO outstanding == pool / 100; locked cash/shares match resting
  orders (`Exchange.check_invariants`).
- **Byte-exact replay** of any recorded scripted run with the same seed (all randomness flows
  through one seeded numpy generator; bots use spawned substreams).
- **Acceptance**: the demo converges to `true_prob ± 5¢` and the market maker is profitable in
  a pure-noise environment.

## Project layout

```
market_sim/      the library
  engine/        matching engine — models, book, ledger, settlement, exchange + invariants
  runner/        config, event sourcing, sinks, the round loop, scenario builder, replay
  agents/        agent protocol, scripted bots, LLM traders
  llm/           model providers (Gemini, OpenAI-compatible) for the LLM agents
  commands/      shared command layer used by both the CLI and the web console
  cli/           terminal CLI
  server/        FastAPI + WebSocket backend, hosts the built UI from static/
web/             React/Vite frontend (builds into market_sim/server/static/)
scenarios/       scenario YAMLs (+ archive/, templates/)
experiments/     research / example scripts (quickstart.py)
tests/           pytest + hypothesis
docs/            architecture notes
```

## License

MIT — see [LICENSE](LICENSE).
