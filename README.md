# Generative Market Simulation

A reproducible, event-sourced **prediction-market engine** faithful to Polymarket's
contract semantics, together with **tool-using LLM traders** and an offline evaluation of
their trading-behavior rationality.

> **🔗 Live demo (no install):** https://gmsdemo-mocha.vercel.app/
>
> Five tool-using LLM traders in a 3-market world, replayed entirely in the browser.
> Press **play**; open an agent to watch its tool-call reasoning; try **⚙ how matching works**.

---

## Repository layout

| Folder | What it is |
|---|---|
| **`00-Slide/`** | Progress-update slide decks (`Jun9_GMS_V0_Update.pdf`, `Jun12_GMS_Supplement.pdf`). |
| **`01-Project/`** | The full project — the prediction-market engine, terminal CLI, FastAPI + WebSocket web app with its React/Vite frontend, scripted + LLM agents, the offline eval harness, the test suite, and the recorded run/eval data. |
| **`02-Project-Demo/`** | A server-less build of the LLM showcase: the same frontend as `01-Project`, with its transport swapped from WebSocket → bundled recordings, so it deploys as a static site. This is what the live link above serves. |
| **`03-Docs/`** | `PROJECT_GUIDE.md` (full project guide and code walkthrough) and `LLM_RATIONALITY_EVAL_REPORT.md` (the 8-probe rationality evaluation). |

---

## What the engine does

A single order book per market in **YES-price coordinates**, with **mint/merge**:
buying YES against buying NO **mints** a fresh share pair into a collateral pool; selling
YES against selling NO **merges** a pair back out. It uses integer-cent pricing,
round-based **blind submission** with randomized execution order, and append-only JSONL
event sourcing with **byte-exact replay**.

One engine, three faces:

1. a **terminal CLI** for running, inspecting, and replaying simulations;
2. a **browser dashboard** (order book, price chart, trade tape, P&L) that also exposes an
   in-page Agent CLI and an LLM showcase;
3. a Python library (`market_sim`) you can script directly.

**Agents.** Scripted bots (NoiseTrader, NaiveMM, ZIC, Fundamentalist) and **tool-using
Gemini/Vertex LLM traders**, each with a private, heterogeneous, decaying probability
signal. The **rationality eval** scores model behavior across 8 probes (P1–P8) with a
GO / NO-GO scorecard — see `03-Docs/`.

---

## Quick start

### Full project (`01-Project/`)

```bash
cd 01-Project
make setup      # create venv + install Python deps, install frontend deps
make test       # engine invariants, determinism, replay, acceptance, web
make web        # build the UI and serve → http://127.0.0.1:8000  (press play)
```

Terminal CLI (state persists between commands):

```bash
./market init --config demo.yaml
./market run --rounds 200
./market status
./market replay --log runs/demo.jsonl     # verify byte-exact replay
```

The LLM eval and LLM Showcase scenarios additionally need Vertex AI credentials: copy
`01-Project/.env.example` to `01-Project/.env`, fill in your GCP project, and run
`gcloud auth application-default login`. The engine, CLI, web dashboard, and tests run
without a `.env`.

### Static demo (`02-Project-Demo/`)

```bash
cd 02-Project-Demo
npm install
npm run dev     # http://localhost:5173
```

…or just open the deployed build: **https://gmsdemo-mocha.vercel.app/**

---

## Documentation (`03-Docs/`)

- **`PROJECT_GUIDE.md`** — end-to-end walkthrough: engine design (the six core decisions,
  integer accounting, conservation invariants), the deterministic round loop and replay
  contract, the agent protocol, the web stack, the eval, the recorded data products, and
  the test matrix.
- **`LLM_RATIONALITY_EVAL_REPORT.md`** — the layered (L1/L2/L3) methodology, the eight
  probes in detail, the scoring system and GO / NO-GO criteria, and measured results.

---

## Notes

- The web apps include an **English / Chinese** language toggle (top-right; `?lang=zh`
  overrides).
- `01-Project/runs/` and `01-Project/eval_runs/` hold the recorded simulation logs, session
  snapshots, and eval results referenced by the docs.
