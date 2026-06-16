"""The round loop. Owns the single numpy Generator and the event id/timestamp.

Per-round draw order (the determinism contract): news flips (market-id order) ->
agent shuffle. Market outcomes are pre-sampled ONCE at init in market-id order, so
resolution consumes no randomness. Bots draw from their own spawned substreams.
"""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial

import numpy as np

from market_sim.agents.base import (
    Action,
    Agent,
    Cancel,
    CreateAccount,
    CreateMarket,
    DecisionContext,
    Hold,
    HumanAgent,
    MarketView,
    PlaceOrder,
    PortfolioView,
    Transfer,
)
from market_sim.agents.scripted import BOT_REGISTRY
from market_sim.engine.exchange import Exchange
from market_sim.engine.models import Account, Market, MarketStatus, Side, TimeInForce, Token
from market_sim.engine.settlement import resolve_market

from .config import Config
from .events import Event
from .sinks import EventSink, ListSink

_SETTLE_EVENT_TYPE = {
    "transfer_yes": "fill",
    "transfer_no": "fill",
    "mint": "mint",
    "merge": "merge",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Runner:
    def __init__(self, config: Config, sink: EventSink | None = None) -> None:
        self.config = config
        self.sink = sink if sink is not None else ListSink()
        self.round_no = 0
        self._eid = 0
        # serializes event emission so concurrent agent decisions (parallel LLM
        # tool calls) can't race on the event id / sink. Not picklable -> recreated.
        self._emit_lock = threading.Lock()
        self.snapshots: dict[int, dict] = {}
        self.recent_news: list[dict] = []
        self.news_log: list[dict] = []   # full, id-stamped history (for the get_news tool)
        self._news_id = 0
        # prob-mode private signals: agent_id -> id-stamped list of its own estimates
        self.private_news: dict[str, list[dict]] = {}
        self.signal_sigma: dict[str, float] = {}  # agent_id -> base noise (prob mode only)
        self.human_pending: dict[str, list[Action]] = {}

        # --- build markets + accounts ---
        markets: dict[str, Market] = {}
        for mc in config.markets:
            markets[mc.id] = Market(
                id=mc.id, question=mc.question, true_prob=mc.true_prob,
                resolve_round=mc.resolve_round, fixed_outcome=mc.fixed_outcome,
            )

        accounts: dict[str, Account] = {}
        self.agent_types: dict[str, str] = {}
        self.initial_cash: dict[str, int] = {}
        agent_specs: list[tuple[str, str, dict]] = []  # (agent_id, type, params)
        for ac in config.agents:
            for aid in config.expand_agent_ids(ac):
                accounts[aid] = Account(aid, cash_available=ac.initial_cash)
                self.agent_types[aid] = ac.type
                self.initial_cash[aid] = ac.initial_cash
                agent_specs.append((aid, ac.type, dict(ac.params)))
                if config.news.mode == "prob" and "signal_sigma" in ac.params:
                    self.signal_sigma[aid] = float(ac.params["signal_sigma"])
                    self.private_news[aid] = []

        self.exchange = Exchange(markets, accounts, allow_self_trade=config.allow_self_trade)

        # --- build agents ---
        self.agents: dict[str, Agent] = {}
        for aid, atype, params in agent_specs:
            if atype == "human":
                self.agents[aid] = HumanAgent(aid, params)
            else:
                cls = BOT_REGISTRY.get(atype)
                if cls is None:
                    raise ValueError(f"unknown agent type: {atype}")
                self.agents[aid] = cls(aid, params)
        self.agent_ids_sorted = sorted(self.agents)

        # --- hand each agent the scenario capability flags (like rng below). Only
        #     tool-using LLM agents read them, to decide which extra tools to declare. ---
        for aid in self.agents:
            self.agents[aid].caps = config.capabilities

        # --- seed the one rng + spawn bot substreams (id-sorted, deterministic) ---
        root = np.random.SeedSequence(config.seed)
        children = root.spawn(1 + len(self.agent_ids_sorted))
        self.rng = np.random.default_rng(children[0])
        for ss, aid in zip(children[1:], self.agent_ids_sorted):
            self.agents[aid].rng = np.random.default_rng(ss)

        # --- pre-sample latent outcomes (market-id order) ---
        for mid in sorted(markets):
            m = markets[mid]
            if m.fixed_outcome is not None:
                m.outcome = int(m.fixed_outcome)
            else:
                m.outcome = 1 if self.rng.random() < m.true_prob else 0

        # --- initial config + snapshot events ---
        self._emit("config", None, {"config": config.model_dump()})
        snap0 = self._build_snapshot(0)
        self.snapshots[0] = snap0
        self._emit("snapshot", None, {"state": snap0})

    # ----------------------------------------------------------- pickling

    def __getstate__(self) -> dict:
        # the event sink (file handle / sockets) is not picklable; the Session
        # reattaches a fresh append sink on load. The emit lock is also dropped.
        d = self.__dict__.copy()
        d["sink"] = None
        d.pop("_emit_lock", None)
        return d

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._emit_lock = threading.Lock()

    # ------------------------------------------------------------------ emit

    def _emit(self, etype: str, agent_id: str | None, payload: dict, result: dict | None = None) -> Event:
        # locked so concurrent decisions (parallel LLM tool calls) get a monotonic
        # event id and ts and never interleave a half-written JSONL line.
        with self._emit_lock:
            ev = Event(self._eid, self.round_no, etype, agent_id, payload, result, ts=_now_iso())
            self._eid += 1
            self.sink.emit(ev)
        return ev

    # ------------------------------------------------------------------ loop

    def run(self, rounds: int | None = None) -> None:
        n = rounds if rounds is not None else self.config.rounds
        for _ in range(n):
            self.step()

    def step(self) -> dict:
        self.round_no += 1
        r = self.round_no
        self._emit("round_start", None, {"round": r})

        # 1a. news / private signals (deterministic order)
        if self.config.news.enabled and self.config.news.mode == "prob":
            self._publish_private_signals(r)
        else:
            self._publish_news(r)
        # 1b. resolve expiring markets (market-id order), before decisions
        self._resolve_due(r)
        # 1c. expire GTD orders whose validity has passed (after resolution, so a resolving
        #     market already cleared its orders). Scripted runs have no GTD orders -> no-op.
        for mid, oid in self.exchange.expire_due(r):
            self._emit("order_expired", None, {"market": mid, "order_id": oid})

        # 2. freeze the decision snapshot (blind submit). Build every agent's context
        #    up front (all reads, no side effects), then decide. Decisions only READ
        #    the frozen book and never touch self.rng (signals were drawn before, the
        #    execution shuffle happens after), so when LLM traders are present we run
        #    them CONCURRENTLY — blind-submit already makes a round's decisions mutually
        #    independent, and parallelism only saves wall-clock. Pure-scripted runs keep
        #    the deterministic sequential path, preserving byte-exact replay.
        views = self._market_views()
        actions_by_agent: dict[str, list[Action]] = {}
        specs: list[tuple[str, Agent, DecisionContext]] = []
        for aid in self.agent_ids_sorted:
            agent = self.agents[aid]
            if agent.is_human:
                actions_by_agent[aid] = self.human_pending.pop(aid, [])
                continue
            ctx = DecisionContext(
                round=r, agent_id=aid, rng=agent.rng, markets=views,
                portfolio=self._portfolio_view(aid), news=list(self.recent_news),
                signals=[n for n in self.private_news.get(aid, []) if n["round"] == r],
                query=partial(self._agent_query, aid),
                on_queue=partial(self._agent_queue, aid),
                on_view=partial(self._agent_view, aid),
                on_briefing=partial(self._agent_briefing, aid),
                on_model_turn=partial(self._agent_model_turn, aid),
            )
            specs.append((aid, agent, ctx))

        if self.has_llm and len(specs) > 1:
            # run all agents of a round concurrently (a round is blind-submit, so their
            # decisions are independent); the cap just bounds the burst to the model's rate
            # limit (429s), and per-call pacing in the provider further smooths requests/sec.
            with ThreadPoolExecutor(max_workers=min(5, len(specs))) as pool:
                decided = list(pool.map(self._run_decision, specs))
        else:
            decided = [self._run_decision(s) for s in specs]
        cap = self.config.max_actions_per_agent
        for aid, acts, _t in decided:
            actions_by_agent[aid] = acts[:cap]
            # actions beyond the per-round cap are dropped — but a tool-using agent already
            # ANNOUNCED them (an order_queued event at decide time), so resolve each dropped
            # one with an invalid_action carrying its client_id; otherwise the UI leaves it
            # stuck "queued" forever. Scripted bots never announce (no client_id) -> skipped,
            # so their event stream stays byte-exact.
            for dropped in acts[cap:]:
                cid = getattr(dropped, "client_id", None)
                if cid is None:
                    continue
                if isinstance(dropped, PlaceOrder):
                    payload = {"market": dropped.market, "token": dropped.token.value,
                               "side": dropped.side.value, "price": dropped.price,
                               "qty": dropped.qty, "client_id": cid}
                    self._add_order_type_fields(payload, dropped)
                elif isinstance(dropped, Cancel):
                    payload = {"order_id": dropped.order_id, "client_id": cid}
                elif isinstance(dropped, Transfer):
                    payload = {"to": dropped.to, "amount": dropped.amount, "client_id": cid}
                elif isinstance(dropped, CreateAccount):
                    payload = {"account_id": dropped.account_id,
                               "initial_cash": dropped.initial_cash, "client_id": cid}
                elif isinstance(dropped, CreateMarket):
                    payload = {"market_id": dropped.market_id, "question": dropped.question,
                               "resolve_round": dropped.resolve_round, "client_id": cid}
                else:
                    payload = {"client_id": cid}
                self._emit("invalid_action", aid, payload,
                           {"status": "rejected", "reason": f"dropped (over {cap}/round action cap)"})

        # 3. execution order.
        #    LLM scenarios: REAL finish-time priority within the round — whoever
        #    finished deciding first gets matched first (price-time priority, time =
        #    when the agent actually finished). Humans (if any) act last. Pure-scripted
        #    runs keep the random (seeded, deterministic) order -> byte-exact replay.
        if self.has_llm:
            order = [aid for aid, _acts, _t in sorted(decided, key=lambda x: x[2])]
            order += [aid for aid in self.agent_ids_sorted if self.agents[aid].is_human]
        else:
            perm = self.rng.permutation(len(self.agent_ids_sorted))
            order = [self.agent_ids_sorted[i] for i in perm]

        # 4. execute, capturing an order-by-order matching trace — the authoritative
        #    record the "round clearing" demo replays: the order book before/after each
        #    order, what it crossed, and how each cross settled (transfer / mint / merge).
        #    The trace is a pure side-record: it reads the same engine the events come
        #    from and never affects engine state or determinism.
        trace_steps: list[dict] = []
        decisions: list[dict] = []
        seq = 0
        for aid in order:
            acts = actions_by_agent[aid]
            queued = [self._decision_entry(act) for act in acts
                      if isinstance(act, (PlaceOrder, Cancel))]
            if queued:
                decisions.append({"agent": aid, "orders": queued})
            for act in acts:
                if isinstance(act, Hold):
                    continue
                mkt = self._act_market(act)
                before = self._book_state(mkt) if mkt else None
                res = self._execute(aid, act, r)
                after = self._book_state(mkt) if mkt else None
                step = self._trace_step(seq, aid, act, res, before, after)
                if step is not None:
                    trace_steps.append(step)
                    seq += 1

        if trace_steps:
            self._emit("clearing_trace", None, {
                "round": r, "execution_order": [d["agent"] for d in decisions],
                "decisions": decisions, "steps": trace_steps,
            })

        # 5. invariants
        self.exchange.check_invariants()

        # 6. snapshot + round_end
        snap = self._build_snapshot(r)
        self.snapshots[r] = snap
        self._emit("snapshot", None, {"state": snap})
        self._emit("round_end", None, {"round": r})
        return snap

    def _run_decision(self, spec: tuple[str, Agent, DecisionContext]) -> tuple[str, list[Action], float]:
        """Run one agent's decision (may run on a worker thread). Returns its actions
        plus the real time it FINISHED deciding (used for finish-time execution
        priority). A single agent's failure must not abort the round, so we trap it
        and hold. LLM agents leave a belief/rationale in ``last_call`` -> emit it as an
        ``llm_call`` event (the emit is locked, so concurrent agents serialize safely)."""
        aid, agent, ctx = spec
        try:
            acts = agent.decide(ctx)
        except Exception as e:  # noqa: BLE001 — isolate a bad agent, keep the round alive
            agent.last_call = {"belief": {}, "rationale": "", "ok": False,
                               "error": f"{type(e).__name__}: {e}"[:200], "round": ctx.round}
            acts = [Hold()]
        finished_at = time.monotonic()   # when this agent finished deciding
        if getattr(agent, "last_call", None) is not None:
            self._emit("llm_call", aid, agent.last_call)
            agent.last_call = None
        return aid, acts, finished_at

    # ------------------------------------------------------------------ phases

    def _publish_news(self, r: int) -> None:
        nc = self.config.news
        if not nc.enabled or nc.every_rounds <= 0 or r % nc.every_rounds != 0:
            return
        acc = round((1 - nc.epsilon) * 100)
        for mid in sorted(self.exchange.markets):
            m = self.exchange.markets[mid]
            if m.status is not MarketStatus.OPEN:
                continue
            flip = self.rng.random() < nc.epsilon
            signal = int(m.outcome) ^ int(flip)
            lean = "YES" if signal == 1 else "NO"
            text = f"r{r}: signal on {mid} leans {lean} (~{acc}% reliable)"
            self._news_id += 1
            payload = {"id": self._news_id, "round": r, "market": mid, "signal": signal,
                       "lean": lean, "accuracy_pct": acc, "text": text}
            self.recent_news.append(payload)
            self.recent_news = self.recent_news[-10:]
            self.news_log.append(payload)
            self._emit("news", None, payload)

    def _publish_private_signals(self, r: int) -> None:
        """prob mode: each agent with a configured noise level gets a PRIVATE noisy
        estimate of every open market's true probability this round. Noise is
        heterogeneous across agents (signal_sigma) and shrinks over the horizon
        (sigma_decay). Draws happen in (agent, market) sorted order so the run stays
        deterministic. Each agent only ever sees its own signals."""
        nc = self.config.news
        T = max(1, self.config.rounds)
        frac = (r - 1) / max(1, T - 1)            # 0 at round 1 -> 1 at the horizon
        shrink = max(0.0, 1.0 - nc.sigma_decay * frac)
        for aid in self.agent_ids_sorted:
            base = self.signal_sigma.get(aid)
            if base is None:
                continue
            sigma_t = max(0.005, base * shrink)
            for mid in sorted(self.exchange.markets):
                m = self.exchange.markets[mid]
                if m.status is not MarketStatus.OPEN:
                    continue
                est = float(m.true_prob) + float(self.rng.normal(0.0, sigma_t))
                est = min(0.99, max(0.01, est))
                prob_pct = round(est * 100)
                sigma_pct = round(sigma_t * 100)
                if nc.disclose_sigma:
                    text = f"r{r}: your read on {mid} ≈ {prob_pct}% YES (±{sigma_pct}%)"
                else:
                    text = f"r{r}: your read on {mid} ≈ {prob_pct}% YES"
                self._news_id += 1
                payload = {"id": self._news_id, "round": r, "agent": aid, "market": mid,
                           "prob_pct": prob_pct,
                           "sigma_pct": sigma_pct if nc.disclose_sigma else None,
                           "text": text}
                self.private_news.setdefault(aid, []).append(payload)
                self._emit("signal", aid, payload)

    def _resolve_due(self, r: int) -> None:
        for mid in sorted(self.exchange.markets):
            m = self.exchange.markets[mid]
            if m.status is MarketStatus.OPEN and r >= m.resolve_round:
                info = resolve_market(self.exchange, mid, int(m.outcome), r)
                self._emit("resolution", None, {
                    "market": mid, "outcome": info["outcome"],
                    "winning_token": info["winning_token"],
                    "true_prob_pct": round(m.true_prob * 100),
                    "cancelled_orders": len(info["cancelled_orders"]),
                })
                for p in info["payouts"]:
                    self._emit("payout", p["agent"], {
                        "market": mid, "winning_token": p["winning_token"],
                        "qty": p["qty"], "amount": p["amount"],
                    })

    @staticmethod
    def _add_order_type_fields(d: dict, act: PlaceOrder) -> None:
        """Add tif/post_only/expire_round to a payload/order dict ONLY when non-default.
        Plain GTC orders (every scripted bot) add nothing -> byte-identical events."""
        if act.tif != "GTC":
            d["tif"] = act.tif
        if act.post_only:
            d["post_only"] = True
        if act.expire_round is not None:
            d["expire_round"] = act.expire_round

    def _execute(self, agent_id: str, act: Action, r: int):
        if isinstance(act, PlaceOrder):
            payload = {"market": act.market, "token": act.token.value, "side": act.side.value,
                       "price": act.price, "qty": act.qty}
            # echo the call-time id so the UI can fold this fill back onto the order_queued
            # step it announced earlier (omitted for scripted bots -> byte-exact replay)
            if act.client_id is not None:
                payload["client_id"] = act.client_id
            # order-type fields land in the event ONLY when non-default, so plain GTC
            # scripted orders keep byte-identical payloads (and replay stays byte-exact).
            self._add_order_type_fields(payload, act)
            res = self.exchange.place_order(agent_id, act.market, act.token, act.side,
                                            act.price, act.qty, r,
                                            tif=TimeInForce(act.tif), post_only=act.post_only,
                                            expire_round=act.expire_round)
            if res.status == "rejected":
                self._emit("invalid_action", agent_id, payload, {"status": "rejected", "reason": res.reason})
                return res
            self._emit("place_order", agent_id, payload, {
                "status": "accepted", "order_id": res.order_id,
                "filled_qty": res.filled_qty, "resting_qty": res.resting_qty,
            })
            for f in res.fills:
                etype = _SETTLE_EVENT_TYPE[f.settle.value]
                fp = {"market": f.market_id, "price": f.price, "qty": f.qty,
                      "settle": f.settle.value, "taker": f.taker_id, "maker": f.maker_id,
                      "pool_delta": f.pool_delta}
                fp.update(f.roles)
                self._emit(etype, f.taker_id, fp)
            return res
        elif isinstance(act, Cancel):
            res = self.exchange.cancel_order(agent_id, act.order_id)
            payload = {"order_id": act.order_id}
            if act.client_id is not None:
                payload["client_id"] = act.client_id
            self._emit("cancel_order", agent_id, payload,
                       {"status": res.status, "reason": res.reason})
            return res
        elif isinstance(act, Transfer):
            return self._exec_transfer(agent_id, act)
        elif isinstance(act, CreateAccount):
            return self._exec_create_account(agent_id, act)
        elif isinstance(act, CreateMarket):
            return self._exec_create_market(agent_id, act)
        return None  # Hold -> no event

    # --------------------------------------------------- open-scenario actions
    #
    # transfer / create_account / create_market settle here in the execution phase
    # (like orders), so blind-submit holds: an agent's create/transfer is announced at
    # decide time (order_queued) and only takes effect at round end. Each validates
    # against LIVE state, rejecting (invalid_action) without mutating on any failure —
    # the same reject-clean discipline as place_order. Conservation is re-checked by the
    # round's check_invariants() (or execute_now's, on the console path).

    def _reject(self, agent_id: str, etype: str, payload: dict, reason: str):
        self._emit("invalid_action", agent_id, payload, {"status": "rejected", "reason": reason})
        return {"status": "rejected", "reason": reason}

    def _exec_transfer(self, agent_id: str, act: Transfer):
        payload = {"from": agent_id, "to": act.to, "amount": act.amount}
        if act.client_id is not None:
            payload["client_id"] = act.client_id
        accts = self.exchange.ledger.accounts
        if act.to not in accts:
            return self._reject(agent_id, "transfer", payload, "unknown_recipient")
        if act.to == agent_id:
            return self._reject(agent_id, "transfer", payload, "self_transfer")
        if act.amount < 1:
            return self._reject(agent_id, "transfer", payload, "bad_amount")
        if act.amount > accts[agent_id].cash_available:
            return self._reject(agent_id, "transfer", payload, "insufficient_cash")
        self.exchange.ledger.transfer(agent_id, act.to, act.amount)
        self._emit("transfer", agent_id, payload, {"status": "ok"})
        return {"status": "ok"}

    def _exec_create_account(self, agent_id: str, act: CreateAccount):
        payload = {"account_id": act.account_id, "funder": agent_id,
                   "initial_cash": act.initial_cash}
        if act.client_id is not None:
            payload["client_id"] = act.client_id
        accts = self.exchange.ledger.accounts
        if act.account_id in accts:
            return self._reject(agent_id, "account_created", payload, "account_exists")
        if act.initial_cash < 0:
            return self._reject(agent_id, "account_created", payload, "bad_amount")
        if act.initial_cash > accts[agent_id].cash_available:
            return self._reject(agent_id, "account_created", payload, "insufficient_cash")
        self.exchange.create_account(act.account_id, agent_id, act.initial_cash)
        # register as a PASSIVE wallet: it appears in snapshots (pnl baseline = its
        # funded amount -> 0) but is NOT added to self.agents / agent_ids_sorted, so it
        # gets no decision turn, no private signal, and no spawned rng substream.
        self.agent_types[act.account_id] = "wallet"
        self.initial_cash[act.account_id] = act.initial_cash
        self._emit("account_created", agent_id, payload, {"status": "ok"})
        return {"status": "ok"}

    def _exec_create_market(self, agent_id: str, act: CreateMarket):
        payload = {"market_id": act.market_id, "question": act.question,
                   "resolve_round": act.resolve_round}
        if act.client_id is not None:
            payload["client_id"] = act.client_id
        if act.market_id in self.exchange.markets:
            return self._reject(agent_id, "market_created", payload, "market_exists")
        if act.resolve_round <= self.round_no:
            return self._reject(agent_id, "market_created", payload, "resolve_round_in_past")
        if not str(act.question).strip():
            return self._reject(agent_id, "market_created", payload, "empty_question")
        # Sample the latent truth from an ISOLATED rng keyed on (seed, market_id) so we
        # never consume from self.rng (which would desync the main news/shuffle stream).
        # A process-stable sha256 hash makes the truth depend only on (seed, market_id),
        # independent of WHEN in the run the market was created — so it survives resume.
        sid = int.from_bytes(hashlib.sha256(act.market_id.encode("utf-8")).digest()[:8], "big")
        mrng = np.random.default_rng(np.random.SeedSequence([int(self.config.seed), sid]))
        true_prob = float(mrng.random())
        outcome = 1 if mrng.random() < true_prob else 0
        self.exchange.create_market(act.market_id, act.question, act.resolve_round, true_prob, outcome)
        # payload carries NO true_prob/outcome — the creator stays blind to the truth.
        self._emit("market_created", agent_id, payload, {"status": "ok"})
        return {"status": "ok"}

    def execute_now(self, agent_id: str, act: Action):
        """Execute a human/console action immediately against the current book
        (used by the CLI so a manual order can eat a resting quote), then refresh
        the current round's snapshot. Returns the engine result."""
        if agent_id not in self.agents:
            raise KeyError(f"unknown agent {agent_id}")
        res = self._execute(agent_id, act, self.round_no)
        self.exchange.check_invariants()
        snap = self._build_snapshot(self.round_no)
        self.snapshots[self.round_no] = snap
        self._emit("snapshot", None, {"state": snap})
        return res

    @property
    def has_llm(self) -> bool:
        return any(t in ("llm", "llm_agentic") for t in self.agent_types.values())

    # ------------------------------------------------------------------ injection

    def inject_action(self, agent_id: str, act: Action) -> None:
        """Queue a human/console action for the next round's execution phase."""
        if agent_id not in self.agents:
            raise KeyError(f"unknown agent {agent_id}")
        self.human_pending.setdefault(agent_id, []).append(act)

    # ------------------------------------------------------------------ agent reads

    def _news_source(self, agent_id: str) -> list[dict]:
        """The news list visible to this agent: private signals in prob mode, the
        global public feed in lean mode."""
        if self.config.news.mode == "prob":
            return self.private_news.get(agent_id, [])
        return self.news_log

    def get_news_list(self, agent_id: str, last: int = 15) -> list[dict]:
        """Headlines only — the agent expands the ones it cares about via get_news_detail.
        prob mode returns this agent's own probability reads; lean mode the public leans."""
        out = []
        for n in self._news_source(agent_id)[-last:]:
            if "prob_pct" in n:  # prob mode
                out.append({"id": n["id"], "round": n["round"], "market": n["market"],
                            "prob_pct": n["prob_pct"], "sigma_pct": n.get("sigma_pct")})
            else:                # lean mode
                out.append({"id": n["id"], "round": n["round"], "market": n["market"], "lean": n["lean"]})
        return out

    def get_news_item(self, agent_id: str, news_id: int) -> dict:
        for n in self._news_source(agent_id):
            if n["id"] == news_id:
                return dict(n)
        return {"error": f"no news with id {news_id}"}

    def _agent_query(self, agent_id: str, verb: str, args: dict | None = None) -> dict:
        """Read-only query into the FROZEN round-start state for a tool-using agent.

        Called only during the decision phase, where nothing has executed yet, so the
        live exchange == the round-start snapshot (blind submit preserved). Every call
        is logged as an `agent_query` event so the information-gathering trail is
        reconstructable.
        """
        args = args or {}
        ex = self.exchange
        try:
            if verb == "get_markets":
                rows = []
                for mid in sorted(ex.markets):
                    m = ex.markets[mid]
                    rows.append({
                        "id": mid, "question": m.question, "status": m.status.value,
                        "best_bid": ex.best_bid(mid), "best_ask": ex.best_ask(mid),
                        "last_trade": ex.last_price[mid], "mid": ex.mid(mid),
                        "volume": ex.volume[mid],
                        "resolves_in": max(0, m.resolve_round - self.round_no),
                    })
                result = {"markets": rows}
            elif verb == "get_orderbook":
                mid = args.get("market")
                if mid not in ex.markets:
                    result = {"error": f"unknown market '{mid}'"}
                else:
                    depth = int(args.get("depth") or self.config.depth_k)
                    result = {"market": mid, "book": ex.get_book(mid, depth),
                              "best_bid": ex.best_bid(mid), "best_ask": ex.best_ask(mid),
                              "mid": ex.mid(mid), "last_trade": ex.last_price[mid]}
            elif verb == "get_trade_history":
                mid = args.get("market")
                if mid not in ex.markets:
                    result = {"error": f"unknown market '{mid}'"}
                else:
                    result = {"market": mid, "trades": ex.get_tape(mid, int(args.get("last") or 20))}
            elif verb == "get_portfolio":
                result = ex.get_portfolio(agent_id)
            elif verb == "get_news":
                result = {"news": self.get_news_list(agent_id, int(args.get("last") or 15))}
            elif verb == "get_news_detail":
                result = self.get_news_item(agent_id, int(args.get("id")))
            else:
                result = {"error": f"unknown query verb '{verb}'"}
        except Exception as e:  # noqa: BLE001 — surface as a query error, never crash the round
            result = {"error": f"{type(e).__name__}: {e}"}
        self._emit("agent_query", agent_id, {"verb": verb, "args": args}, result)
        return result

    def _agent_queue(self, agent_id: str, payload: dict) -> None:
        """Announce an order/cancel at the instant a tool-using agent decides it, as an
        `order_queued` event — so the UI shows the action in its true model-call position
        (interleaved with the agent's reads) before round-end matching is known (blind
        submit). The settled result arrives later via place_order/invalid_action/
        cancel_order, correlated by the shared payload['client_id']. Locked like every
        emit, so concurrent agents' announcements stay well-formed."""
        self._emit("order_queued", agent_id, payload, {"status": "queued"})

    def _agent_view(self, agent_id: str, payload: dict) -> None:
        """Announce a tool-using agent's committed view (belief + plan) at commit_view time —
        BEFORE it trades — as an `agent_view` event, so belief/plan land in the trail ahead
        of the orders they justify (true call order). payload = {"belief": {...}, "plan": ...}."""
        self._emit("agent_view", agent_id, payload, None)

    def _agent_briefing(self, agent_id: str, text: str) -> None:
        """Announce the literal per-round wake-up briefing fed to a tool-using agent (a
        `briefing` event), at the very start of its turn — the verbatim system→model
        input the single-round walkthrough demo replays."""
        self._emit("briefing", agent_id, {"text": text})

    def _agent_model_turn(self, agent_id: str, payload: dict) -> None:
        """Announce one model turn (a `model_turn` event): the raw text the model produced
        and the function calls it requested, the instant it returns — the verbatim
        model→system output side of the dialogue, before the calls' results come back.
        payload = {"turn": i, "text": str, "calls": [{"name","args"}...], "error": str|None}."""
        self._emit("model_turn", agent_id, payload)

    # ------------------------------------------------------------------ clearing trace

    def _decision_entry(self, act: Action) -> dict:
        """One agent-submitted order/cancel as it was queued (blind submit), for the
        clearing trace's decision phase."""
        if isinstance(act, PlaceOrder):
            entry = {"client_id": act.client_id, "market": act.market, "token": act.token.value,
                     "side": act.side.value, "price": act.price, "qty": act.qty}
            self._add_order_type_fields(entry, act)  # conditional -> GTC trace byte-identical
            return entry
        return {"kind": "cancel", "client_id": act.client_id, "order_id": act.order_id}

    def _act_market(self, act: Action) -> str | None:
        """The market an action touches: explicit for a PlaceOrder; for a Cancel, found by
        locating the resting order BEFORE it is removed (None if it no longer exists)."""
        if isinstance(act, PlaceOrder):
            return act.market
        if isinstance(act, Cancel):
            for book in self.exchange.books.values():
                if book.get(act.order_id) is not None:
                    return book.market_id
        return None

    def _book_state(self, market_id: str) -> dict:
        """A compact snapshot of one market's book + tops + pool — captured before and
        after each order so the demo can animate the book mutating order-by-order."""
        ex = self.exchange
        return {"market": market_id, "book": ex.get_book(market_id, self.config.depth_k),
                "best_bid": ex.best_bid(market_id), "best_ask": ex.best_ask(market_id),
                "last_trade": ex.last_price[market_id], "mid": ex.mid(market_id),
                "pool": ex.markets[market_id].collateral_pool}

    def _trace_step(self, seq: int, aid: str, act: Action, res, before, after) -> dict | None:
        """Build one clearing-trace step: the order, the book before/after, and every
        cross it made (with the settle type) — the authoritative per-order matching record."""
        if isinstance(act, PlaceOrder):
            if res is None:
                return None
            order = {"market": act.market, "token": act.token.value, "side": act.side.value,
                     "price": act.price, "qty": act.qty, "client_id": act.client_id}
            self._add_order_type_fields(order, act)  # conditional -> GTC trace byte-identical
            if res.status == "rejected":
                return {"seq": seq, "agent": aid, "kind": "order", "order": order,
                        "book_before": before, "book_after": after, "status": "rejected",
                        "reason": res.reason, "fills": [], "filled_qty": 0, "resting_qty": 0}
            order["order_id"] = res.order_id
            fills = [{"maker": f.maker_id, "maker_order_id": f.maker_order_id, "price": f.price,
                      "qty": f.qty, "settle": f.settle.value, "pool_delta": f.pool_delta,
                      "roles": f.roles} for f in res.fills]
            status = ("filled" if res.filled_qty > 0 and res.resting_qty == 0
                      else "partial" if res.filled_qty > 0 else "resting")
            return {"seq": seq, "agent": aid, "kind": "order", "order": order,
                    "book_before": before, "book_after": after, "status": status,
                    "fills": fills, "filled_qty": res.filled_qty, "resting_qty": res.resting_qty}
        if isinstance(act, Cancel):
            return {"seq": seq, "agent": aid, "kind": "cancel",
                    "order": {"order_id": act.order_id, "client_id": act.client_id},
                    "book_before": before, "book_after": after,
                    "status": res.status if res is not None else "?",
                    "reason": res.reason if res is not None else None,
                    "fills": [], "filled_qty": 0, "resting_qty": 0}
        return None

    # ------------------------------------------------------------------ views

    def _market_views(self) -> dict[str, MarketView]:
        views: dict[str, MarketView] = {}
        ex = self.exchange
        for mid, m in ex.markets.items():
            views[mid] = MarketView(
                id=mid, question=m.question, status=m.status.value,
                best_bid=ex.best_bid(mid), best_ask=ex.best_ask(mid),
                last_trade=ex.last_price[mid], mid=ex.mid(mid), true_prob=m.true_prob,
                resolves_in=max(0, m.resolve_round - self.round_no),
                depth=ex.get_book(mid, self.config.depth_k),
            )
        return views

    def _portfolio_view(self, agent_id: str) -> PortfolioView:
        p = self.exchange.get_portfolio(agent_id)
        return PortfolioView(p["cash_available"], p["cash_locked"], p["positions"], p["open_orders"])

    def _build_snapshot(self, r: int) -> dict:
        ex = self.exchange
        markets = []
        for mid in sorted(ex.markets):
            m = ex.markets[mid]
            markets.append({
                "id": mid, "question": m.question, "status": m.status.value,
                "best_bid": ex.best_bid(mid), "best_ask": ex.best_ask(mid),
                "last_trade": ex.last_price[mid], "mid": ex.mid(mid),
                "true_prob_pct": round(m.true_prob * 100),
                "resolves_in": max(0, m.resolve_round - r),
                "outcome": m.outcome if m.status is MarketStatus.RESOLVED else None,
                "collateral_pool": m.collateral_pool, "volume": ex.volume[mid],
                "depth": ex.get_book(mid, self.config.depth_k),
            })
        agents = []
        mids = list(ex.markets)
        for aid in sorted(ex.ledger.accounts):
            a = ex.ledger.accounts[aid]
            equity = a.cash_available + a.cash_locked
            for mid in mids:
                mm = ex.mid(mid)
                equity += a.position(mid, Token.YES) * mm + a.position(mid, Token.NO) * (100 - mm)
            agents.append({
                "agent_id": aid, "type": self.agent_types.get(aid, "?"),
                "cash_available": a.cash_available, "cash_locked": a.cash_locked,
                "positions": {m: dict(row) for m, row in sorted(a.positions.items()) if any(row.values())},
                "equity": equity, "pnl": equity - self.initial_cash.get(aid, 0),
            })
        return {"round": r, "markets": markets, "agents": agents}

    # ------------------------------------------------------------------ helpers

    def latest_snapshot(self) -> dict:
        return self.snapshots[self.round_no]
