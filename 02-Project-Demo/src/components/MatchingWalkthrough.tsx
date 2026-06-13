import { useEffect, useState } from 'react'
import { useStore, snapshotAt, roundsWithClearing } from '../store'
import { SETTLE_COLORS, SETTLE_LABEL } from '../types'
import type { BookState, ClearingFill, ClearingStep, Depth } from '../types'

// ── Demo B ──────────────────────────────────────────────────────────────────
// "Round clearing, step by step." A full-screen, narrated walkthrough of ONE
// round's execution phase, built from the authoritative `clearing_trace`:
//   ① blind decision → ② finish-time queue → ③ order-by-order matching → ④ result
// Aimed squarely at the meeting blocker — give a concrete, steppable example of how
// orders actually clear, with mint/merge/transfer made explicit.

function bg(settle: string) {
  return SETTLE_COLORS[settle] || '#64748b'
}

// the side of the book an order rests on (YES coords). buy-YES / sell-NO are bids.
function isBidOrder(token?: string, side?: string) {
  return (token === 'YES' && side === 'buy') || (token === 'NO' && side === 'sell')
}

// prices whose total qty grew from before→after on a given side — i.e. the level the
// order newly rested at (robust to the YES/NO mirror, no coordinate math needed).
function grownPrices(before: Depth | undefined, after: Depth | undefined, side: 'bids' | 'asks') {
  const b = new Map<number, number>((before?.[side] || []).map((l) => [l[0], l[1]]))
  const out = new Set<number>()
  for (const [p, q] of after?.[side] || []) if (q > (b.get(p) || 0)) out.add(p)
  return out
}

function MiniBook({ state, highlight }: { state: BookState | null; highlight: Record<number, string> }) {
  if (!state) return <div className="text-[11px] text-slate-300">—</div>
  const bids = state.book.bids
  const asks = state.book.asks
  const max = Math.max(1, ...bids.map((b) => b[1]), ...asks.map((a) => a[1]))
  const Row = ({ price, qty, side }: { price: number; qty: number; side: 'bid' | 'ask' }) => {
    const w = Math.max(4, (qty / max) * 100)
    const isBid = side === 'bid'
    const ring = highlight[price]
    return (
      <div className={`relative flex items-center justify-between px-2 py-[2px] text-[11px] ${ring ? 'rounded ' + ring : ''}`}>
        <div className={`absolute inset-y-0 right-0 ${isBid ? 'bg-emerald-100' : 'bg-rose-100'}`} style={{ width: `${w}%` }} />
        <span className={`tabular relative z-10 font-medium ${isBid ? 'text-emerald-700' : 'text-rose-700'}`}>{price}¢</span>
        <span className="tabular relative z-10 text-slate-600">{qty}</span>
      </div>
    )
  }
  return (
    <div className="rounded-md border border-slate-200 bg-white">
      {[...asks].reverse().map((a, i) => <Row key={`a${i}`} price={a[0]} qty={a[1]} side="ask" />)}
      {asks.length === 0 && <div className="px-2 py-0.5 text-[10px] text-slate-300">— no asks —</div>}
      <div className="flex items-center justify-between border-y border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] text-slate-500">
        <span className="tabular">mid {state.mid}¢</span>
        <span className="tabular">last {state.last_trade ?? '–'}¢ · pool {(state.pool / 100).toFixed(0)}</span>
      </div>
      {bids.map((b, i) => <Row key={`b${i}`} price={b[0]} qty={b[1]} side="bid" />)}
      {bids.length === 0 && <div className="px-2 py-0.5 text-[10px] text-slate-300">— no bids —</div>}
    </div>
  )
}

export default function MatchingWalkthrough() {
  const lang = useStore((s) => s.lang)
  const tr = (en: string, zh: string) => (lang === 'zh' ? zh : en)
  const back = useStore((s) => s.backToMain)
  const focusRound = useStore((s) => s.focusRound)
  const openMatching = useStore((s) => s.openMatching)
  const clearingByRound = useStore((s) => s.clearingByRound)
  const snapshots = useStore((s) => s.snapshots)

  const rounds = roundsWithClearing(clearingByRound)
  const trace = clearingByRound[focusRound]
  const steps = trace?.steps || []

  const [idx, setIdx] = useState(0)
  useEffect(() => { setIdx(0) }, [focusRound])
  const cur: ClearingStep | undefined = steps[Math.min(idx, steps.length - 1)]

  const snap = snapshotAt(snapshots, focusRound)
  const trueProb = (mid?: string) => snap?.markets.find((m) => m.id === mid)?.true_prob_pct

  const orderText = (o: ClearingStep['order'], kind: string) =>
    kind === 'cancel' ? `cancel #${o.order_id}` : `${o.side} ${o.token} @${o.price}¢ ×${o.qty} ${o.market}`

  // settle explanation for one fill (the counterintuitive part: mint / merge / transfer)
  const explainFill = (f: ClearingFill) => {
    const p = f.price, no = 100 - p, r = f.roles || {}
    if (f.settle === 'mint')
      return tr(
        `MINT — buy-YES × buy-NO create a fresh pair. ${r.yes_buyer} pays ${p}¢ for 1 YES, ${r.no_buyer} pays ${no}¢ for 1 NO; together 100¢ enters the collateral pool (pool ${f.pool_delta >= 0 ? '+' : ''}${f.pool_delta}¢).`,
        `铸造(MINT)——买 YES × 买 NO 凭空造出一对。${r.yes_buyer} 付 ${p}¢ 得 1 份 YES,${r.no_buyer} 付 ${no}¢ 得 1 份 NO;两人合出 100¢ 注入抵押池(池子 ${f.pool_delta >= 0 ? '+' : ''}${f.pool_delta}¢)。`)
    if (f.settle === 'merge')
      return tr(
        `MERGE — sell-YES × sell-NO destroy a pair. The pool releases 100¢: ${r.yes_seller} collects ${p}¢, ${r.no_seller} collects ${no}¢ (pool ${f.pool_delta}¢).`,
        `合并(MERGE)——卖 YES × 卖 NO 销毁一对。抵押池放出 100¢:${r.yes_seller} 收 ${p}¢,${r.no_seller} 收 ${no}¢(池子 ${f.pool_delta}¢)。`)
    const tok = f.settle === 'transfer_yes' ? 'YES' : 'NO'
    return tr(
      `TRANSFER ${tok} — existing ${tok} shares change hands: ${r.buyer} buys ${f.qty} from ${r.seller} @ ${p}¢. The pool is unchanged.`,
      `转移(TRANSFER ${tok})——已有的 ${tok} 份额易手:${r.buyer} 以 ${p}¢ 从 ${r.seller} 买走 ${f.qty} 份。抵押池不变。`)
  }

  if (!trace) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4 bg-slate-50 text-slate-500">
        <div>{tr('No clearing trace for this round.', '该回合没有撮合记录。')}</div>
        {rounds.length > 0 && (
          <div className="flex items-center gap-2 text-sm">
            <span>{tr('Pick a round that cleared:', '选择一个有撮合的回合:')}</span>
            <select className="rounded border border-slate-300 px-2 py-1" value={focusRound}
              onChange={(e) => openMatching(parseInt(e.target.value, 10))}>
              {rounds.map((r) => <option key={r} value={r}>r{r}</option>)}
            </select>
          </div>
        )}
        <button onClick={back} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-white">← {tr('back', '返回')}</button>
      </div>
    )
  }

  const statusColor: Record<string, string> = {
    filled: 'bg-emerald-100 text-emerald-700', partial: 'bg-amber-100 text-amber-700',
    resting: 'bg-sky-100 text-sky-700', rejected: 'bg-rose-100 text-rose-700',
    cancelled: 'bg-slate-200 text-slate-600',
  }

  // highlights for the focused step's books: amber on the maker levels it consumes
  // (in book_before), indigo on the level it newly rests at (in book_after).
  const crossed: Record<number, string> = {}
  if (cur) for (const f of cur.fills) crossed[f.price] = 'ring-1 ring-amber-400'
  const addedAfter: Record<number, string> = {}
  if (cur) {
    const side = isBidOrder(cur.order.token, cur.order.side) ? 'bids' : 'asks'
    for (const p of grownPrices(cur.book_before?.book, cur.book_after?.book, side)) addedAfter[p] = 'ring-1 ring-indigo-400'
  }

  return (
    <div className="flex h-screen flex-col bg-slate-50">
      {/* header */}
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
        <div className="flex items-center gap-3">
          <button onClick={back} className="rounded-md border border-slate-300 px-2.5 py-1 text-sm text-slate-600 hover:bg-slate-50">← {tr('back', '返回')}</button>
          <h1 className="text-lg font-semibold text-slate-800">⚙ {tr('Round matching — step by step', '撮合机制详解 — 逐步走')}</h1>
        </div>
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <span>{tr('round', '回合')}</span>
          <select className="rounded border border-slate-300 px-2 py-1" value={focusRound}
            onChange={(e) => openMatching(parseInt(e.target.value, 10))}>
            {rounds.map((r) => <option key={r} value={r}>r{r}</option>)}
          </select>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* LEFT rail: phases ①② + the step list (phase ③ nav) */}
        <div className="scroll-thin w-80 shrink-0 overflow-y-auto border-r border-slate-200 bg-white p-3">
          {/* phase ① blind decisions */}
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-indigo-600">① {tr('blind decision', '盲投决策')}</div>
          <p className="mb-2 text-[11px] leading-snug text-slate-500">
            {tr('Every trader decides on the SAME start-of-round book — nobody sees anyone else’s orders yet.',
                '所有人面对同一个回合开始时的盘口决策——此刻谁也看不到别人的单。')}
          </p>
          <div className="mb-3 space-y-1.5">
            {trace.decisions.map((d) => (
              <div key={d.agent} className="rounded-md border border-slate-200 px-2 py-1">
                <div className="text-[11px] font-semibold text-slate-700">{d.agent}</div>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {d.orders.map((o, i) => (
                    <span key={i} className="tabular rounded bg-slate-100 px-1 py-0.5 text-[10px] text-slate-600">
                      {o.kind === 'cancel' ? `cancel #${o.order_id}` : `${o.side} ${o.token}@${o.price} ×${o.qty} ${o.market}`}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* phase ② execution order */}
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-indigo-600">② {tr('execution queue', '执行排队')}</div>
          <p className="mb-2 text-[11px] leading-snug text-slate-500">
            {tr('Orders enter the book in the order traders FINISHED deciding (a faster decision is submitted first), then match by price-time priority.',
                '订单按"谁先想完谁先进场"的顺序进入盘口(想得快的先提交),再按价格-时间优先撮合。')}
          </p>
          <div className="mb-3 flex flex-wrap gap-1">
            {trace.execution_order.map((a, i) => (
              <span key={i} className="tabular rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-700">{i + 1}. {a}</span>
            ))}
          </div>

          {/* phase ③ the per-order match list (click to focus) */}
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-indigo-600">③ {tr('matching, order by order', '逐单撮合')}</div>
          <ol className="space-y-1">
            {steps.map((s, i) => {
              const settle = s.fills[0]?.settle
              return (
                <li key={i}>
                  <button onClick={() => setIdx(i)}
                    className={`flex w-full items-center gap-1.5 rounded-md border px-2 py-1 text-left text-[11px] transition ${i === idx ? 'border-indigo-400 bg-indigo-50' : 'border-slate-200 hover:bg-slate-50'}`}>
                    <span className="tabular text-slate-400">{i + 1}</span>
                    <span className="tabular flex-1 truncate text-slate-700">{s.agent}: {orderText(s.order, s.kind)}</span>
                    <span className={`shrink-0 rounded px-1 text-[9px] ${statusColor[s.status] || 'bg-slate-100 text-slate-500'}`}>{s.status}</span>
                    {settle && <span className="shrink-0 rounded px-1 text-[9px] text-white" style={{ background: bg(settle) }}>{SETTLE_LABEL[settle] || settle}</span>}
                  </button>
                </li>
              )
            })}
            {steps.length === 0 && <li className="text-[11px] text-slate-300">{tr('no orders executed this round', '本回合没有订单成交')}</li>}
          </ol>
        </div>

        {/* MAIN: the focused order's match */}
        <div className="scroll-thin min-w-0 flex-1 overflow-y-auto p-5">
          {cur ? (
            <>
              {/* stepper controls */}
              <div className="mb-4 flex items-center justify-between">
                <div className="text-sm text-slate-500">{tr('step', '步骤')} <span className="font-semibold text-slate-800">{idx + 1}</span> / {steps.length}</div>
                <div className="flex gap-2">
                  <button onClick={() => setIdx((i) => Math.max(0, i - 1))} disabled={idx === 0}
                    className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40 hover:bg-white">◀ {tr('prev', '上一步')}</button>
                  <button onClick={() => setIdx((i) => Math.min(steps.length - 1, i + 1))} disabled={idx >= steps.length - 1}
                    className="rounded-md border border-indigo-300 bg-indigo-50 px-3 py-1.5 text-sm font-medium text-indigo-700 disabled:opacity-40 hover:bg-indigo-100">{tr('next', '下一步')} ▶</button>
                </div>
              </div>

              {/* the order */}
              <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex items-center justify-between">
                  <div>
                    <span className="rounded bg-purple-100 px-2 py-0.5 text-sm font-semibold text-purple-700">{cur.agent}</span>
                    <span className="ml-2 tabular text-base font-semibold text-slate-800">{orderText(cur.order, cur.kind)}</span>
                  </div>
                  <span className={`rounded px-2 py-0.5 text-xs font-medium ${statusColor[cur.status] || 'bg-slate-100 text-slate-500'}`}>{cur.status}</span>
                </div>
                {cur.order.market && trueProb(cur.order.market) != null && (
                  <div className="mt-1 text-[11px] text-slate-400">{tr('true YES prob of', '该市场真实 YES 概率')} {cur.order.market}: {trueProb(cur.order.market)}%</div>
                )}
                {cur.status === 'rejected' && <div className="mt-1 text-xs text-rose-500">✗ {cur.reason}</div>}
                {cur.kind === 'order' && cur.status !== 'rejected' && (
                  <div className="mt-1 text-xs text-slate-500">
                    {tr('filled', '成交')} {cur.filled_qty} · {tr('resting', '挂单')} {cur.resting_qty}
                    {cur.resting_qty > 0 && ` — ${tr('the rest joins the book and waits for a future counterparty', '剩余部分进入盘口,等待后续对手盘')}`}
                  </div>
                )}
              </div>

              {/* book before → after */}
              <div className="mb-4 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-400">{tr('book before', '撮合前盘口')} · {cur.order.market}</div>
                  <MiniBook state={cur.book_before} highlight={crossed} />
                </div>
                <div className="text-2xl text-slate-300">→</div>
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-400">{tr('book after', '撮合后盘口')}</div>
                  <MiniBook state={cur.book_after} highlight={addedAfter} />
                </div>
              </div>
              <div className="mb-4 flex gap-4 text-[10px] text-slate-400">
                <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded ring-1 ring-amber-400" /> {tr('levels consumed by the cross', '被吃掉的对手价位')}</span>
                <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded ring-1 ring-indigo-400" /> {tr('newly rested level', '新挂上的价位')}</span>
              </div>

              {/* the crosses + settle explanation (the counterintuitive part) */}
              {cur.fills.length > 0 ? (
                <div className="space-y-2">
                  <div className="text-[10px] uppercase tracking-wide text-slate-400">{tr('how each cross settles', '每一笔成交如何结算')}</div>
                  {cur.fills.map((f, i) => (
                    <div key={i} className="rounded-lg border border-slate-200 bg-white p-3">
                      <div className="mb-1 flex items-center gap-2">
                        <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold text-white" style={{ background: bg(f.settle) }}>{SETTLE_LABEL[f.settle] || f.settle}</span>
                        <span className="tabular text-xs text-slate-600">{cur.agent} × {f.maker} · {f.qty} @ {f.price}¢</span>
                      </div>
                      <p className="text-xs leading-snug text-slate-600">{explainFill(f)}</p>
                    </div>
                  ))}
                </div>
              ) : cur.status === 'resting' ? (
                <div className="rounded-lg border border-sky-200 bg-sky-50 p-3 text-xs text-sky-700">
                  {tr('No cross — nothing on the book met this price, so the whole order rests and waits.',
                      '没有撮合——盘口上没有满足该价格的对手单,整个订单挂入盘口等待。')}
                </div>
              ) : null}
            </>
          ) : (
            <div className="text-sm text-slate-400">{tr('no orders executed this round', '本回合没有订单成交')}</div>
          )}

          {/* phase ④ settlement timing — the part the audience kept missing */}
          <div className="mt-6 rounded-xl border border-slate-200 bg-white p-4">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-indigo-600">④ {tr('when does money settle?', '什么时候真正结算?')}</div>
            <p className="text-xs leading-relaxed text-slate-600">
              {tr('Matching above only moves shares and cash between traders. A market does NOT resolve until its resolve-round: until then each trader’s P&L is marked-to-market at the current mid. At resolution the winning token pays 100¢ and the loser 0¢ — so holding 1 YES + 1 NO is always worth exactly 100¢, which is why minting/merging a pair costs/refunds 100¢.',
                  '上面的撮合只是在交易者之间转移份额和现金。市场要到它的结算回合才会"开奖":在那之前每个人的盈亏是按当前 mid 估值(mark-to-market)。开奖时获胜的 token 付 100¢、失败的付 0¢——所以持有 1 份 YES + 1 份 NO 永远正好值 100¢,这正是铸造/合并一对要花/退 100¢ 的原因。')}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
