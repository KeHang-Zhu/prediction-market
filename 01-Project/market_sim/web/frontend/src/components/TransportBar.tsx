import { useStore } from '../store'
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

export default function TransportBar() {
  const t = useT()
  const lang = useStore((s) => s.lang)
  const playback = useStore((s) => s.playback)
  const maxRound = useStore((s) => s.maxRound)
  const viewRound = useStore((s) => s.viewRound)
  const live = useStore((s) => s.live)
  const hasLlm = useStore((s) => s.hasLlm)
  const busy = useStore((s) => s.busy)
  const saveFlash = useStore((s) => s.saveFlash)
  const scenarios = useStore((s) => s.scenarios)
  const recordings = useStore((s) => s.recordings)
  const play = useStore((s) => s.play)
  const pause = useStore((s) => s.pause)
  const step = useStore((s) => s.step)
  const save = useStore((s) => s.save)
  const resume = useStore((s) => s.resume)
  const reset = useStore((s) => s.reset)
  const loadConfig = useStore((s) => s.loadConfig)
  const openBuilder = useStore((s) => s.openBuilder)
  const setSpeed = useStore((s) => s.setSpeed)
  const setViewRound = useStore((s) => s.setViewRound)
  const goLive = useStore((s) => s.goLive)

  const playing = playback.mode === 'playing'
  const replay = !!playback.replay
  const liveLlm = hasLlm && !replay      // single-step gate applies only to LIVE LLM runs
  const cfgName = playback?.config_name || ''
  const isRec = cfgName.endsWith('.jsonl')
  // friendly label for the canonical scenarios (also reused for recording groups)
  const scenarioLabel = (file: string) =>
    file === 'demo.yaml' ? t.scenarioHuman
    : file === 'demo5.yaml' ? t.scenarioLlm
    : file === 'llm5_only.yaml' ? t.scenarioLlmOnly
    : file.startsWith('templates/') ? file.replace(/^templates\//, '').replace(/\.ya?ml$/, '')
    : file
  const builtinScenarios = scenarios.filter((s) => s.builtin !== false)
  const templateScenarios = scenarios.filter((s) => s.builtin === false)
  const fmtTs = (ts: string) => {
    const m = ts.match(/^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})/)
    return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : ts
  }
  // history is scoped to the active scenario: you only see replays of the scenario you're on
  const curScenario = playback?.scenario || ''
  const myRecordings = recordings.filter((r) => r.scenario === curScenario)
  const canResume = isRec && !!playback?.resumable
  // during a replay the loaded config is a *.jsonl, so map the active scenario (run_name)
  // back to its *.yaml — otherwise the scenario <select> value is '' and a browser renders
  // that as its FIRST option ("Human Demo"), so replaying an LLM run looked like it switched.
  const activeScenarioFile = scenarios.find((s) => s.file.replace(/\.ya?ml$/, '') === curScenario)?.file || ''

  return (
    <div className="border-b border-slate-200 bg-white">
      {/* ── Row 1 · controls (grouped: source │ transport │ state) ── */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 pb-2 pt-2.5">
        {/* source: scenario + history pickers */}
        {scenarios.length > 0 && (
          <label className="flex shrink-0 items-center gap-1.5 text-sm">
            <span className="whitespace-nowrap text-slate-400">{t.scenario}</span>
            <select
              value={isRec ? activeScenarioFile : cfgName}
              onChange={(e) => e.target.value && loadConfig(e.target.value)}
              className="max-w-44 rounded-md border border-slate-200 bg-white px-2 py-1 text-sm text-slate-700 focus:outline-none"
            >
              {builtinScenarios.map((s) => <option key={s.file} value={s.file}>{scenarioLabel(s.file)}</option>)}
              {templateScenarios.length > 0 && (
                <optgroup label={lang === 'zh' ? '模板' : 'Templates'}>
                  {templateScenarios.map((s) => <option key={s.file} value={s.file}>{scenarioLabel(s.file)}</option>)}
                </optgroup>
              )}
            </select>
          </label>
        )}
        <button
          onClick={openBuilder}
          title={lang === 'zh' ? '新建场景' : 'New scenario'}
          className="shrink-0 whitespace-nowrap rounded-md border border-blue-200 bg-white px-2.5 py-1 text-sm font-medium text-blue-600 transition hover:bg-blue-50"
        >
          ＋ {lang === 'zh' ? '新建' : 'New'}
        </button>
        {myRecordings.length > 0 && (
          <label className="flex shrink-0 items-center gap-1.5 text-sm">
            <span className="whitespace-nowrap text-slate-400">{t.recordingsGroup}</span>
            <select
              value={isRec ? cfgName : ''}
              onChange={(e) => e.target.value && loadConfig(e.target.value)}
              className="max-w-52 rounded-md border border-slate-200 bg-white px-2 py-1 text-sm text-slate-700 focus:outline-none"
            >
              <option value="">{t.historyPick}</option>
              {myRecordings.map((r) => (
                <option key={r.file} value={r.file}>
                  {fmtTs(r.ts)} · {r.rounds}r{r.resumable ? ' ⏯' : ''}
                </option>
              ))}
            </select>
          </label>
        )}

        <Divider />

        {/* transport: play/pause · step · reset · save */}
        <div className="flex shrink-0 items-center gap-1.5">
          {playing ? (
            <Btn onClick={pause} title={t.pause} active>❚❚ {t.pause}</Btn>
          ) : (
            <Btn onClick={play} title={t.play}>▶ {t.play}</Btn>
          )}
          <Btn onClick={step} title={t.step} disabled={busy || playing}>⏭ {t.step}</Btn>
          <button
            onClick={() => { if (confirm(t.confirmReset)) reset() }}
            title={t.reset}
            className="shrink-0 whitespace-nowrap rounded-md border border-rose-200 bg-white px-3 py-1.5 text-sm font-medium text-rose-600 transition hover:bg-rose-50"
          >
            ↺ {t.reset}
          </button>
          {/* manual Save: a live run is only persisted to disk when you click this */}
          {!replay && (
            <button
              onClick={save}
              disabled={maxRound < 1}
              title={t.save}
              className={`shrink-0 whitespace-nowrap rounded-md border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                saveFlash
                  ? 'border-emerald-500 bg-emerald-500 text-white'
                  : 'border-emerald-200 bg-white text-emerald-700 hover:bg-emerald-50'
              }`}
            >
              {saveFlash ? t.saved : `💾 ${t.save}`}
            </button>
          )}
        </div>

        {/* state: live-LLM / replay badge + continue */}
        {(liveLlm || replay || canResume) && <Divider />}
        <div className="flex shrink-0 items-center gap-2">
          {liveLlm && (
            <span className="flex items-center gap-1.5 whitespace-nowrap rounded-md bg-purple-100 px-2 py-1 text-xs font-medium text-purple-700">
              ✦ {t.llmLive}
              {busy && <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-purple-500" />}
            </span>
          )}
          {replay && (
            <span className="flex items-center gap-1.5 whitespace-nowrap rounded-md bg-sky-100 px-2 py-1 text-xs font-medium text-sky-700">
              ▶ replay
            </span>
          )}
          {canResume && (
            <button
              onClick={() => resume(cfgName)}
              title={t.continueRun}
              className="shrink-0 whitespace-nowrap rounded-md border border-purple-300 bg-purple-50 px-3 py-1.5 text-sm font-medium text-purple-700 transition hover:bg-purple-100"
            >
              {t.continueRun}
            </button>
          )}
        </div>
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

        {/* speed: meaningless for a LIVE LLM run (paced by model latency) -> hidden there */}
        {!liveLlm && (
          <>
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
          </>
        )}
      </div>
    </div>
  )
}
