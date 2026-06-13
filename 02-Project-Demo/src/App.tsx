import { useEffect } from 'react'
import { useStore } from './store'
import Header from './components/Header'
import TransportBar from './components/TransportBar'
import Showcase from './components/Showcase'
import MatchingTutorial from './components/MatchingTutorial'
import MatchingWalkthrough from './components/MatchingWalkthrough'
import AgentWalkthrough from './components/AgentWalkthrough'

// Pure-frontend LLM showcase: bundled `llm5_only` replays of five tool-using LLM
// traders. No backend — connect() boots the local replay engine on mount. The
// explainer demos (tutorial / matching trace / agent walkthrough) take over the
// whole screen and carry their own back/nav controls.
export default function App() {
  const connect = useStore((s) => s.connect)
  const view = useStore((s) => s.view)
  useEffect(() => {
    connect()
  }, [connect])

  if (view === 'tutorial') return <MatchingTutorial />
  if (view === 'matching') return <MatchingWalkthrough />
  if (view === 'walkthrough') return <AgentWalkthrough />

  return (
    <div className="flex h-screen flex-col bg-slate-50">
      <Header />
      <TransportBar />
      <div className="flex min-h-0 flex-1 flex-col gap-3 p-3">
        <div className="min-h-0 flex-1">
          <Showcase />
        </div>
      </div>
    </div>
  )
}
