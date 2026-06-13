# GMS — LLM Showcase (pure-frontend replay)

> **🔗 Live demo:** https://gmsdemo-mocha.vercel.app/

A **server-less** build of the Generative Market Simulation showcase. It replays
recorded runs of the **`llm5_only`** scenario — **five tool-using LLM traders** (no
market-maker, no noise bots) in a 3-market world — entirely in the browser. No
backend, no API keys, no WebSocket: the recorded events are bundled and driven by a
local replay engine, so this deploys as a static site (Vercel).

It is the exact showcase UI from the live app (`market_sim/web/frontend`); only the
transport was swapped from WebSocket → bundled recordings.

## Two bundled replays (switch in the transport bar)

- **full · 2 rounds · with dialogue** (`src/replay.json`) — carries the verbatim
  model dialogue (`model_turn` + `briefing`) and the authoritative matching trace
  (`clearing_trace`), so **every demo feature works** on it.
- **long · 6 rounds** (`src/replay-6r.json`) — a longer run for watching price
  convergence; recorded before per-turn dialogue capture, so the agent walkthrough
  falls back to the tool-call trail there.

## What you see

- **Showcase (landing):** order book · price convergence (mid vs. true probability) ·
  trade tape, and a per-agent inspector — expand an agent (`llm1`…`llm5`) to watch its
  tool-call trail (read → belief/plan → orders → lesson), private signal-vs-truth
  chart, holdings, and P&L.
- **⚙ how matching works** (header) → a self-contained, stepped tutorial of the
  clearing mechanism (YES/NO basics → MINT → TRANSFER → MERGE → a whole round →
  settlement) with toy Alice/Bob/Carol examples. Needs no replay data.
- **see it on a real round →** (header) → the order-by-order matching trace of a real
  round (book before/after each order, the cross, the settle type).
- **🔬 round walkthrough** (per agent) → the verbatim SYSTEM ↔ MODEL dialogue for one
  agent in one round: system prompt + tools, the wake-up briefing, then each model
  turn (raw text + calls) paired with the system's response.
- **Transport:** replay picker · play / pause / step a round · speed slider · round
  scrubber. Slide speed to ×1 for the original real-time tempo (model-thinking pauses
  included).

## Run locally

```bash
npm install
npm run dev      # http://localhost:5173
```

## Build

```bash
npm run build    # → dist/
npm run preview  # serve the production build
```

## Deploy to Vercel

This project is deployed at **https://gmsdemo-mocha.vercel.app/**.

Vercel auto-detects the Vite preset (build `npm run build`, output `dist`).

```bash
# from this `demo/` directory
vercel            # preview deployment
vercel --prod     # production
```

Or, in the Vercel dashboard, import the repo and set **Root Directory** to `demo`.

## Refresh the bundled recordings

Re-export a run's JSONL to the bundled JSON (the full-feature slot is `src/replay.json`;
the longer slot is `src/replay-6r.json`):

```bash
python3 -c "import json,sys; \
json.dump([json.loads(l) for l in open(sys.argv[1]) if l.strip()], \
open(sys.argv[2],'w'), ensure_ascii=False, separators=(',',':'))" \
  ../01-Project/runs/llm5_only/<timestamp>.jsonl  src/replay.json
```

Only runs recorded with the `model_turn` / `briefing` / `clearing_trace` instrumentation
carry the full dialogue + matching trace; older runs still replay (showcase + tool-call
trail), they just fall back where that data is absent. `AGENT_META` (the system prompt +
tool catalogue shown in the walkthrough) is bundled statically in `src/agentMeta.ts` —
keep it in sync with `market_sim/agents/llm_agent.py`.
