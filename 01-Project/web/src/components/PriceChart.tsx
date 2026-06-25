import { useMemo } from 'react'
import EChart from './EChart'
import { useStore } from '../store'
import { useT } from '../i18n'
import { SETTLE_COLORS } from '../types'

export default function PriceChart() {
  const t = useT()
  const snapshots = useStore((s) => s.snapshots)
  const trades = useStore((s) => s.trades)
  const selected = useStore((s) => s.selectedMarket)
  const viewRound = useStore((s) => s.viewRound)
  const maxRound = useStore((s) => s.maxRound)

  const option = useMemo(() => {
    const rounds = Object.keys(snapshots).map(Number).sort((a, b) => a - b)
    const mid: [number, number][] = []
    const volume: [number, number][] = []
    let truth = 50
    let prevVol = 0
    for (const r of rounds) {
      const m = snapshots[r].markets.find((x) => x.id === selected)
      if (!m) continue
      mid.push([r, m.mid])
      const inc = Math.max(0, m.volume - prevVol)
      volume.push([r, inc])
      prevVol = m.volume
      truth = m.true_prob_pct
    }

    const scatter = Object.keys(SETTLE_COLORS).map((settle) => ({
      name: t.settle[settle],
      type: 'scatter',
      symbolSize: 5,
      xAxisIndex: 0,
      yAxisIndex: 0,
      itemStyle: { color: SETTLE_COLORS[settle], opacity: 0.8 },
      data: trades.filter((t) => t.market === selected && t.settle === settle).map((t) => [t.round, t.price]),
    }))

    return {
      animation: false,
      grid: [
        { left: 46, right: 16, top: 36, height: '60%' },
        { left: 46, right: 16, top: '76%', height: '17%' },
      ],
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: { top: 4, right: 8, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 11, color: '#64748b' } },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      xAxis: [
        { type: 'value', min: 0, max: Math.max(maxRound, 1), gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#e2e8f0' } } },
        { type: 'value', min: 0, max: Math.max(maxRound, 1), gridIndex: 1, name: t.round, nameLocation: 'middle', nameGap: 24, axisLine: { lineStyle: { color: '#e2e8f0' } }, axisLabel: { color: '#94a3b8' } },
      ],
      yAxis: [
        { type: 'value', min: 0, max: 100, gridIndex: 0, name: '¢', nameTextStyle: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#f1f5f9' } }, axisLabel: { color: '#94a3b8' } },
        { type: 'value', gridIndex: 1, splitLine: { show: false }, axisLabel: { color: '#cbd5e1', fontSize: 10 } },
      ],
      series: [
        {
          name: t.mid, type: 'line', showSymbol: false, smooth: false, data: mid,
          xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 2, color: '#0f172a' }, z: 5,
          markLine: {
            symbol: 'none', silent: true,
            data: [{ xAxis: viewRound }],
            lineStyle: { color: '#3b82f6', width: 1.2 },
            label: { show: false },
          },
        },
        {
          name: `${t.trueLabel} ${truth}¢`, type: 'line', showSymbol: false,
          data: [[0, truth], [Math.max(maxRound, 1), truth]],
          xAxisIndex: 0, yAxisIndex: 0, lineStyle: { type: 'dashed', color: '#94a3b8', width: 1.2 }, z: 2,
        },
        ...scatter,
        {
          name: t.vol, type: 'bar', data: volume, xAxisIndex: 1, yAxisIndex: 1,
          itemStyle: { color: '#cbd5e1' }, barMaxWidth: 6,
        },
      ],
    }
  }, [snapshots, trades, selected, viewRound, maxRound, t])

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 px-3 py-2 text-sm font-semibold text-slate-700">
        {t.priceVolume} · {selected ?? '—'}
      </div>
      <EChart option={option} className="flex-1" />
    </div>
  )
}
