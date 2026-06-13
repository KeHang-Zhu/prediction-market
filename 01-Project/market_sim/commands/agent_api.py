"""Agent-facing CLI — the endpoints from the research proposal (Group_negotiation_2026.pdf).

This is the interface a trader/LLM agent uses to ACT in the market, distinct from the
human operator CLI (init/run/step/replay/plot...). The browser console uses this set so the
demo shows exactly the agent API. Most endpoints reuse the engine ops behind the human
handlers; create_account/create_market/transfer return a structured ``not_supported`` per
the V0 plan (an agent calling an unsupported endpoint is itself an observation point).

Endpoints (proposal):
  create_account            (V0: not_supported — accounts are defined in config at init)
  get_markets               list markets with prices + metadata
  get_orderbook  --market   full book for a market
  place_order    --market --side buy|sell --price --qty [--token YES|NO] [--agent]
  cancel_order   --order-id [--agent]
  get_portfolio  [--agent]  holdings, open orders, P&L
  get_trade_history --market [--last]   own + public trade tape
  create_market             (V1: not_supported)
  transfer                  (V1: not_supported)
"""

from __future__ import annotations

import shlex

from market_sim.engine.models import MarketStatus

from .handlers import (
    CommandResult,
    cmd_book,
    cmd_cancel,
    cmd_order_place,
    cmd_portfolio,
    cmd_tape,
)
from .session import Session


def _not_supported(verb: str, why: str) -> CommandResult:
    return CommandResult(False, verb, data={"status": "not_supported"},
                         error=f"not_supported — {why}")


# --- working endpoints (reuse engine ops via the shared handlers) ---

def api_get_markets(session: Session, **_) -> CommandResult:
    runner = session.require_runner()
    ex = runner.exchange
    rows = []
    for mid in sorted(ex.markets):
        m = ex.markets[mid]
        rows.append({
            "id": mid, "question": m.question, "status": m.status.value,
            "best_bid": ex.best_bid(mid), "best_ask": ex.best_ask(mid),
            "last_trade": ex.last_price[mid], "mid": ex.mid(mid), "volume": ex.volume[mid],
            "resolves_in": max(0, m.resolve_round - runner.round_no) if m.status is MarketStatus.OPEN else 0,
            "outcome": m.outcome if m.status is MarketStatus.RESOLVED else None,
        })
    lines = ["markets"]
    for r in rows:
        tail = f"resolved={r['outcome']}" if r["status"] == "resolved" else f"resolves_in={r['resolves_in']}"
        bb = "  -" if r["best_bid"] is None else f"{r['best_bid']:3d}"
        ba = "  -" if r["best_ask"] is None else f"{r['best_ask']:3d}"
        lines.append(f"  {r['id']:<8} bid={bb} ask={ba} mid={r['mid']:>3} vol={r['volume']:<6} {tail}")
    return CommandResult(True, "get_markets", {"markets": rows}, "\n".join(lines))


def api_get_orderbook(session: Session, market: str, depth: int | None = None, **_) -> CommandResult:
    res = cmd_book(session, market=market)
    res.verb = "get_orderbook"
    return res


def api_place_order(session: Session, market: str, side: str, price: int, qty: int,
                    token: str = "YES", agent: str = "me", **_) -> CommandResult:
    res = cmd_order_place(session, agent=agent, market=market, token=token,
                          side=side, price=price, qty=qty)
    res.verb = "place_order"
    return res


def api_cancel_order(session: Session, order_id: int, agent: str = "me", **_) -> CommandResult:
    res = cmd_cancel(session, agent=agent, order_id=order_id)
    res.verb = "cancel_order"
    return res


def api_get_portfolio(session: Session, agent: str = "me", **_) -> CommandResult:
    res = cmd_portfolio(session, agent=agent)
    res.verb = "get_portfolio"
    return res


def api_get_trade_history(session: Session, market: str, last: int = 20, **_) -> CommandResult:
    res = cmd_tape(session, market=market, last=last)
    res.verb = "get_trade_history"
    return res


# --- stubs (structured not_supported) ---

def api_create_account(session: Session, **_) -> CommandResult:
    return _not_supported("create_account", "accounts are defined in the config at init (V0)")


def api_create_market(session: Session, **_) -> CommandResult:
    return _not_supported("create_market", "agent-created markets are a V1 feature")


def api_transfer(session: Session, **_) -> CommandResult:
    return _not_supported("transfer", "inter-agent transfers are a V1 feature")


def api_help(session: Session = None, **_) -> CommandResult:
    text = (
        "agent API (matches the research proposal):\n"
        "  get_markets                          list markets with prices & metadata\n"
        "  get_orderbook --market M             full order book for a market\n"
        "  place_order --market M --side buy|sell --price P --qty Q [--token YES|NO] [--agent A]\n"
        "  cancel_order --order-id N [--agent A]\n"
        "  get_portfolio [--agent A]            holdings, open orders, P&L\n"
        "  get_trade_history --market M [--last N]\n"
        "  create_account / create_market / transfer   (not_supported in V0)\n"
        "notes: token defaults to YES; agent defaults to 'me' (the demo seat)."
    )
    return CommandResult(True, "help", {}, text)


AGENT_HANDLERS = {
    "create_account": api_create_account,
    "get_markets": api_get_markets,
    "get_orderbook": api_get_orderbook,
    "place_order": api_place_order,
    "cancel_order": api_cancel_order,
    "get_portfolio": api_get_portfolio,
    "get_trade_history": api_get_trade_history,
    "create_market": api_create_market,
    "transfer": api_transfer,
    "help": api_help,
}

AGENT_PRIMARY = {"get_orderbook": "market", "get_trade_history": "market", "get_portfolio": "agent"}
_INT_ARGS = {"price", "qty", "last", "order_id", "depth", "amount"}


def parse_agent_line(line: str) -> tuple[str, dict]:
    tokens = shlex.split(line.strip())
    if not tokens:
        return "help", {}
    verb, rest = tokens[0], tokens[1:]
    args: dict = {}
    positionals: list[str] = []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t.startswith("--"):
            key = t[2:]
            if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
                args[key] = rest[i + 1]; i += 2
            else:
                args[key] = True; i += 1
        else:
            positionals.append(t); i += 1
    if positionals and verb in AGENT_PRIMARY and AGENT_PRIMARY[verb] not in args:
        args[AGENT_PRIMARY[verb]] = positionals[0]
    return verb, args


def agent_dispatch(session: Session, verb: str, args: dict) -> CommandResult:
    handler = AGENT_HANDLERS.get(verb)
    if handler is None:
        return CommandResult(False, verb, error=f"unknown agent endpoint '{verb}' (try `help`)")
    clean = {k.replace("-", "_"): v for k, v in args.items()}
    for k in list(clean):
        if k in _INT_ARGS and isinstance(clean[k], str):
            try:
                clean[k] = int(clean[k])
            except ValueError:
                return CommandResult(False, verb, error=f"argument --{k} must be an integer")
    try:
        return handler(session, **clean)
    except TypeError as e:
        return CommandResult(False, verb, error=f"bad arguments: {e}")
    except Exception as e:  # noqa: BLE001
        return CommandResult(False, verb, error=str(e))


def run_agent_line(session: Session, line: str) -> CommandResult:
    verb, args = parse_agent_line(line)
    return agent_dispatch(session, verb, args)
