import { REGIME } from '../lib/mock'

// Deterministic "why" feature snapshot per pair for the tooltip. The real
// values will come from the regime-pusher payload (or be recomputed from
// klines client-side) in a later pass — the shape is what matters here.
const WHY: Record<string, { ret: number; dd: number; adx: number }> = {
  'ETH-USDT': { ret: 0.4, dd: 1.2, adx: 18 },
  'BNB-USDT': { ret: -0.2, dd: 1.4, adx: 15 },
  'XRP-USDT': { ret: 1.1, dd: 1.0, adx: 20 },
  'DOGE-USDT': { ret: 2.4, dd: 0.3, adx: 31 },
}

export function RegimeChip({ pair }: { pair: string }) {
  const r = REGIME[pair]
  if (!r) return null
  const trending = r.label === 'trending'
  const w = WHY[pair] ?? { ret: 0.5, dd: 1.0, adx: 18 }
  const foot = trending ? 'up' : r.label === 'danger' ? 'dn' : 'rang'
  const rows: [string, string, string][] = [
    ['24h return', `${w.ret >= 0 ? '+' : ''}${w.ret.toFixed(1)}%`, trending ? '≥ 2% trend line' : '< 2% trend line'],
    ['Max drawdown', `−${w.dd.toFixed(1)}%`, '> −3% danger floor'],
    ['ADX', String(w.adx), trending ? 'strong directional' : 'no strong trend'],
    ['Confidence', r.confidence.toFixed(2), 'trailing 24h now-cast'],
  ]
  return (
    <span className={`chip r-${r.label}`}>
      <span className="b" />
      {r.label[0].toUpperCase() + r.label.slice(1)}
      <span className="tip">
        <div className="t-eyebrow">Why {r.label}</div>
        <table className="tip-t">
          <tbody>
            {rows.map(([k, v, d]) => (
              <tr key={k}><td className="tk-k">{k}</td><td className="num">{v}</td><td className="tk-d">{d}</td></tr>
            ))}
          </tbody>
        </table>
        <div className={`t-foot ${foot}`}>
          <span>→ {r.label}</span><span className="num">{r.confidence.toFixed(2)}</span>
        </div>
      </span>
    </span>
  )
}
