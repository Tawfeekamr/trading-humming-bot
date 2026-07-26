import { useEffect, useState } from 'react'
import type { UTCTimestamp } from 'lightweight-charts'

export type Point = { time: UTCTimestamp; value: number }

/**
 * Equal-weight buy-&-hold of `symbols` since `sinceMs`, priced from Binance
 * daily closes. Returns cumulative $ P&L vs `capital` (one point per day) —
 * the benchmark line on the P&L equity curve.
 */
export function useBuyHold(symbols: string[], sinceMs: number, capital: number) {
  const [pts, setPts] = useState<Point[]>([])
  const key = symbols.join(',') + '|' + sinceMs + '|' + capital
  useEffect(() => {
    let alive = true
    setPts([])
    Promise.all(symbols.map(sym =>
      fetch(`https://api.binance.com/api/v3/klines?symbol=${sym}&interval=1d&startTime=${sinceMs}&limit=400`)
        .then(r => r.json())
        .then((rows: unknown) => Array.isArray(rows) ? (rows as any[][]).map(k => ({
          t: Math.floor(k[0] / 1000) as UTCTimestamp, c: +k[4],
        })) : [])
    )).then(all => {
      if (!alive || all.length === 0 || all.some(a => a.length === 0)) return
      const len = Math.min(...all.map(a => a.length))
      const out: Point[] = []
      for (let i = 0; i < len; i++) {
        const avgRet = all.reduce((s, series) => s + series[i].c / series[0].c, 0) / all.length
        out.push({ time: all[0][i].t, value: capital * (avgRet - 1) })
      }
      setPts(out)
    }).catch(() => { /* offline — leave empty */ })
    return () => { alive = false }
  }, [key])
  return pts
}
