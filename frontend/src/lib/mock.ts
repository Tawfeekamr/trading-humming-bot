import type { Trade, RegimeState } from './types'

// Current ML regime per pair (matches what the regime-pusher is sending live).
export const REGIME: Record<string, RegimeState> = {
  'ETH-USDT': { label: 'ranging', confidence: 0.99 },
  'BNB-USDT': { label: 'ranging', confidence: 0.91 },
  'DOGE-USDT': { label: 'trending', confidence: 0.99 },
  'XRP-USDT': { label: 'ranging', confidence: 0.63 },
}

// Deterministic sample trades, stamped relative to "now" so they land on the
// live chart. Used ONLY when /api/v1/trades is unreachable (tunnel down) so the
// UI is always demonstrable; replaced by real trades the moment the backend is.
export function mockTrades(): Trade[] {
  const now = Date.now()
  const mk = (
    minsAgo: number, engine: Trade['engine'], pair: string, side: 'buy' | 'sell',
    entry: number, exit: number | null, qty: number, pnl: number, reason: string,
  ): Trade => ({
    id: minsAgo,
    timestamp: new Date(now - minsAgo * 60000).toISOString(),
    engine, pair, side, entry_price: entry, exit_price: exit, quantity: qty,
    pnl, exit_reason: reason, duration_mins: 30,
    // audit fields stay null in mock — real SL/TP/R arrive post-deploy.
    sl_price: null, tp_price: null, signal_score: null, regime_at_entry: null,
    entry_reason: null, fees: null, r_multiple: null, context_json: null,
  })
  return [
    mk(14, 'grid', 'ETH-USDT', 'buy', 1872.50, 1888.20, 1.75, 27.47, 'tp1'),
    mk(41, 'grid', 'BNB-USDT', 'sell', 574.80, 568.20, 6.53, 43.10, 'tp2'),
    mk(122, 'grid', 'ETH-USDT', 'buy', 1865.00, 1856.50, 1.90, -16.15, 'stop_loss'),
    mk(183, 'grid', 'BNB-USDT', 'buy', 566.50, 572.00, 5.20, 28.60, 'tp1'),
    mk(305, 'grid', 'XRP-USDT', 'buy', 1.0720, 1.1080, 412, 14.83, 'tp2'),
    mk(366, 'grid', 'ETH-USDT', 'sell', 1912.40, 1895.00, 2.07, 36.02, 'tp2'),
    mk(720, 'trend', 'BNB-USDT', 'buy', 564.10, 575.40, 4.10, 46.33, 'tp3'),
    mk(900, 'trend', 'DOGE-USDT', 'buy', 0.0695, 0.0745, 9000, 45.00, 'tp2'),
  ]
}

// OOS gate accuracy of the regime RF (held-out), for the ML/RL page.
export const OOS: { k: string; v: number }[] = [
  { k: 'BNB', v: 0.87 }, { k: 'ETH', v: 0.84 }, { k: 'XRP', v: 0.82 }, { k: 'DOGE', v: 0.78 },
]
