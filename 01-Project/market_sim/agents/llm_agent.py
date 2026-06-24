"""LLM trading agent — plugs into the runner loop via the Agent protocol.

It builds the same observation the eval harness uses (ground-truth probability is
NOT included), calls the model for a forced-JSON decision, and converts the result
into engine actions. The Gemini provider is lazy-imported so non-LLM runs don't need
the [eval] deps; on any model / parse / rate-limit failure the agent simply holds
that round and records the error in ``last_call`` for the UI.

Because every decision is a real (slow, rate-limited) model call, the web UI runs
LLM scenarios in single-step mode only.
"""

from __future__ import annotations

import json

from market_sim.engine.models import Side, Token

from .base import (
    Action,
    Agent,
    Cancel,
    CreateAccount,
    CreateMarket,
    DecisionContext,
    Hold,
    PlaceOrder,
    Transfer,
)


class LLMAgent(Agent):
    is_human = False

    def __init__(self, agent_id: str, params: dict | None = None) -> None:
        super().__init__(agent_id, params)
        p = self.params or {}
        self.model = p.get("model")                       # None -> env GEMINI_MODEL
        self.temperature = float(p.get("temperature", 0.7))
        self.max_recent = int(p.get("recent", 5))
        self._provider = None
        self._recent: list[str] = []

    # the genai client isn't picklable; drop it so the run state can be saved/resumed
    # (it is recreated lazily on the next call).
    def __getstate__(self) -> dict:
        d = self.__dict__.copy()
        d["_provider"] = None
        return d

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._provider = None

    def _get_provider(self):
        if self._provider is None:
            from market_sim.eval.provider import GeminiProvider
            # a 0.25s beat between calls smooths request rate; retries ride out 429s.
            # thinking_level="low" keeps a little reasoning on (better decisions) while
            # staying fast enough to reliably emit the function call.
            self._provider = GeminiProvider(model=self.model, temperature=self.temperature,
                                            use_cache=True, max_retries=5, pace=0.25,
                                            thinking_level="low")
        return self._provider

    def _observation(self, ctx: DecisionContext) -> dict:
        pf = ctx.portfolio
        markets = []
        for mid, mv in ctx.markets.items():
            if mv.status != "open":
                continue
            markets.append({
                "id": mid, "question": mv.question,
                "best_bid": mv.best_bid, "best_ask": mv.best_ask,
                "last_trade": mv.last_trade, "depth": mv.depth,
                "resolves_in_rounds": mv.resolves_in,
            })
        return {
            "round": ctx.round,
            "you": {
                "cash_available": pf.cash_available, "cash_locked": pf.cash_locked,
                "positions": {k: v for k, v in pf.positions.items() if any(v.values())},
                "open_orders": pf.open_orders,
            },
            "markets": markets,
            "news": [n.get("text", "") for n in ctx.news],
            "your_recent_actions": list(self._recent),
        }

    def decide(self, ctx: DecisionContext) -> list[Action]:
        obs = self._observation(ctx)
        user = (
            "Here is your current observation (JSON):\n"
            + json.dumps(obs, ensure_ascii=False, indent=2)
            + "\n\nReturn your decision as the required JSON object (beliefs, rationale, actions)."
        )
        try:
            comp = self._get_provider().complete(user, key=f"live:{self.agent_id}")
        except Exception as e:  # noqa: BLE001 — missing deps / creds -> hold, surface error
            self.last_call = {"belief": {}, "rationale": "", "ok": False,
                              "error": str(e)[:200], "round": ctx.round}
            return [Hold()]

        if not comp.ok or comp.parsed is None:
            self.last_call = {"belief": {}, "rationale": "", "ok": False,
                              "error": (comp.error or "no response")[:200],
                              "api_error": comp.api_error, "round": ctx.round}
            return [Hold()]

        resp = comp.parsed
        self.last_call = {
            "belief": {b.market: round(b.prob, 3) for b in resp.beliefs},
            "rationale": (resp.rationale or "")[:300],
            "ok": True, "attempts": comp.attempts, "round": ctx.round,
        }

        actions: list[Action] = []
        summary: list[str] = []
        for a in resp.actions:
            if (a.type == "place_order" and a.market and a.token in ("YES", "NO")
                    and a.side in ("buy", "sell") and a.price and a.qty):
                actions.append(PlaceOrder(a.market, Token(a.token), Side(a.side), int(a.price), int(a.qty)))
                summary.append(f"{a.side} {a.token}@{a.price}x{a.qty} {a.market}")
            elif a.type == "cancel_order" and a.order_id is not None:
                actions.append(Cancel(int(a.order_id)))
                summary.append(f"cancel #{a.order_id}")
            elif a.type == "hold":
                summary.append("hold")

        self._recent.append(f"r{ctx.round}: " + ("; ".join(summary) if summary else "hold"))
        self._recent = self._recent[-self.max_recent:]
        return actions or [Hold()]


# ===========================================================================
# Agentic tool-using trader — a SINGLE persistent agent across the whole run.
# ===========================================================================

_READ_VERBS = {"get_markets", "get_orderbook", "get_trade_history",
               "get_portfolio", "get_news", "get_news_detail"}

_AGENTIC_SYSTEM = """\
You are an autonomous trader in a binary prediction market. You are ONE continuous
agent: this is a long conversation across many trading rounds, and you remember
everything you have already seen and done. Build a strategy over time — accumulate
positions, make markets, exploit mispricings, learn other traders' habits.

MARKET RULES
- Each market has YES and NO shares. At resolution the winning side pays 100 cents,
  the loser 0; so one YES + one NO is always worth exactly 100 cents. A YES price of
  60 means 60 cents (~60% implied chance of YES). Prices are integer cents 1..99.
- There is NO short selling. To bet AGAINST YES, buy NO. You cannot sell shares you
  do not hold, nor spend more than your available cash.
- BLIND SUBMIT: every trader decides on the same start-of-round snapshot; all orders
  then execute together at the END of the round, matched by price-time priority — orders
  enter in the order traders finished deciding (a faster decision is submitted first). So
  your own order will NOT appear in the book during this round — you see its effect next round.

HOW TO ACT EACH ROUND — do these IN ORDER:
1. READ. The wake-up only lists your cash/positions and which markets are open. Prices,
   depth, the public tape, and YOUR private signal are NOT given — pull them with the read
   tools first. At minimum read your private signal (get_news) and the price/book of the
   markets you care about (get_markets / get_orderbook).
   Read tools (free, no market impact): get_markets, get_orderbook, get_trade_history,
   get_portfolio, get_news, get_news_detail.
2. COMMIT YOUR VIEW. Call commit_view(beliefs, plan): your current YES probability for each
   market you have a view on, and a one-line plan (what you intend to do this round / what
   to watch). Decide what you THINK before you act on it.
3. TRADE. place_order / cancel_order — QUEUED, settle at round end (blind submit). Your
   orders must be consistent with the view you just committed. You CANNOT trade before
   committing a view: any order placed before commit_view is rejected.
4. FINISH. Call finish(lessons): one line on what you LEARNED or CORRECTED this round (a
   mispricing you spotted, a prior you updated, a rival's habit you noticed). This is your
   memory hook for next round.

Order is strict: read → commit_view → trade → finish. Never trade before committing a
view, and never skip reading your own signal. (Trading is optional — but if you trade you
must have committed first; the wrap-up finish is not optional.)"""


_CAPS_SYSTEM_EXTRA = """

NEW ACTIONS (enabled this scenario) — like orders, these are QUEUED (blind submit) and
settle at round end, and they REQUIRE a committed view first:
- transfer(to, amount): move cents of YOUR available cash to ANOTHER existing account. Use
  it to fund a wallet you created or to move money to a partner.
- create_account(account_id, initial_cash): create a NEW passive wallet funded from YOUR
  cash. It only holds/forwards cash — it does NOT trade, gets no signal, no turn. You can
  later transfer cash in or out of it.
- create_market(market_id, question, resolve_round): open a NEW market. The SYSTEM secretly
  fixes its hidden true probability and outcome — you do NOT choose or see them; from next
  round you get a private signal on it like any other market. resolve_round must be > now."""


_ADV_ORDERS_SYSTEM_EXTRA = """

ORDER TYPES (enabled this scenario) — place_order takes an optional order_type:
- GTC (default): a limit order that rests on the book until filled or cancelled.
- GTD: a limit order that auto-expires after expire_round (give expire_round = the last
  round it should stay live); otherwise like GTC.
- FOK (market): fill the FULL qty immediately against resting liquidity, or the whole
  order is cancelled. Set price as your WORST acceptable price (buy high e.g. 99, sell low).
- FAK (market, IOC): fill whatever is available immediately, cancel the rest. Same price =
  worst-price cap.
- post_only (GTC/GTD only): the order is REJECTED if it would trade on entry — use it to
  make sure you only ever add liquidity (rest), never take.
Market orders only fill against existing resting orders, so they need a liquidity backbone
(market makers) to do anything — on a thin book a FOK/FAK may fill little or nothing."""


def _system_for(caps) -> str:
    """The system prompt for this agent: the base prompt unchanged when no extra
    capabilities are on (so existing scenarios are byte-identical), plus a short section
    per capability group the scenario enables."""
    extra = ""
    if caps is not None and (getattr(caps, "transfer", False)
                             or getattr(caps, "create_account", False)
                             or getattr(caps, "create_market", False)):
        extra += _CAPS_SYSTEM_EXTRA
    if caps is not None and getattr(caps, "advanced_orders", False):
        extra += _ADV_ORDERS_SYSTEM_EXTRA
    return _AGENTIC_SYSTEM + extra


def _tool_spec(name, description, properties=None, required=None):
    """One OpenAI function-tool dict (the provider-neutral tool format)."""
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object",
                       "properties": properties or {},
                       "required": required or []}}}


def build_agentic_tool_specs(caps=None):
    """Provider-neutral tool specs (OpenAI function-tool dicts) for the agent API.

    ``caps`` is the scenario's Config.capabilities (or None = all off). The base read +
    trade tools are always present; transfer / create_account / create_market are added
    only when the scenario enables them, so existing scenarios advertise the same toolset.
    Each provider converts these to its native tool format (Gemini -> FunctionDeclaration;
    OpenAI-compatible passes them through unchanged)."""
    market = {"type": "string", "description": "market id, e.g. COIN-A"}
    advanced = caps is not None and getattr(caps, "advanced_orders", False)
    # place_order has two forms: the base limit-only spec and an extended one exposing
    # order types (only when advanced_orders is enabled).
    if advanced:
        place_order = _tool_spec(
            "place_order",
            "Queue an order (settles at round end). Buy NO to bet against YES. "
            "order_type: GTC=resting limit (default); GTD=limit that expires after "
            "expire_round; FOK=market, fill fully now or cancel; FAK=market, fill "
            "what's available now and kill the rest. For market orders set price as "
            "your WORST acceptable price (buy high / sell low). post_only (GTC/GTD "
            "only) rejects the order if it would trade on entry.",
            {"market": market,
             "token": {"type": "string", "enum": ["YES", "NO"]},
             "side": {"type": "string", "enum": ["buy", "sell"]},
             "price": {"type": "integer", "description": "integer cents 1..99 (worst price for market orders)"},
             "qty": {"type": "integer", "description": "positive integer"},
             "order_type": {"type": "string", "enum": ["GTC", "GTD", "FOK", "FAK"],
                            "description": "time-in-force, optional (default GTC)"},
             "post_only": {"type": "boolean", "description": "GTC/GTD only: reject if it would cross (optional)"},
             "expire_round": {"type": "integer", "description": "GTD only: last round the order stays live"}},
            ["market", "token", "side", "price", "qty"])
    else:
        place_order = _tool_spec(
            "place_order",
            "Queue a limit order (settles at round end). Buy NO to bet against YES.",
            {"market": market,
             "token": {"type": "string", "enum": ["YES", "NO"]},
             "side": {"type": "string", "enum": ["buy", "sell"]},
             "price": {"type": "integer", "description": "integer cents 1..99 in the token's own coords"},
             "qty": {"type": "integer", "description": "positive integer"}},
            ["market", "token", "side", "price", "qty"])
    specs = [
        _tool_spec("get_markets",
                   "List all markets with current bid/ask/mid/last/volume and rounds-to-resolution."),
        _tool_spec("get_orderbook",
                   "Full bid/ask ladder (YES-price coords) with depth for one market.",
                   {"market": market,
                    "depth": {"type": "integer", "description": "levels per side (optional)"}},
                   ["market"]),
        _tool_spec("get_trade_history",
                   "Recent trades (the public tape) for one market.",
                   {"market": market,
                    "last": {"type": "integer", "description": "how many recent trades (optional)"}},
                   ["market"]),
        _tool_spec("get_portfolio",
                   "Your own cash (available/locked), positions, and open orders."),
        _tool_spec("get_news",
                   "Headlines of recent news signals (id + round + market + lean). Noisy but informative."),
        _tool_spec("get_news_detail",
                   "Full text + reliability of one news item by id.",
                   {"id": {"type": "integer"}}, ["id"]),
        place_order,
        _tool_spec("cancel_order",
                   "Cancel one of your open orders by id.",
                   {"order_id": {"type": "integer"}}, ["order_id"]),
        _tool_spec("commit_view",
                   "Commit your read of the markets BEFORE trading: your YES probability "
                   "per market and a one-line plan for this round. REQUIRED before any "
                   "place_order / cancel_order — decide what you think, then act on it.",
                   {"beliefs": {"type": "array",
                                "description": "your YES probability per market you have a view on",
                                "items": {"type": "object",
                                          "properties": {
                                              "market": market,
                                              "prob": {"type": "number", "description": "probability of YES, 0..1"}},
                                          "required": ["market", "prob"]}},
                    "plan": {"type": "string",
                             "description": "one short line: what you intend to do this round / what to watch"}},
                   ["beliefs"]),
        _tool_spec("finish",
                   "End your turn AFTER trading: one line on what you LEARNED or CORRECTED "
                   "this round. Beliefs + plan were already given via commit_view.",
                   {"lessons": {"type": "string",
                                "description": "one short line: what you LEARNED or CORRECTED this "
                                "round — a mispricing you spotted, a prior you updated, a rival's habit you noticed"}},
                   []),
    ]
    if caps is not None and getattr(caps, "transfer", False):
        specs.append(_tool_spec(
            "transfer",
            "Move cents of YOUR available cash to ANOTHER existing account "
            "(queued, settles at round end like an order).",
            {"to": {"type": "string", "description": "recipient account id (must already exist)"},
             "amount": {"type": "integer", "description": "positive integer cents to move"}},
            ["to", "amount"]))
    if caps is not None and getattr(caps, "create_account", False):
        specs.append(_tool_spec(
            "create_account",
            "Create a NEW passive wallet funded from YOUR available cash. It only "
            "holds/forwards cash — it does NOT trade, gets no signal, no turn.",
            {"account_id": {"type": "string", "description": "id for the new wallet (must be unused)"},
             "initial_cash": {"type": "integer", "description": "cents to fund it from your cash (>= 0)"}},
            ["account_id", "initial_cash"]))
    if caps is not None and getattr(caps, "create_market", False):
        specs.append(_tool_spec(
            "create_market",
            "Open a NEW market. The SYSTEM secretly fixes its hidden true probability "
            "and outcome — you do NOT choose or see them; you get a private signal on "
            "it from next round like any other market.",
            {"market_id": {"type": "string", "description": "id for the new market (must be unused)"},
             "question": {"type": "string", "description": "the yes/no question the market settles"},
             "resolve_round": {"type": "integer", "description": "round it resolves on (must be > now)"}},
            ["market_id", "question", "resolve_round"]))
    return specs


# Plain-data mirror of build_agentic_tool_specs() for the single-round walkthrough demo.
# Sent in `hello` so the page can show EXACTLY which tools the model may call and what
# each does, with curated kind/signature. Ordered by the round's usage flow
# (read → commit_view → trade → finish). Keep in sync with build_agentic_tool_specs.
AGENTIC_TOOLS_DISPLAY = [
    {"name": "get_markets", "kind": "read", "signature": "()",
     "description": "List all markets with current bid/ask/mid/last/volume and rounds-to-resolution."},
    {"name": "get_orderbook", "kind": "read", "signature": "(market, [depth])",
     "description": "Full bid/ask ladder (YES-price coords) with depth for one market."},
    {"name": "get_trade_history", "kind": "read", "signature": "(market, [last])",
     "description": "Recent trades (the public tape) for one market."},
    {"name": "get_portfolio", "kind": "read", "signature": "()",
     "description": "Your own cash (available/locked), positions, and open orders."},
    {"name": "get_news", "kind": "read", "signature": "()",
     "description": "Headlines of recent news signals (your private probability reads). Noisy but informative."},
    {"name": "get_news_detail", "kind": "read", "signature": "(id)",
     "description": "Full text + reliability of one news item by id."},
    {"name": "commit_view", "kind": "action", "signature": "(beliefs[{market, prob}], [plan])",
     "description": "Commit your YES probability per market + a one-line plan BEFORE trading. Required before any order."},
    {"name": "place_order", "kind": "action", "signature": "(market, token[YES|NO], side[buy|sell], price, qty)",
     "description": "Queue a limit order (settles at round end). Buy NO to bet against YES."},
    {"name": "cancel_order", "kind": "action", "signature": "(order_id)",
     "description": "Cancel one of your open orders by id."},
    {"name": "finish", "kind": "action", "signature": "([lessons])",
     "description": "End your turn AFTER trading: one line on what you learned/corrected this round."},
]


# Plain-data display rows for the capability-gated open-scenario tools — appended to the
# walkthrough catalogue only when the active scenario enables them (keep in sync with the
# specs in build_agentic_tool_specs). All are "action" kind.
_CAPS_TOOLS_DISPLAY = {
    "transfer": {"name": "transfer", "kind": "action", "signature": "(to, amount)",
                 "description": "Move cents of YOUR available cash to another existing account "
                                "(queued, settles at round end)."},
    "create_account": {"name": "create_account", "kind": "action", "signature": "(account_id, initial_cash)",
                       "description": "Create a NEW passive wallet funded from YOUR cash — it only "
                                      "holds/forwards cash, never trades."},
    "create_market": {"name": "create_market", "kind": "action", "signature": "(market_id, question, resolve_round)",
                      "description": "Open a NEW market; the system secretly fixes its hidden truth, "
                                     "and you get a private signal on it from next round."},
}


def _caps_tools_display(caps) -> list:
    """The extra walkthrough catalogue rows for whichever open-scenario tools this scenario
    has enabled (empty for scenarios that leave them off)."""
    if caps is None:
        return []
    return [row for key, row in _CAPS_TOOLS_DISPLAY.items() if getattr(caps, key, False)]


def agentic_agent_meta(caps=None) -> dict:
    """The 'what the system tells the model' bundle for the single-round walkthrough demo:
    the system prompt + the tool catalogue. Capability-aware — when the active scenario
    enables transfer/create_account/create_market, the appended system-prompt section and
    the extra tool rows are included, matching exactly what the model is actually sent.
    With ``caps=None`` (no scenario / capabilities off) it returns the original base bundle."""
    return {"system_prompt": _system_for(caps),
            "tools": AGENTIC_TOOLS_DISPLAY + _caps_tools_display(caps)}


class ToolLoopAgent(Agent):
    """A persistent, tool-using LLM trader. Holds ONE conversation (``self.contents``)
    that spans the entire run, so it accumulates genuine episodic memory. Each round it
    gets a short wake-up briefing, then drives a manual function-calling loop over the
    read-only agent API (queried against the frozen round-start state) and queues its
    orders for end-of-round settlement (blind submit preserved)."""

    is_human = False

    def __init__(self, agent_id: str, params: dict | None = None) -> None:
        super().__init__(agent_id, params)
        p = self.params or {}
        self.model = p.get("model")                       # None -> provider's env default
        self.provider = p.get("provider")                 # "gemini"|"openai"|"deepseek"|... or None
        self.temperature = float(p.get("temperature", 0.7))
        self.max_tool_calls = int(p.get("max_tool_calls", 8))  # model calls per round
        # reasoning / "thinking" mode for OpenAI-compatible providers (e.g. DeepSeek v4).
        self.thinking = bool(p.get("thinking", False))
        self.reasoning_effort = p.get("reasoning_effort")
        self.max_output_tokens = int(p.get("max_output_tokens", 0)) or None
        self._provider = None
        self._tools = None
        self.contents: list = []   # the persistent cross-round conversation (provider-neutral dicts)

    # ``contents`` (the conversation memory) pickles fine; the genai client/tools do
    # not, so drop them — they are rebuilt lazily. This lets a run be saved + resumed
    # with each agent's memory intact.
    def __getstate__(self) -> dict:
        d = self.__dict__.copy()
        d["_provider"] = None
        d["_tools"] = None
        return d

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._provider = None
        self._tools = None

    def _get_provider(self):
        if self._provider is None:
            from market_sim.eval.providers import get_provider
            # Pick the provider for this agent (mixed-per-run): explicit self.provider wins,
            # else inferred from self.model's name (deepseek*/gpt*/gemini*). One provider is
            # chosen once and kept for the whole run — that is what makes the Gemini _native
            # (thought_signature) scheme safe. A 0.25s beat BETWEEN successive tool-call turns
            # smooths the request rate; retries with exponential backoff ride out transient 429s.
            self._provider = get_provider(self.model, self.provider,
                                          temperature=self.temperature, use_cache=False,
                                          max_retries=5, pace=0.25,
                                          thinking=self.thinking,
                                          reasoning_effort=self.reasoning_effort,
                                          max_output_tokens=self.max_output_tokens)
        return self._provider

    def _tool_specs(self):
        if self._tools is None:
            self._tools = build_agentic_tool_specs(self.caps)
        return self._tools

    # --- per-round wake-up briefing (minimal: cash/positions + open market ids only;
    #     prices/depth/tape/signal must be pulled with the read tools) ---

    def _wake_briefing(self, ctx: DecisionContext) -> str:
        # Deliberately minimal: it tells the agent its own cash/positions and which
        # markets are open, but NO prices and NO signal values — so the agent has to
        # pull them with the read tools (get_markets / get_orderbook / get_news) before
        # acting. That makes the run an actual information-gathering loop.
        pf = ctx.portfolio
        pos = {m: v for m, v in pf.positions.items() if any(v.values())}
        open_ids = [mid for mid, mv in ctx.markets.items() if mv.status == "open"]
        resolved = [mid for mid, mv in ctx.markets.items() if mv.status != "open"]
        lines = [f"=== Round {ctx.round} ===",
                 f"Your cash: available {pf.cash_available}, locked {pf.cash_locked}.",
                 f"Your positions: {pos or 'none'}."]
        if pf.open_orders:
            brief = [f"#{o['order_id']} {o['side']} {o['token']}@{o['price']}x{o['qty']} {o['market']}"
                     for o in pf.open_orders]
            lines.append("Your open orders: " + "; ".join(brief))
        markets_line = f"Open markets: {', '.join(open_ids) if open_ids else 'none'}"
        if resolved:
            markets_line += f"  (resolved: {', '.join(resolved)})"
        lines.append(markets_line + ". Prices, depth and the tape are NOT shown here —"
                     " call get_markets / get_orderbook / get_trade_history to see them.")
        if ctx.signals:
            lines.append("You have a FRESH PRIVATE signal this round (your own noisy read of each "
                         "market's YES probability — only you see it). Call get_news to read it; "
                         "averaging your past reads sharpens it and it grows more reliable over time.")
        elif ctx.news:
            lines.append(f"News: {len(ctx.news)} recent headline(s) available — call get_news to read.")
        lines.append("Gather what you need with the read tools, act, then call finish(beliefs, plan).")
        return "\n".join(lines)

    # --- order construction from tool args ---

    def _mk_place(self, fa: dict):
        try:
            ot = str(fa.get("order_type", "GTC")).upper()
            if ot not in ("GTC", "GTD", "FOK", "FAK"):
                ot = "GTC"
            er = fa.get("expire_round")
            act = PlaceOrder(str(fa["market"]), Token(str(fa["token"]).upper()),
                             Side(str(fa["side"]).lower()), int(fa["price"]), int(fa["qty"]),
                             tif=ot, post_only=bool(fa.get("post_only", False)),
                             expire_round=int(er) if er is not None else None)
        except (KeyError, ValueError) as e:
            return None, {"status": "rejected", "reason": f"bad args: {e}"}
        return act, {"status": "queued", "note": "settles at round end (blind submit)"}

    # --- the round ---

    def decide(self, ctx: DecisionContext) -> list[Action]:
        self.last_call = None
        briefing = self._wake_briefing(ctx)
        # announce the literal briefing the model is about to get (system→model input),
        # at the very start of the turn — the walkthrough demo replays this verbatim.
        if ctx.on_briefing:
            ctx.on_briefing(briefing)
        self.contents.append({"role": "user", "text": briefing})

        provider = self._get_provider()
        tools = self._tool_specs()
        pending: list[Action] = []
        summary: list[str] = []
        finished = False
        nudged = False        # have we reminded the model to call finish() once?
        total_retries = 0     # transient-error (429/5xx) retries accumulated this round
        total_backoff = 0.0   # seconds spent on exponential backoff this round
        queue_seq = 0         # per-round counter -> client_id for queued-order correlation
        committed = False     # has the model called commit_view? (gate: no trading before it)
        view_belief: dict = {}  # YES prob per market, from commit_view (reported BEFORE trading)
        view_plan = ""        # one-line plan, from commit_view

        system_prompt = _system_for(self.caps)
        for turn_index in range(self.max_tool_calls):
            turn = provider.tool_turn(self.contents, tools, system=system_prompt,
                                      temperature=self.temperature)
            total_retries += turn.get("retries", 0)
            total_backoff += turn.get("backoff_s", 0.0)
            # announce the raw model output (text + requested calls) the instant it
            # returns — the verbatim model→system side, BEFORE the calls' results.
            if ctx.on_model_turn:
                # strip the synthesized tool_call id so the model_turn event payload stays
                # exactly {"name","args"} per call (recorded runs remain byte-identical).
                ctx.on_model_turn({"turn": turn_index, "text": turn.get("text") or "",
                                   "calls": [{"name": c["name"], "args": c["args"]}
                                             for c in (turn.get("function_calls") or [])],
                                   "error": turn.get("error")})
            if turn["error"] is not None:
                # keep the conversation well-formed (alternating) so next round is valid
                self.contents.append({"role": "assistant", "text": "(no response)",
                                      "tool_calls": [], "_native": None})
                self.last_call = {"belief": view_belief, "rationale": view_plan, "ok": False,
                                  "error": turn["error"][:200],
                                  "api_error": turn["api_error"], "round": ctx.round,
                                  "retries": total_retries, "backoff_s": round(total_backoff, 1)}
                return pending or [Hold()]

            # Append the model's reply (the neutral assistant dict). For Gemini it carries
            # the native Content under `_native`, re-sent VERBATIM next turn so the
            # thought_signature is preserved; the provider already substitutes a text turn
            # for an empty/no-parts reply so the persistent conversation stays well-formed.
            assistant = turn["assistant"]
            if assistant is not None:
                self.contents.append(assistant)
            else:
                self.contents.append({"role": "assistant", "text": turn["text"] or "(no content)",
                                      "tool_calls": [], "_native": None})

            calls = turn["function_calls"]
            if not calls:
                # the model stopped calling tools without finish(). A round isn't "done"
                # until it calls finish() (trading is optional, but the wrap-up is not), so
                # nudge it once to finish; only give up if it still won't.
                if not nudged:
                    nudged = True
                    self.contents.append({"role": "user", "text":
                        "End your turn by calling finish(lessons) to record what you "
                        "learned — even if you placed no orders this round."})
                    continue
                self.last_call = {"belief": view_belief, "rationale": view_plan,
                                  "lessons": (turn["text"] or "(ended without finish)")[:300],
                                  "ok": True, "round": ctx.round, "no_finish": True}
                break

            responses = []
            for fc in calls:
                name, fa, tc_id = fc["name"], fc["args"], fc.get("id", "")
                if name in _READ_VERBS:
                    result = ctx.query(name, fa) if ctx.query else {"error": "no query interface"}
                elif name == "commit_view":
                    # the agent states its belief + plan BEFORE trading; announce it NOW so
                    # it lands in the trail in true call order (between reads and orders).
                    view_belief = {b.get("market"): round(float(b.get("prob")), 3)
                                   for b in (fa.get("beliefs") or []) if b.get("market") is not None}
                    view_plan = (fa.get("plan") or "")[:300]
                    committed = True
                    if ctx.on_view:
                        ctx.on_view({"belief": view_belief, "plan": view_plan})
                    result = {"status": "committed", "note": "you may now place orders"}
                elif name == "place_order":
                    if not committed:        # gate: must commit a view before trading
                        result = {"status": "rejected",
                                  "reason": "call commit_view(beliefs, plan) before placing any order"}
                    else:
                        act, result = self._mk_place(fa)
                        if act is not None:
                            queue_seq += 1
                            act.client_id = f"{self.agent_id}:{ctx.round}:{queue_seq}"
                            pending.append(act)
                            summary.append(f"{fa.get('side')} {fa.get('token')}@{fa.get('price')}"
                                           f"x{fa.get('qty')} {fa.get('market')}")
                            # announce it NOW, in true model-call order; the fill comes later
                            # (blind submit) on the round-end place_order event, same client_id
                            if ctx.on_queue:
                                qp = {"client_id": act.client_id, "kind": "order",
                                      "market": act.market, "token": act.token.value,
                                      "side": act.side.value, "price": act.price, "qty": act.qty}
                                if act.tif != "GTC":
                                    qp["tif"] = act.tif
                                if act.post_only:
                                    qp["post_only"] = True
                                if act.expire_round is not None:
                                    qp["expire_round"] = act.expire_round
                                ctx.on_queue(qp)
                elif name == "cancel_order":
                    if not committed:        # same gate as place_order
                        result = {"status": "rejected",
                                  "reason": "call commit_view(beliefs, plan) before cancelling"}
                    else:
                        try:
                            oid = int(fa["order_id"])
                            queue_seq += 1
                            cid = f"{self.agent_id}:{ctx.round}:{queue_seq}"
                            pending.append(Cancel(oid, client_id=cid))
                            summary.append(f"cancel #{oid}")
                            result = {"status": "queued"}
                            if ctx.on_queue:
                                ctx.on_queue({"client_id": cid, "kind": "cancel", "order_id": oid})
                        except (KeyError, ValueError) as e:
                            result = {"status": "rejected", "reason": f"bad args: {e}"}
                elif name == "transfer":
                    if not committed:
                        result = {"status": "rejected",
                                  "reason": "call commit_view(beliefs, plan) before transferring"}
                    else:
                        try:
                            to = str(fa["to"]); amt = int(fa["amount"])
                            queue_seq += 1
                            cid = f"{self.agent_id}:{ctx.round}:{queue_seq}"
                            pending.append(Transfer(to, amt, client_id=cid))
                            summary.append(f"transfer {amt}->{to}")
                            result = {"status": "queued", "note": "settles at round end (blind submit)"}
                            if ctx.on_queue:
                                ctx.on_queue({"client_id": cid, "kind": "transfer",
                                              "to": to, "amount": amt})
                        except (KeyError, ValueError) as e:
                            result = {"status": "rejected", "reason": f"bad args: {e}"}
                elif name == "create_account":
                    if not committed:
                        result = {"status": "rejected",
                                  "reason": "call commit_view(beliefs, plan) before creating an account"}
                    else:
                        try:
                            acc = str(fa["account_id"]); ic = int(fa["initial_cash"])
                            queue_seq += 1
                            cid = f"{self.agent_id}:{ctx.round}:{queue_seq}"
                            pending.append(CreateAccount(acc, ic, client_id=cid))
                            summary.append(f"create_account {acc}(+{ic})")
                            result = {"status": "queued", "note": "settles at round end (blind submit)"}
                            if ctx.on_queue:
                                ctx.on_queue({"client_id": cid, "kind": "create_account",
                                              "account_id": acc, "initial_cash": ic})
                        except (KeyError, ValueError) as e:
                            result = {"status": "rejected", "reason": f"bad args: {e}"}
                elif name == "create_market":
                    if not committed:
                        result = {"status": "rejected",
                                  "reason": "call commit_view(beliefs, plan) before creating a market"}
                    else:
                        try:
                            mid = str(fa["market_id"]); q = str(fa["question"])
                            rr = int(fa["resolve_round"])
                            queue_seq += 1
                            cid = f"{self.agent_id}:{ctx.round}:{queue_seq}"
                            pending.append(CreateMarket(mid, q, rr, client_id=cid))
                            summary.append(f"create_market {mid}")
                            result = {"status": "queued", "note": "settles at round end (blind submit)"}
                            if ctx.on_queue:
                                ctx.on_queue({"client_id": cid, "kind": "create_market",
                                              "market_id": mid, "question": q, "resolve_round": rr})
                        except (KeyError, ValueError) as e:
                            result = {"status": "rejected", "reason": f"bad args: {e}"}
                elif name == "finish":
                    # belief + plan came from commit_view; finish only carries the lesson
                    self.last_call = {"belief": view_belief, "rationale": view_plan,
                                      "lessons": (fa.get("lessons") or "")[:300],
                                      "ok": True, "round": ctx.round,
                                      "actions": list(summary)}
                    result = {"status": "finished"}
                    finished = True
                else:
                    result = {"error": f"unknown tool '{name}'"}
                responses.append({"role": "tool", "tool_call_id": tc_id, "name": name,
                                  "result": result if isinstance(result, dict) else {"value": result}})

            self.contents.extend(responses)
            if finished:
                break

        if self.last_call is None:
            # ran out of budget without finishing — still record the committed view + actions
            self.last_call = {"belief": view_belief, "rationale": view_plan,
                              "lessons": "(tool budget exhausted)",
                              "ok": True, "round": ctx.round, "actions": list(summary)}
        # record this round's rate-limit cost so it lands in the llm_call event (the log)
        self.last_call["retries"] = total_retries
        self.last_call["backoff_s"] = round(total_backoff, 1)
        return pending or [Hold()]
