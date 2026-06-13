import { useState } from 'react'
import { useStore, roundsWithClearing } from '../store'
import { SETTLE_COLORS } from '../types'

// ── Matching, from scratch ────────────────────────────────────────────────────
// A self-contained, hand-authored tutorial that teaches the clearing mechanism with
// the SIMPLEST possible toy examples: three traders (Alice / Bob / Carol), one
// market, one or two orders at a time. No replay data, no deep books — one concept
// per chapter, stepped frame by frame. This is the "explain it like I'm John" page.

type Lang = 0 | 1 // 0 = en, 1 = zh
type Pair = [string, string]
interface Holding { cash: number; yes: number; no: number }
type Holdings = Record<string, Holding>
interface OrderCard { owner: string; side: 'buy' | 'sell'; token: 'YES' | 'NO'; price: number; qty: number; tag?: Pair }
interface BookLvl { price: number; qty: number; owner: string }
interface Frame {
  text: Pair
  note?: Pair
  orders?: OrderCard[]
  book?: { bids: BookLvl[]; asks: BookLvl[] }
  trade?: { settle: string; price: number; qty: number }
  pool: number
  holdings: Holdings
}
interface Chapter { title: Pair; goal: Pair; frames: Frame[] }

const COLOR: Record<string, string> = {
  Alice: 'bg-indigo-100 text-indigo-700 border-indigo-200',
  Bob: 'bg-rose-100 text-rose-700 border-rose-200',
  Carol: 'bg-amber-100 text-amber-700 border-amber-200',
  Market: 'bg-slate-100 text-slate-600 border-slate-200',
}
const dot: Record<string, string> = { Alice: 'bg-indigo-500', Bob: 'bg-rose-500', Carol: 'bg-amber-500', Market: 'bg-slate-400' }

const CHAPTERS: Chapter[] = [
  {
    title: ['1 · What you trade', '1 · 你在买卖什么'],
    goal: ['A market has YES and NO shares. 1 YES + 1 NO is always worth 100¢.',
           '每个市场有 YES 和 NO 两种份额。1 份 YES + 1 份 NO 永远值 100¢。'],
    frames: [
      {
        text: ['At resolution the winning side pays 100¢ per share, the losing side 0¢. So a YES and a NO together always pay exactly 100¢ — one of them wins.',
                '开奖时,赢的一方每份付 100¢,输的一方付 0¢。所以一份 YES 加一份 NO 加起来永远正好付 100¢——必有一方赢。'],
        note: ['That 100¢-per-pair rule is the whole trick. Keep it in mind.',
               '"一对 = 100¢" 这条规则是全部的关键,记住它。'],
        pool: 0, holdings: {},
      },
      {
        text: ['The YES price is just the market’s implied probability. YES @60¢ ≈ "the crowd thinks there’s a 60% chance of YES".',
                'YES 的价格就是市场隐含的概率。YES @60¢ ≈ "大家觉得有 60% 的可能是 YES"。'],
        note: ['To bet AGAINST yes you buy NO. Buying NO @40¢ is the same bet as "YES is worth at most 60¢".',
               '想赌"不会发生"就买 NO。买 NO @40¢ 等价于赌"YES 顶多值 60¢"。'],
        pool: 0, holdings: {},
      },
    ],
  },
  {
    title: ['2 · MINT — where shares come from', '2 · 铸造 MINT — 份额从哪来'],
    goal: ['Nobody holds shares yet. When a YES-buyer meets a NO-buyer, the market MINTS a fresh pair.',
           '一开始谁都没有份额。当一个"买 YES"的人遇上一个"买 NO"的人,市场会凭空铸造一对新份额。'],
    frames: [
      {
        text: ['Alice thinks YES and bids 60¢ for it. Bob thinks NO and bids 40¢ for it. Neither owns any shares yet.',
                'Alice 觉得会 YES,出价 60¢ 买;Bob 觉得会 NO,出价 40¢ 买。两人手上都还没有任何份额。'],
        orders: [
          { owner: 'Alice', side: 'buy', token: 'YES', price: 60, qty: 1 },
          { owner: 'Bob', side: 'buy', token: 'NO', price: 40, qty: 1 },
        ],
        pool: 0,
        holdings: { Alice: { cash: 1000, yes: 0, no: 0 }, Bob: { cash: 1000, yes: 0, no: 0 } },
      },
      {
        text: ['Their bids add up to a whole pair: buy-YES 60¢ + buy-NO 40¢ = 100¢. So the market MINTS one fresh YES+NO pair. Alice pays 60¢ and gets 1 YES; Bob pays 40¢ and gets 1 NO; their 100¢ goes into the collateral pool.',
                '两人的出价正好凑成一整对:买 YES 60¢ + 买 NO 40¢ = 100¢。于是市场铸造(MINT)一对全新的 YES+NO。Alice 付 60¢ 得 1 份 YES,Bob 付 40¢ 得 1 份 NO,他们合出的 100¢ 进入抵押池。'],
        note: ['The shares were NOT bought from someone else — they were created out of nothing, funded by the two buyers together.',
               '这两份份额不是从别人手里买来的——是凭空造出来的,由两个买家合起来出钱。'],
        orders: [
          { owner: 'Alice', side: 'buy', token: 'YES', price: 60, qty: 1, tag: ['mints with →', '铸造 →'] },
          { owner: 'Bob', side: 'buy', token: 'NO', price: 40, qty: 1 },
        ],
        trade: { settle: 'mint', price: 60, qty: 1 },
        pool: 100,
        holdings: { Alice: { cash: 940, yes: 1, no: 0 }, Bob: { cash: 960, yes: 0, no: 1 } },
      },
      {
        text: ['Recap: 100¢ went into the pool, and it will pay out 100¢ to whichever side wins at resolution. Minting a pair always costs exactly 100¢ — split between the YES buyer (60¢) and the NO buyer (40¢).',
                '小结:100¢ 进了抵押池,开奖时这 100¢ 会付给赢的那一方。铸造一对永远正好花 100¢——由 YES 买家(60¢)和 NO 买家(40¢)分摊。'],
        pool: 100,
        holdings: { Alice: { cash: 940, yes: 1, no: 0 }, Bob: { cash: 960, yes: 0, no: 1 } },
      },
    ],
  },
  {
    title: ['3 · TRANSFER — shares change hands', '3 · 转移 TRANSFER — 份额易手'],
    goal: ['Once shares exist, a holder can sell them to a new buyer. The pool does not change.',
           '份额一旦存在,持有者就能把它卖给新买家。抵押池不变。'],
    frames: [
      {
        text: ['Alice now holds 1 YES (from the mint). Carol also wants YES and bids 70¢ for it.',
                'Alice 现在持有 1 份 YES(上一章铸造来的)。Carol 也想要 YES,出价 70¢ 买。'],
        orders: [{ owner: 'Carol', side: 'buy', token: 'YES', price: 70, qty: 1 }],
        pool: 100,
        holdings: { Alice: { cash: 940, yes: 1, no: 0 }, Carol: { cash: 1000, yes: 0, no: 0 } },
      },
      {
        text: ['Alice sells her YES @70¢. She actually HOLDS it, so this is a legal sell (no short-selling allowed). Buyer and seller cross at 70¢: the YES share moves Alice → Carol, and 70¢ moves Carol → Alice.',
                'Alice 把她的 YES 以 70¢ 卖出。她**确实持有**这份 YES,所以是合法卖出(本市场不允许卖空)。买卖双方在 70¢ 成交:YES 份额从 Alice 转给 Carol,70¢ 从 Carol 转给 Alice。'],
        note: ['An existing share just changed owner. The collateral pool is untouched — no minting, no merging.',
               '一份已经存在的份额只是换了主人。抵押池一分没动——没铸造、也没合并。'],
        orders: [
          { owner: 'Alice', side: 'sell', token: 'YES', price: 70, qty: 1, tag: ['transfers to →', '转移给 →'] },
          { owner: 'Carol', side: 'buy', token: 'YES', price: 70, qty: 1 },
        ],
        trade: { settle: 'transfer_yes', price: 70, qty: 1 },
        pool: 100,
        holdings: { Alice: { cash: 1010, yes: 0, no: 0 }, Carol: { cash: 930, yes: 1, no: 0 } },
      },
    ],
  },
  {
    title: ['4 · MERGE — destroy a pair', '4 · 合并 MERGE — 销毁一对'],
    goal: ['A YES holder and a NO holder both want out. Selling together MERGES the pair back into 100¢.',
           '一个 YES 持有者和一个 NO 持有者都想离场。一起卖出会把这一对合并、退回 100¢。'],
    frames: [
      {
        text: ['Back to just after the mint: Alice holds 1 YES, Bob holds 1 NO. Both want to cash out now instead of waiting for resolution.',
                '回到铸造之后的状态:Alice 有 1 份 YES,Bob 有 1 份 NO。两人都想现在就离场,而不是等开奖。'],
        pool: 100,
        holdings: { Alice: { cash: 940, yes: 1, no: 0 }, Bob: { cash: 960, yes: 0, no: 1 } },
      },
      {
        text: ['Alice offers to sell YES @55¢, Bob offers to sell NO @45¢. The two sells add up to a whole pair (55 + 45 = 100), so the market MERGES: it destroys one YES+NO pair and releases 100¢ from the pool — 55¢ to Alice, 45¢ to Bob.',
                'Alice 挂出 sell YES @55¢,Bob 挂出 sell NO @45¢。两个卖单凑成一整对(55 + 45 = 100),于是市场合并(MERGE):销毁一对 YES+NO,从抵押池放出 100¢——55¢ 给 Alice,45¢ 给 Bob。'],
        note: ['Merge is the exact opposite of mint: a pair is destroyed and the pool refunds 100¢, split by their asking prices.',
               '合并和铸造正好相反:一对份额被销毁,抵押池退回 100¢,按两人的报价分。'],
        orders: [
          { owner: 'Alice', side: 'sell', token: 'YES', price: 55, qty: 1, tag: ['merges with →', '合并 →'] },
          { owner: 'Bob', side: 'sell', token: 'NO', price: 45, qty: 1 },
        ],
        trade: { settle: 'merge', price: 55, qty: 1 },
        pool: 0,
        holdings: { Alice: { cash: 995, yes: 0, no: 0 }, Bob: { cash: 1005, yes: 0, no: 0 } },
      },
    ],
  },
  {
    title: ['5 · One whole round', '5 · 一整轮怎么撮合'],
    goal: ['Everyone decides blind on the same start-of-round book; orders then match in finish order, price-time priority.',
           '所有人面对同一个回合开始的盘口盲投决策;订单再按完成顺序、价格-时间优先撮合。'],
    frames: [
      {
        text: ['A real round is blind submit: every trader decides on the SAME start-of-round book at the same time, and nobody can see anyone else’s orders for this round yet.',
                '真实的一轮是"盲投":所有人**同时**面对同一个回合开始时的盘口做决定,此刻谁也看不到别人这一轮的单。'],
        orders: [
          { owner: 'Alice', side: 'buy', token: 'YES', price: 60, qty: 1, tag: ['decided 1st', '第 1 个想完'] },
          { owner: 'Bob', side: 'buy', token: 'NO', price: 40, qty: 1, tag: ['decided 2nd', '第 2 个想完'] },
          { owner: 'Carol', side: 'buy', token: 'YES', price: 50, qty: 1, tag: ['decided 3rd', '第 3 个想完'] },
        ],
        pool: 0,
        holdings: { Alice: { cash: 1000, yes: 0, no: 0 }, Bob: { cash: 1000, yes: 0, no: 0 }, Carol: { cash: 1000, yes: 0, no: 0 } },
      },
      {
        text: ['Orders enter the book in the order traders FINISHED deciding (a faster decision goes first), then match by price first, and at the same price by who arrived first. Alice finished first, then Bob, then Carol.',
                '订单按"谁先想完谁先进场"的顺序进入盘口(想得快的先进),撮合时先比价格、同价再比谁先到。Alice 先想完,然后 Bob,最后 Carol。'],
        orders: [
          { owner: 'Alice', side: 'buy', token: 'YES', price: 60, qty: 1, tag: ['1', '1'] },
          { owner: 'Bob', side: 'buy', token: 'NO', price: 40, qty: 1, tag: ['2', '2'] },
          { owner: 'Carol', side: 'buy', token: 'YES', price: 50, qty: 1, tag: ['3', '3'] },
        ],
        pool: 0,
        holdings: { Alice: { cash: 1000, yes: 0, no: 0 }, Bob: { cash: 1000, yes: 0, no: 0 }, Carol: { cash: 1000, yes: 0, no: 0 } },
      },
      {
        text: ['Alice (buy YES 60) and Bob (buy NO 40) add up to 100 → they MINT a pair. Carol’s buy YES @50 has no matching counterparty, so it just RESTS on the book and waits for a future round.',
                'Alice(买 YES 60)和 Bob(买 NO 40)凑成 100 → 铸造一对 MINT。Carol 的 buy YES @50 没有对手盘,于是挂在盘口上等下一轮。'],
        note: ['That’s the whole round: decide blind → enter in finish order → match by price-time → unmatched orders rest.',
               '这就是一整轮:盲投决策 → 按完成顺序进场 → 价格-时间撮合 → 没撮上的单挂着等。'],
        book: { bids: [{ price: 50, qty: 1, owner: 'Carol' }], asks: [] },
        trade: { settle: 'mint', price: 60, qty: 1 },
        pool: 100,
        holdings: { Alice: { cash: 940, yes: 1, no: 0 }, Bob: { cash: 960, yes: 0, no: 1 }, Carol: { cash: 1000, yes: 0, no: 0 } },
      },
    ],
  },
  {
    title: ['6 · Settlement (resolution)', '6 · 结算(开奖)'],
    goal: ['A market does not settle every round. It resolves once, at the end: YES pays 100¢, NO pays 0¢.',
           '市场不是每轮结算。它只在最后开奖一次:YES 付 100¢,NO 付 0¢。'],
    frames: [
      {
        text: ['Until the resolve-round, nothing is final — each trader’s P&L is just marked-to-market at the current mid price. Alice is holding 1 YES, Bob is holding 1 NO.',
                '在结算回合到来之前,什么都没定——每个人的盈亏只是按当前中间价(mid)估算。Alice 持有 1 份 YES,Bob 持有 1 份 NO。'],
        pool: 100,
        holdings: { Alice: { cash: 940, yes: 1, no: 0 }, Bob: { cash: 960, yes: 0, no: 1 } },
      },
      {
        text: ['Resolution comes out YES. Every YES share now pays 100¢; every NO share pays 0¢. The pool’s 100¢ is paid out — exactly to the YES holder.',
                '开奖结果是 YES。此时每份 YES 付 100¢,每份 NO 付 0¢。抵押池里的 100¢ 被付出去——正好付给 YES 持有者。'],
        note: ['Alice paid 60¢ for the YES and gets 100¢ back → +40¢. Bob’s NO is worth 0 → −40¢. The pool always covers the winner: that’s why 1 YES + 1 NO ≡ 100¢.',
               'Alice 当初 60¢ 买的 YES 拿回 100¢ → 净赚 40¢。Bob 的 NO 归零 → 亏 40¢。池子永远兜得住赢家——这正是 1 YES + 1 NO ≡ 100¢ 的原因。'],
        pool: 0,
        holdings: { Alice: { cash: 1040, yes: 0, no: 0 }, Bob: { cash: 960, yes: 0, no: 0 } },
      },
    ],
  },
]

function Ladder({ book, lang }: { book: { bids: BookLvl[]; asks: BookLvl[] }; lang: Lang }) {
  const Row = ({ l, side }: { l: BookLvl; side: 'bid' | 'ask' }) => (
    <div className={`flex items-center justify-between rounded px-2 py-1 text-sm ${side === 'bid' ? 'bg-emerald-50' : 'bg-rose-50'}`}>
      <span className={`tabular font-medium ${side === 'bid' ? 'text-emerald-700' : 'text-rose-700'}`}>{l.price}¢ × {l.qty}</span>
      <span className="text-xs text-slate-500">{l.owner}</span>
    </div>
  )
  return (
    <div className="w-56">
      <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-400">{lang ? '盘口(挂着的单)' : 'order book (resting)'}</div>
      <div className="space-y-1 rounded-lg border border-slate-200 bg-white p-2">
        {[...book.asks].reverse().map((l, i) => <Row key={`a${i}`} l={l} side="ask" />)}
        {book.asks.length === 0 && <div className="px-2 py-0.5 text-[11px] text-slate-300">{lang ? '— 无卖单 —' : '— no asks —'}</div>}
        <div className="border-t border-dashed border-slate-200" />
        {book.bids.map((l, i) => <Row key={`b${i}`} l={l} side="bid" />)}
        {book.bids.length === 0 && <div className="px-2 py-0.5 text-[11px] text-slate-300">{lang ? '— 无买单 —' : '— no bids —'}</div>}
      </div>
    </div>
  )
}

export default function MatchingTutorial() {
  const langZh = useStore((s) => s.lang) === 'zh'
  const lang: Lang = langZh ? 1 : 0
  const back = useStore((s) => s.backToMain)
  const openMatching = useStore((s) => s.openMatching)
  const clearingByRound = useStore((s) => s.clearingByRound)
  const cleared = roundsWithClearing(clearingByRound)

  const [ch, setCh] = useState(0)
  const [fr, setFr] = useState(0)
  const chapter = CHAPTERS[ch]
  const frame = chapter.frames[Math.min(fr, chapter.frames.length - 1)]
  const prevFrame = fr > 0 ? chapter.frames[fr - 1] : undefined

  const goChapter = (i: number) => { setCh(i); setFr(0) }
  const T = (p: Pair) => p[lang]

  // money/share deltas vs the previous frame in this chapter (so the change is visible)
  const delta = (name: string, field: keyof Holding): number => {
    const cur = frame.holdings[name]?.[field] ?? 0
    const pre = prevFrame?.holdings[name]?.[field]
    return pre == null ? 0 : cur - pre
  }
  const poolDelta = prevFrame ? frame.pool - prevFrame.pool : 0
  const dStr = (n: number, unit = '') => (n > 0 ? `+${n}${unit}` : `${n}${unit}`)

  return (
    <div className="flex h-screen flex-col bg-slate-50">
      {/* header */}
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
        <div className="flex items-center gap-3">
          <button onClick={back} className="rounded-md border border-slate-300 px-2.5 py-1 text-sm text-slate-600 hover:bg-slate-50">← {langZh ? '返回' : 'back'}</button>
          <h1 className="text-lg font-semibold text-slate-800">⚙ {langZh ? '撮合机制 · 从零讲起' : 'Matching, from scratch'}</h1>
        </div>
        {cleared.length > 0 && (
          <button
            onClick={() => openMatching(cleared[cleared.length - 1])}
            className="rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-500 hover:bg-slate-50"
          >
            {langZh ? '在真实回合上看 →' : 'see it on a real round →'}
          </button>
        )}
      </div>

      {/* chapter tabs */}
      <div className="flex flex-wrap gap-1.5 border-b border-slate-200 bg-white px-5 py-2">
        {CHAPTERS.map((c, i) => (
          <button key={i} onClick={() => goChapter(i)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition ${i === ch ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
            {T(c.title)}
          </button>
        ))}
      </div>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-5 p-6">
          {/* chapter goal */}
          <div className="rounded-xl border border-indigo-100 bg-indigo-50/60 p-4">
            <div className="text-base font-semibold text-slate-800">{T(chapter.title)}</div>
            <div className="mt-1 text-sm text-slate-600">{T(chapter.goal)}</div>
          </div>

          {/* the stage: orders / book / trade / pool */}
          {(frame.orders || frame.book || frame.trade) && (
            <div className="flex flex-wrap items-start gap-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              {frame.orders && (
                <div className="flex flex-col gap-2">
                  <div className="text-[10px] uppercase tracking-wide text-slate-400">{langZh ? '订单' : 'orders'}</div>
                  {frame.orders.map((o, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <span className={`rounded border px-1.5 py-0.5 text-xs font-semibold ${COLOR[o.owner] || COLOR.Market}`}>{o.owner}</span>
                      <span className={`tabular rounded px-2 py-1 text-sm font-medium ${o.side === 'buy' ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
                        {o.side} {o.token} @{o.price}¢ ×{o.qty}
                      </span>
                      {o.tag && <span className="text-xs text-slate-400">{T(o.tag)}</span>}
                    </div>
                  ))}
                </div>
              )}
              {frame.book && <Ladder book={frame.book} lang={lang} />}
              {frame.trade && (
                <div className="flex flex-col items-center justify-center gap-1 rounded-lg px-4 py-3" style={{ background: (SETTLE_COLORS[frame.trade.settle] || '#64748b') + '22' }}>
                  <span className="rounded px-2 py-0.5 text-xs font-bold uppercase text-white" style={{ background: SETTLE_COLORS[frame.trade.settle] || '#64748b' }}>
                    {frame.trade.settle.replace('_', ' ')}
                  </span>
                  <span className="tabular text-sm text-slate-600">{frame.trade.qty} @ {frame.trade.price}¢</span>
                </div>
              )}
            </div>
          )}

          {/* narration */}
          <div className="rounded-xl border-l-4 border-indigo-400 bg-white p-4 text-[15px] leading-relaxed text-slate-700 shadow-sm">
            {T(frame.text)}
          </div>
          {frame.note && (
            <div className="rounded-xl bg-amber-50 px-4 py-3 text-sm leading-relaxed text-amber-800">
              💡 {T(frame.note)}
            </div>
          )}

          {/* participants + pool (with deltas vs the previous step) */}
          {(Object.keys(frame.holdings).length > 0 || frame.pool > 0 || poolDelta !== 0) && (
            <div className="flex flex-wrap items-stretch gap-3">
              {Object.entries(frame.holdings).map(([name, h]) => (
                <div key={name} className={`min-w-[150px] flex-1 rounded-xl border bg-white p-3 ${COLOR[name]?.split(' ')[2] || 'border-slate-200'}`}>
                  <div className="mb-1 flex items-center gap-1.5">
                    <span className={`inline-block h-2.5 w-2.5 rounded-full ${dot[name] || dot.Market}`} />
                    <span className="text-sm font-semibold text-slate-700">{name}</span>
                  </div>
                  <div className="tabular space-y-0.5 text-sm text-slate-600">
                    <div className="flex justify-between"><span className="text-slate-400">{langZh ? '现金' : 'cash'}</span>
                      <span>{h.cash}¢ {delta(name, 'cash') !== 0 && <span className={delta(name, 'cash') > 0 ? 'text-emerald-600' : 'text-rose-500'}>({dStr(delta(name, 'cash'))})</span>}</span></div>
                    <div className="flex justify-between"><span className="text-slate-400">YES</span>
                      <span>{h.yes} {delta(name, 'yes') !== 0 && <span className={delta(name, 'yes') > 0 ? 'text-emerald-600' : 'text-rose-500'}>({dStr(delta(name, 'yes'))})</span>}</span></div>
                    <div className="flex justify-between"><span className="text-slate-400">NO</span>
                      <span>{h.no} {delta(name, 'no') !== 0 && <span className={delta(name, 'no') > 0 ? 'text-emerald-600' : 'text-rose-500'}>({dStr(delta(name, 'no'))})</span>}</span></div>
                  </div>
                </div>
              ))}
              <div className="min-w-[150px] flex-1 rounded-xl border border-slate-300 bg-slate-50 p-3">
                <div className="mb-1 text-sm font-semibold text-slate-600">🏦 {langZh ? '抵押池' : 'collateral pool'}</div>
                <div className="tabular text-2xl font-semibold text-slate-700">
                  {frame.pool}¢ {poolDelta !== 0 && <span className={`text-sm ${poolDelta > 0 ? 'text-emerald-600' : 'text-rose-500'}`}>({dStr(poolDelta)})</span>}
                </div>
                <div className="mt-0.5 text-[11px] text-slate-400">{langZh ? '= 未结算的份额对数 × 100¢' : '= outstanding pairs × 100¢'}</div>
              </div>
            </div>
          )}

          {/* frame stepper */}
          <div className="flex items-center justify-between pt-1">
            <button onClick={() => setFr((i) => Math.max(0, i - 1))} disabled={fr === 0}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40 hover:bg-white">◀ {langZh ? '上一步' : 'prev'}</button>
            <div className="flex items-center gap-1.5">
              {chapter.frames.map((_, i) => (
                <button key={i} onClick={() => setFr(i)} className={`h-2.5 w-2.5 rounded-full ${i === fr ? 'bg-indigo-600' : 'bg-slate-300 hover:bg-slate-400'}`} />
              ))}
            </div>
            {fr < chapter.frames.length - 1 ? (
              <button onClick={() => setFr((i) => i + 1)}
                className="rounded-md border border-indigo-300 bg-indigo-50 px-3 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-100">{langZh ? '下一步' : 'next'} ▶</button>
            ) : ch < CHAPTERS.length - 1 ? (
              <button onClick={() => goChapter(ch + 1)}
                className="rounded-md border border-indigo-300 bg-indigo-50 px-3 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-100">{langZh ? '下一章' : 'next chapter'} ▶▶</button>
            ) : (
              <button onClick={back} className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-700 hover:bg-emerald-100">{langZh ? '完成 ✓' : 'done ✓'}</button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
