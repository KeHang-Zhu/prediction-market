import { useEffect, useRef, useState } from 'react'
import { useT } from '../i18n'
import { useStore } from '../store'
import Console from './Console'
import VisualOps from './VisualOps'
import LLMPanel from './LLMPanel'

type Tab = 'terminal' | 'visual' | 'llm'

function initialTab(): Tab {
  try {
    const q = new URLSearchParams(location.search).get('tab')
    if (q === 'visual' || q === 'llm') return q
  } catch {
    /* ignore */
  }
  return 'terminal'
}

export default function ConsolePanel() {
  const t = useT()
  const hasLlm = useStore((s) => s.hasLlm)
  const [tab, setTab] = useState<Tab>(initialTab())
  const autoSwitched = useRef(false)

  // when an LLM scenario loads, jump to the reasoning tab once
  useEffect(() => {
    if (hasLlm && !autoSwitched.current) {
      autoSwitched.current = true
      setTab('llm')
    }
    if (!hasLlm) autoSwitched.current = false
  }, [hasLlm])

  const TabBtn = ({ id, label }: { id: Tab; label: string }) => (
    <button
      onClick={() => setTab(id)}
      className={`-mb-px border-b-2 px-3 py-1.5 text-sm font-medium transition ${
        tab === id ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700'
      }`}
    >
      {label}
    </button>
  )

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-200 px-3">
        <div className="flex items-center gap-1">
          <TabBtn id="terminal" label={`▸ ${t.tabTerminal}`} />
          <TabBtn id="visual" label={`▦ ${t.tabVisual}`} />
          {hasLlm && <TabBtn id="llm" label={`✦ ${t.tabLlm}`} />}
        </div>
        <span className="text-xs text-slate-400">{t.consoleHint}</span>
      </div>
      <div className="min-h-0 flex-1">
        {tab === 'terminal' ? <Console /> : tab === 'visual' ? <VisualOps /> : <LLMPanel />}
      </div>
    </div>
  )
}
