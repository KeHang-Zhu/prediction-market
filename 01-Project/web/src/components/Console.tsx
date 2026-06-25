import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { useT } from '../i18n'

export default function Console() {
  const t = useT()
  const lines = useStore((s) => s.console)
  const runCommand = useStore((s) => s.runCommand)
  const [input, setInput] = useState('')
  const [history, setHistory] = useState<string[]>([])
  const [hIdx, setHIdx] = useState(-1)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [lines])

  const submit = () => {
    const v = input.trim()
    if (!v) return
    runCommand(v)
    setHistory((h) => [...h, v])
    setHIdx(-1)
    setInput('')
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') submit()
    else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (!history.length) return
      const ni = hIdx < 0 ? history.length - 1 : Math.max(0, hIdx - 1)
      setHIdx(ni); setInput(history[ni])
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (hIdx < 0) return
      const ni = hIdx + 1
      if (ni >= history.length) { setHIdx(-1); setInput('') }
      else { setHIdx(ni); setInput(history[ni]) }
    }
  }

  const color = (k: string) => (k === 'in' ? 'text-blue-600' : k === 'err' ? 'text-rose-600' : 'text-slate-600')

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto scroll-thin px-3 py-2">
        <pre className="tabular whitespace-pre-wrap text-xs leading-relaxed">
          <div className="text-slate-400">{t.consoleWelcome}</div>
          {lines.map((l, i) => (
            <div key={i} className={color(l.kind)}>{l.text}</div>
          ))}
        </pre>
        <div ref={endRef} />
      </div>
      <div className="flex items-center gap-2 border-t border-slate-100 px-3 py-2">
        <span className="tabular text-sm text-blue-500">›</span>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder="get_orderbook --market COIN-A   ·   place_order --market COIN-A --side buy --price 60 --qty 10"
          spellCheck={false}
          className="tabular flex-1 bg-transparent text-sm text-slate-700 placeholder:text-slate-300 focus:outline-none"
        />
        <button onClick={submit} className="rounded-md border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50">{t.run}</button>
      </div>
    </div>
  )
}
