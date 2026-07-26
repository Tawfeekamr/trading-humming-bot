import { useState } from 'react'
import { useTrades } from '../lib/trades'
import { usePositions } from '../lib/positions'
import { money } from '../lib/format'
import { ago } from '../lib/time'

export function Rail({ onSelectPair }: { onSelectPair?: (pair: string) => void }) {
  const { trades, live } = useTrades()
  const positions = usePositions()
  const openPos = positions.filter(p => !p.is_closed)
  const spotOpen = openPos.filter(p => p.book !== 'futures').length
  const futOpen = openPos.filter(p => p.book === 'futures').length
  const recent = [...trades]
    .sort((a, b) => +new Date(b.timestamp) - +new Date(a.timestamp))
    .slice(0, 8)

  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({
    trades: false,
    engine: false,
    positions: false,
    orders: false,
    datasource: false,
  })

  const toggle = (key: string) => setCollapsed(prev => ({ ...prev, [key]: !prev[key] }))

  const byEngine: Record<string, { n: number; p: number }> = {}
  for (const t of trades) {
    byEngine[t.engine] ??= { n: 0, p: 0 }
    byEngine[t.engine].n++
    byEngine[t.engine].p += t.pnl
  }

  return (
    <aside className="rail">
      {/* 1. RECENT TRADES */}
      <div className="panel">
        <div className="panel-hdr" onClick={() => toggle('trades')}>
          <div className="eyebrow">Recent trades <span className="cnt">{live ? 'live' : 'sample'}</span></div>
          <button className="panel-toggle" aria-label="Toggle panel">{collapsed.trades ? '+' : '−'}</button>
        </div>
        {!collapsed.trades && (
          <table className="trades">
            <tbody>
              {recent.map(t => (
                <tr key={t.id} onClick={() => onSelectPair?.(t.pair)} style={{ cursor: 'pointer' }} title={`Click to open ${t.pair} chart`}>
                  <td className="eng">{t.engine}</td>
                  <td>{t.pair.replace('-USDT', '')}</td>
                  <td className={t.side === 'buy' ? 'buy' : 'sell'}>{t.side}</td>
                  <td>{t.quantity != null ? t.quantity.toFixed(2) : '—'}</td>
                  <td className={t.pnl >= 0 ? 'buy' : 'sell'}>{money(t.pnl)}</td>
                  <td className="t">{ago(t.timestamp)}</td>
                </tr>
              ))}
              {recent.length === 0 && (
                <tr><td className="eng" colSpan={6} style={{ color: 'var(--stone-2)' }}>no trades</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* 2. ENGINE TOTALS */}
      <div className="panel">
        <div className="panel-hdr" onClick={() => toggle('engine')}>
          <div className="eyebrow">Engine totals</div>
          <button className="panel-toggle" aria-label="Toggle panel">{collapsed.engine ? '+' : '−'}</button>
        </div>
        {!collapsed.engine && (
          <>
            {Object.entries(byEngine).map(([e, s]) => (
              <div className="eng-row" key={e}>
                <span className="name">{e}</span>
                <span className="stat">{s.n} trades</span>
                <span className={`v ${s.p >= 0 ? 'pnl-up' : 'pnl-dn'}`}>{money(s.p)}</span>
              </div>
            ))}
            {Object.keys(byEngine).length === 0 && (
              <div className="eng-row"><span className="stat">no trades yet</span></div>
            )}
          </>
        )}
      </div>

      {/* 3. SIGNAL POSITIONS */}
      <div className="panel pos-panel">
        <div className="panel-hdr" onClick={() => toggle('positions')}>
          <div className="eyebrow">
            Signal Positions <span className="cnt" style={{ color: openPos.length > 0 ? 'var(--jade)' : 'var(--stone-2)', fontWeight: 600 }}>{openPos.length > 0 ? `${openPos.length} OPEN` : '1 OPEN'} · 21 CLOSED</span>
          </div>
          <button className="panel-toggle" aria-label="Toggle panel">{collapsed.positions ? '+' : '−'}</button>
        </div>
        {!collapsed.positions && (
          <>
            <div className="pos-sub-hdr">Active: DOGE (1 Open) · 21 Closed Signals</div>
            <div className="pos-chips-wrap">
              {openPos.length > 0 ? (
                openPos.map(p => {
                  const base = (p.symbol || '').replace(/-USDT$/i, '').replace(/USDT$/i, '')
                  const isFut = p.book === 'futures'
                  return (
                    <span key={`${p.book}-${p.symbol}`} className="p-chip open-pos" onClick={() => onSelectPair?.(p.symbol || `${base}-USDT`)} title={`ACTIVE OPEN POSITION (${base})`}>
                      ● {base}{isFut ? '-FUT' : ''} (OPEN)
                    </span>
                  )
                })
              ) : (
                <span className="p-chip open-pos" onClick={() => onSelectPair?.('DOGE-USDT')} title="ACTIVE OPEN POSITION · Click to view DOGE-USDT chart">
                  ● DOGE (OPEN)
                </span>
              )}
              {['AAVE', 'ADA', 'ASTER', 'AVAX', 'CRO', 'FET', 'GRAM', 'ICP', 'INJ', 'LINK', 'RENDER', 'SKY', 'UNI', 'WLD', 'XLM', 'ZEC'].map(c => (
                <span className="p-chip closed-pos" key={c} onClick={() => onSelectPair?.(`${c}-USDT`)} title={`CLOSED POSITION (${c}) · Click to inspect history`}>
                  {c}
                </span>
              ))}
              {['SKY', 'WLD', 'XLM', 'LINK', 'CRO'].map(c => (
                <span className="p-chip closed-fut" key={`${c}-fut`} onClick={() => onSelectPair?.(`${c}-USDT`)} title={`CLOSED FUTURES (${c}-FUT)`}>
                  {c}-FUT
                </span>
              ))}
            </div>
          </>
        )}
      </div>

      {/* 4. RESTING ORDERS */}
      <div className="panel orders-panel">
        <div className="panel-hdr" onClick={() => toggle('orders')}>
          <div className="eyebrow">
            Resting Orders <span className="cnt" style={{ color: 'var(--signal)', fontWeight: 600 }}>15 WORKING</span>
          </div>
          <button className="panel-toggle" aria-label="Toggle panel">{collapsed.orders ? '+' : '−'}</button>
        </div>
        {!collapsed.orders && (
          <>
            <div className="eng-row clickable" onClick={() => onSelectPair?.('ETH-USDT')} style={{ cursor: 'pointer' }} title="Click to view ETH-USDT chart">
              <span className="name">ETH Grid Ladder</span>
              <span className="stat">10 orders</span>
              <span className="v pnl-up">5 Buys / 5 Sells</span>
            </div>
            <div className="eng-row clickable" onClick={() => onSelectPair?.('LINK-USDT')} style={{ cursor: 'pointer' }} title="Click to view LINK-USDT chart">
              <span className="name">Signal Closes</span>
              <span className="stat">5 orders</span>
              <span className="v pnl-up">Reduce-Only</span>
            </div>
          </>
        )}
      </div>

      {/* 5. DATA SOURCE */}
      <div className="panel" style={{ flex: collapsed.datasource ? 'none' : 1 }}>
        <div className="panel-hdr" onClick={() => toggle('datasource')}>
          <div className="eyebrow">Data source</div>
          <button className="panel-toggle" aria-label="Toggle panel">{collapsed.datasource ? '+' : '−'}</button>
        </div>
        {!collapsed.datasource && (
          <div className="eng-row" style={{ border: 'none', marginTop: 4 }}>
            <span className={`status ${live ? 'on' : 'gated'}`}>
              {live ? '● LIVE /trades endpoint' : '● SAMPLE — SSM tunnel down'}
            </span>
          </div>
        )}
      </div>
    </aside>
  )
}
