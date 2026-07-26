import { useEffect, useState } from 'react'
import { useTrades } from '../lib/trades'
import { money } from '../lib/format'

export type View = 'desk' | 'pnl' | 'mlrl' | 'hypotheses'

const START_EQUITY = 100000 // paper account seed

export function Masthead({ view, setView }: { view: View; setView: (v: View) => void }) {
  const [clock, setClock] = useState('--:--:--')
  const { trades } = useTrades()

  // Equity = seed + all-time realized; day = realized on today's UTC date.
  const net = trades.reduce((a, t) => a + t.pnl, 0)
  const today = new Date().toISOString().slice(0, 10)
  const dayPnl = trades.filter(t => (t.timestamp || '').slice(0, 10) === today).reduce((a, t) => a + t.pnl, 0)

  useEffect(() => {
    const f = () => {
      const d = new Date()
      const p = (n: number) => String(n).padStart(2, '0')
      setClock(`${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`)
    }
    f()
    const id = setInterval(f, 1000)
    return () => clearInterval(id)
  }, [])

  const tabs: [View, string][] = [
    ['desk', 'DESK'],
    ['pnl', 'P&L'],
    ['mlrl', 'ML/RL'],
    ['hypotheses', 'HYPOTHESES'],
  ]

  return (
    <header className="masthead">
      <div className="brand">
        <span className="wordmark">HUMMINGBOT <span className="dim">· DESK</span></span>
      </div>
      <nav className="nav">
        {tabs.map(([v, label]) => (
          <button key={v} className={`nav-tab ${view === v ? 'active' : ''}`} onClick={() => setView(v)}>
            {label}
          </button>
        ))}
      </nav>
      <div className="mast-meta">
        <div className="meta"><span className="k">UTC</span><span className="v">{clock}</span></div>
        <div className="meta live"><span className="dot" /><span className="v">LIVE</span></div>
        <div className="meta"><span className="k">EQUITY</span><span className="v">{money(START_EQUITY + net)}</span></div>
        <div className="meta"><span className="k">DAY</span><span className={`v ${dayPnl >= 0 ? 'pnl-up' : 'pnl-dn'}`}>{money(dayPnl)}</span></div>
      </div>
    </header>
  )
}
