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
  })
  return [
    mk(14, 'grid', 'ETH-USDT', 'buy', 1819.68, 1821.40, 1.75, 3.0, 'tp1'),
    mk(41, 'grid', 'BNB-USDT', 'sell', 610.20, 612.10, 6.53, 14.0, 'tp2'),
    mk(122, 'grid', 'ETH-USDT', 'buy', 1805.25, 1803.10, 1.90, -2.0, 'stop_loss'),
    mk(183, 'grid', 'BNB-USDT', 'buy', 605.50, 611.00, 5.20, 9.0, 'tp1'),
    mk(305, 'grid', 'XRP-USDT', 'buy', 0.5110, null, 412, 0, 'open'),
    mk(366, 'grid', 'ETH-USDT', 'sell', 1790.82, 1795.00, 2.07, 5.0, 'tp1'),
    mk(720, 'trend', 'BNB-USDT', 'buy', 598.10, 612.40, 4.10, 58.5, 'tp3'),
    mk(900, 'trend', 'DOGE-USDT', 'buy', 0.1572, 0.1601, 9000, 26.1, 'tp2'),
  ]
}

// OOS gate accuracy of the regime RF (held-out), for the ML/RL page.
export const OOS: { k: string; v: number }[] = [
  { k: 'BNB', v: 0.87 }, { k: 'ETH', v: 0.84 }, { k: 'XRP', v: 0.82 }, { k: 'DOGE', v: 0.78 },
]
