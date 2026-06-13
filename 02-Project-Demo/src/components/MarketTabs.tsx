import { useStore, snapshotAt } from '../store'
import { useT } from '../i18n'
import { cent } from '../lib/format'

export default function MarketTabs() {
  const t = useT()
  const snapshots = useStore((s) => s.snapshots)
  const viewRound = useStore((s) => s.viewRound)
  const selected = useStore((s) => s.selectedMarket)
  const selectMarket = useStore((s) => s.selectMarket)

  const snap = snapshotAt(snapshots, viewRound)
  if (!snap) return null

  return (
    <div className="flex flex-wrap gap-2">
      {snap.markets.map((m) => {
        const active = m.id === selected
        const resolved = m.status === 'resolved'
        return (
          <button
            key={m.id}
            onClick={() => selectMarket(m.id)}
            className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm transition ${
              active ? 'border-blue-500 bg-blue-50' : 'border-slate-200 bg-white hover:bg-slate-50'
            }`}
          >
            <span className="font-semibold text-slate-800">{m.id}</span>
            <span className="tabular text-slate-500">{cent(m.mid)}</span>
            {resolved ? (
              <span className={`rounded px-1.5 py-0.5 text-xs ${m.outcome === 1 ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>
                {m.outcome === 1 ? 'YES' : 'NO'}
              </span>
            ) : (
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">{t.trueLabel} {m.true_prob_pct}¢</span>
            )}
          </button>
        )
      })}
    </div>
  )
}
