import { useEffect, useRef, useState } from 'react'
import type { UTCTimestamp } from 'lightweight-charts'

export type Candle = {
  time: UTCTimestamp
  open: number
  high: number
  low: number
  close: number
}

/**
 * Live candlestick feed straight from Binance's public market-data stream.
 * No EC2 / tunnel needed — the chart is alive even with the bot unreachable.
 * Seeds 500 bars via REST, then streams `@kline` updates over WebSocket.
 */
export function useBinanceKlines(symbol: string, interval: string) {
  const [candles, setCandles] = useState<Candle[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let alive = true
    setCandles([])
    fetch(`https://api.binance.com/api/v3/klines?symbol=${symbol}&interval=${interval}&limit=500`)
      .then(r => r.json())
      .then((rows: unknown) => {
        if (!alive || !Array.isArray(rows)) return
        const cs: Candle[] = (rows as any[][]).map(k => ({
          time: Math.floor(k[0] / 1000) as UTCTimestamp,
          open: +k[1], high: +k[2], low: +k[3], close: +k[4],
        }))
        setCandles(cs)
        const ws = new WebSocket(`wss://stream.binance.com:9443/ws/${symbol.toLowerCase()}@kline_${interval}`)
        wsRef.current = ws
        ws.onmessage = ev => {
          const k = JSON.parse(ev.data).k
          const bar: Candle = {
            time: Math.floor(k.t / 1000) as UTCTimestamp,
            open: +k.o, high: +k.h, low: +k.l, close: +k.c,
          }
          setCandles(prev => {
            const next = prev.slice()
            const i = next.findIndex(b => b.time === bar.time)
            if (i >= 0) next[i] = bar
            else { next.push(bar); if (next.length > 1000) next.shift() }
            return next
          })
        }
      })
      .catch(() => { /* blocked/offline — app runs with empty chart */ })
    return () => { alive = false; wsRef.current?.close() }
  }, [symbol, interval])

  const last = candles.length ? candles[candles.length - 1].close : null
  const first = candles.length ? candles[0].open : null
  const changePct = last != null && first != null ? ((last - first) / first) * 100 : null
  return { candles, last, changePct }
}
