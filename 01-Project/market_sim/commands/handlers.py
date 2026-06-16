"""Command handlers + dispatch + raw-line parser.

Every handler takes the Session and keyword args and returns a CommandResult
carrying both structured ``data`` (for the web/JSON) and a plain ``text``
rendering (for the terminal and the browser console).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from market_sim.agents.base import Cancel, PlaceOrder
from market_sim.engine.models import Side, Token
from market_sim.runner.config import load_config
from market_sim.runner.events import read_events
from market_sim.runner.replay import verify_replay

from .session import Session


@dataclass
class CommandResult:
    ok: bool
    verb: str
    data: dict = field(default_factory=dict)
    text: str = ""
    error: str | None = None

    def to_dict(self) -> dict:
        return {"ok": self.ok, "verb": self.verb, "data": self.data, "text": self.text, "error": self.error}


# --------------------------------------------------------------------------- helpers

def _fmt_price(p) -> str:
    return "  -" if p is None else f"{p:3d}"


def _money(c: int) -> str:
    return f"{c/100:,.2f}"


def _book_text(market: str, book: dict, bb, ba, mid, last) -> str:
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    lines = [f"order book  {market}    bid={_fmt_price(bb)}  ask={_fmt_price(ba)}  mid={mid}  last={_fmt_price(last)}",
             f"{'BIDS (YES)':>22}   |   {'ASKS (YES)':<22}",
             f"{'price':>10}{'qty':>11}   |   {'price':<10}{'qty':<11}"]
    for i in range(max(len(bids), len(asks))):
        lhs = f"{bids[i][0]:>10}{bids[i][1]:>11}" if i < len(bids) else " " * 21
        rhs = f"{asks[i][0]:<10}{asks[i][1]:<11}" if i < len(asks) else ""
        lines.append(f"{lhs}   |   {rhs}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- handlers

def cmd_init(session: Session, config: str, **_) -> CommandResult:
    cfg = load_config(config)
    session.init(cfg)
    runner = session.require_runner()
    data = {
        "run_name": cfg.run_name, "seed": cfg.seed,
        "markets": [m.id for m in cfg.markets],
        "agents": sorted(runner.agents.keys()),
        "log_path": str(session.log_path),
    }
    text = (f"initialized '{cfg.run_name}' (seed={cfg.seed})\n"
            f"  markets: {', '.join(data['markets'])}\n"
            f"  agents:  {', '.join(data['agents'])}\n"
            f"  log:     {data['log_path']}")
    return CommandResult(True, "init", data, text)


def cmd_run(session: Session, rounds: int = None, **_) -> CommandResult:
    runner = session.require_runner()
    n = int(rounds) if rounds is not None else runner.config.rounds
    runner.run(n)
    snap = runner.latest_snapshot()
    text = f"ran {n} rounds -> now at round {runner.round_no}"
    return CommandResult(True, "run", {"round": runner.round_no, "state": snap}, text)


def cmd_step(session: Session, **_) -> CommandResult:
    runner = session.require_runner()
    snap = runner.step()
    return CommandResult(True, "step", {"round": runner.round_no, "state": snap},
                         f"stepped to round {runner.round_no}")


def cmd_book(session: Session, market: str, **_) -> CommandResult:
    runner = session.require_runner()
    ex = runner.exchange
    if market not in ex.markets:
        return CommandResult(False, "book", error=f"unknown market '{market}'")
    book = ex.get_book(market, runner.config.depth_k)
    bb, ba, mid, last = ex.best_bid(market), ex.best_ask(market), ex.mid(market), ex.last_price[market]
    data = {"market": market, "book": book, "best_bid": bb, "best_ask": ba, "mid": mid, "last_trade": last}
    return CommandResult(True, "book", data, _book_text(market, book, bb, ba, mid, last))


def cmd_portfolio(session: Session, agent: str, **_) -> CommandResult:
    runner = session.require_runner()
    if agent not in runner.exchange.ledger.accounts:
        return CommandResult(False, "portfolio", error=f"unknown agent '{agent}'")
    p = runner.exchange.get_portfolio(agent)
    pos_lines = [f"    {m}: YES {row.get('YES', 0)}  NO {row.get('NO', 0)}"
                 for m, row in p["positions"].items() if any(row.values())] or ["    (none)"]
    oo = p["open_orders"]
    oo_lines = [f"    #{o['order_id']} {o['market']} {o['side']} {o['token']}@{o['price']} x{o['qty']}"
                for o in oo] or ["    (none)"]
    text = (f"portfolio  {agent}\n"
            f"  cash available: {_money(p['cash_available'])}   locked: {_money(p['cash_locked'])}\n"
            f"  positions:\n" + "\n".join(pos_lines) + "\n"
            f"  open orders:\n" + "\n".join(oo_lines))
    return CommandResult(True, "portfolio", p, text)


def cmd_tape(session: Session, market: str, last: int = 20, **_) -> CommandResult:
    runner = session.require_runner()
    if market not in runner.exchange.markets:
        return CommandResult(False, "tape", error=f"unknown market '{market}'")
    trades = runner.exchange.get_tape(market, int(last))
    lines = [f"trade tape  {market}  (last {len(trades)})"]
    for t in trades:
        lines.append(f"  r{t['round']:>4}  {t['settle']:<12} {t['price']:>3}¢ x{t['qty']:<4} "
                     f"{t['taker']} <- {t['maker']}")
    if not trades:
        lines.append("  (no trades yet)")
    return CommandResult(True, "tape", {"market": market, "trades": trades}, "\n".join(lines))


def cmd_order_place(session: Session, agent: str, market: str, token: str, side: str,
                    price: int, qty: int, tif: str = "GTC", order_type: str = None,
                    post_only: bool = False, expire_round: int | None = None, **_) -> CommandResult:
    runner = session.require_runner()
    if agent not in runner.agents:
        return CommandResult(False, "order_place", error=f"unknown agent '{agent}'")
    try:
        tok = Token(token.upper())
        sd = Side(side.lower())
    except ValueError:
        return CommandResult(False, "order_place", error="token must be YES/NO and side buy/sell")
    tif_val = str(order_type or tif or "GTC").upper()  # --order-type is an alias for --tif
    action = PlaceOrder(market, tok, sd, int(price), int(qty),
                        tif=tif_val, post_only=bool(post_only),
                        expire_round=int(expire_round) if expire_round is not None else None)
    res = session.submit_order(agent, action)
    info = {"submitted": {"agent": agent, "market": market, "token": tok.value,
                          "side": sd.value, "price": int(price), "qty": int(qty)}}
    # res may be a PlaceResult (immediate) or a snapshot dict / None (injected)
    text_lines = [f"order placed: {agent} {sd.value} {tok.value} {price}¢ x{qty} on {market}"]
    if hasattr(res, "status"):
        info["result"] = {"status": res.status, "order_id": res.order_id,
                          "filled_qty": res.filled_qty, "resting_qty": res.resting_qty,
                          "reason": res.reason}
        if res.status == "rejected":
            text_lines = [f"order REJECTED ({res.reason}): {agent} {sd.value} {tok.value} {price}¢ x{qty}"]
        else:
            text_lines.append(f"  -> order #{res.order_id}: filled {res.filled_qty}, resting {res.resting_qty}")
            for f in res.fills:
                text_lines.append(f"     fill {f.settle.value} {f.qty}@{f.price}¢ vs {f.maker_id}")
    else:
        info["queued"] = True
        text_lines.append("  -> queued for next round (live playback)")
    return CommandResult(True, "order_place", info, "\n".join(text_lines))


def cmd_cancel(session: Session, agent: str, order_id: int, **_) -> CommandResult:
    runner = session.require_runner()
    res = session.submit_order(agent, Cancel(int(order_id)))
    if hasattr(res, "status"):
        return CommandResult(True, "cancel", {"status": res.status, "order_id": int(order_id)},
                             f"cancel #{order_id}: {res.status}")
    return CommandResult(True, "cancel", {"queued": True}, f"cancel #{order_id} queued")


def cmd_replay(session: Session, log: str, **_) -> CommandResult:
    matched, idx, detail = verify_replay(log)
    text = ("REPLAY OK — byte-exact match (ts masked)" if matched
            else f"REPLAY MISMATCH at event {idx}\n{detail}")
    return CommandResult(matched, "replay", {"matched": matched, "first_diff": idx, "detail": detail}, text)


def cmd_plot(session: Session, log: str = None, out: str = None, **_) -> CommandResult:
    log_path = log or (str(session.log_path) if session.log_path else None)
    if not log_path or not Path(log_path).exists():
        return CommandResult(False, "plot", error=f"log not found: {log_path}")
    out_path = out or str(Path(log_path).with_suffix(".png"))
    n_markets = _render_plot(log_path, out_path)
    return CommandResult(True, "plot", {"out": out_path, "markets": n_markets},
                         f"wrote price/volume figure ({n_markets} markets) -> {out_path}")


def cmd_status(session: Session, **_) -> CommandResult:
    if session.runner is None:
        return CommandResult(True, "status", {"active": False}, "no active run — use `init`")
    runner = session.runner
    snap = runner.latest_snapshot()
    lines = [f"run '{runner.config.run_name}'  round {runner.round_no}  seed {runner.config.seed}"]
    for m in snap["markets"]:
        st = m["status"]
        extra = f" outcome={m['outcome']}" if m["outcome"] is not None else f" resolves_in={m['resolves_in']}"
        lines.append(f"  {m['id']:<8} mid={m['mid']:>3} last={_fmt_price(m['last_trade'])} "
                     f"vol={m['volume']:<6} {st}{extra}")
    data = {"active": True, "round": runner.round_no, "state": snap}
    return CommandResult(True, "status", data, "\n".join(lines))


def cmd_help(session: Session = None, **_) -> CommandResult:
    text = (
        "commands:\n"
        "  init --config <file.yaml>           initialize a run\n"
        "  run [--rounds N]                    advance N rounds\n"
        "  step                                advance one round\n"
        "  book <market>                       show order book\n"
        "  portfolio <agent>                   show an account\n"
        "  tape <market> [--last N]            recent trades\n"
        "  order place --agent A --market M --token YES|NO --side buy|sell --price P --qty Q\n"
        "  cancel --agent A --order-id N       cancel an order\n"
        "  replay --log <file.jsonl>           verify byte-exact replay\n"
        "  plot [--log <file.jsonl>] [--out p] price/volume figure\n"
        "  status                              current run state\n"
        "  help                                this message"
    )
    return CommandResult(True, "help", {}, text)


# --------------------------------------------------------------------------- plotting

def _render_plot(log_path: str, out_path: str) -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    events = read_events(log_path)
    # gather per-market series from snapshot events
    series: dict[str, dict] = {}
    for e in events:
        if e["type"] != "snapshot":
            continue
        r = e["round"]
        for m in e["payload"]["state"]["markets"]:
            s = series.setdefault(m["id"], {"round": [], "mid": [], "vol": [], "true": m["true_prob_pct"]})
            s["round"].append(r)
            s["mid"].append(m["mid"])
            s["vol"].append(m["volume"])
    mids = sorted(series)
    if not mids:
        raise ValueError("no snapshot data in log")
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), height_ratios=[3, 1], sharex=True)
    ax_p, ax_v = axes
    for mid in mids:
        s = series[mid]
        ax_p.plot(s["round"], s["mid"], label=f"{mid} mid", linewidth=1.6)
        ax_p.axhline(s["true"], linestyle="--", linewidth=1.0, alpha=0.6,
                     label=f"{mid} true={s['true']}¢")
        # per-round volume = diff of cumulative
        vol = s["vol"]
        rounds = s["round"]
        inc = [vol[0]] + [vol[i] - vol[i - 1] for i in range(1, len(vol))]
        ax_v.plot(rounds, inc, linewidth=1.0, alpha=0.8, label=f"{mid}")
    ax_p.set_ylabel("YES price (¢)")
    ax_p.set_ylim(0, 100)
    ax_p.set_title(f"Price trajectory — {Path(log_path).name}")
    ax_p.legend(fontsize=8, ncol=2)
    ax_p.grid(alpha=0.3)
    ax_v.set_ylabel("volume")
    ax_v.set_xlabel("round")
    ax_v.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return len(mids)


# --------------------------------------------------------------------------- dispatch

HANDLERS = {
    "init": cmd_init,
    "run": cmd_run,
    "step": cmd_step,
    "book": cmd_book,
    "portfolio": cmd_portfolio,
    "tape": cmd_tape,
    "order_place": cmd_order_place,
    "cancel": cmd_cancel,
    "replay": cmd_replay,
    "plot": cmd_plot,
    "status": cmd_status,
    "help": cmd_help,
}

# positional argument -> named arg, per verb
PRIMARY = {
    "init": "config", "run": "rounds", "book": "market", "portfolio": "agent",
    "tape": "market", "replay": "log", "plot": "log",
}

_INT_ARGS = {"rounds", "price", "qty", "last", "order_id", "round"}


def dispatch(session: Session, verb: str, args: dict) -> CommandResult:
    handler = HANDLERS.get(verb)
    if handler is None:
        return CommandResult(False, verb, error=f"unknown command '{verb}' (try `help`)")
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
    except Exception as e:  # noqa: BLE001 — surface engine/config errors as command errors
        return CommandResult(False, verb, error=str(e))


def parse_command_line(line: str) -> tuple[str, dict]:
    """Parse a raw console line into ``(verb, args)`` (the browser console path)."""
    tokens = shlex.split(line.strip())
    if not tokens:
        return "help", {}
    verb = tokens[0]
    rest = tokens[1:]
    if verb == "order" and rest and rest[0] == "place":
        verb, rest = "order_place", rest[1:]
    args: dict = {}
    positionals: list[str] = []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t.startswith("--"):
            key = t[2:]
            if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
                args[key] = rest[i + 1]
                i += 2
            else:
                args[key] = True
                i += 1
        else:
            positionals.append(t)
            i += 1
    if positionals and verb in PRIMARY and PRIMARY[verb] not in args:
        args[PRIMARY[verb]] = positionals[0]
    return verb, args


def run_line(session: Session, line: str) -> CommandResult:
    verb, args = parse_command_line(line)
    return dispatch(session, verb, args)
