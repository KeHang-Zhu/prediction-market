# Architecture

Generative Market Simulation is a binary prediction-market engine with a terminal CLI, a
browser visualizer, and an LLM-agent layer. The Python engine is the single source of
truth; every other surface (CLI, web, eval) is a thin shell over it.

## Layers (`market_sim/`)

```
engine/      Pure matching engine — no I/O, no randomness of its own.
             models (Order/Account/Market/Trade), book (price-time matching),
             ledger (cash/positions + the four settlement ops), settlement
             (resolution), exchange (facade + conservation invariants).
runner/      The round loop and the determinism contract. config (pydantic + YAML
             loader), events (canonical JSON event schema), sinks (memory / JSONL /
             callback / fanout), simulation (the round loop), builder (high-level
             spec -> Config), replay (byte-exact verification).
agents/      Agent protocol + actions (base), scripted bots (NoiseTrader, NaiveMM,
             ZIC, Fundamentalist), and the LLM traders (llm_agent: LLMAgent and the
             persistent tool-using ToolLoopAgent).
eval/        Offline LLM behavioural-rationality eval (probes, judge, scorecard) and
             the model providers (providers/: a neutral LLMProvider interface, the
             Gemini/Vertex provider, and an OpenAI-compatible provider for DeepSeek
             and similar endpoints, behind a get_provider factory).
commands/    One command layer shared by the CLI and the web console (handlers +
             dispatch), the live Session, and the agent-facing action API.
cli/         The terminal CLI (typer).
server/      FastAPI app + WebSocket transport, session/playback, and hosting of the
             built browser UI (static/).
```

The frontend (React/Vite) lives at the top level in `web/` and builds into
`market_sim/server/static/`, which the server hosts at `/`.

## Engine model

A single order book per market in YES-price coordinates. Buying YES versus buying NO
**mints** a YES+NO share pair into a collateral pool; selling YES versus selling NO
**merges** a pair back out. Prices are integer cents (1..99); one YES + one NO is always
worth exactly 100 cents. At resolution the winning side pays 100, the loser 0.

## Determinism

Rounds use blind submit: every agent decides on the same start-of-round snapshot, and all
orders execute together at round end. All randomness flows through one seeded numpy
generator; scripted bots draw from spawned substreams so they never perturb the global
draw sequence. The event stream is append-only JSONL and replays byte-for-byte (wall-clock
timestamps masked). LLM runs are intentionally not byte-exact (model nondeterminism), so
each launch is recorded and replayed rather than re-run.

## What's guaranteed (tested)

- Conservation every round: total cash + all collateral pools == initial total; per market
  YES outstanding == NO outstanding == pool / 100; locked cash/shares match resting orders
  (`Exchange.check_invariants`).
- Byte-exact replay of any recorded scripted run with the same seed.
- Acceptance: the demo converges to `true_prob ± 5¢` and the market maker is profitable in a
  pure-noise environment.

Run `pytest` to check all of the above (engine oracles, hypothesis property tests, runner
determinism, the WebSocket server, and acceptance).
