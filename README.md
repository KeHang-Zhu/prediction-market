# Generative Market Simulation

A reproducible, event-sourced **prediction-market engine** faithful to Polymarket's
contract semantics, together with **tool-using LLM traders** and a standalone package that
evaluates their trading-behavior rationality.

> **🔗 Live demo (no install):** https://gmsdemo-mocha.vercel.app/
>
> Five tool-using LLM traders in a 3-market world, replayed entirely in the browser.
> Press **play**; open an agent to watch its tool-call reasoning; try **⚙ how matching works**.

---

## Repository layout

| Folder | What it is |
|---|---|
| **`00-Slide/`** | Progress-update slide decks. |
| **`01-Project/`** | The simulator — the prediction-market engine, terminal CLI, FastAPI + WebSocket server, the React/Vite frontend (`web/`), scripted + LLM agents (the model layer supports Gemini/Vertex and OpenAI-compatible endpoints like DeepSeek), scenario configs, the test suite, and recorded run data. |
| **`02-Project-Demo/`** | A server-less build of the LLM showcase: the same frontend as `01-Project`, with its transport swapped from WebSocket → bundled recordings, so it deploys as a static site. This is what the live link above serves. |
| **`03-Docs/`** | `PROJECT_GUIDE.md` (full project guide and code walkthrough) and `LLM_RATIONALITY_EVAL_REPORT.md` (the 8-probe rationality evaluation). |
| **`04-eval/`** | The standalone `gms-eval` package — the offline LLM trading-rationality eval (8 probes + GO/NO-GO scorecard). Depends on the simulator's engine. |

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
LLM traders** (Gemini/Vertex or OpenAI-compatible endpoints like DeepSeek), each with a
private, heterogeneous, decaying probability signal. The separate **rationality eval**
(`04-eval/`) scores model behavior across 8 probes (P1–P8) with a GO / NO-GO scorecard;
the report is in `03-Docs/`.

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
./market init --config scenarios/demo.yaml
./market run --rounds 200
./market status
./market replay --log runs/demo.jsonl     # verify byte-exact replay
```

LLM scenarios need model credentials — Vertex ADC for Gemini, or an API key for an
OpenAI-compatible model (DeepSeek/OpenAI). Copy `01-Project/.env.example` to
`01-Project/.env` and fill in what you use (`gcloud auth application-default login` for
Vertex). The engine, CLI, web dashboard, and tests run without a `.env`.

### Rationality eval (`04-eval/`)

```bash
cd 04-eval
pip install -e ../01-Project[llm]   # the simulator (provides the engine)
pip install -e .                    # the eval package
python -m gms_eval.run_eval --probes P1 --repeats 1
```

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
- `01-Project/runs/` holds the recorded simulation logs and session snapshots;
  `04-eval/eval_runs/` holds the eval scorecards referenced by the report.
