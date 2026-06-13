"""Pure aggregation over eval Trials: L1 rates, per-probe pass rates, summary.

No I/O. Every quantity is a simple function of `list[Trial]` (see .base.Trial), so
the same numbers can be recomputed anywhere from the raw trial records.
"""

from __future__ import annotations

import math

from .base import Trial


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes of n trials, clamped to [0, 1]."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    lo = (center - half) / denom
    hi = (center + half) / denom
    return (max(0.0, lo), min(1.0, hi))


def l1_metrics(trials: list[Trial]) -> dict:
    """Level-1 (format/validity) aggregate rates over non-errored trials.

    Trials that failed for infrastructure reasons (e.g. a 429 rate limit) are not
    model behavior, so they are excluded from every rationality rate.
    """
    trials = [t for t in trials if not getattr(t, "errored", False)]
    n = len(trials)
    total_actions = sum(t.n_actions for t in trials)
    return {
        "parse_success_rate": (sum(t.parse_ok for t in trials) / n) if n else 0.0,
        "response_ok_rate": (sum(t.valid for t in trials) / n) if n else 0.0,
        "action_valid_rate": (
            sum(t.n_valid_actions for t in trials) / total_actions
            if total_actions else 0.0
        ),
        "hallucinated_endpoint_rate": (
            sum(t.n_hallucinated for t in trials) / total_actions
            if total_actions else 0.0
        ),
        "avg_actions_per_trial": (total_actions / n) if n else 0.0,
    }


def probe_pass_rates(trials: list[Trial]) -> dict[str, dict]:
    """Per-probe pass rate with Wilson CI (non-errored trials), first-seen order."""
    trials = [t for t in trials if not getattr(t, "errored", False)]
    order: list[str] = []
    counts: dict[str, list[int]] = {}  # probe_id -> [n, passed]
    for t in trials:
        if t.probe_id not in counts:
            order.append(t.probe_id)
            counts[t.probe_id] = [0, 0]
        counts[t.probe_id][0] += 1
        counts[t.probe_id][1] += int(t.passed)
    out: dict[str, dict] = {}
    for pid in order:
        n, k = counts[pid]
        lo, hi = wilson_ci(k, n)
        out[pid] = {
            "n": n,
            "k": k,
            "passed": k,
            "rate": (k / n) if n else 0.0,
            "ci_lo": lo,
            "ci_hi": hi,
        }
    return out


def summarize(trials: list[Trial]) -> dict:
    """Full summary: counts, L1 metrics, and per-probe pass rates.

    n_trials counts every attempt; n_errored counts infra failures (excluded from
    the rates); n_evaluated is the number of real model replies the rates are over.
    """
    n_errored = sum(1 for t in trials if getattr(t, "errored", False))
    return {
        "n_trials": len(trials),
        "n_errored": n_errored,
        "n_evaluated": len(trials) - n_errored,
        "l1": l1_metrics(trials),
        "probes": probe_pass_rates(trials),
    }
