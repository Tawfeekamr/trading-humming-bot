/// Portfolio circuit breaker.
///
/// Halts new (non-reduce-only) entries when portfolio equity breaches either a
/// max-drawdown-from-peak or a daily-loss-from-start-of-day threshold. Once
/// tripped the halt **latches** — it stays in effect (and survives restarts via
/// `risk_state.json`) until cleared by:
///   * the UTC-midnight daily rollover (see `Engine::feed_breaker`), which
///     releases the halt against a fresh start-of-day baseline, or
///   * an explicit manual reset (clearing `risk_state.json` + restart).
///
/// Previously the halt expired after a 30-minute in-memory cooldown and was
/// silently re-armed on every restart — the persisted trip timestamp was
/// discarded on load (`set_halted_state` reset `halted_at` to `Instant::now()`).
/// The net effect was that a trip only blocked trading for ~30 minutes while the
/// `halted` flag in `risk_state.json` read `true` indefinitely. Both are fixed
/// here: the gate is the raw latched flag (no cooldown), and the trip timestamp
/// is a persisted wall-clock value restored verbatim on load.
pub struct CircuitBreaker {
    max_drawdown_pct: f64,
    daily_loss_limit_pct: f64,
    peak_equity: f64,
    start_of_day_equity: f64,
    halted: bool,
    /// Wall-clock unix seconds at which the breaker tripped. Persisted honestly
    /// across restarts (the old monotonic `Instant` was reset to `now` on load,
    /// throwing away the real trip time). `None` while not halted.
    halted_at_unix: Option<i64>,
    last_reset_date: String,
}

impl CircuitBreaker {
    pub fn new(max_drawdown_pct: f64, daily_loss_limit_pct: f64) -> Self {
        Self {
            max_drawdown_pct,
            daily_loss_limit_pct,
            peak_equity: 0.0,
            start_of_day_equity: 0.0,
            halted: false,
            halted_at_unix: None,
            last_reset_date: String::new(),
        }
    }

    pub fn set_peak_equity(&mut self, equity: f64) {
        self.peak_equity = equity;
    }

    pub fn peak_equity(&self) -> f64 {
        self.peak_equity
    }

    pub fn set_start_of_day_equity(&mut self, equity: f64) {
        self.start_of_day_equity = equity;
    }

    pub fn update_peak(&mut self, current_equity: f64) {
        if current_equity > self.peak_equity {
            self.peak_equity = current_equity;
        }
    }

    /// Check max-drawdown-from-peak. Latches the halt and returns true if the
    /// drawdown threshold is breached.
    pub fn check(&mut self, current_equity: f64) -> bool {
        if self.peak_equity <= 0.0 { return false; }
        let drawdown_pct = (self.peak_equity - current_equity) / self.peak_equity * 100.0;
        if drawdown_pct >= self.max_drawdown_pct {
            self.trip();
            true
        } else {
            false
        }
    }

    /// Check daily-loss-from-start-of-day. Latches the halt and returns true if
    /// the daily-loss threshold is breached.
    pub fn check_daily(&mut self, current_equity: f64) -> bool {
        if self.start_of_day_equity <= 0.0 { return false; }
        let loss_pct = (self.start_of_day_equity - current_equity) / self.start_of_day_equity * 100.0;
        if loss_pct >= self.daily_loss_limit_pct {
            self.trip();
            true
        } else {
            false
        }
    }

    /// Latch the halt, stamping the trip time. Idempotent: a re-trip while
    /// already halted preserves the original trip timestamp.
    fn trip(&mut self) {
        if !self.halted {
            self.halted_at_unix = Some(chrono::Utc::now().timestamp());
        }
        self.halted = true;
    }

    /// Release the halt. Used by the UTC-midnight daily rollover and by manual
    /// reset. The next `check`/`check_daily` tick re-evaluates fresh, so a
    /// still-breached drawdown re-latches immediately while a recovered (or
    /// newly-rebased) book stays open.
    pub fn clear_halt(&mut self) {
        self.halted = false;
        self.halted_at_unix = None;
    }

    /// Whether new entries are currently blocked. This is the raw latched flag —
    /// there is no time-based cooldown, so a trip blocks until `clear_halt`.
    pub fn is_halted(&self) -> bool {
        self.halted
    }

    /// Raw halt flag, for persistence and change-detection. Identical to
    /// `is_halted` now that the cooldown is gone; kept as a distinctly-named
    /// accessor so the save/change-detect call sites read clearly.
    pub fn is_halted_raw(&self) -> bool { self.halted }

    pub fn last_reset_date(&self) -> &str { &self.last_reset_date }
    pub fn set_last_reset_date(&mut self, d: String) { self.last_reset_date = d; }
    pub fn start_of_day_equity(&self) -> f64 { self.start_of_day_equity }

    pub fn halted_at_unix(&self) -> Option<i64> {
        self.halted_at_unix
    }

    /// Restore halt state from `risk_state.json`. The persisted trip timestamp
    /// is preserved verbatim (not reset to `now`), so a halt survives a restart.
    pub fn set_halted_state(&mut self, halted: bool, halted_at_unix: Option<i64>) {
        self.halted = halted;
        self.halted_at_unix = if halted { halted_at_unix } else { None };
    }

    /// Full manual reset: clear the halt and re-baseline peak equity.
    pub fn reset(&mut self, equity: f64) {
        self.halted = false;
        self.halted_at_unix = None;
        self.peak_equity = equity;
    }

    /// Record a PnL event and check circuit breaker thresholds
    pub fn record_pnl(&mut self, _pnl: f64, current_equity: f64) -> bool {
        self.update_peak(current_equity);
        self.check(current_equity) || self.check_daily(current_equity)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 10% max drawdown / 5% daily loss — matches `config/strategy.yaml`.
    fn cb() -> CircuitBreaker {
        let mut c = CircuitBreaker::new(10.0, 5.0);
        c.set_peak_equity(100_000.0);
        c.set_start_of_day_equity(100_000.0);
        c
    }

    #[test]
    fn halt_latches_and_does_not_expire() {
        // Regression: a trip used to clear itself after a 30-min cooldown.
        let mut c = cb();
        assert!(!c.is_halted());
        assert!(c.check(88_000.0)); // 12% drawdown from 100k peak → trips.
        assert!(c.is_halted(), "must be halted immediately after trip");
        // There is no longer any time-based expiry to wait out.
        assert!(c.is_halted(), "must latch — no cooldown expiry exists");
    }

    #[test]
    fn daily_loss_trips_and_latches() {
        let mut c = cb();
        assert!(c.check_daily(94_000.0)); // 6% daily loss from 100k → trips (>=5%).
        assert!(c.is_halted());
    }

    #[test]
    fn no_trip_inside_thresholds() {
        let mut c = cb();
        assert!(!c.check(95_000.0)); // 5% drawdown < 10%.
        assert!(!c.check_daily(97_000.0)); // 3% daily < 5%.
        assert!(!c.is_halted());
    }

    #[test]
    fn clear_halt_releases_then_re_evaluates() {
        let mut c = cb();
        c.check(88_000.0); // trip on drawdown
        assert!(c.is_halted());
        c.clear_halt();
        assert!(!c.is_halted(), "clear_halt must release the latch");
        // Equity is still 12% below peak, so the next check re-latches.
        assert!(c.check(88_000.0));
        assert!(c.is_halted(), "still-breached drawdown must re-latch on next tick");
    }

    #[test]
    fn clear_halt_lets_recovered_book_stay_open() {
        let mut c = cb();
        c.check_daily(94_000.0); // trip on 6% daily loss
        assert!(c.is_halted());
        c.clear_halt();
        // Equity has recovered to within thresholds → stays open.
        assert!(!c.check(99_000.0));
        assert!(!c.is_halted());
    }

    #[test]
    fn trip_stamps_unix_timestamp() {
        let mut c = cb();
        assert_eq!(c.halted_at_unix(), None);
        c.check(88_000.0);
        assert!(c.halted_at_unix().is_some(), "trip must stamp a wall-clock timestamp");
    }

    #[test]
    fn set_halted_state_preserves_trip_timestamp_across_restart() {
        // Regression: load used to reset halted_at to Instant::now(), discarding
        // the persisted trip time and silently re-arming a fresh 30-min window.
        let mut c = cb();
        c.set_halted_state(true, Some(1_785_415_286));
        assert!(c.is_halted());
        assert_eq!(
            c.halted_at_unix(),
            Some(1_785_415_286),
            "restored halt must keep the original persisted trip timestamp"
        );
    }

    #[test]
    fn set_halted_state_false_clears_timestamp() {
        let mut c = cb();
        c.set_halted_state(true, Some(1_785_415_286));
        c.set_halted_state(false, None);
        assert!(!c.is_halted());
        assert_eq!(c.halted_at_unix(), None);
    }
}
