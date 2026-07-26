import { useState } from 'react'
import { Ticker } from './Ticker'
import { CoinCard } from './CoinCard'
import { Rail } from './Rail'
import { FocusOverlay } from './FocusOverlay'

const PAIRS = ['ETH-USDT', 'BNB-USDT', 'XRP-USDT', 'DOGE-USDT']

export function Desk({ interval }: { interval: string }) {
  const [focusPair, setFocusPair] = useState<string | null>(null)

  return (
    <>
      <Ticker symbols={PAIRS} />
      <main className="workspace">
        <section className="grid">
          {PAIRS.map(p => (
            <CoinCard key={p} pair={p} interval={interval} onOpen={() => setFocusPair(p)} />
          ))}
        </section>
        <Rail />
      </main>
      {focusPair && (
        <FocusOverlay pair={focusPair} interval={interval} onClose={() => setFocusPair(null)} />
      )}
    </>
  )
}
