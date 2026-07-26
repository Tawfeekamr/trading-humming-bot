import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { useTrades } from '../lib/trades'
import { money } from '../lib/format'

const ENGINES = ['grid', 'trend', 'swing', 'mr']

export function Pnl() {
  const { trades, live } = useTrades()
  const net = trades.reduce((a, t) => a + t.pnl, 0)
  const count = trades.length
  const wins = trades.filter(t => t.pnl > 0).length
  const winRate = count ? Math.round((wins / count) * 100) : 0

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

  const sorted = [...trades].sort((a, b) => +new Date(a.timestamp) - +new Date(b.timestamp))
  let run = 0
  const pts = [0, ...sorted.map(t => (run += t.pnl))]

  return (
    <div className="scroll-wrap">
      <div className="page-head">
        <div><div className="eyebrow">Performance</div><h1>Portfolio P&amp;L</h1></div>
        <span className="range-pill">{live ? 'live · /trades' : 'sample data'} · {count} trades</span>
      </div>

      <div className="tiles">
        <Tile k="Net P&L" v={money(net)} cls={net >= 0 ? 'pnl-up' : 'pnl-dn'} s={`${count} trades`} bar={net >= 0 ? 'var(--jade)' : 'var(--ox)'} w="60%" />
        <Tile k="Win rate" v={winRate + '%'} s={`${wins} wins / ${count - wins} losses`} bar="var(--signal)" w={winRate + '%'} />
        <Tile k="Best engine" v={engRows[0]?.n ?? '—'} cls="pnl-up" s={engRows[0] ? money(engRows[0].p) : ''} bar="var(--jade)" w="70%" />
        <Tile k="Worst engine" v={engRows.at(-1)?.n ?? '—'} cls="pnl-dn" s={engRows.length ? money(engRows.at(-1)!.p) : ''} bar="var(--ox)" w="85%" />
      </div>

      <div className="pnl-grid">
        <section className="panel pnl-card">
          <div className="eyebrow">P&L by engine <span className="cnt">net realized</span></div>
          <Bars items={engRows.map(e => ({ n: e.n, v: e.p }))} />
        </section>
        <section className="panel pnl-card">
          <div className="eyebrow">Cumulative realized P&L <span className="cnt">strategy</span></div>
          <Equity points={pts} />
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

function Equity({ points }: { points: number[] }) {
  const ref = useRef<SVGSVGElement>(null)
  const [w, setW] = useState(500)
  useEffect(() => {
    const f = () => setW(ref.current?.clientWidth ?? 500)
    f()
    const ro = new ResizeObserver(f)
    if (ref.current) ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  const h = 170
  const n = points.length
  const min = Math.min(...points), max = Math.max(...points)
  const range = (max - min) || 1
  const x = (i: number) => (n > 1 ? (i / (n - 1)) * w : 0)
  const y = (v: number) => 12 + (1 - (v - min) / range) * (h - 24)
  const d = points.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const end = points.at(-1) ?? 0
  const up = end >= 0
  const col = up ? '#4A9D8A' : '#C2523F'
  return (
    <>
      <div className="eq-legend">
        <span className="li"><span className="ln" style={{ borderColor: col }} /> Cumulative <b className={up ? 'pnl-up' : 'pnl-dn'}>{(end >= 0 ? '+' : '') + end.toFixed(2)}</b></span>
        <span className="li delta">buy &amp; hold benchmark — pending historical import</span>
      </div>
      <svg ref={ref} className="equity" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        <path d={`${d} L${w},${h} L0,${h} Z`} fill={col} opacity={0.1} />
        <path d={d} fill="none" stroke={col} strokeWidth={1.6} />
      </svg>
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
