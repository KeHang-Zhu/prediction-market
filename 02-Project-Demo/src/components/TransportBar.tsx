import { useStore, REPLAY_METAS } from '../store'
import { useT } from '../i18n'
import { Divider } from './ui'

function Btn({ onClick, children, title, active, disabled }: any) {
  return (
    <button
      onClick={onClick}
      title={title}
      disabled={disabled}
      className={`shrink-0 whitespace-nowrap rounded-md border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
        active
          ? 'border-blue-500 bg-blue-500 text-white'
          : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
      }`}
    >
      {children}
    </button>
  )
}

// Replay-only transport (static build): pick a bundled recording, play / pause / step
// a round, scrub, and re-pace with the speed slider. The live-run controls (save,
// reset, continue, scenario picker) have no meaning for a bundled recording.
export default function TransportBar() {
  const t = useT()
  const zh = useStore((s) => s.lang) === 'zh'
  const playback = useStore((s) => s.playback)
  const maxRound = useStore((s) => s.maxRound)
  const viewRound = useStore((s) => s.viewRound)
  const live = useStore((s) => s.live)
  const play = useStore((s) => s.play)
  const pause = useStore((s) => s.pause)
  const step = useStore((s) => s.step)
  const setSpeed = useStore((s) => s.setSpeed)
  const setViewRound = useStore((s) => s.setViewRound)
  const goLive = useStore((s) => s.goLive)
  const activeReplay = useStore((s) => s.activeReplay)
  const loadReplay = useStore((s) => s.loadReplay)

  const playing = playback.mode === 'playing'
  const replayLabel = (m: typeof REPLAY_METAS[number]) =>
    zh
      ? `${m.dialogue ? '完整' : '长回合'} · ${m.rounds} 回合${m.dialogue ? ' · 含对话' : ''}`
      : `${m.dialogue ? 'full' : 'long'} · ${m.rounds}r${m.dialogue ? ' · dialogue' : ''}`

  return (
    <div className="border-b border-slate-200 bg-white">
      {/* ── Row 1 · controls ── */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 pb-2 pt-2.5">
        {/* replay picker (the two bundled llm5_only runs) */}
        {REPLAY_METAS.length > 1 && (
          <label className="flex shrink-0 items-center gap-1.5 text-sm">
            <span className="whitespace-nowrap text-slate-400">{t.recordingsGroup}</span>
            <select
              value={activeReplay}
              onChange={(e) => loadReplay(parseInt(e.target.value, 10))}
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-sm text-slate-700 focus:outline-none"
            >
              {REPLAY_METAS.map((m, i) => <option key={m.key} value={i}>{replayLabel(m)}</option>)}
            </select>
          </label>
        )}

        <Divider />

        <div className="flex shrink-0 items-center gap-1.5">
          {playing ? (
            <Btn onClick={pause} title={t.pause} active>❚❚ {t.pause}</Btn>
          ) : (
            <Btn onClick={play} title={t.play}>▶ {t.play}</Btn>
          )}
          <Btn onClick={step} title={t.step} disabled={playing}>⏭ {t.step}</Btn>
        </div>

        <Divider />

        <span className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md bg-sky-100 px-2 py-1 text-xs font-medium text-sky-700">
          ▶ {t.scenarioLlmOnly}
        </span>
      </div>

      {/* ── Row 2 · timeline (full width) + speed ── */}
      <div className="flex items-center gap-3 border-t border-slate-100 px-5 py-2">
        <span className="shrink-0 text-sm text-slate-400">{t.round}</span>
        <input
          type="range" min={0} max={Math.max(maxRound, 1)} step={1} value={viewRound}
          onChange={(e) => setViewRound(parseInt(e.target.value, 10))}
          className="min-w-0 flex-1 accent-blue-500"
        />
        <span className="tabular w-16 shrink-0 text-right text-sm text-slate-700">{viewRound} / {maxRound}</span>
        <Btn onClick={goLive} title={t.live} active={live}>{live ? `● ${t.live}` : `○ ${t.live}`}</Btn>
        <Divider />
        <div className="flex shrink-0 items-center gap-2 text-sm text-slate-600">
          <span className="whitespace-nowrap text-slate-400">{t.speed}</span>
          <input
            type="range" min={0.5} max={30} step={0.5} value={playback.speed}
            onChange={(e) => setSpeed(parseFloat(e.target.value))}
            className="w-24 accent-blue-500 sm:w-28"
          />
          <span className="tabular w-12 text-slate-700">×{playback.speed.toFixed(1)}</span>
        </div>
      </div>
    </div>
  )
}
