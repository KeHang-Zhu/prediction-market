"""Eval orchestrator — the only module that calls the model.

Runs each probe `repeats` times against a real Gemini model, validates every
returned action against the frozen engine state, applies each probe's judge, and
emits a scorecard (text + summary.json + trials.jsonl + scorecard.png). Importing
this module never calls the model: the model is reached only via run_eval/main.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

from .base import Trial, validate_action
from .probes import ALL_PROBES, PROBE_BY_ID
from .provider import GeminiProvider
from .metrics import summarize
from . import scorecard


def _user_message(observation: dict) -> str:
    return (
        "Here is your current observation (JSON):\n"
        + json.dumps(observation, ensure_ascii=False, indent=2)
        + "\n\nReturn your decision as the required JSON object (beliefs, rationale, actions)."
    )


def run_trial(probe, provider: GeminiProvider, repeat: int) -> Trial:
    """Run one probe once: build -> model call -> validate actions -> judge."""
    setup = probe.build()
    comp = provider.complete(_user_message(setup.observation), key=f"{probe.id}:{repeat}")
    if not comp.ok or comp.parsed is None:
        reason = (f"api_error: {comp.error}" if comp.api_error else f"unparseable: {comp.error}")
        return Trial(
            probe.id, repeat, comp.parse_ok, False,
            0, 0, 0, 0,
            False, reason, "", comp.attempts, errored=comp.api_error,
        )
    resp = comp.parsed
    checks = [validate_action(setup.exchange, setup.agent_id, a) for a in resp.actions]
    n_valid = sum(1 for c in checks if c.valid)
    n_invalid = len(checks) - n_valid
    n_hall = sum(1 for c in checks if c.hallucinated)
    jr = probe.judge(resp, setup)
    return Trial(
        probe.id, repeat, comp.parse_ok, True,
        len(resp.actions), n_valid, n_invalid, n_hall,
        jr.passed, jr.reason, (resp.rationale or "")[:300], comp.attempts,
    )


def run_eval(
    model: str | None = None,
    repeats: int = 5,
    probe_ids: list[str] | None = None,
    temperature: float = 0.7,
    use_cache: bool = True,
) -> tuple[list[Trial], dict]:
    """Run the full eval and return (trials, summary). Calls the model."""
    probes = ALL_PROBES if not probe_ids else [PROBE_BY_ID[p] for p in probe_ids]
    provider = GeminiProvider(model=model, temperature=temperature, use_cache=use_cache)
    trials: list[Trial] = []
    for p in probes:
        for i in range(repeats):
            trials.append(run_trial(p, provider, i))
    summary = summarize(trials)
    summary.update({"model": provider.model, "repeats": repeats, "temperature": temperature})
    return trials, summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the L2 behavioral-rationality eval.")
    ap.add_argument("--repeats", type=int, default=5, help="runs per probe")
    ap.add_argument("--model", type=str, default=None, help="model id (default: env GEMINI_MODEL)")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--probes", type=str, default=None, help="comma list of probe ids (default: all)")
    ap.add_argument("--no-cache", action="store_true", help="bypass the on-disk response cache")
    ap.add_argument(
        "--outdir",
        default=str(Path(__file__).resolve().parents[2] / "eval_runs"),
        help="directory for summary.json / trials.jsonl / scorecard.png",
    )
    args = ap.parse_args()

    probe_ids = [p.strip() for p in args.probes.split(",") if p.strip()] if args.probes else None
    trials, summary = run_eval(
        model=args.model,
        repeats=args.repeats,
        probe_ids=probe_ids,
        temperature=args.temperature,
        use_cache=not args.no_cache,
    )

    print(scorecard.render_text(summary, summary["model"]))

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    summary_path = outdir / "summary.json"
    trials_path = outdir / "trials.jsonl"
    figure_path = outdir / "scorecard.png"

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with trials_path.open("w", encoding="utf-8") as f:
        for t in trials:
            f.write(json.dumps(dataclasses.asdict(t), ensure_ascii=False) + "\n")
    scorecard.render_figure(summary, figure_path, summary["model"])

    print(f"summary:   {summary_path}")
    print(f"trials:    {trials_path}")
    print(f"scorecard: {figure_path}")


if __name__ == "__main__":
    main()
