import { useEffect, useState } from 'react'
import { baseOf, fmtPct } from '../lib/format'

type Tick = { px: number; chg: number }

function useBinanceTickers(symbols: string[]) {
  const [prices, setPrices] = useState<Record<string, Tick>>({})
  const key = symbols.join(',')
  useEffect(() => {
    const streams = symbols.map(s => `${s.toLowerCase()}@ticker`).join('/')
    const ws = new WebSocket(`wss://stream.binance.com:9443/stream?streams=${streams}`)
    ws.onmessage = ev => {
      const d = JSON.parse(ev.data).data
      if (d && d.s) setPrices(prev => ({ ...prev, [d.s]: { px: +d.c, chg: +d.P } }))
    }
    return () => ws.close()
  }, [key])
  return prices
}

export function Ticker({ symbols }: { symbols: string[] }) {
  const prices = useBinanceTickers(symbols)
  const items = symbols.map(sym => {
    const t = prices[sym]
    const px = t ? (t.px >= 10 ? t.px.toFixed(2) : t.px.toFixed(4)) : '…'
    const chg = t ? fmtPct(t.chg) : ''
    const up = (t?.chg ?? 0) >= 0
    return { sym, px, chg, up }
  })
  // Duplicated once for a seamless CSS marquee loop.
  const loop = [...items, ...items]
  return (
    <div className="ticker">
      <div className="track">
        {loop.map((it, i) => (
          <span className="tk" key={i}>
            <span className="sym">{baseOf(it.sym)}<span style={{ color: 'var(--stone-2)' }}>/USDT</span></span>
            <span className="px">{it.px}</span>
            <span className={`chg ${it.up ? 'up' : 'dn'}`}>{it.chg}</span>
            <span className="sep">◐</span>
          </span>
        ))}
      </div>
    </div>
  )
}
