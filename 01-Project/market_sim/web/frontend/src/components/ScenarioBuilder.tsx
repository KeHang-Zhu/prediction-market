import { useState } from 'react'
import { useStore } from '../store'

// One agent group in the builder: `count` LLM agents that share this model + reasoning mode.
type Group = { model: string; count: number; thinking: boolean; reasoning_effort: string }

// A lightweight modal form that builds a scenario template from a high-level spec and
// sends it to the backend ({type:'save_scenario'}). On success the backend saves
// templates/<slug>.yaml, loads it, and refreshes the picker; the store closes this modal.
// LLM agents are defined as GROUPS — each group is a model + count + thinking depth, so a
// run can mix models and give each group its own reasoning settings.
export default function ScenarioBuilder() {
  const open = useStore((s) => s.builderOpen)
  const close = useStore((s) => s.closeBuilder)
  const saveScenario = useStore((s) => s.saveScenario)
  const lang = useStore((s) => s.lang)
  const tr = (en: string, zh: string) => (lang === 'zh' ? zh : en)

  const [name, setName] = useState('')
  const [rounds, setRounds] = useState(50)
  const [seed, setSeed] = useState(42)
  const [temperature, setTemperature] = useState(0.7)
  const [maxToolCalls, setMaxToolCalls] = useState(8)
  const [groups, setGroups] = useState<Group[]>([
    { model: 'deepseek-v4-flash', count: 2, thinking: true, reasoning_effort: 'low' },
    { model: 'gemini-3.5-flash', count: 2, thinking: false, reasoning_effort: 'low' },
  ])
  const [signals, setSignals] = useState(true)
  const [sigmaMin, setSigmaMin] = useState(0.04)
  const [sigmaMax, setSigmaMax] = useState(0.12)
  const [includeMm, setIncludeMm] = useState(true)
  const [mmCount, setMmCount] = useState(2)
  const [includeNoise, setIncludeNoise] = useState(true)
  const [noiseCount, setNoiseCount] = useState(1)
  const [nMarkets, setNMarkets] = useState(3)
  const [caps, setCaps] = useState({
    transfer: false, create_account: false, create_market: false, advanced_orders: false,
  })

  if (!open) return null

  const totalLlm = groups.reduce((s, g) => s + (Number(g.count) || 0), 0)
  const setGroup = (i: number, patch: Partial<Group>) =>
    setGroups(groups.map((g, j) => (j === i ? { ...g, ...patch } : g)))
  const addGroup = () =>
    setGroups([...groups, { model: '', count: 1, thinking: false, reasoning_effort: 'low' }])
  const removeGroup = (i: number) => setGroups(groups.filter((_, j) => j !== i))

  const submit = () => {
    if (!name.trim()) return
    saveScenario(name.trim(), {
      // grouped LLM roster — each group expands to `count` agents with its own model + thinking.
      llm_groups: groups.map((g) => ({
        model: g.model.trim() || null,
        count: Number(g.count) || 0,
        thinking: g.thinking,
        reasoning_effort: g.thinking ? g.reasoning_effort : null,
      })),
      temperature, max_tool_calls: maxToolCalls,         // shared by all groups
      signals, sigma_min: sigmaMin, sigma_max: sigmaMax,
      include_mm: includeMm, mm_count: mmCount,
      include_noise: includeNoise, noise_count: noiseCount,
      markets: nMarkets, rounds, seed,
      capabilities: caps,
    })
  }

  const numCls = 'w-20 rounded-md border border-slate-200 px-2 py-1 text-sm text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-400'
  const Row = ({ label, children }: any) => (
    <label className="flex items-center justify-between gap-3 py-1">
      <span className="text-sm text-slate-600">{label}</span>
      <span className="flex items-center gap-2">{children}</span>
    </label>
  )
  const Section = ({ title, children, span }: any) => (
    <div className={`rounded-lg border border-slate-200 p-3${span ? ' col-span-2' : ''}`}>
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{title}</div>
      {children}
    </div>
  )
  const Check = ({ on, set, label }: any) => (
    <label className="flex items-center gap-1.5 text-sm text-slate-600">
      <input type="checkbox" checked={on} onChange={(e) => set(e.target.checked)} className="accent-blue-500" />
      {label}
    </label>
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={close}>
      <div className="max-h-[90vh] w-[680px] max-w-full overflow-auto rounded-xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <h2 className="text-base font-semibold text-slate-800">{tr('New scenario', '新建场景')}</h2>
          <button onClick={close} className="rounded-md px-2 py-1 text-slate-400 hover:bg-slate-100">✕</button>
        </div>

        <div className="grid grid-cols-2 gap-3 p-5">
          <div className="col-span-2">
            <Row label={tr('Template name', '模板名称')}>
              <input value={name} onChange={(e) => setName(e.target.value)} autoFocus
                placeholder={tr('my scenario', '我的场景')}
                className="w-64 rounded-md border border-slate-200 px-2 py-1 text-sm text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-400" />
            </Row>
          </div>

          <Section title={tr('LLM agent groups', 'LLM 智能体分组')} span>
            <div className="mb-1 flex items-center gap-2 px-1 text-[10px] uppercase tracking-wide text-slate-400">
              <span className="flex-1">{tr('model', '模型')}</span>
              <span className="w-14 text-center">{tr('count', '个数')}</span>
              <span className="w-14 text-center">{tr('think', '思考')}</span>
              <span className="w-24 text-center">{tr('effort', '强度')}</span>
              <span className="w-6" />
            </div>
            <div className="flex flex-col gap-1.5">
              {groups.map((g, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input value={g.model} onChange={(e) => setGroup(i, { model: e.target.value })}
                    placeholder={tr('model id, e.g. deepseek-v4-flash', '模型名,如 deepseek-v4-flash')}
                    className="flex-1 rounded-md border border-slate-200 px-2 py-1 text-sm text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-400" />
                  <input type="number" min={0} max={20} value={g.count}
                    onChange={(e) => setGroup(i, { count: +e.target.value })}
                    className="w-14 rounded-md border border-slate-200 px-2 py-1 text-center text-sm text-slate-700 focus:outline-none" />
                  <div className="flex w-14 justify-center">
                    <input type="checkbox" checked={g.thinking}
                      onChange={(e) => setGroup(i, { thinking: e.target.checked })} className="accent-blue-500" />
                  </div>
                  <select value={g.reasoning_effort} disabled={!g.thinking}
                    onChange={(e) => setGroup(i, { reasoning_effort: e.target.value })}
                    className="w-24 rounded-md border border-slate-200 px-1 py-1 text-sm text-slate-700 focus:outline-none disabled:bg-slate-50 disabled:text-slate-400">
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                  </select>
                  <button onClick={() => removeGroup(i)} disabled={groups.length <= 1}
                    className="w-6 rounded text-slate-400 hover:bg-slate-100 disabled:opacity-30"
                    title={tr('remove group', '删除该组')}>✕</button>
                </div>
              ))}
            </div>
            <div className="mt-2 flex items-center justify-between">
              <button onClick={addGroup}
                className="rounded-md border border-slate-200 px-2 py-1 text-sm text-slate-600 hover:bg-slate-50">
                + {tr('add group', '添加一组')}
              </button>
              <span className="text-[11px] text-slate-400">{tr('total', '共')} {totalLlm} {tr('LLM agents', '个 LLM agent')}</span>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-x-5 border-t border-slate-100 pt-2">
              <Row label={tr('temperature (all)', '温度(全部)')}>
                <input type="number" step={0.1} min={0} max={2} value={temperature} onChange={(e) => setTemperature(+e.target.value)} className={numCls} />
              </Row>
              <Row label={tr('max tool calls (all)', '每轮工具上限(全部)')}>
                <input type="number" min={1} max={20} value={maxToolCalls} onChange={(e) => setMaxToolCalls(+e.target.value)} className={numCls} />
              </Row>
            </div>
            <div className="mt-1 text-[10px] text-slate-400">
              {tr('Provider auto-detected from the model name (deepseek-* → DeepSeek, gpt-*/o* → OpenAI, gemini-* → Gemini). Thinking applies only to models that support it; others ignore it.',
                  'provider 按模型名自动识别(deepseek-* → DeepSeek,gpt-*/o* → OpenAI,gemini-* → Gemini)。思考只对支持的模型生效,其它模型会忽略。')}
            </div>
          </Section>

          <Section title={tr('Private signals', '私有信号')}>
            <Row label={tr('enabled', '开启')}>
              <input type="checkbox" checked={signals} onChange={(e) => setSignals(e.target.checked)} className="accent-blue-500" />
            </Row>
            <Row label={tr('sigma min (best)', 'σ 最小(最准)')}>
              <input type="number" step={0.01} min={0} max={0.5} value={sigmaMin} disabled={!signals} onChange={(e) => setSigmaMin(+e.target.value)} className={numCls} />
            </Row>
            <Row label={tr('sigma max (worst)', 'σ 最大(最差)')}>
              <input type="number" step={0.01} min={0} max={0.5} value={sigmaMax} disabled={!signals} onChange={(e) => setSigmaMax(+e.target.value)} className={numCls} />
            </Row>
          </Section>

          <Section title={tr('Liquidity bots', '流动性机器人')}>
            <Row label={tr('market makers', '做市商')}>
              <input type="checkbox" checked={includeMm} onChange={(e) => setIncludeMm(e.target.checked)} className="accent-blue-500" />
              <input type="number" min={0} max={8} value={mmCount} disabled={!includeMm} onChange={(e) => setMmCount(+e.target.value)} className={numCls} />
            </Row>
            <Row label={tr('noise bots', '噪声机器人')}>
              <input type="checkbox" checked={includeNoise} onChange={(e) => setIncludeNoise(e.target.checked)} className="accent-blue-500" />
              <input type="number" min={0} max={8} value={noiseCount} disabled={!includeNoise} onChange={(e) => setNoiseCount(+e.target.value)} className={numCls} />
            </Row>
          </Section>

          <Section title={tr('World', '世界设置')}>
            <Row label={tr('markets', '市场数')}>
              <input type="number" min={1} max={6} value={nMarkets} onChange={(e) => setNMarkets(+e.target.value)} className={numCls} />
            </Row>
            <Row label={tr('rounds', '回合数')}>
              <input type="number" min={1} max={2000} value={rounds} onChange={(e) => setRounds(+e.target.value)} className={numCls} />
            </Row>
            <Row label={tr('seed', '随机种子')}>
              <input type="number" value={seed} onChange={(e) => setSeed(+e.target.value)} className={numCls} />
            </Row>
          </Section>

          <Section title={tr('Agent tools (all LLMs)', '智能体工具(全部 LLM)')}>
            <div className="flex flex-col gap-1.5 pt-1">
              <Check on={caps.transfer} set={(v: boolean) => setCaps({ ...caps, transfer: v })} label={tr('transfer', '转账')} />
              <Check on={caps.create_account} set={(v: boolean) => setCaps({ ...caps, create_account: v })} label={tr('create_account', '创建账户')} />
              <Check on={caps.create_market} set={(v: boolean) => setCaps({ ...caps, create_market: v })} label={tr('create_market', '创建市场')} />
              <Check on={caps.advanced_orders} set={(v: boolean) => setCaps({ ...caps, advanced_orders: v })} label={tr('advanced_orders (FOK/FAK/GTD/post-only)', '高级订单(FOK/FAK/GTD/post-only)')} />
            </div>
          </Section>
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-slate-100 px-5 py-3">
          <span className="text-xs text-slate-400">
            {tr('Saved as a template, loaded immediately. Run it live (Step/Play) or offline via the CLI.',
                '存为模板并立即加载。可在网页 Step/Play 实时跑，或用 CLI 离线跑。')}
          </span>
          <div className="flex items-center gap-2">
            <button onClick={close} className="rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50">
              {tr('Cancel', '取消')}
            </button>
            <button onClick={submit} disabled={!name.trim() || totalLlm < 1}
              className="rounded-md border border-blue-500 bg-blue-500 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-blue-600 disabled:cursor-not-allowed disabled:opacity-50">
              {tr('Save & load', '保存并加载')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
