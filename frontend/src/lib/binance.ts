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
 * Live candlestick feed. Tries Binance first (REST seed + WS stream); if the
 * pair isn't listed on Binance (CRO, ASTER, GRAM, SKY, …), falls back to
 * Gate.io's public klines (REST, polled every 30s). Mock candles only as a last
 * resort so the chart is never blank.
 */
export function useBinanceKlines(symbol: string, interval: string) {
  const [candles, setCandles] = useState<Candle[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let alive = true
    let pollId: ReturnType<typeof setInterval> | undefined
    setCandles([])
    const cleanup = () => { alive = false; wsRef.current?.close(); if (pollId) clearInterval(pollId) }

    // Gate.io fallback for non-Binance alts.
    const gatePair = symbol.replace(/USDT$/i, '_USDT').toUpperCase()
    const fetchGate = async (): Promise<Candle[]> => {
      try {
        const r = await fetch(`https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=${gatePair}&interval=${interval}&limit=500`)
        const rows = await r.json()
        if (!Array.isArray(rows)) return []
        return rows.map((k: any[]) => ({
          time: Math.floor(Number(k[0])) as UTCTimestamp,
          open: +k[5], high: +k[3], low: +k[4], close: +k[2],
        })).sort((a, b) => (a.time as number) - (b.time as number))
      } catch { return [] }
    }
    const useGate = async () => {
      const g = await fetchGate()
      if (!alive) return
      if (g.length) {
        setCandles(g)
        pollId = setInterval(async () => { const g2 = await fetchGate(); if (alive && g2.length) setCandles(g2) }, 30000)
      } else {
        setCandles(mockCandles(symbol))
      }
    }

    fetch(`https://api.binance.com/api/v3/klines?symbol=${symbol}&interval=${interval}&limit=500`)
      .then(r => r.json())
      .then((rows: unknown) => {
        if (!alive) return
        if (!Array.isArray(rows) || !rows.length) { useGate(); return }
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
      .catch(() => { if (alive) useGate() })

    return cleanup
  }, [symbol, interval])

  const last = candles.length ? candles[candles.length - 1].close : null
  const first = candles.length ? candles[0].open : null
  const changePct = last != null && first != null ? ((last - first) / first) * 100 : null
  return { candles, last, changePct }
}

function mockCandles(symbol: string): Candle[] {
  const now = Math.floor(Date.now() / 1000) as UTCTimestamp
  const startPrices: Record<string, number> = {
    ETHUSDT: 1815, BNBUSDT: 575, XRPUSDT: 1.10, DOGEUSDT: 0.073,
  }
  let base = startPrices[symbol.toUpperCase()] ?? 100
  const out: Candle[] = []
  const n = 200
  const intervalSec = 300
  for (let i = n - 1; i >= 0; i--) {
    const time = (now - i * intervalSec) as UTCTimestamp
    const delta = (Math.sin(i * 0.15) + Math.cos(i * 0.08)) * (base * 0.004)
    const open = base
    const close = base + delta
    const high = Math.max(open, close) + Math.abs(delta) * 0.5 + 0.01
    const low = Math.min(open, close) - Math.abs(delta) * 0.5 - 0.01
    out.push({ time, open, high, low, close })
    base = close
  }
  return out
}
