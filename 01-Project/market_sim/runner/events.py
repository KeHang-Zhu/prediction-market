"""Event model + canonical JSON serialization + replay comparison.

"Byte-exact replay" is defined precisely as: the canonical serialization of every
event matches, with the wall-clock ``ts`` masked to a constant sentinel (ts is the
only non-reproducible field). event_id, round, type, agent_id, payload and result
must all match byte-for-byte.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

TS_SENTINEL = "<ts>"

# Full event-type vocabulary (schema is append-only).
#
# order_queued: emitted at the MOMENT a tool-using agent calls place_order/cancel_order
# (during the decision phase), capturing the order in its true model-call position —
# interleaved with the agent's reads. The matching place_order / invalid_action /
# cancel_order event (carrying the round-end fill/reject result, blind-submit) is emitted
# later at execution and correlated back via a shared client_id. Scripted bots never emit
# it (they return actions without a client_id), so scripted streams stay byte-exact.
EVENT_TYPES = {
    "config", "round_start", "news", "signal", "agent_view", "order_queued", "place_order",
    "cancel_order", "fill", "mint", "merge", "resolution", "payout", "snapshot",
    "invalid_action", "round_end", "llm_call", "agent_query",
    # open-scenario agent actions (Config.capabilities; emitted at execution like place_order):
    #   transfer        — payload {from, to, amount, client_id?}: peer-to-peer cash move
    #   account_created — payload {account_id, funder, initial_cash, client_id?}: passive wallet
    #   market_created  — payload {market_id, question, resolve_round, client_id?}: NEVER carries
    #                     true_prob/outcome (the creator is blind to a market's latent truth)
    "transfer", "account_created", "market_created",
    # order_expired — payload {market, order_id}: a GTD order auto-cancelled when its
    #                 expire_round passed (emitted by the runner's expiry phase, agent_id=None)
    "order_expired",
    # demo/visualization events (no engine effect; drive the explainer pages):
    #   briefing      — the literal per-round wake-up text fed to a tool-using agent
    #   model_turn    — one raw model turn (text + requested function calls), in call order
    #   clearing_trace — the round's execution phase replayed order-by-order with the
    #                    order book before/after each match (authoritative matching trace)
    "briefing", "model_turn", "clearing_trace",
}

# agent_view: emitted the moment a tool-using agent commits its read (commit_view) — its
# YES probability per market + a one-line plan — BEFORE it trades. Lands in the trail in
# true call order (after the reads, before the orders), so belief/plan precede the orders
# they justify. The end-of-turn llm_call still carries belief/plan/lessons (for the
# aggregate + old-recording fallback); only `lessons` is new at finish() time.


@dataclass
class Event:
    event_id: int
    round: int
    type: str
    agent_id: str | None
    payload: dict
    result: dict | None = None
    ts: str = ""

    def to_dict(self, mask_ts: bool = False) -> dict:
        return {
            "event_id": self.event_id,
            "round": self.round,
            "type": self.type,
            "agent_id": self.agent_id,
            "payload": self.payload,
            "result": self.result,
            "ts": TS_SENTINEL if mask_ts else self.ts,
        }


def canonical_json(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_line(event: Event) -> str:
    return canonical_json(event.to_dict(mask_ts=False))


def read_events(path: str) -> list[dict]:
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def compare_streams(a: list[dict], b: list[dict]) -> tuple[bool, int | None, str]:
    """Compare two event streams with ts masked. Returns (matched, first_diff_index, detail)."""
    n = min(len(a), len(b))
    for i in range(n):
        da = dict(a[i]); da["ts"] = TS_SENTINEL
        db = dict(b[i]); db["ts"] = TS_SENTINEL
        sa, sb = canonical_json(da), canonical_json(db)
        if sa != sb:
            return False, i, f"event[{i}] differs:\n  original: {sa}\n  replay:   {sb}"
    if len(a) != len(b):
        return False, n, f"length differs: original={len(a)} replay={len(b)}"
    return True, None, "streams identical (ts masked)"
