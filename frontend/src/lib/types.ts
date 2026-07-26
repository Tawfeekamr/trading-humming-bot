// Mirrors the Rust TradeRow from /api/v1/trades (src/strategy/trade_journal.rs).
export type Trade = {
  id: number
  timestamp: string // RFC3339, e.g. 2026-07-25T19:26:06+00:00
  engine: string    // grid | trend | swing | mr | signal
  pair: string      // "ETH-USDT"
  side: 'buy' | 'sell' | null
  entry_price: number | null
  exit_price: number | null
  quantity: number | null
  pnl: number
  exit_reason: string | null
  duration_mins: number | null
}

export type RegimeLabel = 'ranging' | 'trending' | 'danger'
export type RegimeState = { label: RegimeLabel; confidence: number }
