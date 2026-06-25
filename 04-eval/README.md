# gms-eval

Offline **LLM trading-behaviour rationality eval** for Generative Market Simulation.

It checks whether an LLM acting as a trader makes rational micro-decisions in a binary
prediction market. Each of the 8 frozen single-step **probes** builds a market situation
(order book, positions, news), asks the model for a structured JSON decision (beliefs +
actions), and scores pass/fail with a deterministic, executable rule (no LLM judge).

- **L1 — interface validity**: valid JSON? legal engine actions? no hallucinated endpoints?
- **L2 — behavioural rationality**: across 8 trading traps — catch mispricing, take profit,
  make markets, arbitrage, avoid overtrading, respect the budget, update beliefs, remember
  resting orders.

This package depends on the simulator (`generative-market-simulation`) for the engine and
uses its own self-contained forced-JSON Gemini client.

## Install

```bash
# 1) install the simulator (provides the engine)
pip install -e ../01-Project[llm]
# 2) install this eval package
pip install -e .
```

(Once the simulator is published to PyPI, step 1 becomes `pip install generative-market-simulation`
and this package installs standalone.)

Auth: Vertex AI via ADC (`gcloud auth application-default login`). Copy `.env.example` to
`.env` and set `GOOGLE_CLOUD_PROJECT` / `GEMINI_MODEL`.

## Run

```bash
python -m gms_eval.run_eval --probes p1 --repeats 1     # one probe, one repeat
python -m gms_eval.run_eval                             # the full suite
```

Outputs (text summary + `summary.json` + `trials.jsonl` + a scorecard PNG) are written under
`eval_runs/`.

## Layout

```
gms_eval/
  schema.py         forced-JSON decision schema + the neutral system prompt + observation
  gemini_client.py  self-contained forced-JSON Gemini client (caches completions)
  base.py           probe contracts + validate_action (the L1 oracle)
  probes.py         the 8 L2 probes (build + executable judge)
  scenarios.py      helpers that build frozen single-step market states
  metrics.py        parse/valid/hallucination/pass-rate summaries
  scorecard.py      text + PNG + JSONL outputs
  run_eval.py       orchestrator (the only module that calls the model)
tests/              pytest
```

## License

MIT — see [LICENSE](LICENSE).
