use std::time::Instant;

pub struct CircuitBreaker {
    max_drawdown_pct: f64,
    daily_loss_limit_pct: f64,
    peak_equity: f64,
    start_of_day_equity: f64,
    halted: bool,
    cooldown_secs: u64,
    halted_at: Option<Instant>,
}

impl CircuitBreaker {
    pub fn new(max_drawdown_pct: f64, daily_loss_limit_pct: f64) -> Self {
        Self {
            max_drawdown_pct,
            daily_loss_limit_pct,
            peak_equity: 0.0,
            start_of_day_equity: 0.0,
            halted: false,
            cooldown_secs: 1800,
            halted_at: None,
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

    pub fn check(&mut self, current_equity: f64) -> bool {
        if self.peak_equity <= 0.0 { return false; }
        let drawdown_pct = (self.peak_equity - current_equity) / self.peak_equity * 100.0;
        if drawdown_pct >= self.max_drawdown_pct {
            self.halted = true;
            self.halted_at = Some(Instant::now());
            true
        } else {
            false
        }
    }

    pub fn check_daily(&mut self, current_equity: f64) -> bool {
        if self.start_of_day_equity <= 0.0 { return false; }
        let loss_pct = (self.start_of_day_equity - current_equity) / self.start_of_day_equity * 100.0;
        if loss_pct >= self.daily_loss_limit_pct {
            self.halted = true;
            self.halted_at = Some(Instant::now());
            true
        } else {
            false
        }
    }

    pub fn is_halted(&self) -> bool {
        self.halted && self.halted_at.map_or(true, |at| at.elapsed().as_secs() < self.cooldown_secs)
    }

    pub fn reset(&mut self, equity: f64) {
        self.halted = false;
        self.halted_at = None;
        self.peak_equity = equity;
    }

    /// Record a PnL event and check circuit breaker thresholds
    pub fn record_pnl(&mut self, pnl: f64, current_equity: f64) -> bool {
        self.update_peak(current_equity);
        self.check(current_equity) || self.check_daily(current_equity)
    }
}
