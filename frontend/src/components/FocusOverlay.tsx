import { useEffect, useMemo } from 'react'
import { useBinanceKlines } from '../lib/binance'
import { useTrades } from '../lib/trades'
import { tradeMarkers } from '../lib/markers'
import { LwChart, type PriceLine } from './LwChart'
import { RegimeChip } from './RegimeChip'
import { baseOf, pairToSymbol, px, fmtPct, money } from '../lib/format'
import { ago } from '../lib/time'

export function FocusOverlay({ pair, interval, onClose }: {
  pair: string
  interval: string
  onClose: () => void
}) {
  const { candles, last, changePct } = useBinanceKlines(pairToSymbol(pair), interval)
  const { trades, live } = useTrades(pair)
  const markers = tradeMarkers(trades)
  const up = (changePct ?? 0) >= 0
  const sorted = [...trades].sort((a, b) => +new Date(b.timestamp) - +new Date(a.timestamp))
  const latest = sorted[0]

  // Real levels only — ENTRY + the bot's actual SL / TP / EXIT (when the audit
  // migration has populated them). Old trades (null) render no level lines.
  // No more fabricated percentages.
  const priceLines: PriceLine[] = useMemo(() => {
    if (!latest || latest.entry_price == null) return []
    const out: PriceLine[] = [{ price: latest.entry_price, color: '#E8A33D', title: 'ENTRY' }]
    if (latest.sl_price != null) out.push({ price: latest.sl_price, color: '#C2523F', title: 'STOP LOSS', dashed: true })
    if (latest.tp_price != null) out.push({ price: latest.tp_price, color: '#4A9D8A', title: 'TAKE PROFIT', dashed: true })
    if (latest.exit_price != null) {
      out.push({ price: latest.exit_price, color: '#3498DB', title: `FILLED (${(latest.exit_reason || 'EXIT').toUpperCase()})`, dashed: true })
    }
    return out
  }, [latest])

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  // Parse the engine-specific context blob (signal TP ladder + raw message, etc.)
  const ctx = useMemo(() => {
    if (!latest?.context_json) return null
    try { return JSON.parse(latest.context_json) as Record<string, unknown> } catch { return null }
  }, [latest])

  return (
    <div className="focus show">
      <div className="focus-bar">
        <span className="pair">{baseOf(pair)}<span className="q">/USDT</span></span>
        <span className="px num">{last != null ? px(last) : '…'}</span>
        <span className={`chg num ${up ? 'up' : 'dn'}`}>{fmtPct(changePct)}</span>
        <RegimeChip pair={pair} />
        <div className="pills">
          <span className="pill on">grid</span>
          <span className="pill off">trend</span>
        </div>
        <div className="esc" onClick={onClose}><kbd>Esc</kbd> overview</div>
      </div>

      <div className="focus-body">
        <LwChart candles={candles} markers={markers} priceLines={priceLines} />
        <div className="focus-side">
          <div className="panel">
            <div className="eyebrow">Trade log <span className="cnt">{baseOf(pair)} · {live ? 'live' : 'sample'}</span></div>
            <table className="trades">
              <tbody>
                {sorted.map(t => (
                  <tr key={t.id}>
                    <td className="eng">{t.engine}</td>
                    <td className={t.side === 'buy' ? 'buy' : 'sell'}>{t.side}</td>
                    <td>{t.quantity != null ? t.quantity.toFixed(2) : '—'}</td>
                    <td>@ {px(t.entry_price)}</td>
                    <td className={t.pnl >= 0 ? 'buy' : 'sell'}>
                      {t.exit_price != null ? money(t.pnl) : 'open'}
                    </td>
                    <td className="t">{ago(t.timestamp)}</td>
                  </tr>
                ))}
                {sorted.length === 0 && (
                  <tr><td className="eng" colSpan={6} style={{ color: 'var(--stone-2)' }}>no trades for {baseOf(pair)}</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {latest && (
            <div className="panel">
              <div className="eyebrow">Latest trade <span className="cnt">{latest.engine}{latest.r_multiple != null ? ` · ${latest.r_multiple.toFixed(2)}R` : ''}</span></div>
              <div className="tdetail">
                <div className="head">
                  <span className={`side-tag side-${latest.side === 'buy' ? 'long' : 'short'}`}>
                    {(latest.side ?? '').toUpperCase()}
                  </span>
                  <span className="num" style={{ color: 'var(--stone)', fontSize: 11 }}>{baseOf(pair)} · {latest.engine}</span>
                </div>
                <span className="k">ENTRY</span><span className="v">{px(latest.entry_price)}</span>
                <span className="k">STOP LOSS</span>
                <span className="v" style={{ color: latest.sl_price != null ? 'var(--ox)' : 'var(--stone-2)' }}>{px(latest.sl_price)}</span>
                <span className="k">TAKE PROFIT</span>
                <span className="v" style={{ color: latest.tp_price != null ? 'var(--jade)' : 'var(--stone-2)' }}>{px(latest.tp_price)}</span>
                <span className="k">EXIT</span>
                <span className="v" style={{ color: latest.exit_price != null ? '#3498DB' : 'var(--stone-2)' }}>{px(latest.exit_price)}</span>
                <span className="k">QUANTITY</span><span className="v">{latest.quantity != null ? latest.quantity.toFixed(3) : '—'}</span>
                <span className="k">P&amp;L</span>
                <span className={`v ${latest.pnl >= 0 ? 'pnl-up' : 'pnl-dn'}`}>
                  {latest.exit_price != null ? money(latest.pnl) : '— open —'}
                </span>
                {latest.fees != null && (<><span className="k">FEES</span><span className="v" style={{ color: 'var(--stone)' }}>{money(-latest.fees)}</span></>)}
                <span className="k">EXIT REASON</span><span className="v" style={{ color: 'var(--stone)' }}>{latest.exit_reason ?? '—'}</span>
                {latest.entry_reason && (<><span className="k">ENTRY REASON</span><span className="v" style={{ color: 'var(--stone)' }}>{latest.entry_reason}</span></>)}
                {latest.regime_at_entry && (<><span className="k">REGIME @ ENTRY</span><span className="v" style={{ color: 'var(--stone)' }}>{latest.regime_at_entry}</span></>)}
              </div>
              <div className="lvl-legend">
                <span><i className="sw sw-entry" />Entry {px(latest.entry_price)}</span>
                {latest.sl_price != null && <span><i className="sw sw-sl" />SL {px(latest.sl_price)}</span>}
                {latest.tp_price != null && <span><i className="sw sw-tp" />TP {px(latest.tp_price)}</span>}
              </div>
            </div>
          )}

          {ctx && (
            <div className="panel">
              <div className="eyebrow">Trade context <span className="cnt">audit blob</span></div>
              <table className="trades">
                <tbody>
                  {Array.isArray(ctx.take_profits) && (
                    <tr><td className="eng">TP ladder</td><td>{(ctx.take_profits as number[]).map((t, i) => `TP${i + 1}:${px(t)}`).join('  ')}</td></tr>
                  )}
                  {Array.isArray(ctx.tp_hits) && (
                    <tr><td className="eng">TP hits</td><td>{(ctx.tp_hits as boolean[]).map((h, i) => `TP${i + 1}:${h ? '✓' : '—'}`).join('  ')}</td></tr>
                  )}
                  {typeof ctx.channel === 'string' && (<tr><td className="eng">channel</td><td>{ctx.channel}</td></tr>)}
                  {typeof ctx.level === 'string' && (<tr><td className="eng">grid level</td><td>{ctx.level}</td></tr>)}
                  {typeof ctx.raw_message === 'string' && ctx.raw_message && (
                    <tr><td className="eng">signal msg</td><td style={{ color: 'var(--stone-2)', maxWidth: 220, whiteSpace: 'normal' }}>{ctx.raw_message}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
