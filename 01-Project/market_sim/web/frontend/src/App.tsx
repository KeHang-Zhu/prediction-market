import { useEffect } from 'react'
import { useStore, snapshotAt, showcaseAgentIds } from './store'
import Header from './components/Header'
import TransportBar from './components/TransportBar'
import MarketTabs from './components/MarketTabs'
import OrderBookPanel from './components/OrderBookPanel'
import PriceChart from './components/PriceChart'
import TradeTape from './components/TradeTape'
import PortfolioPanel from './components/PortfolioPanel'
import ConsolePanel from './components/ConsolePanel'
import Showcase from './components/Showcase'
import MatchingWalkthrough from './components/MatchingWalkthrough'
import MatchingTutorial from './components/MatchingTutorial'
import AgentWalkthrough from './components/AgentWalkthrough'
import ScenarioBuilder from './components/ScenarioBuilder'

export default function App() {
  const connect = useStore((s) => s.connect)
  const snapshots = useStore((s) => s.snapshots)
  const maxRound = useStore((s) => s.maxRound)
  const view = useStore((s) => s.view)
  useEffect(() => {
    connect()
  }, [connect])

  // explainer demos take over the whole screen (they carry their own back/nav controls).
  // The scenario builder is a modal overlay reachable from every view.
  if (view === 'tutorial') return <><MatchingTutorial /><ScenarioBuilder /></>
  if (view === 'matching') return <><MatchingWalkthrough /><ScenarioBuilder /></>
  if (view === 'walkthrough') return <><AgentWalkthrough /><ScenarioBuilder /></>

  const snap = snapshotAt(snapshots, maxRound)
  // The layout is driven by the scenario, not a manual toggle: agentic (LLM) scenarios
  // get the unified showcase page (market + per-agent tool-calls); the human demo gets
  // the classic trading dashboard.
  const isShowcase = showcaseAgentIds(snap).length >= 1

  return (
    <div className="flex h-screen flex-col bg-slate-50">
      <Header />
      <TransportBar />
      <div className="flex min-h-0 flex-1 flex-col gap-3 p-3">
        {isShowcase ? (
          <div className="min-h-0 flex-1">
            <Showcase />
          </div>
        ) : (
          <>
            <MarketTabs />
            <div className="grid min-h-0 flex-1 grid-cols-12 gap-3">
              <div className="col-span-3 min-h-0">
                <OrderBookPanel />
              </div>
              <div className="col-span-6 flex min-h-0 flex-col gap-3">
                <div className="min-h-0 flex-[3]">
                  <PriceChart />
                </div>
                <div className="min-h-0 flex-[2]">
                  <TradeTape />
                </div>
              </div>
              <div className="col-span-3 min-h-0">
                <PortfolioPanel />
              </div>
            </div>
            <div className="h-64 min-h-0">
              <ConsolePanel />
            </div>
          </>
        )}
      </div>
      <ScenarioBuilder />
    </div>
  )
}
