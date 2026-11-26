import { useStrategies } from '../lib/strategies'

// Every number on this page is either read live from the bot API or copied
// from the dissertation (Chapters 3-4). Nothing is simulated.

const ENGINES = ['grid', 'trend']

function stateChip(state: string) {
  const s = state.toUpperCase()
  // Grid reports Active / Paused / Disabled; trend reports POSITION / WAITING.
  if (s === 'ACTIVE' || s === 'POSITION') return { cls: 'ok', lbl: `✓ ${s}` }
  if (s === 'PAUSED' || s === 'DISABLED') return { cls: 'blk', lbl: `✕ ${s}` }
  return { cls: 'ex', lbl: `○ ${s}` }
}

export function MlRl() {
  const { rows, live } = useStrategies()
  const engineRows = rows
    .filter(r => ENGINES.includes(r.name))
    .sort((a, b) => a.name.localeCompare(b.name) || a.pair.localeCompare(b.pair))

  return (
    <div className="scroll-wrap">
      <div className="page-head">
        <div><div className="eyebrow">Machine Learning</div><h1>ML &amp; RL</h1></div>
        <span className="range-pill">{live ? 'engine states: live from the bot' : 'bot not connected'}</span>
      </div>

      <section className="section-card live-card">
        <div className="section-h">
          <span className="dotl g" />ML Regime Classifier
          <span className="sub">supervised baseline · runs in the live paper system</span>
          <span className={`status-badge ${live ? 'on' : 'off'}`}>{live ? '● LIVE' : '○ NOT CONNECTED'}</span>
        </div>
        <div className="ml-stats">
          <span>Model <b>Random Forest, 200 trees</b></span>
          <span>Calibration <b>isotonic</b></span>
          <span>Label <b>trailing-24h now-cast</b></span>
          <span>Regimes <b>ranging · trending · danger</b></span>
          <span>Updated every <b>180 s</b></span>
          <span>Out-of-sample accuracy <b>ETH 0.849 · BNB 0.870</b></span>
        </div>

        <div className="matrix-sec">
          <div className="eyebrow">Engine gating <span className="cnt">grid runs in ranging markets, trend in trending ones</span></div>
          {live ? (
            engineRows.length ? (
              <div className="matrix-wrap">
                <table className="gate-matrix">
                  <thead>
                    <tr><th>ENGINE</th><th>PAIR</th><th>STATE</th><th>REASON (FROM THE BOT)</th></tr>
                  </thead>
                  <tbody>
                    {engineRows.map(r => {
                      const c = stateChip(r.state)
                      return (
                        <tr key={`${r.name}-${r.pair}`}>
                          <td>{r.name === 'grid' ? 'Grid engine' : 'Trend engine'}</td>
                          <td>{r.pair}</td>
                          <td><span className={`m-chip ${c.cls}`}>{c.lbl}</span></td>
                          <td>{r.details || '—'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : <p className="muted-note">The bot returned no grid or trend engines.</p>
          ) : (
            <p className="muted-note">
              Live engine states appear here when the bot API is reachable (open the SSM port-forward
              to port 3030). No sample data is shown on this panel.
            </p>
          )}
        </div>
      </section>

      <section className="section-card dead-card">
        <div className="section-h">
          <span className="dotl a" />RL Router (PPO)
          <span className="sub">offline research experiment · Chapters 3–4</span>
          <span className="status-badge off">○ NOT DEPLOYED</span>
        </div>
        <div className="ml-stats">
          <span>Algorithm <b>PPO (Stable-Baselines3)</b></span>
          <span>Training <b>1M steps per fold</b></span>
          <span>Observation <b>25 values</b></span>
          <span>Actions <b>10: grid / trend / swing × 3 sizes, or FLAT</b></span>
          <span>Reward <b>return vs buy-and-hold − fees − 0.5 × drawdown increase</b></span>
          <span>Evaluation <b>6 walk-forward folds × ETH, BNB</b></span>
        </div>
        <div className="eyebrow" style={{ marginTop: 16 }}>What the evaluation found</div>
        <div className="rl-reasons">
          <div className="r"><span className="ic">1</span><span>No edge: the paired return test was not significant (<b>p = 0.67</b> ETH, <b>0.74</b> BNB), and neither policy beat buy-and-hold.</span></div>
          <div className="r"><span className="ic">2</span><span>The agent used only <b>4.3%</b> (ETH) and <b>5.2%</b> (BNB) of its capital, against <b>49%</b> for an untrained network.</span></div>
          <div className="r"><span className="ic">3</span><span>Doing nothing scored higher than the trained agent under its own reward in <b>19 of 20</b> seed-fold cells.</span></div>
          <div className="r"><span className="ic">4</span><span>With the double-counted fee removed, doing nothing still won in <b>20 of 20</b> cells.</span></div>
        </div>
        <p className="muted-note" style={{ marginTop: 14 }}>
          The PPO agent was never connected to live trading. See the Findings tab for the full walk-through.
        </p>
      </section>
    </div>
  )
}
