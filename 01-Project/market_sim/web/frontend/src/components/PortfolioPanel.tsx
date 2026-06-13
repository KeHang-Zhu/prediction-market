import { useStore, snapshotAt } from '../store'
import { useT } from '../i18n'
import { money, signedMoney } from '../lib/format'

const TYPE_COLOR: Record<string, string> = {
  noise: 'bg-slate-100 text-slate-600',
  mm: 'bg-blue-100 text-blue-700',
  fundamentalist: 'bg-emerald-100 text-emerald-700',
  zic: 'bg-purple-100 text-purple-700',
  human: 'bg-amber-100 text-amber-700',
}

export default function PortfolioPanel() {
  const t = useT()
  const snapshots = useStore((s) => s.snapshots)
  const viewRound = useStore((s) => s.viewRound)
  const selected = useStore((s) => s.selectedMarket)
  const snap = snapshotAt(snapshots, viewRound)

  const agents = snap ? [...snap.agents].sort((a, b) => b.pnl - a.pnl) : []

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
        <span className="text-sm font-semibold text-slate-700">{t.portfolios}</span>
        {selected && <span className="text-xs text-slate-400">{t.pos} · {selected}</span>}
      </div>

      {/* vertical-only scroll: stacked cards, never overflows horizontally */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden scroll-thin">
        {agents.map((a) => {
          const pos = selected ? a.positions[selected] : undefined
          return (
            <div key={a.agent_id} className="border-b border-slate-50 px-3 py-2 hover:bg-slate-50">
              <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className="truncate font-medium text-slate-700">{a.agent_id}</span>
                  <span className={`shrink-0 rounded px-1 py-0.5 text-[10px] ${TYPE_COLOR[a.type] || 'bg-slate-100 text-slate-500'}`}>
                    {a.type}
                  </span>
                </div>
                <span className={`tabular shrink-0 text-sm font-semibold ${a.pnl >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                  {signedMoney(a.pnl)}
                </span>
              </div>
              <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-slate-400">
                <span className="tabular truncate">
                  {t.equity} {money(a.equity)}
                </span>
                <span className="tabular shrink-0">
                  {pos ? `Y${pos.YES} N${pos.NO}` : '–'}
                </span>
              </div>
              <div className="tabular text-[11px] text-slate-400">
                {t.avail} {money(a.cash_available)} · {t.lock} {money(a.cash_locked)}
              </div>
            </div>
          )
        })}
        {agents.length === 0 && <div className="px-3 py-3 text-sm text-slate-300">{t.noData}</div>}
      </div>
    </div>
  )
}
