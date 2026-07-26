import { useState, useMemo, useEffect } from 'react'
import { useTrades } from '../lib/trades'
import { money, px } from '../lib/format'
import { ago } from '../lib/time'

export function TradeHistoryModal({
  onClose,
  onSelectPair,
}: {
  onClose: () => void
  onSelectPair: (pair: string) => void
}) {
  const { trades, live } = useTrades()

  const [pairFilter, setPairFilter] = useState('ALL')
  const [engineFilter, setEngineFilter] = useState('ALL')
  const [sideFilter, setSideFilter] = useState('ALL')
  const [outcomeFilter, setOutcomeFilter] = useState('ALL')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 12

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  // Extract unique pairs and engines for filter options
  const uniquePairs = useMemo(() => {
    const s = new Set<string>()
    trades.forEach(t => s.add(t.pair))
    return Array.from(s).sort()
  }, [trades])

  const ALL_CORE_ENGINES = ['signal', 'grid', 'trend']
  const uniqueEngines = useMemo(() => {
    const s = new Set<string>(ALL_CORE_ENGINES)
    trades.forEach(t => s.add(t.engine))
    return Array.from(s).sort()
  }, [trades])

  // Filtered dataset
  const filtered = useMemo(() => {
    return trades.filter(t => {
      if (pairFilter !== 'ALL' && t.pair !== pairFilter) return false
      if (engineFilter !== 'ALL' && t.engine !== engineFilter) return false
      if (sideFilter !== 'ALL' && (t.side || '').toLowerCase() !== sideFilter.toLowerCase()) return false
      if (outcomeFilter === 'WIN' && t.pnl < 0) return false
      if (outcomeFilter === 'LOSS' && t.pnl >= 0) return false
      if (search.trim()) {
        const q = search.toLowerCase()
        const text = `${t.pair} ${t.engine} ${t.side} ${t.exit_reason || ''} ${t.entry_reason || ''} ${t.entry_price ?? ''} ${t.exit_price ?? ''} ${t.pnl}`.toLowerCase()
        if (!text.includes(q)) return false
      }
      return true
    }).sort((a, b) => +new Date(b.timestamp) - +new Date(a.timestamp))
  }, [trades, pairFilter, engineFilter, sideFilter, outcomeFilter, search])

  // Summary Metrics for filtered view
  const metrics = useMemo(() => {
    const totalPnL = filtered.reduce((acc, t) => acc + t.pnl, 0)
    const wins = filtered.filter(t => t.pnl > 0)
    const losses = filtered.filter(t => t.pnl < 0)
    const winRate = filtered.length > 0 ? (wins.length / filtered.length) * 100 : 0
    const grossProfit = wins.reduce((acc, t) => acc + t.pnl, 0)
    const grossLoss = Math.abs(losses.reduce((acc, t) => acc + t.pnl, 0))
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? 99.9 : 0
    return { totalPnL, count: filtered.length, winRate, profitFactor }
  }, [filtered])

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const paginated = useMemo(() => {
    const start = (page - 1) * pageSize
    return filtered.slice(start, start + pageSize)
  }, [filtered, page])

  return (
    <div className="focus show modal-backdrop" style={{ zIndex: 1000 }}>
      <div className="hist-modal">
        {/* Header Bar */}
        <div className="hist-hdr">
          <div className="title-grp">
            <h2>TRADE HISTORY & AUDIT LOG</h2>
            <span className="cnt">{metrics.count} EXECUTIONS · {live ? 'LIVE' : 'SAMPLE'}</span>
          </div>
          <div className="esc" onClick={onClose}><kbd>Esc</kbd> Close</div>
        </div>

        {/* Filter Controls Bar */}
        <div className="hist-filters">
          <div className="filter-item">
            <label>Pair</label>
            <select value={pairFilter} onChange={e => { setPairFilter(e.target.value); setPage(1); }}>
              <option value="ALL">All Pairs ({uniquePairs.length})</option>
              {uniquePairs.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>

          <div className="filter-item">
            <label>Engine</label>
            <select value={engineFilter} onChange={e => { setEngineFilter(e.target.value); setPage(1); }}>
              <option value="ALL">All Engines ({uniqueEngines.length})</option>
              {uniqueEngines.map(e => <option key={e} value={e}>{e.toUpperCase()}</option>)}
            </select>
          </div>

          <div className="filter-item">
            <label>Side</label>
            <select value={sideFilter} onChange={e => { setSideFilter(e.target.value); setPage(1); }}>
              <option value="ALL">All Sides</option>
              <option value="buy">BUY (Long)</option>
              <option value="sell">SELL (Short)</option>
            </select>
          </div>

          <div className="filter-item">
            <label>Outcome</label>
            <select value={outcomeFilter} onChange={e => { setOutcomeFilter(e.target.value); setPage(1); }}>
              <option value="ALL">All Outcomes</option>
              <option value="WIN">Winners (+PnL)</option>
              <option value="LOSS">Losers (-PnL)</option>
            </select>
          </div>

          <div className="filter-item search-item">
            <label>Search</label>
            <input
              type="text"
              placeholder="Search pair, engine, exit reason..."
              value={search}
              onChange={e => { setSearch(e.target.value); setPage(1); }}
            />
          </div>
        </div>

        {/* KPI Summary Bar */}
        <div className="hist-kpi-bar">
          <div className="kpi-card">
            <span className="lbl">FILTERED P&amp;L</span>
            <span className={`val ${metrics.totalPnL >= 0 ? 'pnl-up' : 'pnl-dn'}`}>{money(metrics.totalPnL)}</span>
          </div>
          <div className="kpi-card">
            <span className="lbl">TRADES</span>
            <span className="val">{metrics.count}</span>
          </div>
          <div className="kpi-card">
            <span className="lbl">WIN RATE</span>
            <span className="val">{metrics.winRate.toFixed(1)}%</span>
          </div>
          <div className="kpi-card">
            <span className="lbl">PROFIT FACTOR</span>
            <span className="val">{metrics.profitFactor.toFixed(2)}x</span>
          </div>
        </div>

        {/* Trade Table */}
        <div className="hist-table-wrap">
          <table className="hist-table">
            <thead>
              <tr>
                <th>TIMESTAMP</th>
                <th>ENGINE</th>
                <th>PAIR</th>
                <th>SIDE</th>
                <th>QTY</th>
                <th>ENTRY PX</th>
                <th>EXIT PX</th>
                <th>REALIZED P&amp;L</th>
                <th>EXIT REASON</th>
                <th>ACTION</th>
              </tr>
            </thead>
            <tbody>
              {paginated.map(t => (
                <tr key={t.id} onClick={() => { onSelectPair(t.pair); onClose(); }}>
                  <td className="t">{ago(t.timestamp)}</td>
                  <td><span className="eng-chip">{t.engine}</span></td>
                  <td className="pair-cell">{t.pair}</td>
                  <td><span className={`side-tag side-${(t.side || '').toLowerCase()}`}>{(t.side || '').toUpperCase()}</span></td>
                  <td className="num">{t.quantity != null ? t.quantity.toFixed(3) : '—'}</td>
                  <td className="num">@{px(t.entry_price)}</td>
                  <td className="num">@{px(t.exit_price)}</td>
                  <td className={`num ${t.pnl >= 0 ? 'pnl-up' : 'pnl-dn'}`}>{money(t.pnl)}</td>
                  <td className="reason-cell">{t.exit_reason || '—'}</td>
                  <td>
                    <button className="inspect-btn">Inspect Chart ↗</button>
                  </td>
                </tr>
              ))}
              {paginated.length === 0 && (
                <tr>
                  <td colSpan={10} style={{ textAlign: 'center', padding: '30px', color: 'var(--stone-2)' }}>
                    No trades match the selected filter criteria.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Bar */}
        {totalPages > 1 && (
          <div className="hist-page-bar">
            <button disabled={page === 1} onClick={() => setPage(p => Math.max(1, p - 1))}>← Prev</button>
            <span>Page {page} of {totalPages}</span>
            <button disabled={page === totalPages} onClick={() => setPage(p => Math.min(totalPages, p + 1))}>Next →</button>
          </div>
        )}
      </div>
    </div>
  )
}
