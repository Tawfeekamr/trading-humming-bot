import type { SeriesMarker, UTCTimestamp } from 'lightweight-charts'
import type { Trade } from './types'

/** Buy = green up-arrow below the bar; sell = red down-arrow above. */
export function tradeMarkers(trades: Trade[]): SeriesMarker<UTCTimestamp>[] {
  return trades
    .filter(t => t.entry_price != null && t.side)
    .map(t => ({
      time: Math.floor(new Date(t.timestamp).getTime() / 1000) as UTCTimestamp,
      position: t.side === 'buy' ? ('belowBar' as const) : ('aboveBar' as const),
      color: t.side === 'buy' ? '#4A9D8A' : '#C2523F',
      shape: t.side === 'buy' ? ('arrowUp' as const) : ('arrowDown' as const),
      text: t.engine,
      size: 1,
    }))
}
