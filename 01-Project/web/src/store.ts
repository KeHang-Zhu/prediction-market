import { create } from 'zustand'
import type {
  AgentMeta, ClearingTrace, ConsoleLine, EventDict, ModelTurn, NewsItem,
  Playback, Recording, Scenario, Snapshot, Trade,
} from './types'

export interface LlmCall {
  round: number
  agent: string
  belief: Record<string, number>
  rationale: string
  ok: boolean
  error?: string
}

// one agent's full step-by-step trail within a single round, in true model-call order.
// 'view' = the committed belief+plan (from commit_view, BEFORE trading); 'lesson' = the
// post-trade learning (from finish). Both are interleaved in the trail at their real call
// position, so the order reads: read → view(belief·plan) → orders → lesson.
export interface TurnStep {
  kind: 'read' | 'order' | 'cancel' | 'reject' | 'view' | 'lesson'
  text: string
  // for 'view' steps: the committed YES probability per market (shown before the orders)
  belief?: Record<string, number>
  // lifecycle of an order/cancel: 'queued' the instant the agent submits it (blind
  // submit — fill unknown yet), then resolved to filled/partial/resting/rejected/
  // cancelled when the round-end settle event folds back in. undefined for reads.
  status?: 'queued' | 'filled' | 'partial' | 'resting' | 'rejected' | 'cancelled'
  // correlation id tying an order_queued step to its later settle event (same round/agent)
  clientId?: string
  // expandable raw detail of the tool call: reads carry {verb, args, result};
  // orders/cancels carry {payload, result}. Lets the showcase open any call.
  detail?: { verb?: string; args?: any; result?: any; payload?: any }
}
export interface AgentSignal {
  market: string
  prob_pct: number
  sigma_pct: number | null
}
// one private read, kept per-agent across rounds for the convergence chart
export interface SignalPoint {
  round: number
  market: string
  prob_pct: number
  sigma_pct: number | null
}
export interface AgentTurn {
  key: string
  agent: string
  round: number
  signals: AgentSignal[] // this agent's private reads this round (prob mode)
  steps: TurnStep[]
  belief: Record<string, number>
  plan: string
  ok: boolean
  error?: string
  thinking: boolean // has reads/signals/an llm_call (filters out pure scripted-bot turns)
  retries?: number  // transient-error (429/5xx) retries this round
  backoff?: number  // seconds spent on exponential backoff this round
  lessons?: string  // one line: what the agent learned/corrected this round (from finish)
}

function summarizeQuery(verb: string, args: any): string {
  switch (verb) {
    case 'get_markets': return 'get_markets'
    case 'get_orderbook': return `get_orderbook ${args?.market ?? ''}`
    case 'get_trade_history': return `get_trade_history ${args?.market ?? ''}`
    case 'get_portfolio': return 'get_portfolio'
    case 'get_news': return 'get_news'
    case 'get_news_detail': return `get_news_detail #${args?.id ?? ''}`
    default: return verb || 'query'
  }
}

let socket: WebSocket | null = null
let cmdSeq = 0

type Lang = 'en' | 'zh'

function initialLang(): Lang {
  try {
    const q = new URLSearchParams(location.search).get('lang')
    if (q === 'zh' || q === 'en') return q
    const v = localStorage.getItem('gms_lang')
    if (v === 'zh' || v === 'en') return v
  } catch {
    /* no localStorage / URL */
  }
  return 'en'
}

interface State {
  lang: Lang
  setLang: (l: Lang) => void
  connected: boolean
  playback: Playback
  snapshots: Record<number, Snapshot>
  maxRound: number
  trades: Trade[]
  news: NewsItem[]
  seen: Set<number>
  viewRound: number
  live: boolean
  selectedMarket: string | null
  console: ConsoleLine[]
  lastResult: { ok: boolean; verb: string; text: string; error: string | null } | null
  hasLlm: boolean
  busy: boolean
  saveFlash: boolean
  builderOpen: boolean
  scenarios: Scenario[]
  recordings: Recording[]
  llmCalls: LlmCall[]
  agentTurns: AgentTurn[]
  signalsByAgent: Record<string, SignalPoint[]>   // each agent's private read history

  // ---- explainer demos: full-screen routing + their data ----
  view: 'main' | 'matching' | 'walkthrough' | 'tutorial'
  focusRound: number                              // round the open demo is explaining
  focusAgent: string | null                       // agent the walkthrough is explaining
  agentMeta: AgentMeta | null                     // system prompt + tool catalogue (from hello)
  clearingByRound: Record<number, ClearingTrace>  // authoritative per-round matching trace
  briefingsByKey: Record<string, string>          // `${agent}@${round}` -> literal wake-up briefing
  modelTurnsByKey: Record<string, ModelTurn[]>    // `${agent}@${round}` -> raw model turns in order

  connect: () => void
  send: (msg: any) => void
  // transport
  play: () => void
  pause: () => void
  step: () => void
  save: () => void
  resume: (name: string) => void
  reset: () => void
  loadConfig: (name: string) => void
  setSpeed: (v: number) => void
  // scenario builder
  openBuilder: () => void
  closeBuilder: () => void
  saveScenario: (name: string, spec: any) => void
  // view
  setViewRound: (r: number) => void
  goLive: () => void
  selectMarket: (m: string) => void
  runCommand: (line: string) => void
  // explainer-demo navigation
  openMatching: (round: number) => void
  openWalkthrough: (agent: string, round: number) => void
  openTutorial: () => void
  backToMain: () => void
  // internal
  _handle: (msg: any) => void
}

const TRADE_CAP = 5000

export const useStore = create<State>((set, get) => ({
  lang: initialLang(),
  setLang: (l) => {
    try { localStorage.setItem('gms_lang', l) } catch { /* ignore */ }
    set({ lang: l })
  },
  connected: false,
  playback: { mode: 'paused', speed: 4, current_round: 0, max_round: 0 },
  snapshots: {},
  maxRound: 0,
  trades: [],
  news: [],
  seen: new Set<number>(),
  viewRound: 0,
  live: true,
  selectedMarket: null,
  console: [],
  lastResult: null,
  hasLlm: false,
  busy: false,
  saveFlash: false,
  builderOpen: false,
  scenarios: [],
  recordings: [],
  llmCalls: [],
  agentTurns: [],
  signalsByAgent: {},
  view: 'main',
  focusRound: 0,
  focusAgent: null,
  agentMeta: null,
  clearingByRound: {},
  briefingsByKey: {},
  modelTurnsByKey: {},

  connect: () => {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    socket = new WebSocket(`${proto}://${location.host}/ws`)
    socket.onopen = () => set({ connected: true })
    socket.onclose = () => {
      set({ connected: false })
      setTimeout(() => get().connect(), 1500) // auto-reconnect
    }
    socket.onmessage = (ev) => {
      try {
        get()._handle(JSON.parse(ev.data))
      } catch {
        /* ignore malformed */
      }
    }
  },

  send: (msg) => {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg))
  },

  play: () => get().send({ type: 'play' }),
  pause: () => get().send({ type: 'pause' }),
  step: () => get().send({ type: 'step' }),
  save: () => get().send({ type: 'save' }),
  resume: (name) => get().send({ type: 'resume', config: name }),
  reset: () => get().send({ type: 'reset_run' }),
  loadConfig: (name) => get().send({ type: 'load', config: name }),
  openBuilder: () => set({ builderOpen: true }),
  closeBuilder: () => set({ builderOpen: false }),
  saveScenario: (name, spec) => get().send({ type: 'save_scenario', name, spec }),
  setSpeed: (v) => {
    set((s) => ({ playback: { ...s.playback, speed: v } }))
    get().send({ type: 'speed', value: v })
  },

  setViewRound: (r) => set({ viewRound: r, live: false }),
  goLive: () => set((s) => ({ live: true, viewRound: s.maxRound })),
  selectMarket: (m) => set({ selectedMarket: m }),

  openMatching: (round) => set({ view: 'matching', focusRound: round }),
  openWalkthrough: (agent, round) => set({ view: 'walkthrough', focusAgent: agent, focusRound: round }),
  openTutorial: () => set({ view: 'tutorial' }),
  backToMain: () => set({ view: 'main' }),

  runCommand: (line) => {
    const trimmed = line.trim()
    if (!trimmed) return
    set((s) => ({ console: [...s.console, { kind: 'in', text: `> ${trimmed}` }] }))
    cmdSeq += 1
    get().send({ type: 'command', id: `c${cmdSeq}`, line: trimmed })
  },

  _handle: (msg) => {
    const s = get()
    switch (msg.type) {
      case 'hello': {
        set({
          playback: msg.playback,
          hasLlm: !!msg.playback?.has_llm,
          busy: !!msg.playback?.busy,
          scenarios: msg.scenarios || [],
          recordings: msg.recordings || [],
          agentMeta: msg.agent_meta || null,
        })
        break
      }
      case 'library': {
        set({
          scenarios: msg.scenarios || [], recordings: msg.recordings || [],
          // agent_meta rides along so the walkthrough tool catalogue tracks the
          // just-loaded scenario's capabilities; keep the old value if absent.
          agentMeta: msg.agent_meta ?? s.agentMeta,
        })
        break
      }
      case 'saved': {
        // a save_scenario reply (file under templates/) also closes the builder modal;
        // the load it triggered already reset + refreshed the picker.
        set({ saveFlash: true, builderOpen: (msg.file || '').startsWith('templates/') ? false : s.builderOpen })
        setTimeout(() => set({ saveFlash: false }), 2000)
        break
      }
      case 'reset': {
        set({
          snapshots: {}, trades: [], news: [], seen: new Set<number>(),
          maxRound: 0, viewRound: 0, live: true, selectedMarket: null,
          llmCalls: [], agentTurns: [], signalsByAgent: {},
          clearingByRound: {}, briefingsByKey: {}, modelTurnsByKey: {},
          view: 'main', focusAgent: null, focusRound: 0,
        })
        break
      }
      case 'playback': {
        const live = s.live
        set({
          playback: msg,
          hasLlm: !!msg.has_llm,
          busy: !!msg.busy,
          maxRound: Math.max(s.maxRound, msg.max_round),
          viewRound: live ? Math.max(s.maxRound, msg.max_round) : s.viewRound,
        })
        break
      }
      case 'event_batch': {
        applyEvents(get, set, msg.events as EventDict[])
        break
      }
      case 'command_result': {
        const txt = msg.ok ? (msg.text || '(ok)') : `error: ${msg.error || 'failed'}`
        set((st) => ({
          console: [...st.console, { kind: msg.ok ? 'out' : 'err', text: txt }],
          lastResult: { ok: msg.ok, verb: msg.verb, text: msg.text || '', error: msg.error ?? null },
        }))
        break
      }
      case 'error': {
        set((st) => ({ console: [...st.console, { kind: 'err', text: msg.message }] }))
        break
      }
    }
  },
}))

function applyEvents(get: () => State, set: (p: Partial<State>) => void, events: EventDict[]) {
  const s = get()
  const snaps = { ...s.snapshots }
  let trades = s.trades
  let news = s.news
  const seen = s.seen
  let maxRound = s.maxRound
  let selected = s.selectedMarket
  const newTrades: Trade[] = []
  const newNews: NewsItem[] = []
  const newLlm: LlmCall[] = []
  const newSignals: (SignalPoint & { agent: string })[] = []
  // explainer-demo accumulators (cloned lazily on first touch)
  let clearing = s.clearingByRound
  let briefings = s.briefingsByKey
  let modelTurns = s.modelTurnsByKey

  // per-agent, per-round step trail (reads + orders + belief/plan)
  const turns = s.agentTurns.slice()
  const turnIdx = new Map<string, number>()
  turns.forEach((tn, i) => turnIdx.set(tn.key, i))
  let turnsTouched = false
  const turnFor = (agent: string, round: number): AgentTurn => {
    const key = `${agent}@${round}`
    let i = turnIdx.get(key)
    if (i === undefined) {
      const tn: AgentTurn = { key, agent, round, signals: [], steps: [], belief: {}, plan: '', ok: true, thinking: false }
      turns.push(tn); i = turns.length - 1; turnIdx.set(key, i)
    }
    turnsTouched = true
    return turns[i]
  }

  for (const e of events) {
    if (seen.has(e.event_id)) continue
    seen.add(e.event_id)
    // advance to the round being streamed as soon as ANY of its events arrives (not just
    // the closing snapshot) — so the showcase tracks the in-progress round and an agent's
    // tool calls show up one by one as they drip in, rather than all at the round's end.
    if (e.round > maxRound) maxRound = e.round
    if (e.type === 'snapshot') {
      const st: Snapshot = e.payload.state
      snaps[st.round] = st
      if (st.round > maxRound) maxRound = st.round
      if (!selected && st.markets.length) selected = st.markets[0].id
    } else if (e.type === 'fill' || e.type === 'mint' || e.type === 'merge') {
      const p = e.payload
      newTrades.push({ round: e.round, market: p.market, price: p.price, qty: p.qty, settle: p.settle, taker: p.taker, maker: p.maker })
    } else if (e.type === 'news') {
      const p = e.payload
      newNews.push({ round: e.round, market: p.market, signal: p.signal, accuracy_pct: p.accuracy_pct, text: p.text })
    } else if (e.type === 'signal') {
      const tn = turnFor(e.agent_id || '?', e.round)
      tn.signals.push({ market: e.payload?.market, prob_pct: e.payload?.prob_pct, sigma_pct: e.payload?.sigma_pct ?? null })
      tn.thinking = true
      newSignals.push({ agent: e.agent_id || '?', round: e.round, market: e.payload?.market,
        prob_pct: e.payload?.prob_pct, sigma_pct: e.payload?.sigma_pct ?? null })
    } else if (e.type === 'agent_query') {
      const tn = turnFor(e.agent_id || '?', e.round)
      tn.steps.push({ kind: 'read', text: summarizeQuery(e.payload?.verb, e.payload?.args),
        detail: { verb: e.payload?.verb, args: e.payload?.args, result: e.result } })
      tn.thinking = true
    } else if (e.type === 'agent_view') {
      // the agent committed its belief + plan BEFORE trading — lands in the trail ahead of
      // the orders it justifies (true call order: read → view → orders → lesson)
      const p = e.payload
      const belief = p?.belief || {}
      const tn = turnFor(e.agent_id || '?', e.round)
      tn.steps.push({ kind: 'view', belief, text: p?.plan || '' })
      tn.belief = belief
      tn.plan = p?.plan || ''
      tn.thinking = true
    } else if (e.type === 'order_queued') {
      // the agent just submitted an order/cancel — placed in its TRUE model-call position
      // (interleaved with reads). The fill/reject is unknown now (blind submit) and gets
      // folded onto this same step later by the settle event sharing its client_id.
      const p = e.payload
      const tn = turnFor(e.agent_id || '?', e.round)
      if (p?.kind === 'cancel') {
        tn.steps.push({ kind: 'cancel', status: 'queued', clientId: p.client_id,
          text: `cancel #${p.order_id}`, detail: { payload: p } })
      } else {
        tn.steps.push({ kind: 'order', status: 'queued', clientId: p.client_id,
          text: `${p.side} ${p.token} @${p.price} ×${p.qty} ${p.market}`, detail: { payload: p } })
      }
      tn.thinking = true
    } else if (e.type === 'place_order') {
      const p = e.payload, r = e.result || {}
      const base = `${p.side} ${p.token} @${p.price} ×${p.qty} ${p.market}`
      let tail = ''
      if (r.filled_qty) tail = ` → filled ${r.filled_qty}${r.resting_qty ? `, resting ${r.resting_qty}` : ''}`
      else if (r.resting_qty) tail = ` → resting ${r.resting_qty}`
      const status: TurnStep['status'] = r.filled_qty ? (r.resting_qty ? 'partial' : 'filled') : 'resting'
      const tn = turnFor(e.agent_id || '?', e.round)
      // fold the round-end fill back onto the order_queued step (matched by client_id);
      // fall back to appending for old recordings that predate order_queued / scripted bots
      const prev = p.client_id != null
        ? tn.steps.find((s) => s.kind === 'order' && s.clientId === p.client_id) : undefined
      if (prev) {
        prev.text = base + tail; prev.status = status
        prev.detail = { ...(prev.detail || {}), payload: p, result: e.result }
      } else {
        tn.steps.push({ kind: 'order', status, text: base + tail, detail: { payload: p, result: e.result } })
      }
    } else if (e.type === 'invalid_action') {
      const p = e.payload
      // a rejected/dropped order shows its intent; a dropped cancel has no order fields
      const txt = p.side != null
        ? `${p.side} ${p.token} @${p.price} ×${p.qty} ${p.market} ✗ ${e.result?.reason || 'rejected'}`
        : `cancel #${p.order_id} ✗ ${e.result?.reason || 'rejected'}`
      const tn = turnFor(e.agent_id || '?', e.round)
      const prev = p.client_id != null ? tn.steps.find((s) => s.clientId === p.client_id) : undefined
      if (prev) {                                   // a queued order the engine rejected at round end
        prev.kind = 'reject'; prev.status = 'rejected'; prev.text = txt
        prev.detail = { ...(prev.detail || {}), payload: p, result: e.result }
      } else {
        tn.steps.push({ kind: 'reject', status: 'rejected', text: txt, detail: { payload: p, result: e.result } })
      }
    } else if (e.type === 'cancel_order') {
      const p = e.payload
      const txt = `cancel #${p?.order_id} (${e.result?.status ?? ''})`
      const tn = turnFor(e.agent_id || '?', e.round)
      const prev = p?.client_id != null
        ? tn.steps.find((s) => s.kind === 'cancel' && s.clientId === p.client_id) : undefined
      if (prev) {
        prev.text = txt
        prev.status = e.result?.status === 'cancelled' ? 'cancelled' : prev.status
        prev.detail = { ...(prev.detail || {}), payload: p, result: e.result }
      } else {
        tn.steps.push({ kind: 'cancel', text: txt, detail: { payload: p, result: e.result } })
      }
    } else if (e.type === 'llm_call') {
      const p = e.payload
      newLlm.push({ round: e.round, agent: e.agent_id || '?', belief: p.belief || {}, rationale: p.rationale || '', ok: !!p.ok, error: p.error })
      const tn = turnFor(e.agent_id || '?', e.round)
      tn.belief = p.belief || {}
      tn.plan = p.rationale || ''
      tn.ok = !!p.ok
      tn.error = p.error
      tn.retries = p.retries || 0
      tn.backoff = p.backoff_s || 0
      tn.lessons = p.lessons || ''
      tn.thinking = true
      // new protocol: belief/plan are already inline via an agent_view step, so surface the
      // post-trade lesson as its own step at the END of the trail (learned AFTER the orders).
      // old recordings have no view step -> belief/plan/lessons render in the bottom block.
      if (p.lessons && tn.steps.some((s) => s.kind === 'view')) {
        tn.steps.push({ kind: 'lesson', text: p.lessons })
      }
    } else if (e.type === 'clearing_trace') {
      // the authoritative order-by-order matching record for this round (Demo B)
      if (clearing === s.clearingByRound) clearing = { ...clearing }
      clearing[e.payload.round] = e.payload as ClearingTrace
    } else if (e.type === 'briefing') {
      // the literal wake-up text the system fed this agent this round (Demo A)
      if (briefings === s.briefingsByKey) briefings = { ...briefings }
      briefings[`${e.agent_id}@${e.round}`] = e.payload?.text || ''
    } else if (e.type === 'model_turn') {
      // one raw model turn (text + requested calls), appended in call order (Demo A)
      if (modelTurns === s.modelTurnsByKey) modelTurns = { ...modelTurns }
      const key = `${e.agent_id}@${e.round}`
      const arr = modelTurns[key] ? modelTurns[key].slice() : []
      arr.push({ turn: e.payload?.turn ?? arr.length, text: e.payload?.text || '',
        calls: e.payload?.calls || [], error: e.payload?.error ?? null })
      modelTurns[key] = arr
    }
  }

  if (newTrades.length) {
    trades = trades.concat(newTrades)
    if (trades.length > TRADE_CAP) trades = trades.slice(trades.length - TRADE_CAP)
  }
  if (newNews.length) news = news.concat(newNews)

  const live = s.live
  const patch: Partial<State> = {
    snapshots: snaps,
    trades,
    news,
    maxRound,
    selectedMarket: selected,
    viewRound: live ? maxRound : s.viewRound,
  }
  if (newLlm.length) patch.llmCalls = s.llmCalls.concat(newLlm).slice(-200)
  if (turnsTouched) patch.agentTurns = turns.slice(-400)
  if (clearing !== s.clearingByRound) patch.clearingByRound = clearing
  if (briefings !== s.briefingsByKey) patch.briefingsByKey = briefings
  if (modelTurns !== s.modelTurnsByKey) patch.modelTurnsByKey = modelTurns
  if (newSignals.length) {
    const sba = { ...s.signalsByAgent }
    const touched = new Set<string>()
    for (const sg of newSignals) {
      if (!touched.has(sg.agent)) { sba[sg.agent] = (sba[sg.agent] || []).slice(); touched.add(sg.agent) }
      sba[sg.agent].push({ round: sg.round, market: sg.market, prob_pct: sg.prob_pct, sigma_pct: sg.sigma_pct })
    }
    for (const k of touched) if (sba[k].length > 1500) sba[k] = sba[k].slice(-1500)
    patch.signalsByAgent = sba
  }
  set(patch)
}

// selector: the snapshot at the current view round (or nearest earlier one)
export function snapshotAt(snapshots: Record<number, Snapshot>, round: number): Snapshot | null {
  if (snapshots[round]) return snapshots[round]
  for (let r = round; r >= 0; r--) {
    if (snapshots[r]) return snapshots[r]
  }
  return null
}

// the agentic (tool-using) agents in a scenario — the subjects of the showcase view
export function showcaseAgentIds(snap: Snapshot | null): string[] {
  if (!snap) return []
  return snap.agents.filter((a) => a.type === 'llm_agentic').map((a) => a.agent_id)
}

// one agent's turn at a specific round (exact match, newest wins)
export function turnAt(turns: AgentTurn[], agent: string, round: number): AgentTurn | null {
  const key = `${agent}@${round}`
  for (let i = turns.length - 1; i >= 0; i--) if (turns[i].key === key) return turns[i]
  return null
}

// rounds that carry an authoritative clearing trace (for the matching demo's round picker)
export function roundsWithClearing(clearing: Record<number, ClearingTrace>): number[] {
  return Object.keys(clearing).map(Number).sort((a, b) => a - b)
}
