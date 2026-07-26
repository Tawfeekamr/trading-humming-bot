import { useEffect } from 'react'
import { useBinanceKlines } from '../lib/binance'
import { useTrades } from '../lib/trades'
import { tradeMarkers } from '../lib/markers'
import { LwChart } from './LwChart'
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

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

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
        <LwChart candles={candles} markers={markers} />
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
              <div className="eyebrow">Latest trade <span className="cnt">{latest.engine}</span></div>
              <div className="tdetail">
                <div className="head">
                  <span className={`side-tag side-${latest.side === 'buy' ? 'long' : 'short'}`}>
                    {(latest.side ?? '').toUpperCase()}
                  </span>
                  <span className="num" style={{ color: 'var(--stone)', fontSize: 11 }}>{baseOf(pair)} · {latest.engine}</span>
                </div>
                <span className="k">ENTRY</span><span className="v">{px(latest.entry_price)}</span>
                <span className="k">EXIT</span>
                <span className="v" style={{ color: latest.exit_price != null ? 'var(--paper)' : 'var(--stone-2)' }}>
                  {px(latest.exit_price)}
                </span>
                <span className="k">QUANTITY</span><span className="v">{latest.quantity != null ? latest.quantity.toFixed(3) : '—'}</span>
                <span className="k">P&amp;L</span>
                <span className={`v ${latest.pnl >= 0 ? 'pnl-up' : 'pnl-dn'}`}>
                  {latest.exit_price != null ? money(latest.pnl) : '— open —'}
                </span>
                <span className="k">EXIT REASON</span><span className="v" style={{ color: 'var(--stone)' }}>{latest.exit_reason ?? '—'}</span>
              </div>
              <div className="lvl-legend">
                <span><i className="sw sw-entry" />Entry @ {px(latest.entry_price)}</span>
                {latest.exit_price != null && <span><i className="sw sw-tp" />Exit @ {px(latest.exit_price)}</span>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
