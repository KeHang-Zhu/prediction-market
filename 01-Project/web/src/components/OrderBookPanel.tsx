import { useStore, snapshotAt } from '../store'
import { useT } from '../i18n'
import { cent } from '../lib/format'
import type { MarketState } from '../types'

function Row({ price, qty, max, side }: { price: number; qty: number; max: number; side: 'bid' | 'ask' }) {
  const w = max > 0 ? Math.max(4, (qty / max) * 100) : 0
  const isBid = side === 'bid'
  return (
    <div className="relative flex items-center justify-between px-3 py-[3px] text-sm">
      <div
        className={`absolute inset-y-0 ${isBid ? 'right-0 bg-emerald-100' : 'right-0 bg-rose-100'}`}
        style={{ width: `${w}%` }}
      />
      <span className={`tabular relative z-10 font-medium ${isBid ? 'text-emerald-700' : 'text-rose-700'}`}>{price}¢</span>
      <span className="tabular relative z-10 text-slate-600">{qty}</span>
    </div>
  )
}

export default function OrderBookPanel() {
  const t = useT()
  const snapshots = useStore((s) => s.snapshots)
  const viewRound = useStore((s) => s.viewRound)
  const selected = useStore((s) => s.selectedMarket)
  const snap = snapshotAt(snapshots, viewRound)
  const market: MarketState | undefined = snap?.markets.find((m) => m.id === selected)

  if (!market) {
    return <Panel><div className="p-4 text-sm text-slate-400">{t.noMarket}</div></Panel>
  }

  const bids = market.depth.bids
  const asks = market.depth.asks
  const maxQty = Math.max(1, ...bids.map((b) => b[1]), ...asks.map((a) => a[1]))
  const spread = market.best_bid != null && market.best_ask != null ? market.best_ask - market.best_bid : null

  return (
    <Panel>
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
        <span className="text-sm font-semibold text-slate-700">{t.orderBook} · {market.id}</span>
        <span className="tabular text-xs text-slate-400">{t.vol} {market.volume} · {t.pool} {(market.collateral_pool / 100).toFixed(0)}</span>
      </div>
      <div className="flex-1 overflow-y-auto overflow-x-hidden scroll-thin">
        {/* asks: worst at top, best ask just above the spread */}
        {[...asks].reverse().map((a, i) => (
          <Row key={`a${i}`} price={a[0]} qty={a[1]} max={maxQty} side="ask" />
        ))}
        {asks.length === 0 && <div className="px-3 py-1 text-xs text-slate-300">{t.noAsks}</div>}

        <div className="flex items-center justify-between border-y border-slate-200 bg-slate-50 px-3 py-1.5">
          <span className="tabular text-sm font-semibold text-slate-700">{t.mid} {cent(market.mid)}</span>
          <span className="tabular text-xs text-slate-500">
            {t.last} {cent(market.last_trade)}{spread != null && <> · {t.spread} {spread}¢</>}
          </span>
        </div>

        {bids.map((b, i) => (
          <Row key={`b${i}`} price={b[0]} qty={b[1]} max={maxQty} side="bid" />
        ))}
        {bids.length === 0 && <div className="px-3 py-1 text-xs text-slate-300">{t.noBids}</div>}
      </div>
    </Panel>
  )
}

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      {children}
    </div>
  )
}
