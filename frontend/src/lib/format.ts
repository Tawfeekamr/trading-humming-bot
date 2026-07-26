export const money = (v: number) =>
  (v >= 0 ? '+' : '−') + '$' + Math.abs(Math.round(v)).toLocaleString()

export const px = (v: number | null | undefined) =>
  v == null ? '—' : v >= 10 ? v.toFixed(2) : v.toFixed(4)

export const pairToSymbol = (p: string) => p.replace('-', '') // ETH-USDT → ETHUSDT
export const baseOf = (p: string) => p.replace(/-USDT$/, '')

export const fmtPct = (v: number | null, digits = 2) =>
  v == null ? '' : `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`
