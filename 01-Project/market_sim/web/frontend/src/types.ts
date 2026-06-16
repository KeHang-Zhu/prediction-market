export interface Depth {
  bids: [number, number][]
  asks: [number, number][]
}

export interface MarketState {
  id: string
  question: string
  status: string
  best_bid: number | null
  best_ask: number | null
  last_trade: number | null
  mid: number
  true_prob_pct: number
  resolves_in: number
  outcome: number | null
  collateral_pool: number
  volume: number
  depth: Depth
}

export interface AgentState {
  agent_id: string
  type: string
  cash_available: number
  cash_locked: number
  positions: Record<string, { YES: number; NO: number }>
  equity: number
  pnl: number
}

export interface Snapshot {
  round: number
  markets: MarketState[]
  agents: AgentState[]
}

export interface Trade {
  round: number
  market: string
  price: number
  qty: number
  settle: string
  taker: string
  maker: string
}

export interface NewsItem {
  round: number
  market: string
  signal: number
  accuracy_pct: number
  text: string
}

export interface Playback {
  mode: string
  speed: number
  current_round: number
  max_round: number
  has_llm?: boolean
  replay?: boolean
  busy?: boolean
  config_name?: string
  scenario?: string     // run_name of the active scenario (scopes the history picker)
  resumable?: boolean   // the loaded recording can be continued
}

export interface EventDict {
  event_id: number
  round: number
  type: string
  agent_id: string | null
  payload: any
  result: any
  ts: string
}

export interface ConsoleLine {
  kind: 'in' | 'out' | 'err'
  text: string
}

export interface Scenario {
  file: string
  builtin?: boolean   // true = curated built-in YAML; false = user-built template
}

export interface Recording {
  file: string      // path relative to runs/, e.g. demo5/2026-06-08_2230.jsonl
  scenario: string  // parent folder (the scenario it came from)
  ts: string        // the timestamp stem
  rounds: number    // how many rounds it ran
  resumable: boolean // a sibling engine-state snapshot exists -> can be continued
}

// ---- explainer-demo payloads (clearing trace + LLM dialogue) ----

export interface BookState {
  market: string
  book: Depth
  best_bid: number | null
  best_ask: number | null
  last_trade: number | null
  mid: number
  pool: number
}

export interface ClearingFill {
  maker: string
  maker_order_id: number
  price: number
  qty: number
  settle: string // transfer_yes | transfer_no | mint | merge
  pool_delta: number
  roles: Record<string, string>
}

export interface ClearingOrder {
  market?: string
  token?: string
  side?: string
  price?: number
  qty?: number
  client_id?: string | null
  order_id?: number
  kind?: string // 'cancel' for queued cancels
}

export interface ClearingStep {
  seq: number
  agent: string
  kind: 'order' | 'cancel'
  order: ClearingOrder
  book_before: BookState | null
  book_after: BookState | null
  status: string // filled | partial | resting | rejected | cancelled | not_found
  reason?: string | null
  fills: ClearingFill[]
  filled_qty: number
  resting_qty: number
}

export interface ClearingDecision {
  agent: string
  orders: ClearingOrder[]
}

export interface ClearingTrace {
  round: number
  execution_order: string[]
  decisions: ClearingDecision[]
  steps: ClearingStep[]
}

export interface ModelCall {
  name: string
  args: Record<string, any>
}

export interface ModelTurn {
  turn: number
  text: string
  calls: ModelCall[]
  error?: string | null
}

export interface ToolSpec {
  name: string
  kind: string // read | action
  signature: string
  description: string
}

export interface AgentMeta {
  system_prompt: string
  tools: ToolSpec[]
}

export const SETTLE_COLORS: Record<string, string> = {
  transfer_yes: '#2563eb', // blue
  transfer_no: '#7c3aed',  // violet
  mint: '#059669',         // emerald
  merge: '#d97706',        // amber
}

export const SETTLE_LABEL: Record<string, string> = {
  transfer_yes: 'transfer YES',
  transfer_no: 'transfer NO',
  mint: 'mint',
  merge: 'merge',
}
