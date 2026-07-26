import { useBinanceKlines } from '../lib/binance'
import { useTrades } from '../lib/trades'
import { tradeMarkers } from '../lib/markers'
import { baseOf, pairToSymbol, px, fmtPct } from '../lib/format'
import { LwChart } from './LwChart'
import { RegimeChip } from './RegimeChip'

export function CoinCard({ pair, interval, onOpen }: {
  pair: string
  interval: string
  onOpen: () => void
}) {
  const { candles, last, changePct } = useBinanceKlines(pairToSymbol(pair), interval)
  const { trades } = useTrades(pair)
  const markers = tradeMarkers(trades)
  const up = (changePct ?? 0) >= 0
  const pnl = trades.reduce((a, t) => a + t.pnl, 0)

  return (
    <div className="card" onClick={onOpen}>
      <div className="card-head">
        <span className="pair">{baseOf(pair)}<span className="q">/USDT</span></span>
        <RegimeChip pair={pair} />
      </div>
      <LwChart candles={candles} markers={markers} compact />
      <div className="card-foot">
        <span className="px num">{last != null ? px(last) : '…'}</span>
        <span className={`chg num ${up ? 'up' : 'dn'}`}>{fmtPct(changePct)}</span>
        <span className="right">
          <span className="trades">{trades.length} trades</span>
          <span className={`pnl ${pnl >= 0 ? 'pnl-up' : 'pnl-dn'}`}>{(pnl >= 0 ? '+' : '') + pnl.toFixed(2)}</span>
        </span>
      </div>
    </div>
  )
}
