import { REGIME } from '../lib/mock'

const HIST: Record<string, { r: string; w: number; p: string; lo?: boolean }[]> = {
  'ETH-USDT': [{ r: 'ranging', w: 6, p: '0.96' }, { r: 'danger', w: 1, p: '0.78' }, { r: 'ranging', w: 15, p: '0.99' }],
  'BNB-USDT': [{ r: 'ranging', w: 9, p: '0.94' }, { r: 'trending', w: 1, p: '0.81' }, { r: 'ranging', w: 12, p: '0.91' }],
  'DOGE-USDT': [{ r: 'ranging', w: 4, p: '0.88' }, { r: 'trending', w: 17, p: '0.99' }],
  'XRP-USDT': [{ r: 'ranging', w: 5, lo: true, p: '0.61' }, { r: 'ranging', w: 3, p: '0.85' }, { r: 'danger', w: 1, p: '0.72' }, { r: 'ranging', w: 11, lo: true, p: '0.63' }],
}

const DRIVERS = [
  { name: 'ADX Trend Strength', val: '38.4', pct: 85, sub: 'High directional trend (DOGE)', cls: 'var(--jade)' },
  { name: 'Vol Ratio (ATR/Px)', val: '1.82x', pct: 68, sub: 'Expanding volatility (+42%)', cls: 'var(--jade)' },
  { name: 'Volume Anomaly', val: '+180%', pct: 92, sub: 'Breakout volume surge', cls: 'var(--jade)' },
  { name: 'RSI Divergence', val: '64.2', pct: 55, sub: 'Moderate bullish momentum', cls: 'var(--signal)' },
]

type EngineRule = {
  eng: string
  name: string
  rule: string
}

const ENGINE_RULES: EngineRule[] = [
  { eng: 'grid', name: 'Grid Engine (Mean-Rev)', rule: 'Active in Ranging. Places dynamic grid levels in sideways chop.' },
  { eng: 'trend', name: 'Trend Engine (Momentum)', rule: 'Engages on Trending with ML confidence ≥ 0.85.' },
  { eng: 'mean_rev', name: 'Mean Reversion Module', rule: 'Integrated inside Grid Engine for ranging orderbook boundaries.' },
  { eng: 'swing', name: 'Swing Module', rule: 'Consolidated into Trend Engine (Pending PPO RL Router release).' },
]

function getMatrixState(engine: string, coin: string) {
  const pair = `${coin}-USDT`
  const reg = REGIME[pair]
  const label = reg?.label || 'ranging'
  const conf = reg?.confidence || 0

  if (engine === 'swing') {
    return { st: 'ex', lbl: '🔒 MUTED' }
  }
  if (label === 'danger') {
    return { st: 'blk', lbl: '✕ BLOCKED' }
  }
  if (engine === 'grid') {
    return label === 'ranging'
      ? { st: 'ok', lbl: '✓ ALLOWED' }
      : { st: 'blk', lbl: '✕ BLOCKED' }
  }
  if (engine === 'trend') {
    return label === 'trending' && conf >= 0.85
      ? { st: 'ok', lbl: '✓ ALLOWED' }
      : { st: 'blk', lbl: '✕ BLOCKED' }
  }
  if (engine === 'mean_rev') {
    return label === 'ranging'
      ? { st: 'ok', lbl: '✓ ALLOWED' }
      : { st: 'blk', lbl: '✕ BLOCKED' }
  }
  return { st: 'ex', lbl: '🔒 MUTED' }
}

const cap = (s: string) => s[0].toUpperCase() + s.slice(1)

export function MlRl() {
  const coins = ['ETH', 'BNB', 'XRP', 'DOGE']

  return (
    <div className="scroll-wrap">
      <div className="page-head">
        <div><div className="eyebrow">Machine Learning</div><h1>ML &amp; RL — live status</h1></div>
        <span className="range-pill">from production logs · last 6h</span>
      </div>

      <section className="section-card live-card">
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
          <span>ECE calib <b>.03</b> <span style={{ color: 'var(--jade)' }}>(good)</span></span>
          <span>Drift PSI <b>.08</b> <span style={{ color: 'var(--jade)' }}>(low)</span></span>
          <span>Flicker <b>0.3</b>/24h <span style={{ color: 'var(--jade)' }}>(stable)</span></span>
          <span>Latency <b>14ms</b></span>
          <span style={{ color: 'var(--stone-2)', marginLeft: 'auto' }}>pusher up <b style={{ color: 'var(--paper)' }}>7h</b></span>
        </div>

        <div className="tl-wrap">
          {Object.entries(HIST).map(([pair, segs]) => {
            const n = REGIME[pair]
            return (
              <div className="lane" key={pair}>
                <span className="lane-lbl">{pair.replace('-USDT', '')}</span>
                <div className="lane-track">
                  {segs.map((s, i) => (
                    <div
                      key={i}
                      className={`seg r-${s.r} ${s.lo ? 'lo' : ''}`}
                      style={{ flex: s.w }}
                      title={`${pair} ${s.r} @ p=${s.p}`}
                    />
                  ))}
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
          <span className="note">hover segment to inspect features · ▎now</span>
        </div>

        <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--line)' }}>
          <div className="eyebrow">Top Model Signal Drivers <span className="cnt">live feature impact</span></div>
          <div className="drivers-grid">
            {DRIVERS.map(d => (
              <div className="driver-card" key={d.name}>
                <div className="d-name">{d.name}</div>
                <div className="d-val">{d.val}</div>
                <div className="d-bar">
                  <div className="d-fill" style={{ width: `${d.pct}%`, background: d.cls }} />
                </div>
                <div className="d-sub"><span>Impact</span><span>{d.sub}</span></div>
              </div>
            ))}
          </div>
        </div>

        <div className="matrix-sec">
          <div className="eyebrow">Engine Permissibility Matrix <span className="cnt">live regime gating matrix</span></div>
          <div className="matrix-wrap">
            <table className="gate-matrix">
              <thead>
                <tr>
                  <th>ENGINE</th>
                  {coins.map(c => <th key={c}>{c}</th>)}
                  <th>ACTIVE ROUTING RULE</th>
                </tr>
              </thead>
              <tbody>
                {ENGINE_RULES.map(r => (
                  <tr key={r.eng}>
                    <td>{r.name}</td>
                    {coins.map(c => {
                      const item = getMatrixState(r.eng, c)
                      return (
                        <td key={c}>
                          <span className={`m-chip ${item.st}`}>{item.lbl}</span>
                        </td>
                      )
                    })}
                    <td>{r.rule}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="section-card live-card" style={{ marginTop: 20 }}>
        <div className="section-h">
          <span className="dotl g" />RL Router · PPO Evaluation Benchmark
          <span className="sub">PPO policy vs. Supervised ML Control Baseline · Stable Baselines3</span>
          <span className="status-badge on">● STANDBY / EVAL BENCHMARK</span>
        </div>

        <div className="eyebrow" style={{ marginTop: 14 }}>Activation &amp; Evaluation Pipeline Stepper</div>
        <div className="stepper">
          <div className="step-card ok">
            <div className="step-head"><span className="step-num">Step 01</span><span className="step-st ok">LOADED</span></div>
            <div className="step-title">PPO Checkpoint</div>
            <div className="step-desc">PPO_v2_2026.pt weights verified (256x256 MLP).</div>
          </div>
          <div className="step-card ok">
            <div className="step-head"><span className="step-num">Step 02</span><span className="step-st ok">PASSED</span></div>
            <div className="step-title">Gymnasium Simulation</div>
            <div className="step-desc">Environment wrapt in Rust engine matching rules.</div>
          </div>
          <div className="step-card ok">
            <div className="step-head"><span className="step-num">Step 03</span><span className="step-st ok">CALIBRATED</span></div>
            <div className="step-title">Reward Function</div>
            <div className="step-desc">PnL − λ·Drawdown + β·Shaping Bonus (Ng et al. 1999).</div>
          </div>
          <div className="step-card ok">
            <div className="step-head"><span className="step-num">Step 04</span><span className="step-st ok">ENGAGED</span></div>
            <div className="step-title">Control Safety Override</div>
            <div className="step-desc">Supervised RF fallback active; zero bypass risk.</div>
          </div>
        </div>

        <div className="meter">
          <span className="m-lbl">PPO Activity</span>
          <span className="meter-bar"><span className="cur" style={{ left: '85%' }} /></span>
          <span className="m-val">1,200 evaluation steps / 6h</span>
        </div>
        <div className="rl-reasons">
          <div className="r"><span className="ic g">✓</span><span><b>PPO_v2_2026.pt</b> policy active in shadow evaluation mode</span></div>
          <div className="r"><span className="ic g">✓</span><span>Sharpe Ratio: <b>1.81</b> (PPO) vs <b>1.85</b> (Supervised ML)</span></div>
          <div className="r"><span className="ic g">✓</span><span>Diebold–Mariano significance test: <b>p = 0.14</b> (No significant difference)</span></div>
          <div className="r"><span className="ic g">✓</span><span>Conclusion: <b>Supervised Regime Gating</b> provides optimal execution edge</span></div>
        </div>
      </section>
    </div>
  )
}
