# LLM Trading-Behavior Rationality Evaluation Report

> Evaluation target: in a binary prediction market, can an LLM acting as a trader make **rational trading decisions**?
> Code location: `market_sim/eval/`
> Report date: 2026-06-09
> Latest result: `gemini-3.5-flash` **passes 8/8 (GO)** across the 8 L2 probes.

---

## 0. Summary (TL;DR)

We assess an LLM's trading rationality with 8 **frozen single-step scenarios (probes)**. Each scenario manually constructs a market situation (order book, positions, news), feeds it to the model, has the model return a structured JSON decision (beliefs + actions), and then mechanically scores pass/fail using a single **deterministic, executable decision rule**—without using another LLM as a judge.

The evaluation has two layers:

- **L1 (interface validity)**: Is the model's output valid JSON? Are the actions legal actions the engine can execute? Does it hallucinate nonexistent endpoints?
- **L2 (behavioral rationality)**: Across 8 classic trading traps, does the model make rational choices (catch mispricing, take profit, make markets, arbitrage, avoid overtrading, respect the budget, update beliefs, remember its own resting orders)?

Latest run (`gemini-3.5-flash`, 5 repeats per probe): L1 is perfect, L2 passes all 8 probes, reaching GO.

---

## 1. Background and Goals

The overall project is a **reproducible prediction-market engine** (Polymarket-style contract semantics). On top of this engine, we want to answer a research question:

> **Put an LLM into the market as a trader—is its behavior "rational"?**

"Rational" does not mean whether it makes money (that is the higher L3 layer, involving live competition, and is out of scope for now), but rather whether it commits the cognitive errors common to human traders on a series of **micro-decisions that have clearly correct answers**. This evaluation corresponds to the L2 layer of research plan §5.4.

By design, three things are deliberately enforced:

1. **Neutral prompt**: the system prompt gives only the market rules and output format, plus one line "your goal is to maximize final wealth." **It gives no strategy hints and no demonstration trades**—we are testing the model's "naked" ability (the "Arm 1" spirit in the plan). See `schema.py:SYSTEM_PROMPT`.
2. **No truth leakage**: the observation **never contains** the market's true probability `true_prob`; the model can only infer it from prices and news. See the comments in `schema.py:build_observation` and the assertion in `test_agent_api.py:test_get_markets`.
3. **Executable scoring**: the "correct answer" for each probe is written as runnable code (the judge), which mechanically scores the model's returned actions and is fully reproducible.

---

## 2. Layered Evaluation Methodology (L1 / L2 / L3)

| Layer | What it tests | In scope for this report |
|---|---|---|
| **L1 interface** | output format validity, action validity, whether it hallucinates endpoints | ✅ |
| **L2 behavioral rationality** | pass rate across the 8 trading-trap scenarios | ✅ |
| **L3 live P&L** | P&L from beating the ZIC (zero-intelligence) baseline in multi-agent live competition | ❌ (live competition is out of scope for now; marked `n/a` in the scorecard) |

One "trial" = one probe run once = one model call. Each trial produces one `Trial` record (`base.py:Trial`) recording: whether parsing succeeded, whether the action was valid, the action count, whether it passed the judge, the model's one-sentence rationale, the number of retries, and whether it failed due to an infrastructure error (e.g., rate-limit 429).

> **Important**: trials that fail for **infrastructure reasons** (429/network, etc.) are excluded from all rationality metrics (the first line of `metrics.py:l1_metrics`, `if not t.errored`), because that is not a behavioral problem of the model.

---

## 3. Inputs to the Model: System Prompt and Observation

### 3.1 System Prompt (shared by all probes)

`schema.py:SYSTEM_PROMPT` is an "API document" whose content is equivalent to the rules a human trader would read:

- Binary market rules: each market has YES / NO shares; at resolution the winning side pays 100 cents per share and the losing side 0; therefore **1 YES share + 1 NO share is always equal to 100 cents**.
- Prices are **integer cents in 1..99**; a YES price of 60 = 60 cents ≈ an implied 60% probability.
- **No shorting**: to bet that YES will not happen, buy NO. Buying YES@p while buying NO@(100−p) is a risk-free lock.
- Cash/share constraints: a buy order locks `price*qty` of cash, a sell order locks shares; you cannot exceed available cash or holdings.
- **Output format**: you may only return a single fixed-structure JSON (see below). One line reminds you "do not invent endpoints or fields."

### 3.2 Enforced Output Schema

The model is forced to return this structure (`schema.py:AgentResponse`; the provider hard-constrains it with `response_schema` + `response_mime_type="application/json"`):

```json
{
  "beliefs":  [{"market": "<id>", "prob": 0.0~1.0}],
  "rationale": "<one-sentence rationale>",
  "actions":  [ {"type":"place_order","market":...,"token":"YES|NO","side":"buy|sell","price":...,"qty":...},
                {"type":"cancel_order","order_id":...},
                {"type":"hold"} ]
}
```

### 3.3 Observation (snapshot of each probe's situation)

`schema.py:build_observation` packs the frozen engine state into a dict and feeds it to the model, containing:

- `round`: the current round (fixed at 17, see `probes.py:ROUND`).
- `you`: your available/locked cash, holdings, and open resting orders.
- `markets`: for each open market, `best_bid / best_ask / last_trade / depth (order-book ladder) / resolves_in_rounds`.
- `news`: the list of news texts given for this probe (noisy signals).
- `your_recent_actions`: a hint string of last round's actions (used by the "memory" probes).

**Note**: this is an "open-book" mode—prices, order book, and news are all written into the observation in advance. The model does not need, and has no tools, to actively query. (By contrast, the "tool-calling / closed-book" path `ToolLoopAgent` currently has no evaluation; that is a follow-up task.)

---

## 4. How the Probes (Scenarios) Are Set Up — Construction Primitives

All situations are built from a handful of primitives in `scenarios.py`; understanding these functions means understanding "how the probes are constructed":

| Primitive | Purpose | Implementation notes |
|---|---|---|
| `make_exchange(markets_spec, cash=50000)` | Build a fresh exchange containing the subject `me` (default cash 50000) and a deep-pocketed market-maker `mm` (cash 10 million) | All resting orders in the situation are provided by `mm` |
| `add_yes_ask(ex, market, price, qty)` | Create a **YES ask** (order book the subject can buy into) | **Trick**: have `mm` buy NO@(100−price)—in a single matching book, buying NO is equivalent to posting a YES ask, so selling pressure can be created **without any YES inventory** |
| `add_yes_bid(ex, market, price, qty)` | Create a **YES bid** (order book the subject can sell into) | `mm` directly buys YES@price |
| `give_shares(ex, agent, market, token, qty)` | **Pre-seed a position** for the subject | Mint-style: the bank (`mm`) contributes `100*qty` into the collateral pool and takes the opposite side, **strictly preserving the engine invariants** |
| `place_self_order(ex, market, token, side, price, qty)` | Have **the subject itself** post an unfilled order (used by the "information update" and "order memory" probes) | Places an order directly as `me` and records the `order_id` |

> **Engineering adaptation for the single matching book (important)**: a genuine two-sided complementary arbitrage (YES ask + NO ask summing to < 100) **cannot exist statically** in a single continuous matching book—it would immediately self-match. So P4 is reformulated as "two explicitly complementary markets whose YES shares are jointly underpriced," and the skill being tested (recognizing cross-contract risk-free profit) is exactly the same. See the comment at the top of `probes.py`.

---

## 5. The Eight Probes, One by One (P1–P8)

For each probe we give: **what it tests → how the situation is constructed (at the code level) → what the model sees → the scoring criterion (judge) → the failure mode it targets**. See the code in `probes.py`.

---

### P1 · free lunch (catch obvious mispricing)

- **What it tests**: there is a "free deal" on the book—will the model grab it?
- **Situation construction**: `COIN-A`; `add_yes_ask(COIN-A, 5, 50)` creates a **5¢ YES ask ×50**; news: "Event A is estimated at about **90%** probability."
- **What the model sees**: YES can be bought for just 5 cents, while the news implies a true probability of about 90%—a huge positive expectation.
- **Judge (`_p1_judge`)**: there exists a buy-YES action with price ≥5 → **pass**.
- **Failure mode**: indifference toward free money lying on the table.

### P2 · take profit on overpriced (take profit at a high price)

- **What it tests**: the shares held are already overvalued—will the model realize the profit?
- **Situation construction**: `give_shares(me, COIN-A, YES, 60)` pre-seeds **a holding of 60 YES**; `add_yes_bid(COIN-A, 95, 50)` creates a **95¢ bid ×50**; news: "only about **30%**"; the recent hint reads "you held 60 YES earlier."
- **What the model sees**: 60 YES in hand, the market willing to take them at 95 cents, while you believe they are worth only ~30.
- **Judge (`_p2_judge`)**: there exists a sell-YES action with price ≤95 → **pass**.
- **Failure mode**: the disposition effect—holding on when one should realize.

### P3 · empty-book quoting (market-making on an empty book)

- **What it tests**: the order book is empty—will the model make a market (two-sided quoting)?
- **Situation construction**: `COIN-A`, **with no resting orders added** (empty book); news: "about **60%**."
- **What the model sees**: no bid and no ask, only a 60% signal.
- **Judge (`_p3_judge`)**: map all actions to implied YES quotes (`implied_yes_quotes`; buying NO is converted to a YES ask, selling NO to a YES bid). Require **both a bid side and an ask side**, with `bid < ask`, `40 ≤ bid ≤ 75`, `45 ≤ ask ≤ 90` (reasonable two sides around 60) → **pass**.
- **Failure mode**: freezing on an empty book, or only daring to quote one side, treating market-making as a directional bet.

### P4 · complementary arbitrage (complementary arbitrage)

- **What it tests**: can the model recognize cross-contract risk-free arbitrage?
- **Situation construction**: two **explicitly complementary** markets `RAIN` and `DRY` (DRY's description states clearly that it is "the exact complement of RAIN, with exactly one being true"); `add_yes_ask(RAIN, 40, 50)`, `add_yes_ask(DRY, 45, 50)`—the two YES asks sum to **40 + 45 = 85 < 100**. No news.
- **What the model sees**: buying RAIN's YES costs 40, buying DRY's YES costs 45, totaling 85; but exactly one will resolve to 100—locking in 15 cents of risk-free profit.
- **Judge (`_p4_judge`)**: must **buy both legs**—buy RAIN YES at price ≥40 and buy DRY YES at price ≥45 → **pass**.
- **Failure mode**: not seeing the cross-contract arbitrage; or buying only one leg (which actually turns a risk-free arbitrage into a directional bet).

### P5 · manage your hand (don't overtrade)

- **What it tests**: when the price is already fair, will the model restrain itself and avoid needless trades?
- **Situation construction**: `add_yes_bid(59, 40)` + `add_yes_ask(61, 40)` create a **tight 59/61 spread**; news: "about **60%, consistent with the current price**."
- **What the model sees**: the market is fairly priced; there is no cheap edge to take.
- **Judge (`_p5_judge`)**: use `would_cross` to check whether any action would **immediately cross and trade** (eat into this fair spread). **None at all** (hold, or rest passively inside the spread) → **pass**.
- **Failure mode**: trading needlessly on a fair spread, paying the bid-ask cost for nothing.

### P6 · budget constraint (respect the budget)

- **What it tests**: when cash is limited, will orders exceed the budget?
- **Situation construction**: `make_exchange(cash=500)` squeezes cash down to **only 500**; `add_yes_ask(60, 50)` creates a 60¢ ask; news: "about **70%**" (profitable, tempting overbuying).
- **What the model sees**: buy at 60 cents, signal at 70%, very tempting to buy a lot—but there are only 500 cents.
- **Judge (`_p6_judge`)**: the total cash locked by all buy orders `Σ price*qty ≤ 500` → **pass**.
- **Failure mode**: lured by the positive expectation into exceeding available cash.

### P7 · information update (information update / anti-anchoring)

- **What it tests**: a strong contrary signal arrives—will the model update its prior decision?
- **Situation construction**: `place_self_order(COIN-A, YES, BUY, 60, 10)` leaves the subject with **its own resting 60¢ bid ×10** (based on the old ~60% level); news: "**strong negative signal**, now estimated at about **20%** (reliability about 80%)"; the recent hint explains where that resting order came from.
- **What the model sees**: it still has an old bid betting "YES will rise," but the new information says the probability has crashed to 20%.
- **Judge (`_p7_judge`)**: any of the following is a **pass**—cancel that old bid / lower the belief on that market to ≤0.40 / sell YES / buy NO.
- **Failure mode**: anchored to the old decision, unresponsive to new information.

### P8 · order memory (order memory)

- **What it tests**: will the model forget its existing resting order and place a duplicate?
- **Situation construction**: `place_self_order(COIN-A, YES, BUY, 55, 10)` leaves the subject with **an existing resting 55¢ buy-YES ×10 order**; news: "you want to **maintain your current exposure** (you already have a resting buy YES@55 x10)"; the judge pre-records the `(market, token, side, price)` fingerprint set of the existing resting order.
- **What the model sees**: the goal is to maintain exposure, and it already has a resting order that exactly satisfies that goal.
- **Judge (`_p8_judge`)**: if any `place_order` hits the fingerprint of an existing resting order (a duplicate) → **fail**; otherwise → **pass**.
- **Failure mode**: forgetting its own resting order and placing an identical one, creating double the exposure.

---

## 6. Scoring System

### 6.1 L1 Interface Metrics (`metrics.py:l1_metrics`, counting only non-infra-failure trials)

| Metric | Meaning |
|---|---|
| `parse_success_rate` | Fraction of trials where the model's **first** reply is schema-valid JSON |
| `response_ok_rate` | Fraction that eventually yields a usable reply within the allowed retries |
| `action_valid_rate` | Fraction of returned actions that pass engine validation (executable) (`base.py:validate_action`) |
| `hallucinated_endpoint_rate` | Fraction with an unknown action type / invented endpoint |
| `avg_actions_per_trial` | Average number of actions returned per trial |

`validate_action` checks each item: the action type is legal, the market exists, token/side are legal, price is in 1..99, quantity ≥1, buy orders do not exceed available cash, sell orders do not exceed holdings, and a cancel must target one's own order.

### 6.2 L2 Pass Rate and Confidence Interval

For each probe we tally `k/n` (passed k out of n) and compute a **Wilson confidence interval** (`metrics.py:wilson_ci`, z=1.96, 95%). The Wilson interval is more robust than the naive proportion under small samples—which is why **even a perfect 5/5 has a lower bound of only 56.6%** (the sample is too small to statistically assert anything higher).

### 6.3 GO / NO-GO Criteria (`scorecard.py`, corresponding to the implementation plan §6)

Only a few are hard gates:

| Criterion | Threshold |
|---|---|
| L1 `action_valid_rate` | ≥ 95% |
| P1 free lunch | ≥ 90% |
| P2 take profit | ≥ 90% |
| P6 budget constraint | ≥ 90% |
| P5 manage your hand | ≥ 70% |

All satisfied → **GO**, otherwise **NO-GO**. (The remaining probes are still evaluated and displayed, but have no hard gate; L3 is not evaluated.)

---

## 7. Test Results

> Configuration: `gemini-3.5-flash`, `temperature=0.7`, 5 repeats per probe (40 trials total), 2026-06-09.

### 7.1 Overview

| Metric | Result |
|---|---|
| `parse_success_rate` | 100% |
| `action_valid_rate` | 100% |
| `hallucinated_endpoint` | 0% |
| `avg_actions_per_trial` | 1.48 |
| **L2 passed** | **8 / 8** ✅ |
| GO / NO-GO | **GO** |

### 7.2 L2, Probe by Probe

| Probe | Result |
|---|---|
| P1 catch mispricing | ✅ 5/5 |
| P2 take profit at a high price | ✅ 5/5 |
| P3 empty-book market-making | ✅ 5/5 |
| P4 complementary arbitrage | ✅ 5/5 |
| P5 don't overtrade | ✅ 5/5 |
| P6 respect the budget | ✅ 5/5 |
| P7 information update | ✅ 5/5 |
| P8 order memory | ✅ 5/5 |

### 7.3 Reasoning on the Key Probes (P3 / P4)

P3 (empty-book market-making) and P4 (cross-contract arbitrage) are the two relatively hard probes; the model did not "pass by luck" but gave correct reasoning:

- **P3**: quoted both sides `bid 55 / ask 65`, with the rationale—*"post bids and asks on either side of the fair value 60/40 to make a market on the empty book."* It understood that market-making must be two-sided.
- **P4**: bought both legs, with the rationale—*"DRY and RAIN are complements, the combined ask is only 85 cents, a risk-free arbitrage"*—accurately recognizing the 40+45<100 risk-free arbitrage.

### 7.4 Result Files

- `eval_runs/gemini-3.5-flash/` (`summary.json` / `trials.jsonl` / `scorecard.png`)

---

## 8. Limitations and Next Steps

1. **Small sample size**: under `repeats=5`, even at 8/8 the 95% confidence-interval lower bound per probe is only 56.6%. To claim "stable perfection" firmly requires `repeats=10~20` (a few dozen more model calls).
2. **This is the score for the "open-book" path**: the current evaluation only covers the one-shot JSON-decision `LLMAgent`—prices and news are fed to the model in advance. It **cannot test the tool-calling path** (`ToolLoopAgent`, configured as `type: llm_agentic`), in which the model must itself use the command-line-style API (`get_markets` / `get_orderbook` / `get_news` …) to gather information and then `commit_view → place_order → finish`. **This "closed-book" path currently has zero results**, and it is harder and will expose new failure modes (placing orders without reading private signals, not completing the flow, etc.).
3. **L3 not evaluated**: the P&L standard of beating the ZIC baseline in live multi-agent competition is not yet included.

**Recommended next steps**: ① raise `repeats` to tighten the confidence intervals and confirm the stability of the perfect score; ② add an equivalent evaluation for the tool-calling path (reuse these 8 scenarios and judges, swapping "feeding information in advance" for "letting the model query with tools itself").

---

## Appendix · How to Reproduce

```bash
# Full set of 8 probes, gemini-3.5-flash, 5 repeats per probe
./.venv/bin/python -m market_sim.eval.run_eval \
    --model gemini-3.5-flash --repeats 5 \
    --outdir eval_runs/gemini-3.5-flash

# Run only specified probes, bypassing the cache for single-probe verification
./.venv/bin/python -m market_sim.eval.run_eval \
    --model gemini-3.5-flash --probes P3,P4 --repeats 1 --no-cache
```

- Authentication: Vertex AI + ADC (machine-level `gcloud auth application-default login`); project/region/model are in `.env`.
- Caching: successful completions are cached to `eval_runs/cache/` keyed by `(model, temperature, system, user, key)`; rerunning the same configuration is free and reproducible; switching models does not hit the old cache (the key includes the model).
