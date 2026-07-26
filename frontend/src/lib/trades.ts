import { useEffect, useState } from 'react'
import type { Trade } from './types'
import { mockTrades } from './mock'

type TradesResult = { trades: Trade[]; live: boolean }

/**
 * Polls `/api/v1/trades` (Vite-proxied to the bot's :3030 over the SSM tunnel).
 * Falls back to mock data if the backend is unreachable, so the UI is always
 * usable. `live` tells the UI whether it's showing real or sample data.
 */
export function useTrades(pair?: string): TradesResult {
  const [trades, setTrades] = useState<Trade[]>([])
  const [live, setLive] = useState(false)

  useEffect(() => {
    let alive = true
    const load = () => {
      const url = '/api/v1/trades?limit=1000' + (pair ? `&pair=${encodeURIComponent(pair)}` : '')
      fetch(url)
        .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((j: { trades?: Trade[] }) => {
          if (!alive) return
          setTrades(j.trades ?? [])
          setLive(true)
        })
        .catch(() => {
          if (!alive) return
          setTrades(mockTrades().filter(t => !pair || t.pair === pair))
          setLive(false)
        })
    }
    load()
    const id = setInterval(load, 15000)
    return () => { alive = false; clearInterval(id) }
  }, [pair])

  return { trades, live }
}
