import type { ReactNode } from 'react'

// A presenter walk-through of the dissertation, in reading order. Every figure
// is copied from the thesis; the section reference is shown on each step so
// the audience can find it in the document.

type Stat = { v: string; l: string }
type Step = { n: number; tag: string; title: string; ref: string; body: ReactNode; stats?: Stat[]; visual?: ReactNode }

/** One horizontal bar: value in %, filled or outline-only. */
function Bar({ label, pct, outline }: { label: string; pct: number; outline?: boolean }) {
  return (
    <div className="fx-row">
      <span className="fx-lbl">{label}</span>
      <span className="fx-track">
        <span className={`fx-fill ${outline ? 'outline' : ''}`} style={{ width: `${Math.max(pct, 0.8)}%` }} />
      </span>
      <span className="fx-val num">{pct.toFixed(1)}%</span>
    </div>
  )
}

const exposureVisual = (
  <div className="fx-chart" role="img" aria-label="ETH: PPO was in the market on 58.1% of bars but used 4.3% of its capital; Random Forest 21.1%; untrained network 49.2%; buy-and-hold 100%">
    <div className="fx-title">ETH · capital actually used (filled) vs reported time in market (outline)</div>
    <Bar label="PPO: time in market" pct={58.1} outline />
    <Bar label="PPO: capital used" pct={4.3} />
    <Bar label="Random Forest: capital used" pct={21.1} />
    <Bar label="Untrained network: capital used" pct={49.2} />
    <Bar label="Buy-and-hold" pct={100} />
    <div className="fx-note">BNB shows the same pattern: 54.2% time in market, 5.2% capital used.</div>
  </div>
)

const returnsVisual = (
  <table className="gate-matrix fx-table">
    <thead><tr><th>Policy</th><th>ETH total return</th><th>BNB total return</th><th>ETH median drawdown</th><th>BNB median drawdown</th></tr></thead>
    <tbody>
      <tr><td>Buy-and-hold</td><td className="num">+38.0%</td><td className="num">−4.3%</td><td className="num">19.5%</td><td className="num">19.8%</td></tr>
      <tr><td>Random Forest (gated)</td><td className="num">−21.2%</td><td className="num">−20.3%</td><td className="num">10.7%</td><td className="num">11.7%</td></tr>
      <tr><td>PPO (seed 42)</td><td className="num">−12.8%</td><td className="num">−25.9%</td><td className="num">3.2%</td><td className="num">5.3%</td></tr>
    </tbody>
  </table>
)

const STEPS: Step[] = [
  {
    n: 1, tag: 'Question', title: 'Can an RL agent route between trading engines better than a supervised model?', ref: 'Section 1.4',
    body: <>The system runs a <b>grid</b> engine for sideways markets and a <b>trend</b> engine for trending ones. The study asks whether a <b>PPO</b> reinforcement-learning agent picks between them better than a <b>calibrated Random Forest</b> regime classifier, on risk-adjusted return and maximum drawdown.</>,
  },
  {
    n: 2, tag: 'Method', title: 'One shared environment, a strict walk-forward test', ref: 'Chapter 3',
    body: <>Both policies trade the same engines under the same fill and fee rules. Each asset is tested on <b>six chronological folds</b> (4,320 training bars, 720 test bars, a 70-bar gap between them), with public Binance data fixed to end on 5 July 2026. Drawdown is measured within each fold, never across the gaps.</>,
    stats: [{ v: '2', l: 'assets (ETH, BNB)' }, { v: '6', l: 'folds per asset' }, { v: '5', l: 'seeds on folds 1 and 4' }],
  },
  {
    n: 3, tag: 'Result 1', title: 'No policy beat simply holding', ref: 'Section 4.2',
    body: <>Neither routed policy beat buy-and-hold on either asset, and PPO was not significantly different from the Random Forest (<b>p = 0.67</b> ETH, <b>0.74</b> BNB). The design is also too small to detect realistic differences: the minimum detectable effect is <b>82–179 percentage points</b>.</>,
    visual: returnsVisual,
  },
  {
    n: 4, tag: 'Result 2', title: 'Lower drawdown came from holding less, not from skill', ref: 'Section 4.1',
    body: <>PPO's drawdowns look much better than the baseline's. But random strategies holding the same tiny amount of capital drew down <b>even less</b> than PPO. A policy that loses less because it holds less is not managing risk.</>,
    stats: [{ v: '3.2%', l: 'PPO median drawdown, ETH' }, { v: '10.7%', l: 'Random Forest, ETH' }],
  },
  {
    n: 5, tag: 'Result 3', title: 'The agent learned to stay out of the market', ref: 'Section 4.1',
    body: <>By the usual measure, time in market, PPO looked active. Measured by capital actually used, it was almost absent. It abstained <b>openly</b> (choosing FLAT on 42–46% of bars) and in a <b>concealed</b> way (choosing actions that count as active but deploy almost nothing). An untrained network in the same setup used about half its capital, so the abstention was learned.</>,
    visual: exposureVisual,
  },
  {
    n: 6, tag: 'Result 4', title: 'Under its own reward, doing nothing wins', ref: 'Sections 4.1 and 4.6',
    body: <>Scoring a policy that never trades with the same reward the agent was trained on: doing nothing scored higher than the trained agent in <b>19 of 20</b> seed-fold cells. The reward counted fees twice, so the agents were retrained with fees counted once. Doing nothing still won in <b>20 of 20</b>. The reward itself made abstention the best choice.</>,
    stats: [{ v: '19 / 20', l: 'do-nothing wins, original reward' }, { v: '20 / 20', l: 'do-nothing wins, fees counted once' }],
  },
  {
    n: 7, tag: 'Why it was hidden', title: 'Standard metrics did not show it', ref: 'Sections 3.7–3.8',
    body: <>The first evaluation reported a strong result that was later withdrawn. An audit found <b>eighteen evaluation defects</b>, such as joining separate test windows into one curve and relying on one seed, six of them only through independent review. Appendix A lists every defect and how it was found.</>,
  },
  {
    n: 8, tag: 'Conclusion', title: 'Check for abstention before claiming an RL result', ref: 'Chapters 5–6',
    body: <>The finding is specific to this reward (return against buy-and-hold with a drawdown penalty). It does not show that RL fails at trading in general. The practical lesson is six checks for any RL trading claim:</>,
    visual: (
      <ol className="fx-list">
        <li>Compare with a <b>do-nothing policy</b> under the same reward.</li>
        <li>Report the size of each <b>reward term</b>, not only the total.</li>
        <li>Match baselines on <b>capital actually used</b>, not time in market.</li>
        <li>Never join separate <b>test windows</b> into one curve.</li>
        <li>State the <b>minimum detectable effect</b> with any null result.</li>
        <li>Report results across <b>several seeds</b>, never one.</li>
      </ol>
    ),
  },
]

export function Findings() {
  return (
    <div className="scroll-wrap">
      <div className="page-head">
        <div>
          <div className="eyebrow">MSc AI Dissertation · Walk-through</div>
          <h1>Reward-induced abstention in an RL trading system</h1>
        </div>
        <span className="range-pill">Student ID 202358755</span>
      </div>

      <div className="fx-steps">
        {STEPS.map(s => (
          <section className="section-card fx-step" key={s.n}>
            <div className="fx-head">
              <span className="fx-n num">{String(s.n).padStart(2, '0')}</span>
              <span className="fx-tag">{s.tag}</span>
              <span className="fx-ref">{s.ref}</span>
            </div>
            <div className="fx-h">{s.title}</div>
            <p className="fx-body">{s.body}</p>
            {s.stats && (
              <div className="fx-stats">
                {s.stats.map(st => (
                  <div className="fx-stat" key={st.l}><span className="fx-sv num">{st.v}</span><span className="fx-sl">{st.l}</span></div>
                ))}
              </div>
            )}
            {s.visual}
          </section>
        ))}
      </div>
    </div>
  )
}
