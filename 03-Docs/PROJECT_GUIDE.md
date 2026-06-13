# Generative Market Simulation (V0) — Project Guide

> A module-by-module walkthrough of the entire codebase (roughly 6,200 lines of Python + 2,300 lines of React/TypeScript). It covers the project's purpose, overall architecture, the implementation logic of each subsystem, data artifacts, testing and quality assurance, findings, and an overall assessment.
> Every `file:line` reference in the text can be used to locate the source code directly. The end of the document includes **live-run verification evidence**.
>
> **2026-06-09 update**: Everything has been re-verified item by item against the latest code (as of commit `019e811`). This update covers: event types growing from 16 to 18 (adding `order_queued`/`agent_view`), the `ToolLoopAgent` gaining a `commit_view` tool plus a strict four-stage flow, LLM round events now streaming out one at a time in real time, replay pacing switching to a pure ×speed multiplier (removing clamping and the 5s inter-round gap), Agent API endpoints growing from 8 to 10, and tests growing from 53 to 54. During this re-verification, 3 stale replay-pacing tests were found to have not kept up with the refactor and were failing; they have been rewritten to match the new semantics (pure ×speed, no clamping), and **all 54 tests now pass** (see §10).

---

## Table of Contents

1. [One-Sentence Summary](#1-one-sentence-summary)
2. [Overall Architecture: One Engine, Three Faces](#2-overall-architecture-one-engine-three-faces)
3. [Core Engine engine/ — Prediction-Market Matching Mechanism](#3-core-engine-engine--prediction-market-matching-mechanism)
4. [Runner runner/ — Round Loop, Event Sourcing, and the Determinism Contract](#4-runner-runner--round-loop-event-sourcing-and-the-determinism-contract)
5. [Agents agents/ — Scripted Bots and Tool-Calling LLM Traders](#5-agents-agents--scripted-bots-and-tool-calling-llm-traders)
6. [Shared Command Layer and CLI (commands/ + cli/)](#6-shared-command-layer-and-cli-commands--cli)
7. [Web Application (web/ Backend + Frontend)](#7-web-application-web-backend--frontend)
8. [LLM Rationality Evaluation eval/](#8-llm-rationality-evaluation-eval)
9. [Data Artifacts (runs/ and eval_runs/)](#9-data-artifacts-runs-and-eval_runs)
10. [Testing and Quality Assurance](#10-testing-and-quality-assurance)
11. [Conclusions: Verifying the Three Core Claims + Issue List](#11-conclusions-verifying-the-three-core-claims--issue-list)
12. [Quick Start and How to Run](#12-quick-start-and-how-to-run)
13. [Overall Assessment](#13-overall-assessment)

---

## 1. One-Sentence Summary

This is a **reproducible, event-sourced prediction-market engine** that faithfully reproduces Polymarket's contract semantics, and on top of that single engine offers three ways to use it:

1. **Terminal CLI** — the "operator console" for running, inspecting, and replaying simulation experiments;
2. **Browser dashboard** — live visualization of the trading process (order book, price chart, trade tape, P&L leaderboard), with a built-in web console where you can type the same commands;
3. **Python library (`market_sim`)** — callable directly from scripts.

Its ultimate research goal is to drop **tool-calling LLM agents** into a market with real microstructure (order book, mint/merge, blind submit) and observe their trading rationality and price-convergence behavior under **private, heterogeneous, time-decaying information**, then use an offline evaluation suite to make a go/no-go call on a model's rationality.

The three hard guarantees at the engine level (all test-covered; the 2026-06-09 review re-ran all 54 tests green):
- **Per-round conservation of funds**;
- **Byte-exact replay for the same seed**;
- **Acceptance**: the demo runs 200 rounds cleanly, the market that has a fundamentalist trader converges to the true probability within ±5¢, and the market maker is profitable in a pure-noise environment.

---

## 2. Overall Architecture: One Engine, Three Faces

```
market_sim/
  engine/      Pure engine — data models, order book, ledger (4 settlement types + invariants), matching exchange, market settlement
  runner/      Config, event sourcing + canonical JSON, sink fan-out, round main loop, replay verification
  commands/    Shared command layer (one dispatch serving both the CLI and the web console)
  agents/      Agent protocol + scripted bots (noise / market-making / ZIC / fundamentalist) + tool-calling LLM traders
  cli/         Typer terminal CLI
  web/         FastAPI + WebSocket backend, sessions/replay, and a React/Vite frontend
  eval/        Offline LLM rationality evaluation (8 probes + scorecard)
tests/         pytest + hypothesis property tests
scripts/       Helper scripts (run_demo5_5rounds.py: runs demo5 offline and writes both JSONL + a resumable .session.pkl)
demo.yaml      Manual demo experiment (scripted bots + 1 human seat)
demo5.yaml     LLM Showcase experiment (5 tool-calling LLMs + private heterogeneous signals)
```

The whole system stays clean thanks to **two decoupling seams**:

- **Shared command layer (`commands/dispatch`)**: the terminal and the browser run the **same command logic**; they merely have different command sets and parse entry points;
- **Event sink fan-out (`runner/sinks`)**: the runner only cares about `emit(event)` and not who is listening — JSONL persistence, WebSocket broadcast, and in-memory buffering are all pluggable downstreams.

**Core data flow** (within one round):

```
Config (YAML) → Runner init (build markets/accounts/agents, derive random-number tree)
  └─ each round step():
      1a Publish news / private signals     (consume the main rng in a deterministic order)
      1b Resolve maturing markets           (use the outcome pre-sampled at init)
      2  Freeze decision snapshot (blind submit) → each agent runs decide() concurrently to produce actions
      3  Determine execution order          (LLMs by finish time, pure scripts by a seeded permutation)
      4  Execute actions → Exchange matching → emit an event per fill
      5  check_invariants()                 (the three conservation invariants)
      6  Generate snapshot + round_end event
  └─ all events → sink → {JSONL log, WebSocket broadcast, in-memory event_log}
```

> The design philosophy (the full argument for the six decisions D1–D6, integer accounting, and the determinism contract) is detailed in the sections below — see §3 (core engine) and §4 (runner and the determinism contract).

---

## 3. Core Engine engine/ — Prediction-Market Matching Mechanism

The engine is the most intricate part of the whole project: a **single-order-book** binary prediction-market exchange that keeps its books in **integer cents**, supports **four settlement types**, and guarantees financial consistency via **three conservation invariants**.

### 3.1 File Composition

| File | Responsibility |
|---|---|
| `engine/models.py` (193 lines) | Data models: `Token/Side/BookSide/SettleType` enums, `Order/Account/Market/Trade/Fill` dataclasses, coordinate-transform functions |
| `engine/book.py` (126 lines) | Order book `OwnerBook`: uses a `SortedDict` to store the BID/ASK price ladders, with a FIFO queue per level, supporting best-price crossing and aggregate-depth queries |
| `engine/ledger.py` (112 lines) | Ledger: cash/share lock primitives + four settlement functions, guaranteeing integer exactness |
| `engine/exchange.py` (294 lines) | Exchange controller: place/match/cancel, true-intent classification, the three invariant checks, fills and price history |
| `engine/settlement.py` (63 lines) | Market-maturity settlement: cancel-and-unlock, pay the winning side at 100 cents/share, drain the collateral pool |

### 3.2 Key Design 1: YES-Price Coordinates + Single-Book Mirroring

In a prediction market, YES and NO are a **complementary pair**: one YES + one NO is always worth 100 cents ($1). This engine maintains only **one** order book, coordinated by the YES price (1–99 cents); NO orders are mapped in via **mirroring** (`models.py:59-71`):

- Buy YES, sell NO → goes into the **BID** (buy side)
- Sell YES, buy NO → goes into the **ASK** (sell side)
- A NO price `n` maps to the YES price `100 - n` (i.e., `NO@45 ≡ YES@55`)

An order object (`Order`) records both its **true intent** (`token / side / limit_price`, each in its own coordinates) and its **book coordinates** (`book_side / book_price` are dynamically derived properties rather than stored fields, keeping a single source of truth). `seq_id` is a globally monotonically increasing sequence number, used for time priority (FIFO) within the same price level.

### 3.3 Key Design 2: Integer-Cent Accounting + Overpayment Refund

All amounts are stored in **integer cents**, eliminating floating-point rounding error. Matching settles at the maker's price; if the taker posted a more aggressive limit, the difference is returned as a **refund** (`(true_limit - pay) * qty`). For example: a maker sells YES@58, a taker buys YES@62 for a quantity of 12 — the taker locks `62×12=744` cents, the trade clears at 58, and `(62-58)×12=48` cents are refunded. This way any limit price settles with integer exactness, with no slippage or rounding loss.

### 3.4 Key Design 3: Four Settlement Types (Including Mint/Merge, Unique to Prediction Markets)

During matching, the engine **automatically classifies** the settlement type from the true intents of the two crossing orders (`exchange.py:_settle`), rather than having it specified at order time:

| Settlement Type | Trigger Condition | Funds/Share Flow | Collateral Pool Change |
|---|---|---|---|
| `TRANSFER_YES` | Buy YES ↔ Sell YES | YES shares transferred between the two parties | 0 |
| `TRANSFER_NO` | Buy NO ↔ Sell NO | NO shares transferred between the two parties | 0 |
| **`MINT` (mint)** | Buy YES ↔ Buy NO | The system **mints a pair** (1 YES + 1 NO); the two buyers jointly pay 100 cents | **+100×q** |
| **`MERGE` (merge)** | Sell YES ↔ Sell NO | The system **destroys a pair**, releasing 100 cents split between the two sellers at the trade price | **−100×q** |

**Mint** and **merge** are the core distinction between a prediction market and a traditional matching engine: when two people want to buy YES and buy NO respectively, there are no existing shares to hand over, so the system **mints** a new pair of shares out of thin air and locks both parties' money into the collateral pool; conversely, when two people sell YES and sell NO respectively, the share pair is **destroyed** and the collateral pool releases cash. `ledger.py:40-107` implements the three settlement functions; the merge split `(p, 100-p)` is exact in the integer domain (`p×q + (100-p)×q = 100×q`, the identity holds with no rounding).

> This is exactly where the market maker's profit comes from: `NaiveMM` posts buy YES and buy NO simultaneously, and when both sides fill at the same price level it triggers a mint, earning the spread.

### 3.5 Key Design 4: The Three Conservation Invariants

`Exchange.check_invariants()` (`exchange.py:245-294`) can be called **after every operation**; a violation raises `InvariantError`:

- **INV-A (cash conservation)**: the sum of all account cash (available + locked) + all market collateral pools == the initial total `total0`.
- **INV-B (share-pool consistency)**: for each market, `pool % 100 == 0`, and `pool/100 == YES outstanding == NO outstanding`. This guarantees that at market maturity the pool holds **exactly** enough to pay the winners (100 cents per share).
- **INV-C (lock exactness)**: the sum of all locked cash == the sum of `limit × remaining` over all resting buy orders; the sum of all locked shares == the sum of `remaining` over all resting sell orders.

### 3.6 Order Lifecycle and Market Settlement

- **Place `place_order`**: ① validate market/price/quantity → ② pre-check budget/inventory (**zero lock on rejection**, no leakage) → ③ lock resources → ④ enter the matching loop `_match`, repeatedly taking the best-price crossing counterparty until the taker is exhausted; any remainder rests in the book.
- **Cancel `cancel_order`**: locate the order → unlock → remove → mark `CANCELLED`.
- **Market settlement `resolve_market`** (`settlement.py`): at maturity, **first cancel and unlock all resting orders** (ordering is critical, otherwise locked cash would leave the pool short), then pay 100 cents per share to holders of the winning token based on the outcome, drain the collateral pool, and mark the market `RESOLVED`.

---

## 4. Runner runner/ — Round Loop, Event Sourcing, and the Determinism Contract

The runner wraps the engine into an **event-driven round loop** and implements the project's most important engineering contract: **byte-exact reproducibility**.

### 4.1 File Composition

| File | Responsibility |
|---|---|
| `runner/config.py` (68 lines) | Pydantic model for the YAML experiment config: `markets/agents/news/seed/rounds/...` + reserved V1 switches (off by default) |
| `runner/events.py` (88 lines) | Event types + `canonical_json` canonical serialization (the key to byte-exact reproducibility) |
| `runner/sinks.py` (71 lines) | The event sink protocol and implementations: `ListSink/JsonlEventSink/CallbackSink/FanoutSink` |
| `runner/simulation.py` (551 lines) | The core round main loop, random-number tree, blind submit, concurrent LLM decisions, private signals |
| `runner/replay.py` (31 lines) | Replay from JSONL and verify byte-exact consistency |

### 4.2 Event Sourcing and Canonical JSON

Each event is `{event_id, round, type, agent_id, payload, result, ts}`, across **18 types** (`config/round_start/news/signal/agent_view/order_queued/place_order/cancel_order/fill/mint/merge/resolution/payout/snapshot/invalid_action/round_end/llm_call/agent_query`). Two of these are "true call-order" events added for tool-calling LLMs:

- **`order_queued`**: emitted at **the exact moment** a tool-using agent calls `place_order/cancel_order` (during the decision phase), inserting the order into the trace at the position where the model actually called it (interleaved with its read calls); the corresponding `place_order/invalid_action/cancel_order` events (carrying the fill/rejection result) emitted at round-end execution are linked back via a shared `client_id`. Scripted bots do not emit this event (their actions have no `client_id`), so the pure-script event stream still stays byte-exact.
- **`agent_view`**: emitted at the moment an agent calls `commit_view` to submit its view (per-market YES probability + a one-line plan); it sits after the agent's reads but before its orders — beliefs/plans appear in the trace ahead of the orders they justify.

`canonical_json` serializes with `sort_keys=True, separators=(",",":"), ensure_ascii=False`, guaranteeing a fixed key order and no whitespace — this is the basis for byte-level comparison. The JSONL log's **first line is always `config`** (containing the full configuration), the "source of truth" for replay.

### 4.3 The Round Main Loop (Six Stages, Blind Submit)

`Runner.step()` (`simulation.py:157-249`), each round:

1. **1a Publish news/signals**: in `lean` mode, flip the global announcement with probability `epsilon`; in `prob` mode, give each agent a private signal;
2. **1b Resolve maturing markets**: call `resolve_market` using the outcome pre-sampled at init;
3. **2 Freeze decision snapshot (blind submit)**: first freeze every agent's observation view into the snapshot from the end of the previous round, then run each agent's `decide()` **concurrently** (`ThreadPoolExecutor(max_workers=min(5, N))`, `simulation.py:199`) — because everyone sees the same stale information, decisions are mutually independent and can be safely parallelized; the concurrency cap also serves to flatten Gemini's 429 rate limiting;
4. **3 Determine execution order**: with LLMs present, sort by **actual finish time** (API latency is unpredictable); for pure scripts, use a **seeded permutation** (to guarantee byte-exact replay); actions dropped for exceeding the `max_actions_per_agent` cap, if they had already been announced via `order_queued` (with a `client_id`), get a follow-up `invalid_action` marking them rejected, so the UI doesn't stay stuck on "queued" forever;
5. **4 Execute actions**: feed each into the `Exchange` for matching, emitting the corresponding event per fill;
6. **5 Invariant check** → **6 Generate snapshot + `round_end`**.

Manual orders from a human/console enter via `inject_action` (queued for the next round, preserving blind submit) or `execute_now` (filled immediately while paused).

### 4.4 The Determinism Contract: A Single Random-Number Tree

The heart of determinism is a **NumPy `SeedSequence` derivation tree** (`simulation.py:104-110`):

```
root = SeedSequence(config.seed)
children = root.spawn(1 + N)         # N = number of agents
runner.rng    = default_rng(children[0])      # main stream: outcome pre-sampling, news, execution permutation
agent[i].rng  = default_rng(children[i+1])    # one independent substream per bot
```

- The **main stream `runner.rng`** is used only for three deterministic things: pre-sampling each market's outcome by market-id order at init, news flips / private signals, and the execution-order permutation;
- **Each scripted bot samples only from its own `ctx.rng` (its substream)** and never touches the main stream — so adding or removing a bot's internal randomness **does not perturb** the main sampling sequence.

A grep check confirms: in `scripted.py`, bots use only `ctx.rng`, with no code touching the main `rng`. This is the mechanistic guarantee that makes "byte-exact replay" hold.

> **Replay scope limitation**: LLM runs (including human-injected actions) **cannot** be byte-exact replayed (the models themselves are non-deterministic) — this is an explicit, by-design scope limit, and is exactly why "LLM runs always save a log on every launch, and are then replayed rather than re-run" (see §7).

### 4.5 Replay Verification

`verify_replay(log_path)`: read the log's first `config` line → rebuild the `Runner` with the same seed and re-run → `compare_streams` compares the canonical JSON event by event (`ts` is masked out with a `<ts>` sentinel and ignored) → return `(matched, first_diff_index, detail)`.

---

## 5. Agents agents/ — Scripted Bots and Tool-Calling LLM Traders

### 5.1 Protocol and Context

- **The `Agent` protocol** (`base.py`): a single interface, `decide(DecisionContext) -> list[Action]`. The `last_call` field lets an LLM record its belief/rationale/error; after each round it is emitted as an `llm_call` event and then cleared.
- **`DecisionContext`**: a frozen single-round snapshot — market view, portfolio view, news, private signal, the read-only query callback `query`, plus two event callbacks for tool-using agents: `on_queue` (emits `order_queued` at the moment of placing/canceling) and `on_view` (emits `agent_view` at the moment of submitting a view).
- **Three actions**: `PlaceOrder` / `Cancel` / `Hold`, with prices in each token's own coordinates, feasibility validated by the engine. `PlaceOrder`/`Cancel` carry an optional `client_id` field, used to link the decision-time `order_queued` announcement to the round-end execution result (scripted bots leave it unset).

> **Information isolation**: `true_prob` in `MarketView` is **visible only to scripted bots**; an LLM agent's view has the true value filtered out and must infer it from prices/signals itself. Queries go through the frozen snapshot and cannot see orders the agent or others just placed this round — this protects the integrity of blind submit.

### 5.2 The Four Scripted Bots (Strategy Math)

| Bot | Parameters | Strategy |
|---|---|---|
| **NoiseTrader** | `q` (activity probability) / `w` (mid offset) / `max_qty` | Per market, with probability `q` randomly pick a token/side and quote randomly within `mid±w`; sell orders are inventory-constrained (switch to buy if no shares) |
| **NaiveMM** (market maker) | `spread`/`size`/`skew_unit` | Each round cancels old orders, posts a two-sided YES bid and YES ask (the ask implemented via buy NO@100-ask) at a fixed spread; skews below the mid by `skew_unit` units per unit of net position to manage inventory. Fills on both sides trigger a mint, earning the spread |
| **ZIC** (Zero-Intelligence Constrained) | `q`/`value` (private valuation)/`max_qty` | Gode–Sunder model: bid randomly below valuation, ask randomly above valuation (only when holding shares). **Currently a reserved bot, not used by any config/test** |
| **Fundamentalist** | `threshold`/`size` | **The only agent that can see `true_prob`**: when the price is underpriced beyond the threshold it buys YES, when overpriced it buys NO, pushing the market toward `round(true_prob×100)`. This is the structural force that converges the market to the true value |

### 5.3 LLM Agents: Single-Turn vs. Tool-Calling

- **`LLMAgent` (single-turn)**: each round builds a complete observation JSON (excluding the true value) → has Gemini return a decision as forced JSON → parses it into actions. There is no conversation history; context is provided by a "summary of the last 5 rounds" window.
- **`ToolLoopAgent` (tool-calling, the core innovation)**: maintains a **single persistent conversation** (`contents`) across the entire run. Each round:
  1. Sends a **minimal briefing** (`_wake_briefing`: its own cash/positions/open orders + open and resolved market ids; if there is a new private signal or news this round, it only hints "there is one, go read it" and **explicitly contains no prices, depths, or signal values** — these must be fetched with the read tools);
  2. Enters a tool loop (at most `max_tool_calls=8` model-call turns per round): the model actively calls **10 tools** — 6 read-only (`get_markets / get_orderbook / get_trade_history / get_portfolio / get_news / get_news_detail`, going through the frozen snapshot, free, returning synchronously), 1 **`commit_view(beliefs, plan)`** (submit per-market YES probabilities and a one-line plan before trading, emitting an `agent_view` event; **any order placed before committing is rejected outright**), 2 writes (`place_order / cancel_order`, queued for round-end execution, emitting an `order_queued` event the instant they are called), and 1 `finish(lessons)` to wrap up (records only a one-line "what I learned/corrected this round" — beliefs and plan were already given in commit_view). The flow is enforced as the four stages **read → commit_view → trade → finish**;
  3. The tool-call trace and the raw return values are both recorded, and the frontend can expand each one to inspect it.

  This "you must actively fetch data before you can trade" design deliberately mimics a real trader's information-gathering process, and also makes it easier to study a bare model's behavior with no strategy hints (the system prompt only states the rules and the flow — no strategy, no examples). `__getstate__/__setstate__` drop the non-serializable Gemini client when pickling, but **retain `contents`**, thereby supporting **memory continuity** after archive/restore.

### 5.4 The Private, Heterogeneous, Decaying Information Structure (`news.mode = prob`)

Each round, each agent configured with `signal_sigma` privately receives a noisy estimate of each market's true probability:

```
s = clip(true_prob + N(0, σ_t)),   σ_t = signal_sigma × (1 − sigma_decay × (t-1)/(T-1))
```

- Different agents have different `signal_sigma` → **heterogeneous information quality** (in demo5, llm1 is most accurate at 0.04, llm5 is the worst at 0.12);
- `σ_t` decays as rounds progress → the closer to the endgame, the **sharper** the signal (information becomes progressively complete);
- When `disclose_sigma=true`, the agent is also told its own current credibility ±σ.

Signal generation strictly consumes random numbers in `(agent_id, market_id)` nested sorted order, guaranteeing determinism.

### 5.5 Gemini/Vertex Calls and Rate Limiting (`provider.py`)

`GeminiProvider` wraps Vertex AI (ADC authentication, no API key): forced JSON output + a single parse retry, a **disk cache** keyed by the SHA256 of `(model, temperature, system, user, key)` (re-running the same prompt is free; only successful responses are cached), **exponential backoff retries** on transient errors (429/503 and DNS/connection-class network errors) (`2^i` seconds, up to 5 times), and `sleep(pace)` throttling before each call (the eval default is `pace=0.5s`). It also provides a `tool_turn` method that drives a manual function-calling loop, used by `ToolLoopAgent` (`pace=0.25s`, no cache, `thinking_level="low"` to keep lightweight reasoning).

---

## 6. Shared Command Layer and CLI (commands/ + cli/)

### 6.1 "Two Interfaces, One Engine"

| | Operator CLI (terminal) | Agent API (web console) |
|---|---|---|
| Parse entry | Typer args / `parse_command_line` | `parse_agent_line` |
| Dispatch table | `HANDLERS` (12 commands) | `AGENT_HANDLERS` (10 endpoints) |
| Commands | `init/run/step/status/book/portfolio/tape/order/cancel/replay/plot/help` | `get_markets/get_orderbook/place_order/cancel_order/get_portfolio/get_trade_history/help` + `create_account/create_market/transfer` returning `not_supported` |
| Role | The **operator** that drives and inspects experiments | The research-proposal API by which traders/LLMs **act** within the market |

The two paths use the **same parse/dispatch logic**, and the working endpoints **reuse the same underlying handler** (e.g., `api_get_orderbook` wraps `cmd_book`). The return value is unified as `CommandResult{ok, verb, data, text, error}` — `data` for web JSON, `text` for the terminal, one logic serving both frontends.

`create_account/create_market/transfer` do not return a 404; instead they return a structured `{ok:false, status:'not_supported'}`, letting the LLM recognize "the endpoint exists but is currently unavailable."

### 6.2 Cross-Process State Persistence

Each CLI command is an **independent process**. `Session.save()` pickles the entire `Runner` (including NumPy random-number state, the Exchange, and agent memory) to `runs/<run_name>.session.pkl`, and records the active run with a `runs/.current` pointer; the next command's `load()` deserializes and restores it, then re-attaches the log sink in **append mode** so new events continue writing to the same log. This achieves a "stateful stateless CLI" with no database.

### 6.3 The Terminal CLI (`cli/main.py`, Typer)

State-changing commands like `init/run/step` call `save()` after executing; read-only commands like `book/portfolio/tape/status/replay/plot` do not save. `_render()` uniformly handles output and red error display.

> **The space-in-path pitfall**: the shebang of the `market` console script generated by pip breaks when the path contains a space. The project provides a `./market` bash wrapper that internally uses `python -m market_sim.cli.main` to avoid this (README:48-49).

---

## 7. Web Application (web/ Backend + Frontend)

### 7.1 Backend (FastAPI + WebSocket)

A **global singleton `SimulationSession`** shares one state with all browsers, sending and receiving commands and the event stream bidirectionally over WebSocket. Key paths:

- **Live auto-run**: `_play_loop` is a background async task that loops calling `runner.step()` (run on a thread via `asyncio.to_thread` to avoid rate-limit blocking of the event loop); new events enter the in-memory `event_log` via a `CallbackSink`. LLM rounds no longer "run a whole round and broadcast all at once"; instead `_step_and_stream` **streams out already-emitted events one at a time in real time** during step execution (dripping at a `REPLAY_MIN_GAP=0.15s` pace), so the agents' tool calls appear in the browser one by one in real time, rather than a long blank followed by a full-round dump. It runs to the endgame per `config.rounds` and auto-pauses; you can pause/step at any time.
- **Cinematic replay**: pick a `.jsonl` from History, and events are **revealed one at a time at the cadence of their original timestamps**. Pacing is a **pure ×speed multiplier** (`_paced_gap`: the actual recorded gap ÷ speed, with a speed floor of 0.5 and **no floor/cap clamping**) — at ×1 it faithfully reproduces the original real-time cadence (including the long pauses of the model thinking), and raising the speed compresses it linearly, so you can "watch the agent think in real time."
- **Scrubber time travel**: drag the round slider, and the **frontend** uses the already-loaded full event log to reconstruct the state of any round offline (no server action needed).
- **Save / Resume**: a live run does **not** persist by default; only clicking Save writes the `event_log` to `runs/<scenario>/<ts>.jsonl` and pickles the engine state; afterward you can both **replay** and **resume from the breakpoint** (restoring the engine + agent memory).

**Multi-layer 429 rate-limit protection** (to handle Gemini rate limits): the decision phase uses `ThreadPoolExecutor(max_workers=min(5, N))` to cap concurrency (at `simulation.py:199`, in the runner layer rather than the web layer) + `provider.pace=0.25s` throttling before each call + 5 exponential-backoff retries. (Earlier versions had a hard 5s inter-round gap, removed in the "faster rounds" refactor — the concurrency cap + pacing are enough to flatten the request rate.)

### 7.2 Frontend (React + Vite + Zustand + ECharts)

The frontend **only renders**; the engine is the single source of truth. `store.ts` manages state with Zustand: a batch of events arrives over WebSocket → `applyEvents` deduplicates (by `event_id`) and classifies them into local state (snapshots are stored in a Dict to speed up time-travel queries; trades/turns/signals each have caps to prevent memory overflow).

- **Classic trading dashboard** (manual demo): order-book depth, price/volume chart (with a `true_prob` reference dashed line, trades colored by settlement type), trade tape, portfolio and P&L leaderboard, and a transport bar (play/pause/step/speed/reset/scrubber);
- **Two bottom tabs**: Terminal (the web Agent CLI terminal) and Visual ops (a form-style GUI) — **both emit exactly the same agent commands**, so typing and clicking are equivalent;
- **LLM Showcase** (demo5): a unified page, with the live market on the left and an inspector for each agent on the right — expand to see its tool-call trace (arranged in **true call order**: reads → commit_view's beliefs/plan → orders, drillable down to the raw args + return value; orders carry lifecycle markers, showing "queued" the instant they are placed and flipping after fill/rejection at round-end; for old recordings without a commit_view step, it falls back to the belief/plan/lessons in `llm_call`), the private-signal convergence chart (solid noisy reading vs. dashed true value, visualizing "information getting progressively more accurate"), and a (trimmed) startup briefing consistent with the real run.
- **Internationalization**: EN / ZH bilingual toggle, persisted selection, supporting `?lang=zh` and `?tab=visual|llm` URL overrides.

---

## 8. LLM Rationality Evaluation eval/

An **offline, single-step** evaluation suite that measures an LLM's economic rationality in a binary prediction market, producing a "can this model tier be used directly in multi-round experiments?" go/no-go conclusion. It does **not** run a live multi-round market (that is the later L3 scope).

### 8.1 Two Tiers of Metrics

- **L1 (interface usability)**: `parse_success_rate` (JSON parsed successfully), `response_ok_rate`, `action_valid_rate` (actions pass engine validation), `hallucinated_endpoint_rate` (whether it hallucinates non-existent endpoints), `avg_actions_per_trial`.
- **L2 (8 probes, microeconomic rationality skills)**, where each probe is a frozen scenario + a deterministic criterion:

| Probe | What it tests | Pass criterion (key points) | Go/no-go threshold |
|---|---|---|---|
| P1 | Free lunch | At a 5¢ ask + ~90% favorable, should buy YES | ≥90% |
| P2 | Take the money | Holding 60 YES + a 95¢ bid, should sell | ≥90% |
| P3 | Empty-book market making | Should quote two-sidedly (40≤bid≤75, 45≤ask≤90) | Observational (no threshold) |
| P4 | Complementary arbitrage | When two contracts' asks sum to <100, should buy both for arbitrage | Observational |
| P5 | Don't trade recklessly | At a fair spread, should not cross the spread and recklessly take liquidity | ≥70% |
| P6 | Budget constraint | Total locked should not exceed cash | ≥90% |
| P7 | Information update | On a strong negative signal, should cancel orders / lower belief / reverse | Observational |
| P8 | Order memory | With an existing resting order, should not place a duplicate | Observational |

Go/no-go also requires L1 `action_valid_rate ≥ 95%`. Statistics use a **Wilson confidence interval**.

### 8.2 Actual Evaluation Results (`eval_runs/summary.json`)

Model `gemini-3.1-flash-lite-preview`, `repeats=1`, 8 trials:

- **L1 full marks**: parse success rate, response OK rate, action valid rate, and hallucination rate are all at ideal values (0 hallucinations, 100% legal actions);
- **probes**: P1/P2/P5/P6/P7/P8 pass, **P3 (empty-book two-sided market making) and P4 (cross-contract complementary arbitrage) fail** — the model posted only a one-sided buy on an empty book and bought only one leg in the arbitrage scenario. This reflects this lightweight model's limitations in **multi-step reasoning and cross-market arbitrage recognition**.

> ⚠️ The evaluation currently runs `repeats=1`, so each probe has only 1 sample, the Wilson confidence intervals are extremely wide, and the conclusions are only directionally indicative.

---

## 9. Data Artifacts (runs/ and eval_runs/)

Both directories are `.gitignore`d (local artifacts, not checked in).

**runs/ (simulation-run artifacts)**
- `runs/.current`: plain text, recording the name of the currently active scenario.
- `runs/<scenario>.jsonl`: append-only event-stream log, one canonical-JSON event per line; the first line is always `config`. The source of truth for byte-exact replay.
- `runs/<scenario>.session.pkl`: a pickled snapshot of the engine/session state, for cross-command persistence and pause/resume.
- `runs/<scenario>/<timestamp>.jsonl` + a same-named `.session.pkl`: a named run saved independently on each launch and **never overwritten** (used by the LLM Showcase).
- `runs/demo.png`: a matplotlib price/volume chart.

**eval_runs/ (LLM evaluation artifacts)**
- `summary.json`: evaluation summary (L1 metrics + each probe's `{n,k,passed,rate,ci_lo,ci_hi}` + model/repeats/temperature).
- `trials.jsonl`: per-trial detail (including rationale, attempts, errored), for offline analysis.
- `scorecard.png`: a scorecard bar chart.
- `cache/<hash>.json`: Gemini request-level cache (including the model's raw text + parsed result), supporting offline re-runs, saving tokens, and preserving a trace of model behavior.

---

## 10. Testing and Quality Assurance

7 test modules, **54 tests** in total (the 2026-06-09 review re-ran them all green, 2.3 seconds).

> During the review, 3 replay-pacing tests were found not to have kept up with the "pure ×speed, no clamping" refactor (one `ImportError` referencing the deleted `REPLAY_MAX_GAP`, two asserting removed floor behavior); they have been rewritten to match the new semantics:
> `test_replay_gap_pure_speed_scaling` (no cap + ÷speed + a divisor floor of 0.5),
> `test_replay_zero_gap_not_floored` (zero gap → zero wait, no longer padded), and
> `test_replay_pause_interrupts` (switched to a recording with re-stamped timestamps to verify the still-valid intent that "a pause can interrupt replay").

| Module | Tests | Coverage |
|---|---|---|
| `test_engine.py` | 12 | "Hand-computed numbers" oracles for the four settlements + a mint→transfer→merge scenario + **hypothesis property tests** (conservation at every step over a 200-order random order stream, locks zeroed after cancel, mint↔merge round-trip conservation independent of price) |
| `test_runner.py` | 4 | Conservation across 60 rounds, byte-exact consistency for the same seed, conservation replayed from a log |
| `test_agent_api.py` | 6 | Agent API endpoint dispatch, **does not leak `true_prob`**, the `not_supported` stub |
| `test_web.py` | 6 | WebSocket handshake → event batch → play state, console order placement, no crash on disconnect/reconnect mid-replay |
| `test_refactor.py` | 18 | Concurrent LLMs (finish-time ordering, monotonic event_id), timestamped files not overwritten, cinematic replay pacing (×speed scaling / zero gap not padded / pause can interrupt), the trimmed briefing forcing tool calls, live events streamed one at a time, the save/resume loop and **agent memory persistence** |
| `test_acceptance.py` | 3 | MM profitable across 5 seeds in a pure-noise environment, demo 200-round convergence within ±5¢, 200-round byte-exact replay |
| `test_eval.py` | 5 | Oracle/anti-oracle for the 8 probes, action L1 validation, Wilson CI and metric aggregation |

---

## 11. Conclusions: Verifying the Three Core Claims + Issue List

### 11.1 The Three Core Claims (all ✅ supported, reproduced via live runs)

| Claim | Conclusion | Evidence |
|---|---|---|
| **① Per-round conservation of funds** | ✅ supported | The three-part invariants at `exchange.py:245-294`; the ledger is integer-exact; hypothesis property tests assert every step over a random (including illegal) order stream; **live run: all 54 tests green** |
| **② Byte-exact replay for the same seed** | ✅ supported | A single `SeedSequence` deriving the main stream + a substream per bot; grep confirms bots touch only their own `ctx.rng`; `compare_streams` masks `ts`; **live run: a fresh 200-round demo `verify_replay` returns byte-for-byte identical (`error: None`)**. Scope limit: LLM runs cannot be byte-exact replayed (by design, documented) |
| **③ Acceptance: 200 clean rounds + convergence within ±5¢ + MM profitable** | ✅ supported | **Live run of the demo for 200 rounds**: COIN-A mid=**68¢** (target 65, within ±5), COIN-C mid=**56¢** (target 55, within ±5), COIN-B resolved at round 120; MM profitable across all 5 seeds |

### 11.2 Issue List (by severity; all low-risk, mostly "doc/artifact state inconsistency" rather than engine defects)

**Medium (recommended fix)**
- ~~**3 stale tests failing**: the replay-pacing tests in `test_refactor.py` still assert the old `[0.15s, 2.0s]` floor/cap behavior and import the deleted `REPLAY_MAX_GAP`, inconsistent with the new "pure ×speed multiplier" implementation.~~
  **Fixed (2026-06-09)**: the three tests have been rewritten to match the new pacing semantics, and the suite is back to 54/54 green (see §10).
- `runner/config.py`: the config has **no input validation** — a negative `seed`, `rounds=0`, and `max_actions=-1` all pass; `count=0` is treated as `count=1`. Recommend adding a pydantic validator.
- `web/session.py`: `event_log` stores a mix of `Event` objects and dicts, with many places using a `hasattr(e,'to_dict')` duck check that the type checker cannot verify; new code that assumes everything is an `Event` will crash at runtime.
- **The `runs/demo.jsonl` left on disk is inconsistent with README step 6's example**: the current one is the leftover of a 6-round CLI session that included a **human manual order**, and running `./market replay` on it directly will mismatch (strict replay does not support injected actions, documented). `make demo` first regenerates a pure-bot 200-round log, and under that flow replay passes.
- `commands/handlers.py` + `cli/main.py`: a **replay-mismatch UX bug** — on a mismatch the `error` field is `None`, `_render` takes the `error: {None}` branch, so the CLI prints `error: None` and swallows the "REPLAY MISMATCH at event N + detail." Recommend writing the detail into `error` on mismatch, or having `_render` prefer printing `text` when text is present.

**Low / Nit (cleanup items)**
- **Dangling README reference**: the README wrote "see GUIDE §9" but shipped no matching GUIDE file; the actual guide is this document, `PROJECT_GUIDE.md`.
- **Minor dead code**: the `ZIC` bot is implemented and registered but unused by any config/test; `fees_bps/enable_market_orders/enable_maker_rewards` are reserved V1 switches (off by default, declared in the README).
- **archive/ old configs + stale logs**: `archive/demo_agentic.yaml` and `demo_llm.yaml` have been superseded by `demo5.yaml` and are unreferenced; `runs/demo_agentic.jsonl` and `demo_llm.jsonl` are stale leftovers of old configs. Recommend cleaning up or adding a note.
- **`rich` is declared as a dependency but unused anywhere in the codebase** (a leftover of the early plan); recommend removing it.
- **Test gaps**: replay of human/console-injected actions (action-replay) has no test; the MM-profitability test hardcodes `wins==5` and the measured margin is thin (one seed only 0.06%), with no regression guardrail against parameter drift; the eval probes' decisions depend on real Gemini calls and `repeats=1` is too small a sample.
- Performance nit: `cancel_order`/`get_portfolio`/`validate_action` do O(N) traversals when there are many markets or orders (no reverse index); harmless at the current scale.

### 11.3 Security Hygiene (✅ good)
`.env` contains only Vertex ADC config (project ID / region / model name); authentication goes through machine-level ADC, with **no API key hardcoded anywhere**. `.gitignore` correctly ignores `.env`, `runs/`, `eval_runs/`, `*.session.pkl`, `node_modules/`, `.venv/`, and build artifacts. `git ls-files` shows only 79 files, all source/config, with no artifacts or secrets checked in.

---

## 12. Quick Start and How to Run

```bash
# 1. Install (Python venv + dependencies, frontend dependencies)
make setup

# 2. Run the test suite (engine invariants / determinism / replay / acceptance / web)
make test

# 3. Build the UI and start the browser app
make web              # -> http://127.0.0.1:8000   (click play)
```

A 5-minute terminal CLI script:

```bash
./market init --config demo.yaml          # 3 coin markets + 3 noise + 1 MM + 1 fundamentalist + 1 human seat
./market run --rounds 200                  # advance 200 rounds, watch convergence
./market status                            # COIN-A mid stable near ~65¢ (its true_prob)
./market order place --agent me --market COIN-A --token YES --side buy --price 75 --qty 12
./market plot   --log runs/demo.jsonl      # convergence chart
./market replay --log runs/demo.jsonl      # byte-exact replay verification
```

> The LLM Showcase needs the `[eval]` dependencies + Vertex ADC (`gcloud auth application-default login`); the model comes from `GEMINI_MODEL` in `.env`.
> Because of the 5 live LLMs, it is **recommended to run offline (CLI) and then replay in the browser**, rather than live single-stepping.
> The repo provides `scripts/run_demo5_5rounds.py`: it runs demo5 offline for some number of rounds and simultaneously writes out a JSONL recording and a resumable `.session.pkl` (equivalent to the web Save button's artifacts); once it finishes, you can replay or resume from the breakpoint in the browser's History.

---

## 13. Overall Assessment

This is a **research prototype of quite high maturity**, with engineering quality clearly above a typical demo:

**Highlights**
1. **A rigorous engine**: integer-cent exact accounting, the three-part `check_invariants` (cash conservation / share-pool consistency / lock exactness) combined with hypothesis property tests form a strong oracle; the mint/merge mechanism unique to prediction markets is implemented cleanly and is integer-exact.
2. **A clear determinism contract**: a single `SeedSequence` deriving the main stream + a substream per bot, with clear branch isolation — pure scripts take a deterministic order guaranteeing byte-exact replay, while LLMs take finish-time order and explicitly declare that this breaks byte-exact replay. Double-confirmed by grep + live run.
3. **Decoupled architecture**: the shared dispatch layer makes the CLI and web console come from the same source, the event-sink fan-out decouples the runner from downstream consumers, and pickle state restoration supports pause/resume while retaining agent memory.
4. **A complete loop**: from engine → scripted/LLM agents → the three faces of CLI/Web → the private heterogeneous information structure → offline rationality evaluation, the research pipeline is complete.
5. **Good security hygiene**: no secrets checked in, and `.gitignore` is appropriate.

**Weaknesses** (all low-risk)
- Mostly "doc/artifact state inconsistency": the leftover `runs/demo.jsonl` makes README step 6's example mismatch, the minor UX bug where `error: None` swallows the error message, and the dangling pointer to a non-existent "GUIDE §9";
- The config lacks input validation; the ZIC bot and a few switches are reserved dead code; the MM margin is thin and the eval `repeats=1` is too small a sample.
- (Fixed) 3 replay-pacing tests had not kept up with the refactor; they were rewritten on 2026-06-09 and the suite is back to 54/54 green.

**Improvement suggestions (priority high to low)**: ① fix the `error: None` display on replay mismatch; ② add pydantic validation to the config; ③ correct/remove the README's dangling "GUIDE §9" reference; ④ clean up the `archive/` and stale `runs/*.jsonl` leftovers (or add a note); ⑤ raise the eval `repeats` to tighten the confidence intervals; ⑥ add a "clear hint/test" for replaying logs that include human orders.

> **Core conclusion**: all three engineering claims are credible and reproducible; the engine and determinism are this project's most solid foundation, and the items still to be improved do not touch correctness — they are at the level of polish and documentation consistency.
