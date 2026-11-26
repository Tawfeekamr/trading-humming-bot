import { useStrategies } from '../lib/strategies'

// Live regime badge, read from the grid engine's status on /api/v1/strategies.
// When the ML model is what pauses the grid, the bot's reason names the regime
// ("ML regime=Trending (conf=0.88>=0.75)"); an active grid means its ranging
// gate passed. Nothing is shown when the bot is not connected.

const ML = /ML regime=(\w+)\s*\(conf=([\d.]+)/

export function RegimeChip({ pair }: { pair: string }) {
  const { rows, live } = useStrategies()
  if (!live) return null
  const grid = rows.find(r => r.name === 'grid' && r.pair === pair)
  if (!grid) return null

  const m = ML.exec(grid.details)
  let label: string
  let conf: string | null = null
  if (m) {
    label = m[1].toLowerCase()
    conf = Number(m[2]).toFixed(2)
  } else if (grid.state.toUpperCase() === 'ACTIVE') {
    label = 'ranging'
  } else {
    label = 'grid paused'
  }
  const cls = label === 'trending' || label === 'danger' ? label : 'ranging'
  const reason = grid.details.split('|')[0].trim()

  return (
    <span className={`chip r-${cls}`}>
      <span className="b" />
      {label[0].toUpperCase() + label.slice(1)}
      <span className="tip">
        <div className="t-eyebrow">Live grid gate · {pair}</div>
        <table className="tip-t">
          <tbody>
            <tr><td className="tk-k">Grid state</td><td className="num">{grid.state}</td><td className="tk-d">from the bot</td></tr>
            {conf && <tr><td className="tk-k">ML confidence</td><td className="num">{conf}</td><td className="tk-d">calibrated RF</td></tr>}
          </tbody>
        </table>
        <div className="t-foot rang"><span>{reason}</span></div>
      </span>
    </span>
  )
}
