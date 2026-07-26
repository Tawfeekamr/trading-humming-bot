import { useState } from 'react'
import { Masthead, type View } from './components/Masthead'
import { Desk } from './components/Desk'
import { Pnl } from './components/Pnl'
import { MlRl } from './components/MlRl'
import { Hypotheses } from './components/Hypotheses'
import { ErrorBoundary } from './components/ErrorBoundary'
import { TradeHistoryModal } from './components/TradeHistoryModal'
import { FocusOverlay } from './components/FocusOverlay'

// 15m candles balance intraday detail against enough history to show recent
// trade markers; the focus chart reuses the same feed.
const INTERVAL = '15m'

export default function App() {
  const [view, setView] = useState<View>('desk')
  const [showHistory, setShowHistory] = useState(false)
  const [appFocusPair, setAppFocusPair] = useState<string | null>(null)

  return (
    <div className="app">
      <Masthead view={view} setView={setView} />
      <div className={`view ${view === 'desk' ? 'active' : ''}`}>
        <ErrorBoundary label="Desk">
          <Desk interval={INTERVAL} onOpenHistory={() => setShowHistory(true)} />
        </ErrorBoundary>
      </div>
      <div className={`view ${view === 'pnl' ? 'active' : ''}`}>
        <ErrorBoundary label="P&L"><Pnl /></ErrorBoundary>
      </div>
      <div className={`view ${view === 'mlrl' ? 'active' : ''}`}>
        <ErrorBoundary label="ML/RL"><MlRl /></ErrorBoundary>
      </div>
      <div className={`view ${view === 'hypotheses' ? 'active' : ''}`}>
        <ErrorBoundary label="Hypotheses"><Hypotheses /></ErrorBoundary>
      </div>

      {/* Full Trade History & Audit Log Modal */}
      {showHistory && (
        <TradeHistoryModal
          onClose={() => setShowHistory(false)}
          onSelectPair={(pair) => setAppFocusPair(pair)}
        />
      )}

      {/* Focus Chart Overlay from Trade History Selection */}
      {appFocusPair && (
        <FocusOverlay
          pair={appFocusPair}
          interval={INTERVAL}
          onClose={() => setAppFocusPair(null)}
        />
      )}
    </div>
  )
}
