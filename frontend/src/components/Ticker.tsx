import { useEffect, useState } from 'react'
import { baseOf, fmtPct, pairToSymbol } from '../lib/format'

type Tick = { px: number; chg: number }

const DEFAULT_PRICES: Record<string, Tick> = {
  ethusdt: { px: 1880.80, chg: -0.42 },
  bnbusdt: { px: 571.53, chg: 1.15 },
  xrpusdt: { px: 1.0992, chg: 2.20 },
  dogeusdt: { px: 0.0734, chg: -0.85 },
}

function useBinanceTickers(symbols: string[]) {
  const [prices, setPrices] = useState<Record<string, Tick>>(() => {
    const init: Record<string, Tick> = {}
    symbols.forEach(s => {
      const k = pairToSymbol(s).toLowerCase()
      const def = DEFAULT_PRICES[k] ?? { px: 100, chg: 0 }
      init[s] = def
      init[k] = def
    })
    return init
  })
  const key = symbols.join(',')
  useEffect(() => {
    const streams = symbols.map(s => `${pairToSymbol(s).toLowerCase()}@ticker`).join('/')
    const ws = new WebSocket(`wss://stream.binance.com:9443/stream?streams=${streams}`)
    ws.onmessage = ev => {
      try {
        const d = JSON.parse(ev.data).data
        if (d && d.s) {
          const k = d.s.toLowerCase()
          setPrices(prev => ({
            ...prev,
            [d.s]: { px: +d.c, chg: +d.P },
            [k]: { px: +d.c, chg: +d.P },
          }))
        }
      } catch {
        // ignore parse error
      }
    }
    return () => ws.close()
  }, [key])
  return prices
}

export function Ticker({ symbols }: { symbols: string[] }) {
  const prices = useBinanceTickers(symbols)
  const items = symbols.map(sym => {
    const k = pairToSymbol(sym).toLowerCase()
    const t = prices[sym] ?? prices[k]
    const pxVal = t ? t.px : 100
    const px = pxVal >= 10 ? pxVal.toFixed(2) : pxVal.toFixed(4)
    const chg = t ? fmtPct(t.chg) : '+0.00%'
    const up = (t?.chg ?? 0) >= 0
    return { sym, px, chg, up }
  })
  // Repeat items 6 times to ensure seamless infinite marquee scrolling without blank gaps on wider screens
  const loop = [...items, ...items, ...items, ...items, ...items, ...items]
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
