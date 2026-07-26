// Mirrors the Rust TradeRow from /api/v1/trades (src/strategy/trade_journal.rs).
export type Trade = {
  id: number
  timestamp: string // RFC3339, e.g. 2026-07-25T19:26:06+00:00
  engine: string    // grid | trend | signal
  pair: string      // "ETH-USDT"
  side: 'buy' | 'sell' | null
  entry_price: number | null
  exit_price: number | null
  quantity: number | null
  pnl: number
  exit_reason: string | null
  duration_mins: number | null
  // ── audit payload (additive; null for trades logged before the audit migration) ──
  sl_price: number | null
  tp_price: number | null
  signal_score: number | null
  regime_at_entry: string | null
  entry_reason: string | null
  fees: number | null
  r_multiple: number | null
  context_json: string | null
}

export type RegimeLabel = 'ranging' | 'trending' | 'danger'
export type RegimeState = { label: RegimeLabel; confidence: number }
