import { useState } from 'react'
import { useStore, snapshotAt, showcaseAgentIds, turnAt } from '../store'
import type { TurnStep } from '../store'
import { useT } from '../i18n'
import { money, signedMoney } from '../lib/format'
import EChart from './EChart'
import MarketTabs from './MarketTabs'
import OrderBookPanel from './OrderBookPanel'
import PriceChart from './PriceChart'
import TradeTape from './TradeTape'

const SIG_COLORS = ['#2563eb', '#059669', '#d97706', '#7c3aed', '#dc2626']

const TYPE_COLOR: Record<string, string> = {
  noise: 'bg-slate-100 text-slate-600',
  mm: 'bg-blue-100 text-blue-700',
  fundamentalist: 'bg-emerald-100 text-emerald-700',
  zic: 'bg-purple-100 text-purple-700',
  human: 'bg-amber-100 text-amber-700',
}

// The unified LLM showcase: the live market on the left (order book · price
// convergence · trade tape), and a per-agent master-detail on the right where you
// expand one agent to watch its tool-call trail — each call openable to its raw
// args + return.

const STEP_ICON: Record<TurnStep['kind'], string> = {
  read: '🔍', order: '▸', cancel: '⊘', reject: '✗', view: '🎯', lesson: '💡',
}
const STEP_CLS: Record<TurnStep['kind'], string> = {
  read: 'text-sky-600', order: 'text-emerald-600', cancel: 'text-slate-400', reject: 'text-rose-500',
  view: 'text-purple-600', lesson: 'text-emerald-600',
}

function Detail({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div>
      <pre className="scroll-thin max-h-48 overflow-auto rounded bg-slate-800 px-2 py-1 text-[11px] leading-snug text-slate-100">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  )
}

function StepRow({ step }: { step: TurnStep }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const d = step.detail
  const hasDetail = !!d && (d.result !== undefined || d.args !== undefined || d.payload !== undefined)
  // an order/cancel still awaiting round-end matching (blind submit): dim it + tag it
  // 'queued'; once the fill folds in, status flips and the tag drops.
  const pending = step.status === 'queued'
  return (
    <li className={`text-xs ${pending ? 'opacity-70' : ''}`}>
      <button
        type="button"
        onClick={() => hasDetail && setOpen((o) => !o)}
        className={`flex w-full items-start gap-1 text-left ${hasDetail ? 'cursor-pointer hover:bg-slate-50' : 'cursor-default'}`}
      >
        <span className={`tabular ${STEP_CLS[step.kind]}`}>{STEP_ICON[step.kind]}</span>
        <span className={`flex-1 ${step.kind === 'reject' ? 'text-rose-500' : 'text-slate-600'}`}>{step.text}</span>
        {pending && (
          <span className="shrink-0 rounded bg-amber-100 px-1 py-0.5 text-[10px] font-medium text-amber-700">{t.queued}</span>
        )}
        {hasDetail && <span className="text-slate-300">{open ? '▾' : '▸'}</span>}
      </button>
      {open && hasDetail && (
        <div className="mb-1 ml-4 mt-1 space-y-1">
          {d!.args !== undefined && <Detail label={t.args} value={d!.args} />}
          {d!.payload !== undefined && <Detail label={t.args} value={d!.payload} />}
          {d!.result !== undefined && <Detail label={t.result} value={d!.result} />}
        </div>
      )}
    </li>
  )
}

// the agent's committed view (belief + plan) — sits in the trail BEFORE its orders, so the
// orders read as "I think A is 61% → therefore I buy YES". Comes from the commit_view call.
function ViewRow({ step }: { step: TurnStep }) {
  const t = useT()
  const belief = step.belief || {}
  return (
    <li className="text-xs">
      <div className="rounded-md border border-purple-100 bg-purple-50/50 px-2 py-1">
        {Object.keys(belief).length > 0 && (
          <div>
            <span className="text-[10px] uppercase tracking-wide text-slate-400">{t.belief}: </span>
            <span className="tabular text-[11px] text-purple-600">
              {Object.entries(belief).map(([m, p]) => `${m} ${Math.round((p as number) * 100)}%`).join(' · ')}
            </span>
          </div>
        )}
        {step.text && (
          <div className="text-slate-600"><span className="text-slate-400">{t.plan}: </span>{step.text}</div>
        )}
      </div>
    </li>
  )
}

// the post-trade lesson (from finish) — sits at the END of the trail, after the orders.
function LessonRow({ step }: { step: TurnStep }) {
  const t = useT()
  return (
    <li className="text-xs">
      <div className="text-emerald-700"><span className="text-slate-400">{t.lessons}: </span>{step.text}</div>
    </li>
  )
}

// Reconstruct the agent's per-round wake-up briefing — EXACTLY what it gets for free:
// cash / positions + the list of open markets. NO prices and NO signal value (the agent
// must pull those with the read tools; you can see what it fetched in its tool trail).
function Briefing({ aid }: { aid: string }) {
  const t = useT()
  const snapshots = useStore((s) => s.snapshots)
  const viewRound = useStore((s) => s.viewRound)
  const snap = snapshotAt(snapshots, viewRound)
  const st = snap?.agents.find((a) => a.agent_id === aid)
  if (!snap || !st) return null
  const pos = Object.entries(st.positions).filter(([, v]) => v.YES || v.NO)
  const openIds = snap.markets.filter((m) => m.status === 'open').map((m) => m.id)
  return (
    <div className="mb-2">
      <div className="mb-0.5 text-[10px] uppercase tracking-wide text-slate-400">{t.observation}</div>
      <div className="tabular text-[11px] text-slate-500">
        {t.avail} {money(st.cash_available)} · {t.lock} {money(st.cash_locked)}
        {pos.length > 0 && (
          <> · {pos.map(([m, v]) => `${m} ${v.YES ? 'Y' + v.YES : ''}${v.NO ? ' N' + v.NO : ''}`.trim()).join('  ')}</>
        )}
      </div>
      <div className="tabular mt-0.5 text-[11px] text-slate-500">
        {t.openMarkets}: <span className="text-slate-600">{openIds.join(', ') || '—'}</span>
      </div>
      <div className="mt-0.5 text-[10px] italic text-slate-400">{t.briefingHint}</div>
    </div>
  )
}

// the private "news" as a convergence chart: this agent's noisy read per market over
// rounds, with the true value (dashed) for the audience — shows accuracy + sharpening.
function SignalChart({ aid }: { aid: string }) {
  const t = useT()
  const sigByAgent = useStore((s) => s.signalsByAgent)
  const snapshots = useStore((s) => s.snapshots)
  const viewRound = useStore((s) => s.viewRound)
  const maxRound = useStore((s) => s.maxRound)

  const snap = snapshotAt(snapshots, viewRound)
  const hist = (sigByAgent[aid] || []).filter((p) => p.round <= viewRound)
  if (!snap || hist.length === 0) return null

  const markets = snap.markets.map((m) => m.id)
  const series: unknown[] = []
  markets.forEach((mid, i) => {
    const color = SIG_COLORS[i % SIG_COLORS.length]
    const pts = hist.filter((p) => p.market === mid).map((p) => [p.round, p.prob_pct])
    if (pts.length === 0) return
    const truth = snap.markets.find((x) => x.id === mid)?.true_prob_pct ?? 50
    series.push({ name: mid, type: 'line', showSymbol: false, data: pts, lineStyle: { width: 2, color }, itemStyle: { color }, z: 5 })
    series.push({ name: `${mid}~true`, type: 'line', showSymbol: false, silent: true,
      data: [[0, truth], [Math.max(maxRound, 1), truth]], lineStyle: { type: 'dashed', width: 1, color, opacity: 0.5 }, z: 2 })
  })
  const option = {
    animation: false,
    grid: { left: 28, right: 8, top: 22, bottom: 18 },
    tooltip: { trigger: 'axis', textStyle: { fontSize: 10 } },
    legend: { data: markets, top: 0, right: 0, itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 9, color: '#64748b' } },
    xAxis: { type: 'value', min: 0, max: Math.max(maxRound, 1), axisLabel: { fontSize: 9, color: '#94a3b8' }, axisLine: { lineStyle: { color: '#e2e8f0' } } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { fontSize: 9, color: '#94a3b8' }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
    series,
  }
  return (
    <div className="mb-2">
      <div className="mb-0.5 text-[10px] uppercase tracking-wide text-slate-400">{t.newsTrend}</div>
      <EChart option={option} className="h-36 w-full" />
    </div>
  )
}

function AgentRow({ aid, selected, onSelect }: { aid: string; selected: boolean; onSelect: () => void }) {
  const t = useT()
  const snapshots = useStore((s) => s.snapshots)
  const viewRound = useStore((s) => s.viewRound)
  const turns = useStore((s) => s.agentTurns)
  const openWalkthrough = useStore((s) => s.openWalkthrough)

  const snap = snapshotAt(snapshots, viewRound)
  if (!snap) return null
  // Track the turn at the round being VIEWED/STREAMED (viewRound), not the last closed
  // snapshot's round — a round's snapshot is its final event, so keying off it would only
  // ever show a fully-built turn. Keying off viewRound lets the trail grow tool by tool
  // as the round's events drip in (and still shows the right turn when scrubbing back).
  const turn = turnAt(turns, aid, viewRound)
  const st = snap.agents.find((a) => a.agent_id === aid)
  const sigma = turn?.signals?.find((s) => s.sigma_pct != null)?.sigma_pct ?? null
  // compact holdings for the (always-visible) row header: e.g. "A Y30 N100 · B N30"
  const positions = st ? Object.entries(st.positions).filter(([, v]) => v.YES || v.NO) : []
  const holdings = positions
    .map(([m, v]) => `${m.replace('COIN-', '')}${v.YES ? ' Y' + v.YES : ''}${v.NO ? ' N' + v.NO : ''}`)
    .join(' · ')

  return (
    <div className={`overflow-hidden rounded-lg border ${selected ? 'border-purple-300 shadow-sm' : 'border-slate-200'} bg-white`}>
      <button
        type="button"
        onClick={onSelect}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left hover:bg-slate-50"
      >
        <span className="flex items-center gap-1.5">
          <span className="text-slate-300">{selected ? '▾' : '▸'}</span>
          <span className="rounded bg-purple-100 px-1.5 py-0.5 text-sm font-semibold text-purple-700">{aid}</span>
          {sigma != null && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500" title="signal ±">±{sigma}%</span>
          )}
          {turn && (turn.backoff ?? 0) > 0 && (
            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-700"
                  title={`rate-limit backoff: ${turn.retries} retr${turn.retries === 1 ? 'y' : 'ies'}, ${turn.backoff}s`}>
              ↻ {turn.backoff}s
            </span>
          )}
        </span>
        <span className="flex min-w-0 items-center gap-2">
          {holdings && (
            <span className="tabular truncate text-[11px] text-slate-500" title="holdings">{holdings}</span>
          )}
          {st && (
            <span className={`tabular shrink-0 text-sm font-semibold ${st.pnl >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}>
              {signedMoney(st.pnl)}
            </span>
          )}
        </span>
      </button>

      {selected && (
        <div className="border-t border-slate-100 px-3 py-2 text-xs">
          {/* jump to the full-screen single-round walkthrough: the literal system↔model dialogue */}
          <button
            type="button"
            onClick={() => openWalkthrough(aid, viewRound)}
            className="mb-2 w-full rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-700 transition hover:bg-indigo-100"
          >
            {t.demoWalkthrough} · r{viewRound} →
          </button>
          {/* what the agent saw (briefing) + its private signal vs truth over time */}
          <Briefing aid={aid} />
          <SignalChart aid={aid} />
          {!turn ? (
            <div className="text-slate-300">{t.idle}</div>
          ) : (
            <>
              {/* TRUE model-call order: reads + orders + cancels interleaved exactly as the
                  agent issued them. A blind-submit order appears as 'queued' the instant it's
                  placed, then its round-end fill is folded back onto the same row. */}
              <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-400">{t.toolCalls}</div>
              {turn.steps.length === 0 ? (
                <div className="text-slate-300">{t.noStepsRound}</div>
              ) : (
                <ol className="space-y-0.5 border-l border-slate-200 pl-2">
                  {/* one linear trail in true call order: read → view(belief·plan) → orders → lesson */}
                  {turn.steps.map((s, i) =>
                    s.kind === 'view' ? <ViewRow key={i} step={s} />
                    : s.kind === 'lesson' ? <LessonRow key={i} step={s} />
                    : <StepRow key={i} step={s} />)}
                </ol>
              )}
              {/* OLD recordings (no inline commit_view step) still carry belief/plan/lessons on
                  the single finish() — render them as the bottom block. New runs show them
                  inline (view step before orders, lesson step after), so skip this. */}
              {!turn.steps.some((s) => s.kind === 'view')
                && (Object.keys(turn.belief).length > 0 || turn.plan || turn.lessons) && (
                <div className="mt-2 rounded-md border border-slate-100 bg-slate-50/60 px-2 py-1.5">
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-400">{t.finishGroup}</div>
                  {Object.keys(turn.belief).length > 0 && (
                    <div className="mb-0.5">
                      <span className="text-[10px] uppercase tracking-wide text-slate-400">{t.belief}: </span>
                      <span className="tabular text-[11px] text-purple-600">
                        {Object.entries(turn.belief).map(([m, p]) => `${m} ${Math.round((p as number) * 100)}%`).join(' · ')}
                      </span>
                    </div>
                  )}
                  {turn.plan && (
                    <div className="text-slate-600"><span className="text-slate-400">{t.plan}: </span>{turn.plan}</div>
                  )}
                  {turn.lessons && (
                    <div className="mt-0.5 text-emerald-700"><span className="text-slate-400">{t.lessons}: </span>{turn.lessons}</div>
                  )}
                </div>
              )}
              {!turn.ok && <div className="mt-1 text-rose-500">{turn.error || 'failed'}</div>}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// a non-LLM participant (market maker / noise / …): compact P&L + positions + cash
function BotRow({ aid }: { aid: string }) {
  const t = useT()
  const snapshots = useStore((s) => s.snapshots)
  const viewRound = useStore((s) => s.viewRound)
  const selected = useStore((s) => s.selectedMarket)

  const snap = snapshotAt(snapshots, viewRound)
  const st = snap?.agents.find((a) => a.agent_id === aid)
  if (!st) return null
  const pos = selected ? st.positions[selected] : undefined

  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-sm font-medium text-slate-700">{aid}</span>
          <span className={`shrink-0 rounded px-1 py-0.5 text-[10px] ${TYPE_COLOR[st.type] || 'bg-slate-100 text-slate-500'}`}>
            {st.type}
          </span>
        </span>
        <span className={`tabular shrink-0 text-sm font-semibold ${st.pnl >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
          {signedMoney(st.pnl)}
        </span>
      </div>
      <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-slate-400">
        <span className="tabular truncate">{t.avail} {money(st.cash_available)} · {t.lock} {money(st.cash_locked)}</span>
        {selected && <span className="tabular shrink-0">{pos ? `Y${pos.YES} N${pos.NO}` : '–'}</span>}
      </div>
    </div>
  )
}

export default function Showcase() {
  const t = useT()
  const snapshots = useStore((s) => s.snapshots)
  const viewRound = useStore((s) => s.viewRound)
  const [sel, setSel] = useState<string | null>(null)

  const snap = snapshotAt(snapshots, viewRound)
  const agents = showcaseAgentIds(snap)
  // the other participants (market makers, noise, …), best P&L first
  const bots = snap
    ? snap.agents.filter((a) => a.type !== 'llm_agentic').sort((a, b) => b.pnl - a.pnl).map((a) => a.agent_id)
    : []
  if (!snap || agents.length === 0) {
    return <div className="flex h-full items-center justify-center text-sm text-slate-300">{t.noLlmYet}</div>
  }
  // no agent is forced open — all rows can be collapsed (each still shows holdings + P&L)
  const active = sel && agents.includes(sel) ? sel : null

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <MarketTabs />
      <div className="grid min-h-0 flex-1 grid-cols-12 gap-3">
        {/* LEFT: order book */}
        <div className="col-span-3 min-h-0">
          <OrderBookPanel />
        </div>
        {/* MIDDLE: price convergence over the trade tape */}
        <div className="col-span-5 flex min-h-0 flex-col gap-3">
          <div className="min-h-0 flex-[3]"><PriceChart /></div>
          <div className="min-h-0 flex-[2]"><TradeTape /></div>
        </div>
        {/* RIGHT: per-agent tool-call inspector */}
        <div className="col-span-4 flex min-h-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
            <span className="text-sm font-semibold text-slate-700">{t.agentsPanel}</span>
            <span className="tabular text-xs text-slate-400">r{viewRound}</span>
          </div>
          <div className="scroll-thin flex-1 space-y-2 overflow-y-auto p-2">
            {agents.map((aid) => (
              <AgentRow key={aid} aid={aid} selected={aid === active} onSelect={() => setSel((cur) => (cur === aid ? null : aid))} />
            ))}
            {bots.length > 0 && (
              <>
                <div className="px-1 pt-1 text-[10px] uppercase tracking-wide text-slate-400">{t.otherAgents}</div>
                {bots.map((aid) => <BotRow key={aid} aid={aid} />)}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
