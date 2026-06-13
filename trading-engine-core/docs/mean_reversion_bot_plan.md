# Mean Reversion Bot: Advanced Feature Plan & Architecture

This document outlines the architecture, logic, and implementation plan for adding a highly resilient Mean Reversion Strategy to the `trading-humming-bot` engine in Rust. 

> [!CAUTION]
> **CRITICAL REALITY CHECK**
> Mean Reversion is mathematically elegant but practically brutal. Before writing a single line of code, the realities of Taker Fees, Slippage, and HFT competition must be modeled. Tick-level execution is mandatory.

---

## 1. Stop Execution Architecture: Layered Failure Modes

There is no "guaranteed" stop during a flash crash. The design goal is to *choose your failure mode deliberately* by layering two stops that fail in different ways, so they don't fail at the same time.

### Layer 1: Resting Protective Order (Exchange Backstop)
This stop survives even if the bot process dies or the WebSocket drops mid-trade. We use a **Stop-Limit** with the limit set far below the trigger (e.g., trigger at -2%, limit at -6%).
*   It behaves almost like a market order but caps the absolute worst case.
*   **Crucial Discipline:** Pre-trade EV and sizing must use the -6% floor as the modeled loss, not the -2% trigger.

### Layer 2: Bot-Managed Active Stop (Primary)
The bot's primary exit mechanism in normal conditions. It is smarter, reacts to tick logic, and can bail early on "stalled momentum" signals.

### Double-Exit Arbitration (The Race Condition)
If both the bot exit and the resting stop fill, you flip from long to short in a crash. The two must arbitrate via the cancel result:
```rust
// Bot logic before sending market exit
match exchange.cancel(protective_order_id).await {
    Ok(Cancelled)      => send_market_exit().await, // We own the exit
    Err(AlreadyFilled) => mark_flat(),              // Backstop won the race, do NOT exit again
}
```

### Critical Subtleties
1.  **Place against *realized* fills:** A market order catching a knife partial-fills across a moving book. Average entry and quantity are uncertain until fills return. Use OCO/bracket orders atomically with entry if the exchange supports it, minimizing the vulnerable window.
2.  **No Dead-Man Switches on Stops:** Apply "cancel everything if I disconnect" timers only to *working/entry* orders. Protective stops must persist when the bot dies.

---

## 2. The Panic-vs-Repricing Classifier

You cannot classify a crash from price alone because the true driver is often *information* (hacks, depegs). The goal is to tilt the odds using microstructure fingerprints to distinguish a revertible overshoot from a genuine repricing.

### The Microstructure Features (Tick/Book Feed)
*   **Retrace Fraction:** How much of the impulse snaps back at T+N seconds. (High = Overshoot)
*   **Bid-Side Refill Rate:** Liquidations sweep the book, but makers refill it fast. Repricings see bids pull. Compare bid depth within *k* bps at T+N vs T0. (High = Overshoot)
*   **Sell-Flow Exhaustion:** A climax is a burst of market sells followed by a sharp drop-off. If aggressive selling persists, it's continuation. Track decaying signed taker volume. (High decay = Overshoot)
*   **Cross-Market Correlation:** If the whole market (BTC, alts, spot, perps) drops together, it's a macro repricing. If isolated, it's a liquidity event. (High correlation = SKIP)
*   **Liquidation/Funding Context:** A liquidation cascade with funding flipping negative is forced selling (reverts).

### The Scorecard (Transparent > ML)
Do not use Machine Learning for this rare-events classification to avoid overfitting. Use a transparent weighted scorecard:

```rust
struct ReversionSignal {
    retrace_frac: f64,      // high  -> overshoot
    bid_refill_ratio: f64,  // high  -> overshoot
    sell_flow_decay: f64,   // high  -> overshoot
    liq_cascade_score: f64, // high  -> overshoot (forced selling)
    cross_market_corr: f64, // high  -> REPRICING (skip)
}

enum Verdict { Trade { size_mult: f64 }, Skip }

fn classify(s: &ReversionSignal, cfg: &ClassifierCfg) -> Verdict {
    let score = cfg.w_retrace * s.retrace_frac
              + cfg.w_refill  * s.bid_refill_ratio
              + cfg.w_exhaust * s.sell_flow_decay
              + cfg.w_liq     * s.liq_cascade_score
              - cfg.w_corr    * s.cross_market_corr; // correlation subtracts

    if score < cfg.enter_threshold { return Verdict::Skip; }
    
    // Scale size by conviction above threshold, capped at 1.0
    let size_mult = ((score - cfg.enter_threshold) / cfg.full_size_margin).clamp(0.0, 1.0);
    Verdict::Trade { size_mult }
}
```

### Advanced Capabilities Unlocked
1.  **Dynamic Sizing:** `size_mult` replaces static capital allocation. High conviction trades get full size; ambiguous setups trade small.
2.  **Intelligent Early Exit:** Run this classifier continuously while in a trade. If continuation features light up mid-trade (book thins, sell flow persists), exit *ahead* of the hard stop.
3.  **Circuit Breaker:** Any move beyond extreme thresholds, or price decoupling across venues (early hack signature), forces a hard cooldown to avoid buying informational dips.

---

## 3. Concrete Next Steps

Do not wire this to live orders yet. 

**Phase 1 Execution:**
Build the `bid_refill_ratio` and `sell_flow_decay` measurement logic on live WebSocket tick data. 
Simply **log** the classifier's output and `size_mult` during real flush events (no trading). This will quickly prove whether the microstructure signals hold true on your specific pairs before risking capital.
