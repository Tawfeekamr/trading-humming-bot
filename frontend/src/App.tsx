import { useState } from 'react'
import { Masthead, type View } from './components/Masthead'
import { Desk } from './components/Desk'
import { Pnl } from './components/Pnl'
import { MlRl } from './components/MlRl'

// 15m candles balance intraday detail against enough history to show recent
// trade markers; the focus chart reuses the same feed.
const INTERVAL = '15m'

export default function App() {
  const [view, setView] = useState<View>('desk')

  return (
    <div className="app">
      <Masthead view={view} setView={setView} />
      <div className={`view ${view === 'desk' ? 'active' : ''}`}><Desk interval={INTERVAL} /></div>
      <div className={`view ${view === 'pnl' ? 'active' : ''}`}><Pnl /></div>
      <div className={`view ${view === 'mlrl' ? 'active' : ''}`}><MlRl /></div>
    </div>
  )
}
