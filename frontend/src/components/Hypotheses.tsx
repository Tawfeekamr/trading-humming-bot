import type { ReactNode } from 'react'

// Verdicts and figures are copied from the dissertation (Section 1.4 and
// Chapter 4). They describe the offline walk-forward evaluation, not the live
// paper account, so nothing here is computed from live trades.

type Verdict = 'NOT TESTED' | 'NOT SUPPORTED' | 'UPHELD, WEAK FORM'

type HypoCard = {
  id: string
  cat: 'System-level' | 'Comparison-level'
  title: string
  statement: string
  mathEq: ReactNode
  verdict: Verdict
  cls: 'info' | 'no' | 'warn'
  metricLbl: string
  metricVal: string
  metricSub: string
  explanation: string
}

const HYPOS: HypoCard[] = [
  {
    id: 'SH1',
    cat: 'System-level',
    title: 'Regime gating lowers drawdown',
    statement: 'The regime-gated hybrid framework exhibits lower maximum drawdown than standalone grid or trend strategies.',
    mathEq: <span><b>MDD</b><sub>gated</sub> &lt; min(<b>MDD</b><sub>grid</sub>, <b>MDD</b><sub>trend</sub>)</span>,
    verdict: 'NOT TESTED',
    cls: 'info',
    metricLbl: 'Comparison run',
    metricVal: 'None',
    metricSub: 'No standalone grid or trend run was evaluated',
    explanation: 'The drawdown differences that were measured (PPO against the baseline) are explained by how much capital each policy held (Section 4.1).',
  },
  {
    id: 'SH2',
    cat: 'System-level',
    title: 'BTC danger gate reduces exposure',
    statement: 'The BTC cross-asset risk gate reduces systemic exposure during DANGER states.',
    mathEq: <span><b>Exposure</b> = 0 for new buys when <b>Regime</b><sub>BTC</sub> = DANGER</span>,
    verdict: 'NOT TESTED',
    cls: 'info',
    metricLbl: 'Scope',
    metricVal: 'Out of scope',
    metricSub: 'The evaluation concentrated on the routing comparison',
    explanation: 'The gate exists in the live system, but its effect was not measured in this study.',
  },
  {
    id: 'SH3',
    cat: 'System-level',
    title: 'Confidence-weighted sizing',
    statement: 'Confidence-weighted sizing improves risk-adjusted return compared with fixed sizing.',
    mathEq: <span><b>Sharpe</b><sub>confidence-sized</sub> &gt; <b>Sharpe</b><sub>fixed</sub></span>,
    verdict: 'NOT TESTED',
    cls: 'info',
    metricLbl: 'Scope',
    metricVal: 'Out of scope',
    metricSub: 'Scoped out for the same reason as SH2',
    explanation: 'No sizing comparison was run.',
  },
  {
    id: 'CH1',
    cat: 'Comparison-level',
    title: 'RL beats the supervised baseline',
    statement: 'At least one RL agent achieves a statistically higher Sharpe ratio than the supervised baseline on walk-forward out-of-sample data.',
    mathEq: <span><b>Sharpe</b><sub>PPO</sub> &gt; <b>Sharpe</b><sub>RF</sub> (p &lt; 0.05)</span>,
    verdict: 'NOT SUPPORTED',
    cls: 'no',
    metricLbl: 'Paired return test',
    metricVal: 'p = 0.67 / 0.74',
    metricSub: 'ETH / BNB · neither policy beat buy-and-hold',
    explanation: 'No Sharpe-specific test was run, and the design is underpowered (Section 4.3).',
  },
  {
    id: 'CH2',
    cat: 'Comparison-level',
    title: 'RL lowers drawdown at regime changes',
    statement: 'RL agents exhibit lower maximum drawdown than the supervised baseline during regime transitions.',
    mathEq: <span><b>MDD</b><sub>transition, PPO</sub> &lt; <b>MDD</b><sub>transition, RF</sub></span>,
    verdict: 'NOT TESTED',
    cls: 'info',
    metricLbl: 'Whole-period median drawdown',
    metricVal: '3.2% vs 10.7%',
    metricSub: 'PPO vs baseline, ETH · not conditioned on transitions',
    explanation: 'PPO drew down less over the whole period, which matches its much lower exposure rather than skill at transitions.',
  },
  {
    id: 'CH3',
    cat: 'Comparison-level',
    title: 'A null result is a valid finding',
    statement: 'A non-significant result across all RL-versus-baseline comparisons is a valid finding that documents where supervised routing suffices.',
    mathEq: <span>H<sub>0</sub>: &mu;<sub>PPO</sub> = &mu;<sub>RF</sub></span>,
    verdict: 'UPHELD, WEAK FORM',
    cls: 'warn',
    metricLbl: 'Minimum detectable effect',
    metricVal: '81.7 / 179.3 pp',
    metricSub: 'ETH / BNB at 80% power, against observed 9.7 / −11.3 pp',
    explanation: 'The design cannot tell "equal" apart from differences far larger than those observed, so parity is not claimed.',
  },
]

export function Hypotheses() {
  return (
    <div className="scroll-wrap">
      <div className="page-head">
        <div>
          <div className="eyebrow">MSc AI Dissertation · Hypotheses</div>
          <h1>What was tested, and the verdicts</h1>
        </div>
        <span className="range-pill">Student ID 202358755</span>
      </div>

      <div className="ml-stats" style={{ marginBottom: 18 }}>
        <span>Assets <b>ETH, BNB</b></span>
        <span>Protocol <b>6 walk-forward folds per asset</b></span>
        <span>Policies <b>PPO vs calibrated Random Forest vs buy-and-hold</b></span>
        <span>Seeds <b>5 (folds 1 and 4)</b></span>
        <span style={{ color: 'var(--stone-2)', marginLeft: 'auto' }}>Source <b style={{ color: 'var(--paper)' }}>Section 1.4, Chapter 4</b></span>
      </div>

      <div className="hypo-grid">
        {HYPOS.map(h => (
          <div className={`hypo-card ${h.cls}`} key={h.id}>
            <div className="h-head">
              <span className="h-id">{h.id}</span>
              <span className="h-cat">{h.cat}</span>
              <span className={`h-badge ${h.cls}`}>{h.verdict}</span>
            </div>
            <div className="h-title">{h.title}</div>
            <div className="h-stmt">"{h.statement}"</div>

            <div className="h-eq-box">
              <div className="eq-label">Formulation</div>
              <div className="eq-code">{h.mathEq}</div>
            </div>

            <div className="h-metric-sec">
              <div className="m-left">
                <span className="m-lbl">{h.metricLbl}</span>
                <span className="m-val">{h.metricVal}</span>
              </div>
              <span className="m-sub">{h.metricSub}</span>
            </div>

            <div className="h-exp">{h.explanation}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
