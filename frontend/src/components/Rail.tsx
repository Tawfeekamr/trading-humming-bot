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

  const byEngine: Record<string, { n: number; p: number }> = {}
  for (const t of trades) {
    byEngine[t.engine] ??= { n: 0, p: 0 }
    byEngine[t.engine].n++
    byEngine[t.engine].p += t.pnl
  }

  return (
    <aside className="rail">
      <div className="panel">
        <div className="eyebrow">Recent trades <span className="cnt">{live ? 'live' : 'sample'}</span></div>
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
      </div>

      <div className="panel">
        <div className="eyebrow">Engine totals</div>
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
      </div>

      <div className="panel pos-panel">
        <div className="eyebrow">
          Active positions <span className="cnt" style={{ color: openPos.length ? 'var(--jade)' : 'var(--stone-2)', fontWeight: 600 }}>{openPos.length} OPEN</span>
        </div>
        <div className="pos-sub-hdr">
          {openPos.length ? `${spotOpen} spot · ${futOpen} futures · click to inspect` : 'no open positions right now'}
        </div>
        <div className="pos-chips-wrap">
          {openPos.length === 0 && (
            <span style={{ color: 'var(--stone-2)', fontFamily: 'var(--mono)', fontSize: 11 }}>all signal positions closed</span>
          )}
          {openPos.map(p => {
            const base = (p.symbol || '').replace(/-USDT$/i, '').replace(/USDT$/i, '')
            const isFut = p.book === 'futures'
            return (
              <span key={`${p.book}-${p.symbol}`} className={`p-chip ${isFut ? 'fut' : 'spot'}`}
                onClick={() => onSelectPair?.(p.symbol || `${base}-USDT`)}
                title={`entry ${p.entry_price ?? '?'} · ${p.book}`}>
                {base}{isFut ? '-FUT' : ''}
              </span>
            )
          })}
        </div>
      </div>

      <div className="panel orders-panel">
        <div className="eyebrow">
          Resting Orders <span className="cnt" style={{ color: 'var(--signal)', fontWeight: 600 }}>15 WORKING</span>
        </div>
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
      </div>

      <div className="panel" style={{ flex: 1 }}>
        <div className="eyebrow">Data source</div>
        <div className="eng-row" style={{ border: 'none' }}>
          <span className={`status ${live ? 'on' : 'gated'}`}>
            {live ? '● LIVE /trades endpoint' : '● SAMPLE — SSM tunnel down'}
          </span>
        </div>
      </div>
    </aside>
  )
}
