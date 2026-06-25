import { useStore } from '../store'
import type { TurnStep } from '../store'
import { useT } from '../i18n'

const STEP_STYLE: Record<TurnStep['kind'], { icon: string; cls: string }> = {
  read: { icon: '🔍', cls: 'text-sky-600' },
  order: { icon: '▸', cls: 'text-emerald-600' },
  cancel: { icon: '⊘', cls: 'text-slate-400' },
  reject: { icon: '✗', cls: 'text-rose-500' },
  view: { icon: '🎯', cls: 'text-purple-600' },
  lesson: { icon: '💡', cls: 'text-emerald-600' },
}

export default function LLMPanel() {
  const t = useT()
  const turns = useStore((s) => s.agentTurns)
  const busy = useStore((s) => s.busy)

  // only agents that actually reason (LLM/agentic), newest round first
  const rows = turns.filter((tn) => tn.thinking).reverse()

  return (
    <div className="flex h-full flex-col">
      {busy && (
        <div className="flex items-center gap-2 border-b border-amber-100 bg-amber-50 px-3 py-1.5 text-xs text-amber-700">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-500" />
          {t.thinking}
        </div>
      )}
      <div className="flex-1 overflow-y-auto scroll-thin px-3 py-2">
        {rows.length === 0 && <div className="text-sm text-slate-300">{t.noLlmYet}</div>}
        {rows.map((tn) => (
          <div key={tn.key} className="mb-2.5 rounded-lg border border-slate-100 bg-slate-50/40 p-2 last:mb-0">
            {/* header: round · agent · belief */}
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="flex items-center gap-1.5">
                <span className="tabular text-slate-400">r{tn.round}</span>
                <span className="rounded bg-purple-100 px-1.5 py-0.5 font-medium text-purple-700">{tn.agent}</span>
                {!tn.ok && <span className="rounded bg-rose-100 px-1.5 py-0.5 text-rose-600">error</span>}
              </span>
              <span className="tabular text-slate-500">
                {t.belief}:{' '}
                {Object.entries(tn.belief).map(([m, p]) => (
                  <span key={m} className="ml-1">{m} {Math.round((p as number) * 100)}%</span>
                ))}
                {Object.keys(tn.belief).length === 0 && <span className="text-slate-300">—</span>}
              </span>
            </div>

            {/* the step-by-step trail */}
            {tn.steps.length > 0 && (
              <ol className="mt-1.5 space-y-0.5 border-l border-slate-200 pl-2.5">
                {tn.steps.map((st, i) => {
                  const sty = STEP_STYLE[st.kind]
                  return (
                    <li key={i} className="flex items-start gap-1.5 text-xs">
                      <span className={`tabular ${sty.cls}`}>{sty.icon}</span>
                      <span className={st.kind === 'reject' ? 'text-rose-500' : 'text-slate-600'}>{st.text}</span>
                    </li>
                  )
                })}
              </ol>
            )}

            {/* plan / note-to-self (or error) */}
            <div className={`mt-1.5 text-sm ${tn.ok ? 'text-slate-700' : 'text-rose-600'}`}>
              {tn.ok
                ? (tn.plan ? <><span className="text-xs text-slate-400">{t.plan}: </span>{tn.plan}</> : null)
                : (tn.error || 'failed')}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
