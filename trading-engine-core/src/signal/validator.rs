use crate::signal::types::ParsedSignal;
use crate::signal::types::SignalAction;
use std::collections::HashSet;

pub struct SignalValidator {
    min_rr_ratio: f64,
    max_sl_distance_pct: f64,
    max_entry_zone_pct: f64,
    min_quality_score: u8,
    available_pairs: HashSet<String>,
    blacklisted_pairs: HashSet<String>,
}

impl SignalValidator {
    pub fn new(config: &crate::config::SignalConfig) -> Self {
        Self {
            min_rr_ratio: config.min_rr_ratio,
            max_sl_distance_pct: config.max_sl_distance_pct,
            max_entry_zone_pct: config.max_entry_zone_pct,
            min_quality_score: config.min_quality_score,
            available_pairs: HashSet::new(),
            blacklisted_pairs: config.blacklisted_pairs.iter().cloned().collect(),
        }
    }

    pub fn set_available_pairs(&mut self, pairs: HashSet<String>) {
        self.available_pairs = pairs;
    }

    /// Validate a parsed signal. Returns (valid, rejection_reason).
    pub fn validate(&self, signal: &ParsedSignal) -> (bool, String) {
        let action = signal.signal_action();

        if action == SignalAction::NotASignal {
            return (false, "Not a trade signal".to_string());
        }

        // CLOSE/UPDATE signals don't need full validation
        if action != SignalAction::OpenLong {
            return (true, String::new());
        }

        // Blacklist check
        if let Some(pair) = &signal.pair {
            if self.blacklisted_pairs.contains(pair) {
                return (false, format!("Pair {} is blacklisted", pair));
            }
        }

        // Must have stop-loss
        if signal.stop_loss.is_none() {
            return (false, "No stop-loss specified".to_string());
        }

        // Must have at least one take-profit
        if signal.take_profits.is_empty() {
            return (false, "No take-profit target specified".to_string());
        }

        // Must have entry price
        if signal.entry_high.is_none() && signal.entry_low.is_none() {
            return (false, "No entry price specified".to_string());
        }

        let entry = signal.entry_high.unwrap_or(signal.entry_low.unwrap_or(0.0));
        if entry <= 0.0 {
            return (false, "Invalid entry price".to_string());
        }

        let sl = signal.stop_loss.unwrap_or(0.0);

        // Stop-loss must be below entry
        if sl >= entry {
            return (false, format!("SL {} >= entry {}", sl, entry));
        }

        // SL distance check
        let sl_distance_pct = (entry - sl) / entry * 100.0;
        if sl_distance_pct > self.max_sl_distance_pct {
            return (false, format!(
                "SL distance {:.1}% > max {}%",
                sl_distance_pct, self.max_sl_distance_pct
            ));
        }

        // Risk:reward ratio check (using TP3 if available, else TP2, else TP1)
        let risk = entry - sl;
        let tp_idx = std::cmp::min(2, signal.take_profits.len() - 1);
        let reward = signal.take_profits[tp_idx] - entry;
        if reward <= 0.0 {
            return (false, format!(
                "TP{} {} <= entry {}",
                tp_idx + 1, signal.take_profits[tp_idx], entry
            ));
        }
        let rr = reward / risk;
        if rr < self.min_rr_ratio {
            return (false, format!(
                "R:R {:.2} (vs TP{}) < min {}",
                rr, tp_idx + 1, self.min_rr_ratio
            ));
        }

        // Entry zone width check
        if let (Some(low), Some(high)) = (signal.entry_low, signal.entry_high) {
            if low > 0.0 {
                let zone_pct = (high - low) / low * 100.0;
                if zone_pct > self.max_entry_zone_pct {
                    return (false, format!(
                        "Entry zone {:.1}% too wide (max {}%)",
                        zone_pct, self.max_entry_zone_pct
                    ));
                }
            }
        }

        // AI quality score check
        if signal.quality_score < self.min_quality_score {
            return (false, format!(
                "Quality score {}/10 < min {} ({})",
                signal.quality_score, self.min_quality_score, signal.quality_reason
            ));
        }

        (true, String::new())
    }
}
