import { useEffect, useState } from 'react'

export type Position = {
  symbol: string
  entry_price?: number | null
  raw_message?: string
  is_closed?: boolean
  channel_name?: string
  exit_reason?: string
  amount?: number
  amount_closed?: number
  [k: string]: unknown
}

/**
 * Open (and lingering) signal positions from `/api/v1/positions`
 * (data/signal_positions.json on the bot). Polled every 30s; empty when the
 * tunnel is down.
 */
export function usePositions(): Position[] {
  const [positions, setPositions] = useState<Position[]>([])
  useEffect(() => {
    let alive = true
    const load = () => fetch('/api/v1/positions')
      .then(r => (r.ok ? r.json() : Promise.reject()))
      .then((d: { positions?: Position[] }) => { if (alive) setPositions(d.positions ?? []) })
      .catch(() => { if (alive) setPositions([]) })
    load()
    const id = setInterval(load, 30000)
    return () => { alive = false; clearInterval(id) }
  }, [])
  return positions
}

export const findPosition = (positions: Position[], pair: string): Position | undefined => {
  const norm = (s: string) => s.replace(/-USDT$/i, '').replace(/-/g, '').toUpperCase()
  const p = norm(pair)
  return positions.find(pos => norm(pos.symbol || '') === p)
}

/** Parse a signal's raw message text for ENTRY / TARGETS / STOP LOSS. */
export type SignalLevels = { entry: number | null; stop: number | null; targets: number[] }
export function parseSignalLevels(raw?: string): SignalLevels {
  const out: SignalLevels = { entry: null, stop: null, targets: [] }
  if (!raw) return out
  const e = raw.match(/ENTRY:\s*([\d.]+)/i)
  const s = raw.match(/STOP\s*LOSS:\s*([\d.]+)/i)
  const t = raw.match(/TARGETS?:\s*([\d.\s-]+)/i)
  if (e) out.entry = parseFloat(e[1])
  if (s) out.stop = parseFloat(s[1])
  if (t) out.targets = (t[1].match(/[\d.]+/g) || []).map(parseFloat).filter(n => !isNaN(n))
  return out
}
