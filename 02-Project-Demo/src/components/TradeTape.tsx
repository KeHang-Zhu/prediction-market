import { useMemo } from 'react'
import { useStore } from '../store'
import { useT } from '../i18n'
import { SETTLE_COLORS } from '../types'

export default function TradeTape() {
  const tr = useT()
  const trades = useStore((s) => s.trades)
  const selected = useStore((s) => s.selectedMarket)
  const viewRound = useStore((s) => s.viewRound)

  const rows = useMemo(() => {
    return trades
      .filter((t) => t.market === selected && t.round <= viewRound)
      .slice(-150)
      .reverse()
  }, [trades, selected, viewRound])

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
        <span className="text-sm font-semibold text-slate-700">{tr.tradeTape} · {selected ?? '—'}</span>
        <span className="text-xs text-slate-400">{rows.length} {tr.shown}</span>
      </div>
      <div className="flex-1 overflow-y-auto scroll-thin">
        <table className="w-full text-sm">
          <tbody className="tabular">
            {rows.map((t, i) => (
              <tr key={i} className="border-b border-slate-50 hover:bg-slate-50">
                <td className="w-12 px-3 py-1 text-slate-400">r{t.round}</td>
                <td className="px-2 py-1">
                  <span
                    className="rounded px-1.5 py-0.5 text-xs font-medium text-white"
                    style={{ background: SETTLE_COLORS[t.settle] || '#64748b' }}
                  >
                    {tr.settle[t.settle] || t.settle}
                  </span>
                </td>
                <td className="px-2 py-1 text-right font-medium text-slate-700">{t.price}¢</td>
                <td className="px-2 py-1 text-right text-slate-500">×{t.qty}</td>
                <td className="px-3 py-1 text-right text-xs text-slate-400">
                  {t.taker} ← {t.maker}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td className="px-3 py-3 text-sm text-slate-300" colSpan={5}>{tr.noTrades}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
