import { useEffect, useState } from 'react'

export type View = 'desk' | 'pnl' | 'mlrl'

export function Masthead({ view, setView }: { view: View; setView: (v: View) => void }) {
  const [clock, setClock] = useState('--:--:--')
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

  const tabs: [View, string][] = [['desk', 'DESK'], ['pnl', 'P&L'], ['mlrl', 'ML/RL']]

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
      </div>
    </header>
  )
}
