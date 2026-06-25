import { create } from 'zustand'
import type { AgentMeta, ClearingTrace, EventDict, ModelTurn, NewsItem, Playback, Snapshot, Trade } from './types'
import { AGENT_META } from './agentMeta'
import REPLAY_FULL from './replay.json'
import REPLAY_FULL5 from './replay-5r.json'
import REPLAY_LONG from './replay-6r.json'

// ─────────────────────────────────────────────────────────────────────────────
// Pure-frontend replay build. The original app streamed a recorded run from a
// FastAPI/WebSocket backend; here the SAME recorded events are bundled and driven
// by a local, server-less replay engine — so the showcase works as a static site
// (Vercel) with no backend. The event-folding logic (applyEvents) and selectors are
// unchanged from the live app, so the UI is byte-for-byte the real showcase; only the
// transport is local. Three recorded runs are bundled (all `llm5_only` — five LLM
// traders, no market-maker/noise) and switchable via the transport bar:
//   • "full5" — DEFAULT: 5 rounds WITH the verbatim model dialogue + matching trace
//               (a longer run that still carries every demo feature).
//   • "full"  — 2 rounds WITH the verbatim model dialogue + matching trace (every demo
//               feature works: agent walkthrough, clearing trace).
//   • "long"  — 6 rounds for watching price convergence (no per-turn dialogue capture).
// ─────────────────────────────────────────────────────────────────────────────

export interface LlmCall {
  round: number
  agent: string
  belief: Record<string, number>
  rationale: string
  ok: boolean
  error?: string
}

// one agent's full step-by-step trail within a single round, in true model-call order.
export interface TurnStep {
  kind: 'read' | 'order' | 'cancel' | 'reject' | 'view' | 'lesson'
  text: string
  belief?: Record<string, number>
  status?: 'queued' | 'filled' | 'partial' | 'resting' | 'rejected' | 'cancelled'
  clientId?: string
  detail?: { verb?: string; args?: any; result?: any; payload?: any }
}
export interface AgentSignal {
  market: string
  prob_pct: number
  sigma_pct: number | null
}
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
  signals: AgentSignal[]
  steps: TurnStep[]
  belief: Record<string, number>
  plan: string
  ok: boolean
  error?: string
  thinking: boolean
  retries?: number
  backoff?: number
  lessons?: string
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

// ── local replay engine ──────────────────────────────────────────────────────
// A small registry of bundled recordings; the active one's events unfold round by
// round, paced by their recorded timestamps (cinematic). Round 0 is config + the
// initial snapshot.
export interface ReplayMeta { key: string; rounds: number; dialogue: boolean }
const maxRoundOf = (evs: EventDict[]) => evs.reduce((m, e) => Math.max(m, e.round || 0), 0)
const hasType = (evs: EventDict[], t: string) => evs.some((e) => e.type === t)

const REPLAYS: { meta: ReplayMeta; events: EventDict[] }[] = [
  // full5 = the DEFAULT: a 5-round run WITH verbatim dialogue (model_turn) + clearing trace
  { events: REPLAY_FULL5 as unknown as EventDict[],
    meta: { key: 'full5', rounds: maxRoundOf(REPLAY_FULL5 as unknown as EventDict[]), dialogue: hasType(REPLAY_FULL5 as unknown as EventDict[], 'model_turn') } },
  // full = the shorter 2-round run, also WITH verbatim dialogue + clearing trace
  { events: REPLAY_FULL as unknown as EventDict[],
    meta: { key: 'full', rounds: maxRoundOf(REPLAY_FULL as unknown as EventDict[]), dialogue: hasType(REPLAY_FULL as unknown as EventDict[], 'model_turn') } },
  // long = the 6-round run for convergence (no per-turn dialogue)
  { events: REPLAY_LONG as unknown as EventDict[],
    meta: { key: 'long', rounds: maxRoundOf(REPLAY_LONG as unknown as EventDict[]), dialogue: hasType(REPLAY_LONG as unknown as EventDict[], 'model_turn') } },
]
export const REPLAY_METAS: ReplayMeta[] = REPLAYS.map((r) => r.meta)

const DEFAULT_SPEED = 3

let active = 0
let EVENTS = REPLAYS[active].events
let FULL_MAX_ROUND = maxRoundOf(EVENTS)
let cursor = 0
let timer: ReturnType<typeof setTimeout> | null = null
let booted = false

function clearTimer() {
  if (timer) { clearTimeout(timer); timer = null }
}

function tsSeconds(ev?: EventDict | null): number | null {
  if (!ev?.ts) return null
  const ms = Date.parse(ev.ts)
  return Number.isNaN(ms) ? null : ms / 1000
}

// A single recorded gap is capped at MAX_RECORDED_GAP_S before ÷speed: the live
// recordings can carry minutes of dead time between rounds (rate-limit backoffs, or
// the operator simply pausing between rounds), which would otherwise stall playback
// for a long time and look like "it only played one round". Genuine model-thinking
// pauses (a few seconds) are well under the cap and pass through intact.
const MAX_RECORDED_GAP_S = 8

function pacedGap(cur: EventDict, nxt: EventDict | null, speed: number): number {
  if (!nxt) return 0
  const a = tsSeconds(cur)
  const b = tsSeconds(nxt)
  const real = a != null && b != null && b > a ? Math.min(MAX_RECORDED_GAP_S, b - a) : 0
  return real / Math.max(0.5, speed)
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
  hasLlm: boolean
  busy: boolean
  llmCalls: LlmCall[]
  agentTurns: AgentTurn[]
  signalsByAgent: Record<string, SignalPoint[]>

  // explainer demos: full-screen routing + their data (same shape as the live app)
  view: 'main' | 'matching' | 'walkthrough' | 'tutorial'
  focusRound: number
  focusAgent: string | null
  agentMeta: AgentMeta | null
  clearingByRound: Record<number, ClearingTrace>
  briefingsByKey: Record<string, string>
  modelTurnsByKey: Record<string, ModelTurn[]>
  activeReplay: number

  connect: () => void
  // transport
  play: () => void
  pause: () => void
  step: () => void
  setSpeed: (v: number) => void
  loadReplay: (idx: number) => void
  // view
  setViewRound: (r: number) => void
  goLive: () => void
  selectMarket: (m: string) => void
  // explainer-demo navigation
  openMatching: (round: number) => void
  openWalkthrough: (agent: string, round: number) => void
  openTutorial: () => void
  backToMain: () => void
}

const TRADE_CAP = 5000

export const useStore = create<State>((set, get) => {
  const revealRound = (): number | null => {
    if (cursor >= EVENTS.length) return null
    const target = EVENTS[cursor].round
    const batch: EventDict[] = []
    while (cursor < EVENTS.length && EVENTS[cursor].round === target) {
      batch.push(EVENTS[cursor]); cursor += 1
    }
    if (batch.length) applyEvents(get, set, batch)
    return target
  }

  const tick = () => {
    clearTimer()
    if (get().playback.mode !== 'playing') return
    if (cursor >= EVENTS.length) { pauseInternal(); return }
    const ev = EVENTS[cursor]; cursor += 1
    applyEvents(get, set, [ev])
    const nxt = cursor < EVENTS.length ? EVENTS[cursor] : null
    if (!nxt) { pauseInternal(); return }
    const gap = pacedGap(ev, nxt, get().playback.speed)
    timer = setTimeout(tick, Math.max(0, gap * 1000))
  }

  const pauseInternal = () => {
    clearTimer()
    set((s) => ({ playback: { ...s.playback, mode: 'paused' } }))
  }

  // Rewind to round 0: clear all derived state and re-reveal the setup events.
  const rewind = () => {
    clearTimer()
    cursor = 0
    set((s) => ({
      snapshots: {}, trades: [], news: [], seen: new Set<number>(),
      maxRound: 0, viewRound: 0, live: true, selectedMarket: null,
      llmCalls: [], agentTurns: [], signalsByAgent: {},
      clearingByRound: {}, briefingsByKey: {}, modelTurnsByKey: {},
      playback: { ...s.playback, mode: 'paused', current_round: 0, max_round: FULL_MAX_ROUND },
    }))
    const batch: EventDict[] = []
    while (cursor < EVENTS.length && EVENTS[cursor].round === 0) {
      batch.push(EVENTS[cursor]); cursor += 1
    }
    if (batch.length) applyEvents(get, set, batch)
  }

  return {
    lang: initialLang(),
    setLang: (l) => {
      try { localStorage.setItem('gms_lang', l) } catch { /* ignore */ }
      set({ lang: l })
    },
    connected: true,
    playback: { mode: 'paused', speed: DEFAULT_SPEED, current_round: 0, max_round: FULL_MAX_ROUND, replay: true, has_llm: true },
    snapshots: {},
    maxRound: 0,
    trades: [],
    news: [],
    seen: new Set<number>(),
    viewRound: 0,
    live: true,
    selectedMarket: null,
    hasLlm: true,
    busy: false,
    llmCalls: [],
    agentTurns: [],
    signalsByAgent: {},
    view: 'main',
    focusRound: 0,
    focusAgent: null,
    agentMeta: AGENT_META,
    clearingByRound: {},
    briefingsByKey: {},
    modelTurnsByKey: {},
    activeReplay: active,

    connect: () => {
      if (booted) return
      booted = true
      rewind()
    },

    play: () => {
      if (get().playback.mode === 'playing') return
      if (cursor >= EVENTS.length) rewind()
      set((s) => ({ playback: { ...s.playback, mode: 'playing' }, live: true }))
      tick()
    },
    pause: () => pauseInternal(),
    step: () => {
      if (get().playback.mode === 'playing') return
      set({ live: true })
      revealRound()
    },
    setSpeed: (v) => set((s) => ({ playback: { ...s.playback, speed: Math.max(0.5, Math.min(30, v)) } })),

    loadReplay: (idx) => {
      if (idx === active || idx < 0 || idx >= REPLAYS.length) return
      active = idx
      EVENTS = REPLAYS[active].events
      FULL_MAX_ROUND = maxRoundOf(EVENTS)
      set({ activeReplay: idx, view: 'main', focusAgent: null, focusRound: 0 })
      rewind()
    },

    setViewRound: (r) => set({ viewRound: r, live: false }),
    goLive: () => set((s) => ({ live: true, viewRound: s.maxRound })),
    selectMarket: (m) => set({ selectedMarket: m }),

    openMatching: (round) => set({ view: 'matching', focusRound: round }),
    openWalkthrough: (agent, round) => set({ view: 'walkthrough', focusAgent: agent, focusRound: round }),
    openTutorial: () => set({ view: 'tutorial' }),
    backToMain: () => set({ view: 'main' }),
  }
})

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
      const p = e.payload
      const belief = p?.belief || {}
      const tn = turnFor(e.agent_id || '?', e.round)
      tn.steps.push({ kind: 'view', belief, text: p?.plan || '' })
      tn.belief = belief
      tn.plan = p?.plan || ''
      tn.thinking = true
    } else if (e.type === 'order_queued') {
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
      const txt = p.side != null
        ? `${p.side} ${p.token} @${p.price} ×${p.qty} ${p.market} ✗ ${e.result?.reason || 'rejected'}`
        : `cancel #${p.order_id} ✗ ${e.result?.reason || 'rejected'}`
      const tn = turnFor(e.agent_id || '?', e.round)
      const prev = p.client_id != null ? tn.steps.find((s) => s.clientId === p.client_id) : undefined
      if (prev) {
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
      if (p.lessons && tn.steps.some((s) => s.kind === 'view')) {
        tn.steps.push({ kind: 'lesson', text: p.lessons })
      }
    } else if (e.type === 'clearing_trace') {
      if (clearing === s.clearingByRound) clearing = { ...clearing }
      clearing[e.payload.round] = e.payload as ClearingTrace
    } else if (e.type === 'briefing') {
      if (briefings === s.briefingsByKey) briefings = { ...briefings }
      briefings[`${e.agent_id}@${e.round}`] = e.payload?.text || ''
    } else if (e.type === 'model_turn') {
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

export function showcaseAgentIds(snap: Snapshot | null): string[] {
  if (!snap) return []
  return snap.agents.filter((a) => a.type === 'llm_agentic').map((a) => a.agent_id)
}

export function turnAt(turns: AgentTurn[], agent: string, round: number): AgentTurn | null {
  const key = `${agent}@${round}`
  for (let i = turns.length - 1; i >= 0; i--) if (turns[i].key === key) return turns[i]
  return null
}

// rounds that carry an authoritative clearing trace (for the matching demo's round picker)
export function roundsWithClearing(clearing: Record<number, ClearingTrace>): number[] {
  return Object.keys(clearing).map(Number).sort((a, b) => a - b)
}
