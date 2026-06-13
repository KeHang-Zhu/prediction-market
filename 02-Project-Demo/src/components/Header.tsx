import { useStore } from '../store'
import { useT } from '../i18n'
import { Divider } from './ui'

function LangToggle() {
  const lang = useStore((s) => s.lang)
  const setLang = useStore((s) => s.setLang)
  return (
    <div className="flex overflow-hidden rounded-md border border-slate-200 text-xs">
      {(['en', 'zh'] as const).map((l) => (
        <button
          key={l}
          onClick={() => setLang(l)}
          className={`px-2 py-1 font-medium transition ${
            lang === l ? 'bg-blue-500 text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
          }`}
        >
          {l === 'en' ? 'EN' : '中'}
        </button>
      ))}
    </div>
  )
}

// Secondary navigation to the full-screen explainer pages. Lives in the header (not the
// transport bar) so it reads as "go to a page", cleanly separated from playback controls.
function ExplainerNav() {
  const t = useT()
  const openTutorial = useStore((s) => s.openTutorial)
  const openMatching = useStore((s) => s.openMatching)
  const clearingByRound = useStore((s) => s.clearingByRound)
  const cleared = Object.keys(clearingByRound).map(Number).sort((a, b) => a - b)
  return (
    <nav className="flex items-center gap-1">
      <button
        onClick={openTutorial}
        className="whitespace-nowrap rounded-md px-2 py-1 font-medium text-indigo-600 transition hover:bg-indigo-50"
      >
        {t.demoMatching}
      </button>
      {cleared.length > 0 && (
        <button
          onClick={() => openMatching(cleared[cleared.length - 1])}
          className="whitespace-nowrap rounded-md px-2 py-1 text-slate-500 transition hover:bg-slate-100"
        >
          {t.demoMatchingReal}
        </button>
      )}
    </nav>
  )
}

export default function Header() {
  const t = useT()
  const connected = useStore((s) => s.connected)
  const playback = useStore((s) => s.playback)
  const maxRound = useStore((s) => s.maxRound)
  const viewRound = useStore((s) => s.viewRound)
  const live = useStore((s) => s.live)

  return (
    <header className="flex items-center justify-between gap-4 border-b border-slate-200 bg-white px-5 py-3">
      <div className="flex min-w-0 items-baseline gap-3">
        <h1 className="shrink-0 text-lg font-semibold tracking-tight text-slate-800">{t.title}</h1>
        <span className="hidden truncate text-xs text-slate-400 xl:inline">{t.subtitle}</span>
      </div>

      <div className="flex shrink-0 items-center gap-3 text-xs">
        <ExplainerNav />
        <Divider />
        <div className="tabular whitespace-nowrap text-slate-500">
          {t.round} <span className="font-semibold text-slate-800">{viewRound}</span>
          <span className="text-slate-300"> / {maxRound}</span>
          {!live && <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">{t.scrubbing}</span>}
        </div>
        <span className="whitespace-nowrap rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">
          {t.mode[playback.mode] ?? playback.mode}
        </span>
        <span className="flex items-center gap-1.5">
          <span className={`inline-block h-2 w-2 rounded-full ${connected ? 'bg-emerald-500' : 'bg-rose-400'}`} />
          <span className="hidden text-slate-500 sm:inline">{connected ? t.connected : t.offline}</span>
        </span>
        <LangToggle />
      </div>
    </header>
  )
}
