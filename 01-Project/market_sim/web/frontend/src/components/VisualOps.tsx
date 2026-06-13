import { useEffect, useState } from 'react'
import { useStore, snapshotAt } from '../store'
import { useT } from '../i18n'

const fieldCls =
  'rounded-md border border-slate-200 bg-white px-2 py-1 text-sm text-slate-700 focus:outline-none focus:border-blue-400'
const btnCls = 'rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50'

export default function VisualOps() {
  const t = useT()
  const snapshots = useStore((s) => s.snapshots)
  const viewRound = useStore((s) => s.viewRound)
  const selected = useStore((s) => s.selectedMarket)
  const runCommand = useStore((s) => s.runCommand)
  const lastResult = useStore((s) => s.lastResult)

  const snap = snapshotAt(snapshots, viewRound)
  const markets = snap?.markets.map((m) => m.id) ?? []
  const agents = snap?.agents.map((a) => a.agent_id) ?? []

  const [agent, setAgent] = useState('me')
  const [market, setMarket] = useState(selected ?? markets[0] ?? '')
  const [token, setToken] = useState<'YES' | 'NO'>('YES')
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [price, setPrice] = useState('60')
  const [qty, setQty] = useState('10')
  const [orderId, setOrderId] = useState('')

  // keep the market field in sync with the globally selected market
  useEffect(() => {
    if (selected) setMarket(selected)
  }, [selected])

  const placeOrder = () =>
    runCommand(`place_order --market ${market} --token ${token} --side ${side} --price ${price} --qty ${qty} --agent ${agent}`)
  const cancel = () => orderId && runCommand(`cancel_order --order-id ${orderId} --agent ${agent}`)

  const Seg = ({ value, set, options }: any) => (
    <div className="flex overflow-hidden rounded-md border border-slate-200 text-sm">
      {options.map((o: any) => (
        <button
          key={o.v}
          onClick={() => set(o.v)}
          className={`px-2.5 py-1 transition ${value === o.v ? o.active : 'bg-white text-slate-500 hover:bg-slate-50'}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )

  return (
    <div className="flex h-full flex-col gap-2 overflow-hidden px-3 py-2">
      {/* place order */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-16 text-xs text-slate-400">{t.vActAs}</span>
        <select value={agent} onChange={(e) => setAgent(e.target.value)} className={fieldCls}>
          {agents.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <select value={market} onChange={(e) => setMarket(e.target.value)} className={fieldCls}>
          {markets.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <Seg value={token} set={setToken} options={[
          { v: 'YES', label: 'YES', active: 'bg-slate-700 text-white' },
          { v: 'NO', label: 'NO', active: 'bg-slate-700 text-white' },
        ]} />
        <Seg value={side} set={setSide} options={[
          { v: 'buy', label: t.vBuy, active: 'bg-emerald-500 text-white' },
          { v: 'sell', label: t.vSell, active: 'bg-rose-500 text-white' },
        ]} />
        <label className="flex items-center gap-1 text-xs text-slate-400">
          {t.vPrice}
          <input value={price} onChange={(e) => setPrice(e.target.value)} className={`${fieldCls} tabular w-16`} />
        </label>
        <label className="flex items-center gap-1 text-xs text-slate-400">
          {t.vQty}
          <input value={qty} onChange={(e) => setQty(e.target.value)} className={`${fieldCls} tabular w-16`} />
        </label>
        <button onClick={placeOrder} className="rounded-md bg-blue-500 px-3 py-1 text-sm font-medium text-white hover:bg-blue-600">
          ▶ {t.vPlaceOrder}
        </button>
      </div>

      {/* cancel + queries + V1 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-16 text-xs text-slate-400">{t.vCancelOrder}</span>
        <input value={orderId} onChange={(e) => setOrderId(e.target.value)} placeholder={t.vOrderId}
          className={`${fieldCls} tabular w-24`} />
        <button onClick={cancel} className={btnCls}>↺ {t.vCancelOrder}</button>
        <span className="mx-1 h-5 w-px bg-slate-200" />
        <span className="text-xs text-slate-400">{t.vQueries}</span>
        <button onClick={() => runCommand('get_markets')} className={btnCls}>{t.vGetMarkets}</button>
        <button onClick={() => runCommand(`get_orderbook --market ${market}`)} className={btnCls}>{t.vGetBook}</button>
        <button onClick={() => runCommand(`get_portfolio --agent ${agent}`)} className={btnCls}>{t.vGetPortfolio}</button>
        <button onClick={() => runCommand(`get_trade_history --market ${market}`)} className={btnCls}>{t.vGetTape}</button>
        <span className="mx-1 h-5 w-px bg-slate-200" />
        <span className="text-xs text-slate-300">{t.vUnsupported}</span>
        <button onClick={() => runCommand('create_market --question demo')} className={`${btnCls} text-slate-400`}>{t.vCreateMarket}</button>
        <button onClick={() => runCommand(`transfer --to mm --amount 100 --agent ${agent}`)} className={`${btnCls} text-slate-400`}>{t.vTransfer}</button>
      </div>

      {/* last result — full multi-line output, scrollable */}
      {lastResult && (
        <div className={`flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border ${lastResult.ok ? 'border-emerald-200 bg-emerald-50' : 'border-rose-200 bg-rose-50'}`}>
          <div className={`border-b px-2 py-1 text-xs font-semibold ${lastResult.ok ? 'border-emerald-200 text-emerald-700' : 'border-rose-200 text-rose-700'}`}>
            {t.vResult} · {lastResult.verb}
          </div>
          <pre className={`tabular min-h-0 flex-1 overflow-auto scroll-thin whitespace-pre px-2 py-1 text-xs leading-relaxed ${lastResult.ok ? 'text-emerald-800' : 'text-rose-700'}`}>
            {lastResult.ok ? (lastResult.text || '(ok)') : `error: ${lastResult.error || 'failed'}`}
          </pre>
        </div>
      )}
    </div>
  )
}
