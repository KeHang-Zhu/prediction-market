import { useStore } from './store'

export type Lang = 'en' | 'zh'

export interface Strings {
  title: string
  subtitle: string
  round: string
  scrubbing: string
  connected: string
  offline: string
  play: string
  pause: string
  step: string
  save: string
  saved: string
  continueRun: string
  reset: string
  speed: string
  live: string
  confirmReset: string
  orderBook: string
  vol: string
  pool: string
  mid: string
  last: string
  spread: string
  noAsks: string
  noBids: string
  priceVolume: string
  tradeTape: string
  shown: string
  noTrades: string
  portfolios: string
  agent: string
  equity: string
  pnl: string
  pos: string
  avail: string
  lock: string
  noData: string
  consoleTitle: string
  consoleHint: string
  run: string
  consoleWelcome: string
  trueLabel: string
  resolved: string
  noMarket: string
  tabTerminal: string
  tabVisual: string
  vMarket: string
  vToken: string
  vSide: string
  vBuy: string
  vSell: string
  vPrice: string
  vQty: string
  vActAs: string
  vPlaceOrder: string
  vOrderId: string
  vCancelOrder: string
  vQueries: string
  vGetMarkets: string
  vGetBook: string
  vGetPortfolio: string
  vGetTape: string
  vUnsupported: string
  vCreateMarket: string
  vTransfer: string
  vResult: string
  tabLlm: string
  llmReasoning: string
  singleStep: string
  llmLive: string
  thinking: string
  belief: string
  lessons: string
  plan: string
  queued: string
  finishGroup: string
  scenario: string
  scenarioHuman: string
  scenarioLlm: string
  scenarioLlmOnly: string
  recordingsGroup: string
  historyPick: string
  noLlmYet: string
  viewDashboard: string
  viewShowcase: string
  demoMatching: string
  demoMatchingReal: string
  demoWalkthrough: string
  signal: string
  idle: string
  noStepsRound: string
  agentsPanel: string
  toolCalls: string
  otherAgents: string
  observation: string
  openMarkets: string
  briefingHint: string
  newsTrend: string
  args: string
  result: string
  settle: Record<string, string>
  mode: Record<string, string>
}

export const STRINGS: Record<Lang, Strings> = {
  en: {
    title: 'Generative Market Simulation',
    subtitle: 'single-book · mint/merge · integer cents',
    round: 'round',
    scrubbing: 'scrubbing',
    connected: 'connected',
    offline: 'offline',
    play: 'play',
    pause: 'pause',
    step: 'next round',
    save: 'save',
    saved: 'saved ✓',
    continueRun: '▶▶ continue',
    reset: 'reset',
    speed: 'speed',
    live: 'LIVE',
    confirmReset: 'Reset the simulation back to round 0?',
    orderBook: 'order book',
    vol: 'vol',
    pool: 'pool',
    mid: 'mid',
    last: 'last',
    spread: 'spread',
    noAsks: '— no asks —',
    noBids: '— no bids —',
    priceVolume: 'price & volume',
    tradeTape: 'trade tape',
    shown: 'shown',
    noTrades: 'no trades yet',
    portfolios: 'portfolios & P&L',
    agent: 'agent',
    equity: 'equity',
    pnl: 'P&L',
    pos: 'pos',
    avail: 'avail',
    lock: 'lock',
    noData: 'no data',
    consoleTitle: 'console',
    consoleHint: 'Agent CLI — matches the proposal API',
    run: 'run',
    consoleWelcome: 'agent API — type `help`.  e.g.  get_markets  ·  get_orderbook --market COIN-A  ·  place_order --market COIN-A --side buy --price 60 --qty 10',
    trueLabel: 'true',
    resolved: 'resolved',
    noMarket: 'no market selected',
    tabTerminal: 'Terminal',
    tabVisual: 'Visual ops',
    vMarket: 'market',
    vToken: 'token',
    vSide: 'side',
    vBuy: 'buy',
    vSell: 'sell',
    vPrice: 'price',
    vQty: 'qty',
    vActAs: 'act as',
    vPlaceOrder: 'place order',
    vOrderId: 'order id',
    vCancelOrder: 'cancel order',
    vQueries: 'queries',
    vGetMarkets: 'markets',
    vGetBook: 'order book',
    vGetPortfolio: 'portfolio',
    vGetTape: 'trades',
    vUnsupported: 'V1 (not supported)',
    vCreateMarket: 'create market',
    vTransfer: 'transfer',
    vResult: 'last result',
    tabLlm: 'LLM',
    llmReasoning: 'LLM reasoning',
    singleStep: 'single-step',
    llmLive: 'LLM · live',
    thinking: 'LLM thinking…',
    belief: 'belief',
    lessons: 'learned',
    plan: 'plan',
    queued: 'queued',
    finishGroup: 'finish — belief · plan · learned',
    scenario: 'scenario',
    scenarioHuman: '👤 Human Demo',
    scenarioLlm: '✦ LLM Showcase · 5 agents',
    scenarioLlmOnly: '✦ 5 LLM only · no bots',
    recordingsGroup: '▶ replays',
    historyPick: '— pick a replay —',
    noLlmYet: 'no agent steps yet — press Step',
    viewDashboard: 'Dashboard',
    viewShowcase: 'Agents',
    demoMatching: '⚙ how matching works',
    demoMatchingReal: 'on a real round →',
    demoWalkthrough: '🔬 round walkthrough',
    signal: 'signal',
    idle: 'idle this round',
    noStepsRound: 'no actions this round',
    agentsPanel: 'agents',
    toolCalls: 'tool calls',
    otherAgents: 'market bots',
    observation: 'wake-up briefing (all it gets free)',
    openMarkets: 'open markets',
    briefingHint: 'prices & your signal: it must fetch with tools →',
    newsTrend: 'signal vs truth',
    args: 'args',
    result: 'result',
    settle: { transfer_yes: 'transfer YES', transfer_no: 'transfer NO', mint: 'mint', merge: 'merge' },
    mode: { paused: 'paused', playing: 'playing' },
  },
  zh: {
    title: '生成式市场模拟',
    subtitle: '单一订单簿 · mint/merge · 整数分计价',
    round: '回合',
    scrubbing: '回看中',
    connected: '已连接',
    offline: '已断开',
    play: '播放',
    pause: '暂停',
    step: '下一轮',
    save: '保存',
    saved: '已保存 ✓',
    continueRun: '▶▶ 续跑',
    reset: '重置',
    speed: '速度',
    live: '实时',
    confirmReset: '将模拟重置到第 0 回合？',
    orderBook: '订单簿',
    vol: '成交量',
    pool: '抵押池',
    mid: '中价',
    last: '最新',
    spread: '价差',
    noAsks: '— 无卖单 —',
    noBids: '— 无买单 —',
    priceVolume: '价格与成交量',
    tradeTape: '成交带',
    shown: '条',
    noTrades: '暂无成交',
    portfolios: '持仓与盈亏',
    agent: '交易者',
    equity: '净值',
    pnl: '盈亏',
    pos: '持仓',
    avail: '可用',
    lock: '锁定',
    noData: '暂无数据',
    consoleTitle: '控制台',
    consoleHint: 'Agent CLI — 与 proposal 接口一致',
    run: '执行',
    consoleWelcome: 'agent 接口 — 输入 `help`。例如  get_markets  ·  get_orderbook --market COIN-A  ·  place_order --market COIN-A --side buy --price 60 --qty 10',
    trueLabel: '真实',
    resolved: '已结算',
    noMarket: '未选择市场',
    tabTerminal: '终端',
    tabVisual: '可视化操作',
    vMarket: '市场',
    vToken: '份额',
    vSide: '方向',
    vBuy: '买入',
    vSell: '卖出',
    vPrice: '价格',
    vQty: '数量',
    vActAs: '身份',
    vPlaceOrder: '下单',
    vOrderId: '订单号',
    vCancelOrder: '撤单',
    vQueries: '查询',
    vGetMarkets: '市场列表',
    vGetBook: '订单簿',
    vGetPortfolio: '持仓',
    vGetTape: '成交记录',
    vUnsupported: 'V1(暂不支持)',
    vCreateMarket: '创建市场',
    vTransfer: '转账',
    vResult: '最近结果',
    tabLlm: 'LLM',
    llmReasoning: 'LLM 推理',
    singleStep: '单步模式',
    llmLive: 'LLM · 实时',
    thinking: 'LLM 思考中…',
    belief: '信念',
    lessons: '本回合学到',
    plan: '计划',
    queued: '挂单中',
    finishGroup: '收尾 finish — 信念 · 计划 · 学到',
    scenario: '场景',
    scenarioHuman: '👤 人工 Demo',
    scenarioLlm: '✦ LLM 演示 · 5 智能体',
    scenarioLlmOnly: '✦ 纯 5 LLM · 无做市/噪声',
    recordingsGroup: '▶ 历史回放',
    historyPick: '— 选择回放 —',
    noLlmYet: '暂无智能体步骤 —— 点"单步"',
    viewDashboard: '交易台',
    viewShowcase: '智能体',
    demoMatching: '⚙ 撮合机制讲解',
    demoMatchingReal: '看真实回合 →',
    demoWalkthrough: '🔬 单轮详解',
    signal: '私有信号',
    idle: '本轮未行动',
    noStepsRound: '本轮无动作',
    agentsPanel: '智能体',
    toolCalls: '工具调用',
    otherAgents: '做市/噪声 bot',
    observation: '叫醒 briefing(它免费拿到的全部)',
    openMarkets: '开放市场',
    briefingHint: '价格/私有信号:需它用工具自查 →',
    newsTrend: '信号 vs 真值',
    args: '入参',
    result: '返回',
    settle: { transfer_yes: '转手 YES', transfer_no: '转手 NO', mint: '铸造', merge: '合并' },
    mode: { paused: '已暂停', playing: '播放中' },
  },
}

export function useT(): Strings {
  const lang = useStore((s) => s.lang)
  return STRINGS[lang]
}
