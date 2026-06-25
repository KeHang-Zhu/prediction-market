"""Render a metrics.summarize() summary into a text report, a bar figure, and a
go/no-go verdict (plan §6).

The summary is the dict produced by metrics.summarize, optionally carrying extra
top-level keys ("model", "repeats", "temperature", "n_trials"). Expected shape:
    {
      "n_trials": int,
      "l1": {"parse_success_rate", "action_valid_rate",
             "hallucinated_endpoint_rate", "avg_actions_per_trial"},
      "probes": {pid: {"k", "n", "rate", "ci_lo", "ci_hi"}, ...},
    }
L3 (live P&L-vs-ZIC) is out of scope, so its go/no-go criterion is excluded.
"""

from __future__ import annotations

from .probes import PROBE_BY_ID

# go/no-go thresholds (plan §6); L1 is the overall action_valid_rate gate.
_PROBE_THRESHOLDS = {"P1": 0.90, "P2": 0.90, "P6": 0.90, "P5": 0.70}
_L1_THRESHOLD = 0.95
_L3_NOTE = "L3 P&L-vs-ZIC criterion not evaluated (live runs out of scope)"


def _probe_name(pid: str) -> str:
    p = PROBE_BY_ID.get(pid)
    return p.name if p is not None else pid


def go_no_go(summary: dict) -> dict:
    """Evaluate the partial go/no-go criteria; L3 excluded (see note)."""
    l1 = summary.get("l1", {}) or {}
    probes = summary.get("probes", {}) or {}
    criteria: list[dict] = []

    avr = l1.get("action_valid_rate")
    criteria.append({
        "name": "L1 action_valid_rate",
        "value": float(avr) if avr is not None else None,
        "threshold": _L1_THRESHOLD,
        "pass": avr is not None and float(avr) >= _L1_THRESHOLD,
    })

    for pid, thr in _PROBE_THRESHOLDS.items():
        rec = probes.get(pid)
        rate = rec.get("rate") if rec else None
        criteria.append({
            "name": f"{pid} {_probe_name(pid)}",
            "value": float(rate) if rate is not None else None,
            "threshold": thr,
            "pass": rate is not None and float(rate) >= thr,
        })

    return {
        "criteria": criteria,
        "overall": all(c["pass"] for c in criteria),
        "note": _L3_NOTE,
    }


def _pct(x) -> str:
    return f"{100 * x:5.1f}%" if x is not None else "   n/a"


def render_text(summary: dict, model: str | None = None) -> str:
    model = model or summary.get("model", "?")
    n = summary.get("n_trials", "?")
    out: list[str] = []
    head = f"L1+L2 behavioral-rationality scorecard | model={model} | n_trials={n}"
    extra = []
    if summary.get("repeats") is not None:
        extra.append(f"repeats={summary['repeats']}")
    if summary.get("temperature") is not None:
        extra.append(f"temperature={summary['temperature']}")
    if extra:
        head += " | " + " ".join(extra)
    out.append(head)
    out.append("=" * len(head))

    if summary.get("n_errored"):
        out.append(f"(excluded {summary['n_errored']} infra-errored trial(s); "
                   f"rates over {summary.get('n_evaluated', '?')} model replies)")

    l1 = summary.get("l1", {}) or {}
    out.append("L1 interface")
    out.append(f"  parse_success_rate       {_pct(l1.get('parse_success_rate'))}")
    out.append(f"  action_valid_rate        {_pct(l1.get('action_valid_rate'))}")
    out.append(f"  hallucinated_endpoint    {_pct(l1.get('hallucinated_endpoint_rate'))}")
    ava = l1.get("avg_actions_per_trial")
    out.append(f"  avg_actions_per_trial    {ava:6.2f}" if ava is not None
               else "  avg_actions_per_trial       n/a")

    out.append("")
    out.append("L2 probes")
    out.append("   id  name                       k/n     rate    95% CI")
    out.append("  " + "-" * 56)
    probes = summary.get("probes", {}) or {}
    for pid in sorted(probes):
        rec = probes[pid] or {}
        k, nn = rec.get("k"), rec.get("n")
        kn = f"{k}/{nn}" if k is not None and nn is not None else "-/-"
        rate = rec.get("rate")
        lo, hi = rec.get("ci_lo"), rec.get("ci_hi")
        ci = f"[{_pct(lo).strip()}–{_pct(hi).strip()}]" if lo is not None and hi is not None else ""
        out.append(f"  {pid:<3} {_probe_name(pid):<25} {kn:>7}  {_pct(rate)}  {ci}")

    out.append("")
    verdict = go_no_go(summary)
    out.append("GO / NO-GO")
    for c in verdict["criteria"]:
        mark = "✓" if c["pass"] else "✗"
        out.append(f"  [{mark}] {c['name']:<28} {_pct(c['value'])} >= {_pct(c['threshold'])}")
    out.append(f"  => {'GO' if verdict['overall'] else 'NO-GO'}")
    out.append(f"  note: {verdict['note']}")
    return "\n".join(out)


def render_figure(summary: dict, out_path: str, model: str | None = None) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    model = model or summary.get("model", "?")
    probes = summary.get("probes", {}) or {}
    pids = sorted(probes)
    rates = [probes[p].get("rate") or 0.0 for p in pids]
    los = [probes[p].get("ci_lo") for p in pids]
    his = [probes[p].get("ci_hi") for p in pids]
    err_lo = [max(0.0, r - (lo if lo is not None else r)) for r, lo in zip(rates, los)]
    err_hi = [max(0.0, (hi if hi is not None else r) - r) for r, hi in zip(rates, his)]

    labels = [f"{p}\n{_probe_name(p)}" for p in pids]
    x = range(len(pids))
    fig, ax = plt.subplots(figsize=(max(7, 1.1 * len(pids)), 4.5))
    ax.bar(x, rates, yerr=[err_lo, err_hi], capsize=4, color="#4C78A8")
    ax.axhline(0.9, ls="--", color="#444", lw=1, label="0.90 gate")
    ax.axhline(0.7, ls="--", color="#999", lw=1, label="0.70 gate")
    ax.set_ylim(0, 1)
    ax.set_ylabel("pass rate")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_title(f"L2 probe pass rates — {model}")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
