import { useEffect, useState } from 'react'

/** Mirrors the Rust StrategyStatus served by `/api/v1/strategies`. */
export type StrategyRow = {
  name: string
  pair: string
  state: string
  pnl: number
  open_orders: number
  details: string
}

type Result = { rows: StrategyRow[]; live: boolean }

/**
 * Polls `/api/v1/strategies` (Vite-proxied to the bot's :3030 over the SSM
 * tunnel). Unlike trades there is NO sample fallback: engine state is shown
 * only when it is real, and `live` is false otherwise.
 */
export function useStrategies(): Result {
  const [rows, setRows] = useState<StrategyRow[]>([])
  const [live, setLive] = useState(false)

  useEffect(() => {
    let alive = true
    const load = () => {
      fetch('/api/v1/strategies')
        .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((j: StrategyRow[]) => {
          if (!alive) return
          setRows(Array.isArray(j) ? j : [])
          setLive(true)
        })
        .catch(() => {
          if (!alive) return
          setRows([])
          setLive(false)
        })
    }
    load()
    const id = setInterval(load, 15000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  return { rows, live }
}
