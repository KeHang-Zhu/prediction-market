import { useState } from 'react'
import { useStore, snapshotAt, showcaseAgentIds, turnAt } from '../store'
import type { ModelCall } from '../types'

// ── Demo A ──────────────────────────────────────────────────────────────────
// "One agent, one round." A full-screen, verbatim transcript of the dialogue
// between the SYSTEM and the MODEL for a single (agent, round): the system prompt
// + tool catalogue, the literal wake-up briefing, then every model turn (its raw
// text + the calls it made) paired with the system's response to each call. This
// is the "what does the system put in / what does the model put out" view.

const READ_VERBS = new Set(['get_markets', 'get_orderbook', 'get_trade_history', 'get_portfolio', 'get_news', 'get_news_detail'])

function fmtArgs(args: Record<string, any>): string {
  const parts: string[] = []
  for (const [k, v] of Object.entries(args || {})) {
    parts.push(`${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
  }
  return parts.join(', ')
}

function Json({ value }: { value: unknown }) {
  return (
    <pre className="scroll-thin max-h-56 overflow-auto rounded bg-slate-800 px-2 py-1 text-[11px] leading-snug text-slate-100">
      {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
    </pre>
  )
}

export default function AgentWalkthrough() {
  const lang = useStore((s) => s.lang)
  const tr = (en: string, zh: string) => (lang === 'zh' ? zh : en)
  const back = useStore((s) => s.backToMain)
  const focusAgent = useStore((s) => s.focusAgent)
  const focusRound = useStore((s) => s.focusRound)
  const openWalkthrough = useStore((s) => s.openWalkthrough)
  const agentMeta = useStore((s) => s.agentMeta)
  const briefingsByKey = useStore((s) => s.briefingsByKey)
  const modelTurnsByKey = useStore((s) => s.modelTurnsByKey)
  const agentTurns = useStore((s) => s.agentTurns)
  const snapshots = useStore((s) => s.snapshots)

  const [showSystem, setShowSystem] = useState(false)

  const snap = snapshotAt(snapshots, focusRound)
  const agents = showcaseAgentIds(snap)
  const key = `${focusAgent}@${focusRound}`
  const briefing = briefingsByKey[key]
  const turns = modelTurnsByKey[key] || []
  const turn = focusAgent ? turnAt(agentTurns, focusAgent, focusRound) : null

  // rounds this agent has a recorded turn for (drives the round picker)
  const agentRounds = Object.keys(briefingsByKey)
    .filter((k) => k.startsWith(`${focusAgent}@`))
    .map((k) => parseInt(k.split('@')[1], 10))
    .sort((a, b) => a - b)

  // read-tool results, queued per verb (the model's calls across turns line up 1:1 with
  // the agent_query results captured in the turn's step trail, in call order).
  const readQ: Record<string, any[]> = {}
  for (const st of turn?.steps || []) {
    if (st.kind === 'read' && st.detail?.verb) (readQ[st.detail.verb] ||= []).push(st.detail.result)
  }
  const readCursor: Record<string, number> = {}
  const systemResponse = (call: ModelCall): { kind: 'data' | 'status'; body: any } => {
    const n = call.name
    if (READ_VERBS.has(n)) {
      const arr = readQ[n] || []
      const i = readCursor[n] || 0
      readCursor[n] = i + 1
      return { kind: 'data', body: i < arr.length ? arr[i] : { note: tr('(result not recorded)', '(结果未记录)') } }
    }
    if (n === 'commit_view') return { kind: 'status', body: tr('✓ committed — you may now place orders', '✓ 已提交观点 — 现在可以下单') }
    if (n === 'place_order') return { kind: 'status', body: tr('✓ queued — settles at round end (blind submit)', '✓ 已排队 — 轮末盲投结算') }
    if (n === 'cancel_order') return { kind: 'status', body: tr('✓ queued', '✓ 已排队') }
    if (n === 'transfer') return { kind: 'status', body: tr('✓ queued — cash transfer settles at round end', '✓ 已排队 — 转账轮末结算') }
    if (n === 'create_account') return { kind: 'status', body: tr('✓ queued — new wallet created at round end', '✓ 已排队 — 轮末创建钱包') }
    if (n === 'create_market') return { kind: 'status', body: tr('✓ queued — new market opens at round end', '✓ 已排队 — 轮末开市') }
    if (n === 'finish') return { kind: 'status', body: tr('✓ finished — turn ends', '✓ 结束本轮') }
    return { kind: 'status', body: tr('(unknown tool)', '(未知工具)') }
  }

  const Picker = () => (
    <div className="flex items-center gap-2 text-sm text-slate-500">
      <select className="rounded border border-slate-300 px-2 py-1" value={focusAgent ?? ''}
        onChange={(e) => openWalkthrough(e.target.value, focusRound)}>
        {agents.map((a) => <option key={a} value={a}>{a}</option>)}
        {focusAgent && !agents.includes(focusAgent) && <option value={focusAgent}>{focusAgent}</option>}
      </select>
      <span>{tr('round', '回合')}</span>
      <select className="rounded border border-slate-300 px-2 py-1" value={focusRound}
        onChange={(e) => openWalkthrough(focusAgent ?? '', parseInt(e.target.value, 10))}>
        {(agentRounds.length ? agentRounds : [focusRound]).map((r) => <option key={r} value={r}>r{r}</option>)}
      </select>
    </div>
  )

  return (
    <div className="flex h-screen flex-col bg-slate-50">
      {/* header */}
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
        <div className="flex items-center gap-3">
          <button onClick={back} className="rounded-md border border-slate-300 px-2.5 py-1 text-sm text-slate-600 hover:bg-slate-50">← {tr('back', '返回')}</button>
          <h1 className="text-lg font-semibold text-slate-800">🔬 {tr('Agent round — system ↔ model', '智能体单轮 — 系统 ↔ 模型')}</h1>
        </div>
        <Picker />
      </div>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl space-y-4 p-5">
          {/* legend */}
          <div className="flex gap-4 text-[11px] text-slate-400">
            <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-sm bg-slate-400" /> {tr('SYSTEM → model (input)', '系统 → 模型(输入)')}</span>
            <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-sm bg-indigo-500" /> {tr('MODEL → system (output)', '模型 → 系统(输出)')}</span>
          </div>

          {/* ① system setup: prompt + tools (collapsible) */}
          <div className="rounded-xl border-l-4 border-slate-300 bg-white p-4 shadow-sm">
            <button onClick={() => setShowSystem((v) => !v)} className="flex w-full items-center justify-between text-left">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{tr('SYSTEM · standing rules + tools (every round)', '系统 · 固定规则 + 工具(每轮恒定)')}</span>
              <span className="text-slate-400">{showSystem ? '▾' : '▸'}</span>
            </button>
            {showSystem && agentMeta && (
              <div className="mt-3 space-y-3">
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-400">{tr('system prompt', '系统提示词')}</div>
                  <pre className="scroll-thin max-h-72 overflow-auto whitespace-pre-wrap rounded bg-slate-50 px-3 py-2 text-[11px] leading-relaxed text-slate-700">{agentMeta.system_prompt}</pre>
                </div>
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-400">{tr('tools the model may call', '模型可调用的工具')}</div>
                  <div className="grid grid-cols-2 gap-1.5">
                    {agentMeta.tools.map((tool) => (
                      <div key={tool.name} className="rounded-md border border-slate-200 px-2 py-1">
                        <div className="flex items-center gap-1.5">
                          <span className={`rounded px-1 py-0.5 text-[9px] ${tool.kind === 'read' ? 'bg-sky-100 text-sky-700' : 'bg-emerald-100 text-emerald-700'}`}>{tool.kind}</span>
                          <span className="tabular text-[11px] font-semibold text-slate-700">{tool.name}</span>
                        </div>
                        <div className="tabular mt-0.5 text-[10px] text-slate-400">{tool.signature}</div>
                        <div className="mt-0.5 text-[10px] leading-snug text-slate-500">{tool.description}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
            {!agentMeta && <div className="mt-2 text-[11px] text-slate-400">{tr('(system metadata not available)', '(系统元信息不可用)')}</div>}
          </div>

          {/* ② this round's wake-up briefing (the literal system→model input) */}
          {briefing != null && (
            <div className="rounded-xl border-l-4 border-slate-300 bg-white p-4 shadow-sm">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{tr('SYSTEM · wake-up briefing this round', '系统 · 本轮开场简报')}</div>
              <pre className="scroll-thin max-h-60 overflow-auto whitespace-pre-wrap rounded bg-slate-50 px-3 py-2 text-[12px] leading-relaxed text-slate-700">{briefing}</pre>
              <div className="mt-1 text-[10px] italic text-slate-400">{tr('Note: no prices, no signal values — the model must pull those with the read tools below.', '注意:不给价格、不给信号数值——模型必须用下面的读取工具自己去拉。')}</div>
            </div>
          )}

          {/* ③ the dialogue: each model turn (output) paired with the system's response (input) */}
          {turns.length > 0 ? (
            turns.map((mt, ti) => (
              <div key={ti} className="space-y-2">
                <div className="text-[10px] uppercase tracking-wide text-indigo-400">{tr('model turn', '模型回合')} {mt.turn + 1}</div>
                {/* MODEL output: its free-text reasoning (if any) + the calls it issued */}
                <div className="rounded-xl border-l-4 border-indigo-400 bg-white p-3 shadow-sm">
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-indigo-500">{tr('MODEL → output', '模型 → 输出')}</div>
                  {mt.text && <p className="mb-2 whitespace-pre-wrap text-[12px] leading-relaxed text-slate-700">{mt.text}</p>}
                  {mt.error && <p className="mb-2 text-[11px] text-rose-500">error: {mt.error}</p>}
                  {mt.calls.length === 0 && !mt.text && <p className="text-[11px] text-slate-400">{tr('(no content)', '(无内容)')}</p>}
                  <div className="space-y-2">
                    {mt.calls.map((call, ci) => {
                      const resp = systemResponse(call)
                      return (
                        <div key={ci} className="rounded-md border border-slate-100 bg-slate-50/60 p-2">
                          <div className="tabular text-[12px] font-medium text-indigo-700">
                            <span className="text-slate-400">▸ </span>{call.name}({fmtArgs(call.args)})
                          </div>
                          <div className="mt-1.5 border-l-2 border-slate-300 pl-2">
                            <div className="mb-0.5 text-[9px] uppercase tracking-wide text-slate-400">{tr('SYSTEM → returns', '系统 → 返回')}</div>
                            {resp.kind === 'data'
                              ? <Json value={resp.body} />
                              : <div className="text-[11px] text-slate-600">{resp.body}</div>}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </div>
            ))
          ) : turn ? (
            // Older recording (no raw model_turn events): degrade gracefully to the
            // tool-call trail it DOES have — each read paired with the system's result.
            <div className="space-y-2">
              <div className="rounded-md bg-amber-50 px-3 py-2 text-[11px] leading-snug text-amber-700">
                {tr('This recording predates verbatim-dialogue capture, so the model’s raw text isn’t available. Showing the tool-call trail instead — load the newest recording for the full system ↔ model transcript.',
                    '这份录制早于"逐字对话"记录,没有模型的原始文本。这里改为展示工具调用轨迹——加载最新的那份录制可看到完整的系统 ↔ 模型对话。')}
              </div>
              {turn.steps.length > 0 ? (
                turn.steps.map((st, i) => (
                  <div key={i} className="rounded-md border border-slate-100 bg-slate-50/60 p-2">
                    {st.kind === 'read' ? (
                      <>
                        <div className="tabular text-[12px] font-medium text-indigo-700"><span className="text-slate-400">▸ </span>{st.detail?.verb || 'read'}({fmtArgs(st.detail?.args || {})})</div>
                        <div className="mt-1.5 border-l-2 border-slate-300 pl-2">
                          <div className="mb-0.5 text-[9px] uppercase tracking-wide text-slate-400">{tr('SYSTEM → returns', '系统 → 返回')}</div>
                          <Json value={st.detail?.result} />
                        </div>
                      </>
                    ) : st.kind === 'view' ? (
                      <div className="text-[12px]">
                        <span className="text-purple-600">🎯 commit_view</span>
                        {st.belief && Object.keys(st.belief).length > 0 && (
                          <span className="tabular ml-1 text-purple-700">{Object.entries(st.belief).map(([m, p]) => `${m} ${Math.round((p as number) * 100)}%`).join(' · ')}</span>
                        )}
                        {st.text && <span className="text-slate-500"> · {st.text}</span>}
                      </div>
                    ) : st.kind === 'lesson' ? (
                      <div className="text-[12px] text-emerald-700">💡 finish — {st.text}</div>
                    ) : (
                      <div className="text-[12px] text-slate-700"><span className="text-slate-400">▸ </span>{st.text}</div>
                    )}
                  </div>
                ))
              ) : (
                <div className="rounded-md border border-slate-100 p-3 text-[12px] text-slate-400">
                  {tr('Only the round summary was recorded for this run — see the outcome below.', '这份录制只记录了本轮小结——见下方"本轮结果"。')}
                </div>
              )}
            </div>
          ) : (
            <div className="rounded-xl border border-slate-200 bg-white p-4 text-[12px] text-slate-400">
              {tr('No turn recorded for this agent at this round.', '该智能体在此回合没有记录。')}
            </div>
          )}

          {/* ④ round outcome: committed belief/plan + the lesson learned */}
          {turn && (Object.keys(turn.belief).length > 0 || turn.plan || turn.lessons) && (
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-purple-600">{tr('round outcome', '本轮结果')}</div>
              {Object.keys(turn.belief).length > 0 && (
                <div className="mb-1 text-[12px]">
                  <span className="text-slate-400">{tr('committed belief', '提交的观点')}: </span>
                  <span className="tabular text-purple-700">{Object.entries(turn.belief).map(([m, p]) => `${m} ${Math.round((p as number) * 100)}%`).join(' · ')}</span>
                </div>
              )}
              {turn.plan && <div className="mb-1 text-[12px] text-slate-600"><span className="text-slate-400">{tr('plan', '计划')}: </span>{turn.plan}</div>}
              {turn.lessons && <div className="text-[12px] text-emerald-700"><span className="text-slate-400">{tr('learned', '学到')}: </span>{turn.lessons}</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
