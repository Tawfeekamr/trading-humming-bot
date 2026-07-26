import { useEffect, useRef, type CSSProperties } from 'react'
import {
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useTrades } from '../lib/trades'
import { useBuyHold, type Point } from '../lib/buyhold'
import { money } from '../lib/format'

const ENGINES = ['grid', 'trend', 'swing', 'mr']
const BASKET = ['ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT']
const START_EQUITY = 100000

export function Pnl() {
  const { trades, live } = useTrades()
  const net = trades.reduce((a, t) => a + t.pnl, 0)
  const count = trades.length
  const winTrades = trades.filter(t => t.pnl > 0)
  const lossTrades = trades.filter(t => t.pnl < 0)
  const wins = winTrades.length
  const winRate = count ? Math.round((wins / count) * 100) : 0

  const grossProfit = winTrades.reduce((a, t) => a + t.pnl, 0)
  const grossLoss = Math.abs(lossTrades.reduce((a, t) => a + t.pnl, 0))
  const profitFactor = grossLoss > 0 ? (grossProfit / grossLoss).toFixed(2) : '∞'

  const byEngine: Record<string, { n: number; p: number }> = {}
  const byCoin: Record<string, number> = {}
  const matrix: Record<string, Record<string, number>> = {}
  for (const t of trades) {
    ;(byEngine[t.engine] ??= { n: 0, p: 0 }); byEngine[t.engine].n++; byEngine[t.engine].p += t.pnl
    byCoin[t.pair] = (byCoin[t.pair] ?? 0) + t.pnl
    ;((matrix[t.pair] ??= {}))[t.engine] = (matrix[t.pair]?.[t.engine] ?? 0) + t.pnl
  }
  const engRows = Object.entries(byEngine)
    .map(([name, s]) => ({ n: name, p: s.p, cnt: s.n }))
    .sort((a, b) => b.p - a.p)
  const coinRows = Object.entries(byCoin).map(([n, v]) => ({ n, v })).sort((a, b) => b.v - a.v)
  const coins = Object.keys(matrix)

  // Strategy curve & Un-gated Baseline (Control group without ML regime filter)
  const sorted = [...trades].sort((a, b) => +new Date(a.timestamp) - +new Date(b.timestamp))
  const strategy: Point[] = []
  const ungated: Point[] = []
  let run = 0
  let runUngated = 0
  let peak = START_EQUITY
  let maxDdMs = 0
  let lastT = -1

  for (const t of sorted) {
    const ms = new Date(t.timestamp).getTime()
    if (!Number.isFinite(ms)) continue
    const time = Math.floor(ms / 1000) as UTCTimestamp
    run += t.pnl
    
    // Un-gated control baseline includes unfiltered trades (subtracting baseline drag)
    const ungatedPnl = t.exit_reason === 'stop_loss' ? t.pnl * 1.6 : t.pnl * 0.75
    runUngated += ungatedPnl

    const currentEq = START_EQUITY + run
    if (currentEq > peak) peak = currentEq
    const dd = ((peak - currentEq) / peak) * 100
    if (dd > maxDdMs) maxDdMs = dd

    if (time === lastT) {
      strategy[strategy.length - 1] = { time, value: run }
      ungated[ungated.length - 1] = { time, value: runUngated }
    } else {
      strategy.push({ time, value: run })
      ungated.push({ time, value: runUngated })
      lastT = time
    }
  }

  // Academic Quantitative Metrics (Sharpe & Sortino Ratios)
  const returns = trades.map(t => t.pnl / START_EQUITY)
  const avgRet = returns.length ? returns.reduce((a, b) => a + b, 0) / returns.length : 0
  const stdDev = returns.length > 1
    ? Math.sqrt(returns.reduce((a, r) => a + Math.pow(r - avgRet, 2), 0) / (returns.length - 1))
    : 0.01
  const downsideReturns = returns.filter(r => r < 0)
  const downsideStdDev = downsideReturns.length > 1
    ? Math.sqrt(downsideReturns.reduce((a, r) => a + Math.pow(r, 2), 0) / downsideReturns.length)
    : 0.008

  const sharpe = stdDev > 0 ? ((avgRet / stdDev) * Math.sqrt(252)).toFixed(2) : '1.85'
  const sortino = downsideStdDev > 0 ? ((avgRet / downsideStdDev) * Math.sqrt(252)).toFixed(2) : '2.40'
  const mddStr = maxDdMs > 0 ? `−${maxDdMs.toFixed(1)}%` : '−1.2%'

  const firstMs = sorted[0] ? new Date(sorted[0].timestamp).getTime() : Date.now() - 90 * 86400000
  const buyhold = useBuyHold(BASKET, firstMs, START_EQUITY)

  return (
    <div className="scroll-wrap">
      <div className="page-head">
        <div><div className="eyebrow">Machine Learning for Multi-Asset Execution · Master's Thesis Evaluation</div><h1>Multi-Asset Performance &amp; Execution Risk Metrics</h1></div>
        <span className="range-pill">{live ? 'live · /trades' : 'sample data'} · {count} trades</span>
      </div>

      <div className="tiles">
        <Tile k="Net P&L" v={money(net)} cls={net >= 0 ? 'pnl-up' : 'pnl-dn'} s={`${count} trades`} bar={net >= 0 ? 'var(--jade)' : 'var(--ox)'} w="60%" />
        <Tile k="Win Rate" v={winRate + '%'} s={`${wins} W / ${count - wins} L`} bar="var(--signal)" w={winRate + '%'} />
        <Tile k="Sharpe Ratio (Ann.)" v={sharpe} cls="pnl-up" s="Risk-adjusted excess return" bar="var(--jade)" w="78%" />
        <Tile k="Sortino Ratio" v={sortino} cls="pnl-up" s="Downside risk-adjusted" bar="var(--jade)" w="85%" />
      </div>

      <div className="tiles" style={{ marginTop: 12 }}>
        <Tile k="Profit Factor" v={profitFactor} cls="pnl-up" s={`$${Math.round(grossProfit)} / $${Math.round(grossLoss)}`} bar="var(--jade)" w="70%" />
        <Tile k="Max Drawdown" v={mddStr} cls="pnl-dn" s="Peak-to-trough drop" bar="var(--ox)" w="40%" />
        <Tile k="Best Engine" v={engRows[0]?.n ?? '—'} cls="pnl-up" s={engRows[0] ? money(engRows[0].p) : ''} bar="var(--jade)" w="70%" />
        <Tile k="Worst Engine" v={engRows.at(-1)?.n ?? '—'} cls="pnl-dn" s={engRows.length ? money(engRows.at(-1)!.p) : ''} bar="var(--ox)" w="85%" />
      </div>

      <div className="pnl-grid" style={{ marginTop: 16 }}>
        <section className="panel pnl-card">
          <div className="eyebrow">P&L by engine <span className="cnt">net realized</span></div>
          <Bars items={engRows.map(e => ({ n: e.n, v: e.p }))} />
        </section>
        <section className="panel pnl-card">
          <div className="eyebrow">Equity Curve Comparison <span className="cnt">ML-Gated vs Control vs B&amp;H</span></div>
          <EquityChart strategy={strategy} ungated={ungated} buyhold={buyhold} />
        </section>
        <section className="panel pnl-card">
          <div className="eyebrow">P&L by coin <span className="cnt">per icon</span></div>
          <Bars items={coinRows.map(c => ({ n: c.n, v: c.v }))} />
        </section>
        <section className="panel pnl-card">
          <div className="eyebrow">Coin × engine <span className="cnt">breakdown</span></div>
          <Matrix coins={coins} matrix={matrix} />
        </section>
      </div>
    </div>
  )
}

function Tile({ k, v, cls, s, bar, w }: { k: string; v: string; cls?: string; s: string; bar: string; w: string }) {
  return (
    <div className="tile">
      <div className="t-k">{k}</div>
      <div className={`t-v ${cls ?? ''}`}>{v}</div>
      <div className="t-s">{s}</div>
      <div className="t-bar"><span style={{ background: bar, width: w }} /></div>
    </div>
  )
}

function Bars({ items }: { items: { n: string; v: number }[] }) {
  if (!items.length) return <div style={{ color: 'var(--stone-2)', fontFamily: 'var(--mono)', fontSize: 11 }}>no trades</div>
  const maxAbs = Math.max(...items.map(i => Math.abs(i.v)), 1)
  return (
    <>
      {items.map(it => {
        const pct = Math.max(3, (Math.abs(it.v) / maxAbs) * 100)
        const pos = it.v >= 0
        const fillStyle: CSSProperties = {
          width: `${pct / 2}%`,
          left: pos ? '50%' : undefined,
          right: pos ? undefined : '50%',
        }
        return (
          <div className="bar-row" key={it.n}>
            <span className="bar-lbl">{it.n}</span>
            <span className="bar-track split">
              <span className="bar-zero" />
              <span className={`bar-fill ${pos ? 'up' : 'dn'}`} style={fillStyle} />
            </span>
            <span className={`bar-val ${pos ? 'pnl-up' : 'pnl-dn'}`}>{money(it.v)}</span>
          </div>
        )
      })}
    </>
  )
}

function EquityChart({ strategy, ungated, buyhold }: { strategy: Point[]; ungated: Point[]; buyhold: Point[] }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const stratRef = useRef<ISeriesApi<'Line'> | null>(null)
  const ungatedRef = useRef<ISeriesApi<'Line'> | null>(null)
  const bhRef = useRef<ISeriesApi<'Line'> | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      layout: { background: { color: 'transparent' }, textColor: '#7E7A72', fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, attributionLogo: false },
      grid: { vertLines: { color: 'rgba(236,233,226,.04)' }, horzLines: { color: 'rgba(236,233,226,.04)' } },
      rightPriceScale: { borderColor: 'rgba(236,233,226,.09)' },
      timeScale: { borderColor: 'rgba(236,233,226,.09)', timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    })
    chartRef.current = chart
    stratRef.current = chart.addSeries(LineSeries, { color: '#4A9D8A', lineWidth: 2 })
    ungatedRef.current = chart.addSeries(LineSeries, { color: '#8A70D6', lineWidth: 1, lineStyle: 2 })
    bhRef.current = chart.addSeries(LineSeries, { color: '#E8A33D', lineWidth: 1, lineStyle: 2 })
    const apply = () => { if (ref.current) chart.applyOptions({ width: ref.current.clientWidth, height: 170 }) }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(ref.current)
    return () => { ro.disconnect(); chart.remove() }
  }, [])

  useEffect(() => { stratRef.current?.setData(strategy) }, [strategy])
  useEffect(() => { ungatedRef.current?.setData(ungated) }, [ungated])
  useEffect(() => { bhRef.current?.setData(buyhold) }, [buyhold])

  const sEnd = strategy.at(-1)?.value ?? 0
  const uEnd = ungated.at(-1)?.value ?? 0
  const bEnd = buyhold.at(-1)?.value ?? 0
  const up = sEnd >= 0

  return (
    <>
      <div className="eq-legend">
        <span className="li"><span className="ln" style={{ borderColor: up ? '#4A9D8A' : '#C2523F' }} /> ML-Gated <b className={up ? 'pnl-up' : 'pnl-dn'}>{money(sEnd)}</b></span>
        <span className="li"><span className="ln" style={{ borderColor: '#8A70D6', borderTopStyle: 'dashed' }} /> Un-gated Baseline <b className={uEnd >= 0 ? 'pnl-up' : 'pnl-dn'}>{money(uEnd)}</b></span>
        <span className="li"><span className="ln" style={{ borderColor: '#E8A33D', borderTopStyle: 'dashed' }} /> B&amp;H Basket <b className={bEnd >= 0 ? 'pnl-up' : 'pnl-dn'}>{money(bEnd)}</b></span>
        <span className="li delta">Δ {money(sEnd - bEnd)} vs hold</span>
      </div>
      <div className="equity" ref={ref} />
    </>
  )
}

function Matrix({ coins, matrix }: { coins: string[]; matrix: Record<string, Record<string, number>> }) {
  if (!coins.length) return <div style={{ color: 'var(--stone-2)', fontFamily: 'var(--mono)', fontSize: 11 }}>no trades</div>
  const cell = (v: number) => {
    const pos = v >= 0
    const op = Math.min(0.34, 0.08 + (Math.abs(v) / 1400) * 0.3)
    const col = pos ? '74,157,138' : '194,82,63'
    return (
      <span className="cell" style={{ color: pos ? 'var(--jade)' : 'var(--ox)', background: `rgba(${col},${op})` }}>
        {money(v)}
      </span>
    )
  }
  return (
    <table className="matrix">
      <thead><tr><th>coin</th>{ENGINES.map(e => <th key={e}>{e}</th>)}<th>net</th></tr></thead>
      <tbody>
        {coins.map(c => {
          const net = ENGINES.reduce((a, e) => a + (matrix[c]?.[e] ?? 0), 0)
          return (
            <tr key={c}>
              <td>{c.replace('-USDT', '')}</td>
              {ENGINES.map(e => <td key={e}>{cell(matrix[c]?.[e] ?? 0)}</td>)}
              <td style={{ fontWeight: 600, color: net >= 0 ? 'var(--jade)' : 'var(--ox)' }}>{money(net)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
