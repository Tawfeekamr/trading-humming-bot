import { useTrades } from '../lib/trades'
import { money } from '../lib/format'
import { ago } from '../lib/time'

export function Rail() {
  const { trades, live } = useTrades()
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
              <tr key={t.id}>
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
