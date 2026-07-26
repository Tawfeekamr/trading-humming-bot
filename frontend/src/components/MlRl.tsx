import { REGIME } from '../lib/mock'

const HIST: Record<string, { r: string; w: number; lo?: boolean }[]> = {
  'ETH-USDT': [{ r: 'ranging', w: 6 }, { r: 'danger', w: 1 }, { r: 'ranging', w: 15 }],
  'BNB-USDT': [{ r: 'ranging', w: 9 }, { r: 'trending', w: 1 }, { r: 'ranging', w: 12 }],
  'DOGE-USDT': [{ r: 'ranging', w: 4 }, { r: 'trending', w: 17 }],
  'XRP-USDT': [{ r: 'ranging', w: 5, lo: true }, { r: 'ranging', w: 3 }, { r: 'danger', w: 1 }, { r: 'ranging', w: 11, lo: true }],
}
const GATE: { eng: string; items: [string, 'ok' | 'x'][] }[] = [
  { eng: 'trend', items: [['DOGE', 'ok'], ['BNB', 'x'], ['XRP', 'x']] },
  { eng: 'grid', items: [['ETH', 'ok'], ['BNB', 'ok'], ['XRP', 'ok'], ['DOGE', 'x']] },
]
const cap = (s: string) => s[0].toUpperCase() + s.slice(1)

export function MlRl() {
  return (
    <div className="scroll-wrap">
      <div className="page-head">
        <div><div className="eyebrow">Machine Learning</div><h1>ML &amp; RL — live status</h1></div>
        <span className="range-pill">from production logs · last 6h</span>
      </div>

      <section className="section-card live">
        <div className="section-h">
          <span className="dotl g" />ML Regime Classifier
          <span className="sub">RF · n=200 · isotonic-calibrated · trailing-24h now-cast</span>
          <span className="status-badge on">● ACTIVE</span>
        </div>
        <div className="ml-stats">
          <span><b>119</b> pushes / 6h</span>
          <span>every <b>180s</b></span>
          <span>pairs <b>4</b></span>
          <span>OOS acc <b>0.84</b> avg</span>
          <span style={{ color: 'var(--stone-2)' }}>pusher up <b style={{ color: 'var(--paper)' }}>7h</b></span>
        </div>

        <div className="tl-wrap">
          {Object.entries(HIST).map(([pair, segs]) => {
            const n = REGIME[pair]
            return (
              <div className="lane" key={pair}>
                <span className="lane-lbl">{pair.replace('-USDT', '')}</span>
                <div className="lane-track">
                  {segs.map((s, i) => <div key={i} className={`seg r-${s.r} ${s.lo ? 'lo' : ''}`} style={{ flex: s.w }} />)}
                </div>
                <span className="lane-now">
                  <span className={`r-${n?.label}`}>{n ? cap(n.label) : ''}</span> <b>{n?.confidence.toFixed(2)}</b>
                </span>
              </div>
            )
          })}
        </div>
        <div className="tl-axis"><span /><span className="ticks"><span>−6h</span><span>−4h</span><span>−2h</span><span>now</span></span><span /></div>
        <div className="tl-legend">
          <span className="li"><span className="sq" style={{ background: 'rgba(126,122,114,.5)' }} />ranging</span>
          <span className="li"><span className="sq" style={{ background: 'rgba(74,157,138,.85)' }} />trending</span>
          <span className="li"><span className="sq" style={{ background: 'rgba(194,82,63,.85)' }} />danger</span>
          <span className="note">each segment = one pushed label · faded = low confidence · ▎now</span>
        </div>

        <div className="gate">
          {GATE.map(g => (
            <div className="gate-group" key={g.eng}>
              <span className="eng">{g.eng}</span>
              {g.items.map(([p, st]) => <span key={p} className={`gchip ${st}`}>{st === 'x' ? '✕' : '✓'} {p}</span>)}
            </div>
          ))}
          <span className="gate-note">trend skips entries in Ranging/Danger · grid blocks on Trending/Danger — from current regime labels</span>
        </div>
      </section>

      <section className="section-card dead">
        <div className="section-h">
          <span className="dotl a" />RL Router · PPO
          <span className="sub">route capital · size positions · force flat</span>
          <span className="status-badge off">● DORMANT</span>
        </div>
        <div className="meter">
          <span className="m-lbl">activity</span>
          <span className="meter-bar"><span className="cur" /></span>
          <span className="m-val">0 decisions / 6h</span>
        </div>
        <div className="rl-reasons">
          <div className="r"><span className="ic x">✕</span><span>no <b>live_router</b> process running</span></div>
          <div className="r"><span className="ic x">✕</span><span>policy on disk still has <b>swing</b> action</span></div>
          <div className="r"><span className="ic s">↻</span><span>retrain PPO without swing → engage</span></div>
          <div className="r"><span className="ic g">✓</span><span>safe: <b>engine_known</b> ignores unknown routing</span></div>
        </div>
      </section>
    </div>
  )
}
