# Generative Market Simulation — V0

A reproducible, event-sourced **prediction-market engine** faithful to Polymarket's
contract semantics, with three faces over one engine:

1. a **terminal CLI** for running, inspecting, and replaying simulations;
2. a **browser dashboard** that visualizes the trading process live (order book, price
   chart, trade tape, P&L) and lets you type the *same* CLI commands in an in-page console;
3. a Python library (`market_sim`) you can script directly.

The engine implements a single order book in YES-price coordinates with **mint/merge**
(buying YES vs. buying NO mints a share pair into a collateral pool; selling YES vs.
selling NO merges it back out), integer-cent pricing, round-based blind submission with
random execution order, and append-only JSONL event sourcing with **byte-exact replay**.

> The design rationale (the six core decisions D1–D6, the integer accounting, and the
> determinism contract) is documented in `../03-Docs/PROJECT_GUIDE.md`.

---

## Quick start

```bash
# 1. set everything up (Python venv + deps, frontend deps)
make setup

# 2. run the test suite (engine invariants, determinism, replay, acceptance, web)
make test

# 3. build the UI and launch the browser app
make web              # -> http://127.0.0.1:8000   (press play)
```

`make` is optional — the explicit commands are below.

### Without make

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
cd market_sim/web/frontend && npm install && npm run build && cd -

./.venv/bin/python -m pytest                 # tests
./.venv/bin/python -m market_sim.web         # serve the app on :8000
```

> **Note on the project path containing a space.** The generated `market` console script's
> shebang breaks when the install path has a space, so use either `./market <cmd>`
> (a wrapper provided here) or `./.venv/bin/python -m market_sim.cli.main <cmd>`.

---

## The browser app

`make web` builds the React bundle and serves it (the Python engine is the single source
of truth; the browser only renders the event stream). It auto-loads `demo.yaml`, paused at
round 0 — press **play**.

Panels:
- **Order book** — live bid/ask ladder in YES-price coordinates with depth bars.
- **Price & volume** — per-market price trajectory with the `true_prob` reference line,
  trade markers colored by settle type, and a volume sub-chart.
- **Trade tape** — streaming fills color-coded by settle type
  (`transfer_yes` / `transfer_no` / `mint` / `merge`).
- **Portfolios & P&L** — per-agent cash (available/locked), positions, and a P&L leaderboard.

Controls:
- **Transport** — play / pause / step / speed / **reset** (rebuild at round 0).
- **Scrubber** — drag the round slider to time-travel backward through the recorded run;
  the panels reconstruct that round's state. Click **LIVE** to snap back to the head.
- **Scenario** — a two-way picker (top-left): **Human Demo** (`demo.yaml`, scripted bots +
  a human seat, classic trading dashboard) or **LLM Showcase** (`demo5.yaml`, 5 agentic LLMs
  with private heterogeneous signals, on the unified showcase page).
- **History** — a separate picker listing every saved run (`runs/<scenario>/<ts>.jsonl`,
  grouped by scenario, newest first); pick one for a cinematic, timestamp-paced replay.
- **Bottom panel — tabs:**
  - **Terminal** — an in-page **Agent CLI** that speaks the *research-proposal API*
    (`get_markets`, `get_orderbook --market M`, `place_order --market M --side buy --price P
    --qty Q [--token YES|NO] [--agent A]`, `cancel_order --order-id N`, `get_portfolio`,
    `get_trade_history --market M`; `create_account` / `create_market` / `transfer` return a
    structured `not_supported`). This is the interface an agent/LLM uses to act — handy for demos.
  - **Visual ops** — a form-based GUI (agent / market / token / side / price / qty, place &
    cancel, query buttons) that emits the *exact same* agent commands, so clicking and typing
    are equivalent.
  - (The **LLM Showcase** uses the unified page instead of these tabs — see below.)
  - While playing, agent orders inject into the next round (preserving blind-submit); while
    paused they execute immediately against the current book.
- **Language** — English / Chinese toggle (top-right); the choice persists. URL overrides:
  `?lang=zh`, `?tab=visual|llm`.

### LLM Showcase — live launch, save, and cinematic replay

Pick **LLM Showcase** (`demo5.yaml`) to put **five persistent, tool-using Gemini (Vertex)
agents** (`type: llm_agentic`) into the market, each with a private, heterogeneous, decaying
probability signal. The page splits into the live market (left) and a per-agent inspector
(right) where you expand an agent to watch its tool-call trail — and open any call to its raw
args + return.

- **Live launch** — press **play** and it auto-runs to the configured horizon, streaming each
  round as it computes. The five agents' decisions for a round are **independent under blind
  submit, so they run concurrently** (~5× faster); rounds are paced by model latency, off the
  event loop. Pause / step any time.
- **Every launch is saved** — to `runs/demo5/<timestamp>.jsonl` (never overwritten), with a
  timestamp on every event including each tool call.
- **Cinematic replay** — pick the run from **History** and it replays event-by-event at the
  recorded tempo (scrub / step / speed-scaled), so you watch the agents think in real time.

Requires the `[eval]` deps and Vertex ADC (see *LLM rationality eval* below); the model comes
from `.env` `GEMINI_MODEL`. LLM runs are **not** byte-exact reproducible (model nondeterminism)
— which is exactly why each launch is saved and replayed rather than re-run.

> **Two interfaces, by design.** The *human terminal CLI* below is the **operator** console
> (init / run / step / replay / plot …) for driving and inspecting experiments. The *browser
> Terminal tab* is the **agent CLI** — the action API a trader/LLM uses inside the market.
> Both are thin shells over the same engine.

### Frontend dev mode (hot reload)

```bash
make dev-api     # backend on :8000  (terminal 1)
make dev-ui      # Vite dev server on :5173, proxies /ws + /api  (terminal 2)
```

---

## The terminal CLI

State persists between invocations (each command is its own process).

```bash
./market init --config demo.yaml                 # initialize a run
./market run --rounds 200                         # advance 200 rounds
./market step                                     # advance one round
./market status                                   # current state of all markets
./market book COIN-A                              # order book ladder
./market portfolio mm                             # an account (available/locked/positions)
./market tape COIN-A --last 20                    # recent trades
./market order place --agent me --market COIN-A \
    --token YES --side buy --price 75 --qty 12    # manual order (eats the book)
./market replay --log runs/demo.jsonl             # verify byte-exact replay
./market plot  --log runs/demo.jsonl --out runs/demo.png   # price/volume figure
```

`make demo` runs the whole sequence end-to-end and writes `runs/demo.png`.

### 5-minute demo script

1. `./market init --config demo.yaml` — three coin markets, 3 noise traders + 1 market
   maker + 1 fundamentalist + a human seat.
2. `./market run --rounds 200` — watch it converge.
3. `./market status` / `./market book COIN-A` — COIN-A's mid sits near 65¢ (its `true_prob`).
4. `./market order place --agent me --market COIN-A --token YES --side buy --price 75 --qty 12`
   — a manual order crosses the book; the fills mix `transfer_yes` and `mint`.
5. `./market plot --log runs/demo.jsonl` — the convergence figure.
6. `./market replay --log runs/demo.jsonl` — byte-exact replay confirms determinism.

---

## What it guarantees (tested)

- **Conservation, every round**: total cash + all collateral pools == initial total;
  per market YES outstanding == NO outstanding == pool / 100; locked cash/shares exactly
  match resting orders. (`Exchange.check_invariants`)
- **Byte-exact replay**: re-running a logged config with the same seed reproduces the event
  stream byte-for-byte (wall-clock `ts` masked). All randomness flows through one seeded
  numpy `Generator`; bots use spawned substreams so they never perturb the draw sequence.
- **Acceptance**: the demo runs clean for 200 rounds, the fundamentalist's markets converge
  to `true_prob ± 5¢`, and the market maker is profitable in a pure-noise environment.

Run `make test` to check all of the above (engine oracles + hypothesis property tests +
runner determinism + web WebSocket + acceptance).

---

## Project layout

```
market_sim/
  engine/      pure engine — models, book, ledger (4 settles + invariants), exchange, settlement
  runner/      config, event sourcing + canonical JSON, sinks, the round loop, replay
  commands/    shared command layer (one dispatch used by BOTH the CLI and the web console)
  agents/      Agent protocol + scripted bots (NoiseTrader, NaiveMM, ZIC, Fundamentalist)
  cli/         typer terminal CLI
  web/         FastAPI + WebSocket backend, session/playback, and the React/Vite frontend
tests/         pytest + hypothesis
demo.yaml      the demo experiment
```

Two seams keep things decoupled: the **shared command layer** (`commands/dispatch`) means
the terminal and browser run identical command logic, and the **event-sink fanout**
(`runner/sinks`) lets the runner emit to the JSONL log and the WebSocket broadcast without
knowing who is listening.

---

## Deferred (not built in V0)

Since V0, the **LLM pieces are built**: the agentic tool-using trader
(`agents/llm_agent.py`), the **private heterogeneous decaying signal** information structure
(`news.mode: prob`), the live auto-run + per-launch save + cinematic replay, and the offline
**LLM rationality eval** (8 probes + scorecard; see `../03-Docs/LLM_RATIONALITY_EVAL_REPORT.md`,
and the full project walkthrough in `../03-Docs/PROJECT_GUIDE.md`).

Still out of scope (V1 backlog, config switches pre-wired and defaulting off): market orders,
fees, maker rewards, categorical markets, and `create_market`/`transfer`.
