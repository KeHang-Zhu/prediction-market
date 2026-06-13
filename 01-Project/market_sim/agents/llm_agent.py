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

from .base import Action, Agent, Cancel, DecisionContext, Hold, PlaceOrder


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


def _build_agentic_tools():
    """FunctionDeclarations for the agent API (built lazily — needs google.genai)."""
    from google.genai import types

    S, T = types.Schema, types.Type

    def obj(props=None, required=None):
        return S(type=T.OBJECT, properties=props or {}, required=required or [])

    market = S(type=T.STRING, description="market id, e.g. COIN-A")
    decls = [
        types.FunctionDeclaration(
            name="get_markets",
            description="List all markets with current bid/ask/mid/last/volume and rounds-to-resolution.",
            parameters=obj()),
        types.FunctionDeclaration(
            name="get_orderbook",
            description="Full bid/ask ladder (YES-price coords) with depth for one market.",
            parameters=obj({"market": market,
                            "depth": S(type=T.INTEGER, description="levels per side (optional)")},
                           ["market"])),
        types.FunctionDeclaration(
            name="get_trade_history",
            description="Recent trades (the public tape) for one market.",
            parameters=obj({"market": market,
                            "last": S(type=T.INTEGER, description="how many recent trades (optional)")},
                           ["market"])),
        types.FunctionDeclaration(
            name="get_portfolio",
            description="Your own cash (available/locked), positions, and open orders.",
            parameters=obj()),
        types.FunctionDeclaration(
            name="get_news",
            description="Headlines of recent news signals (id + round + market + lean). Noisy but informative.",
            parameters=obj()),
        types.FunctionDeclaration(
            name="get_news_detail",
            description="Full text + reliability of one news item by id.",
            parameters=obj({"id": S(type=T.INTEGER)}, ["id"])),
        types.FunctionDeclaration(
            name="place_order",
            description="Queue a limit order (settles at round end). Buy NO to bet against YES.",
            parameters=obj({
                "market": market,
                "token": S(type=T.STRING, enum=["YES", "NO"]),
                "side": S(type=T.STRING, enum=["buy", "sell"]),
                "price": S(type=T.INTEGER, description="integer cents 1..99 in the token's own coords"),
                "qty": S(type=T.INTEGER, description="positive integer"),
            }, ["market", "token", "side", "price", "qty"])),
        types.FunctionDeclaration(
            name="cancel_order",
            description="Cancel one of your open orders by id.",
            parameters=obj({"order_id": S(type=T.INTEGER)}, ["order_id"])),
        types.FunctionDeclaration(
            name="commit_view",
            description="Commit your read of the markets BEFORE trading: your YES probability "
                        "per market and a one-line plan for this round. REQUIRED before any "
                        "place_order / cancel_order — decide what you think, then act on it.",
            parameters=obj({
                "beliefs": S(type=T.ARRAY, description="your YES probability per market you have a view on",
                             items=obj({"market": market,
                                        "prob": S(type=T.NUMBER, description="probability of YES, 0..1")},
                                       ["market", "prob"])),
                "plan": S(type=T.STRING, description="one short line: what you intend to do this round / what to watch"),
            }, ["beliefs"])),
        types.FunctionDeclaration(
            name="finish",
            description="End your turn AFTER trading: one line on what you LEARNED or CORRECTED "
                        "this round. Beliefs + plan were already given via commit_view.",
            parameters=obj({
                "lessons": S(type=T.STRING, description="one short line: what you LEARNED or CORRECTED this "
                             "round — a mispricing you spotted, a prior you updated, a rival's habit you noticed"),
            }, [])),
    ]
    return [types.Tool(function_declarations=decls)]


# Plain-data mirror of _build_agentic_tools() for the single-round walkthrough demo.
# Sent in `hello` so the page can show EXACTLY which tools the model may call and what
# each does — without importing google.genai. Ordered by the round's usage flow
# (read → commit_view → trade → finish). Keep in sync with _build_agentic_tools.
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


def agentic_agent_meta() -> dict:
    """The static 'what the system tells the model' bundle for the single-round
    walkthrough demo: the system prompt + the tool catalogue. Sent once in `hello`."""
    return {"system_prompt": _AGENTIC_SYSTEM, "tools": AGENTIC_TOOLS_DISPLAY}


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
        self.model = p.get("model")
        self.temperature = float(p.get("temperature", 0.7))
        self.max_tool_calls = int(p.get("max_tool_calls", 8))  # model calls per round
        self._provider = None
        self._tools = None
        self.contents: list = []   # the persistent cross-round conversation

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
            from market_sim.eval.provider import GeminiProvider
            # a 0.25s beat BETWEEN successive tool-call turns smooths the request rate;
            # extra retries with exponential backoff still ride out any transient 429s.
            # thinking_level="low" keeps light reasoning on; the empty-content guard +
            # finish-nudge below still protect against a thinking turn that emits no call.
            self._provider = GeminiProvider(model=self.model, temperature=self.temperature,
                                            use_cache=False, max_retries=5, pace=0.25,
                                            thinking_level="low")
        return self._provider

    def _tools_decls(self):
        if self._tools is None:
            self._tools = _build_agentic_tools()
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
            act = PlaceOrder(str(fa["market"]), Token(str(fa["token"]).upper()),
                             Side(str(fa["side"]).lower()), int(fa["price"]), int(fa["qty"]))
        except (KeyError, ValueError) as e:
            return None, {"status": "rejected", "reason": f"bad args: {e}"}
        return act, {"status": "queued", "note": "settles at round end (blind submit)"}

    # --- the round ---

    def decide(self, ctx: DecisionContext) -> list[Action]:
        from google.genai import types

        self.last_call = None
        briefing = self._wake_briefing(ctx)
        # announce the literal briefing the model is about to get (system→model input),
        # at the very start of the turn — the walkthrough demo replays this verbatim.
        if ctx.on_briefing:
            ctx.on_briefing(briefing)
        self.contents.append(types.Content(
            role="user", parts=[types.Part(text=briefing)]))

        provider = self._get_provider()
        tools = self._tools_decls()
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

        for turn_index in range(self.max_tool_calls):
            turn = provider.tool_turn(self.contents, tools, system=_AGENTIC_SYSTEM,
                                      temperature=self.temperature)
            total_retries += turn.get("retries", 0)
            total_backoff += turn.get("backoff_s", 0.0)
            # announce the raw model output (text + requested calls) the instant it
            # returns — the verbatim model→system side, BEFORE the calls' results.
            if ctx.on_model_turn:
                ctx.on_model_turn({"turn": turn_index, "text": turn.get("text") or "",
                                   "calls": turn.get("function_calls") or [],
                                   "error": turn.get("error")})
            if turn["error"] is not None:
                # keep the conversation well-formed (alternating) so next round is valid
                self.contents.append(types.Content(role="model",
                                                   parts=[types.Part(text="(no response)")]))
                self.last_call = {"belief": view_belief, "rationale": view_plan, "ok": False,
                                  "error": turn["error"][:200],
                                  "api_error": turn["api_error"], "round": ctx.round,
                                  "retries": total_retries, "backoff_s": round(total_backoff, 1)}
                return pending or [Hold()]

            # Append the model's reply VERBATIM when it has content (it carries the
            # thought_signature, which a hand-rebuilt Part would lose). Some models
            # occasionally return a Content with NO parts; appending that poisons the
            # persistent conversation — every later request then fails with
            # "400 must include at least one parts field". Substitute a minimal text
            # part in that case so the conversation stays well-formed.
            c = turn["content"]
            if c is not None and getattr(c, "parts", None):
                self.contents.append(c)
            else:
                self.contents.append(types.Content(
                    role="model", parts=[types.Part(text=turn["text"] or "(no content)")]))

            calls = turn["function_calls"]
            if not calls:
                # the model stopped calling tools without finish(). A round isn't "done"
                # until it calls finish() (trading is optional, but the wrap-up is not), so
                # nudge it once to finish; only give up if it still won't.
                if not nudged:
                    nudged = True
                    self.contents.append(types.Content(role="user", parts=[types.Part(
                        text="End your turn by calling finish(lessons) to record what you "
                             "learned — even if you placed no orders this round.")]))
                    continue
                self.last_call = {"belief": view_belief, "rationale": view_plan,
                                  "lessons": (turn["text"] or "(ended without finish)")[:300],
                                  "ok": True, "round": ctx.round, "no_finish": True}
                break

            responses = []
            for fc in calls:
                name, fa = fc["name"], fc["args"]
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
                                ctx.on_queue({"client_id": act.client_id, "kind": "order",
                                              "market": act.market, "token": act.token.value,
                                              "side": act.side.value, "price": act.price, "qty": act.qty})
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
                responses.append(types.Part(function_response=types.FunctionResponse(
                    name=name, response=result if isinstance(result, dict) else {"value": result})))

            self.contents.append(types.Content(role="user", parts=responses))
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
