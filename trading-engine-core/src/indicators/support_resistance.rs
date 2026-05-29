/// Support and resistance level detection using pivot points.
///
/// A high is resistance if it's the highest in a window of N bars on each side.
/// A low is support if it's the lowest in a window of N bars on each side.
/// Levels within merge_threshold_pct are merged and their strength incremented.
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LevelKind {
    Support,
    Resistance,
}

#[derive(Debug, Clone)]
pub struct Level {
    pub price: f64,
    pub kind: LevelKind,
    pub strength: u32,
    pub last_touch: i64,
}

#[derive(Debug, Clone)]
pub struct SupportResistance {
    levels: Vec<Level>,
    lookback: usize,
    merge_threshold_pct: f64,
    high_history: Vec<f64>,
    low_history: Vec<f64>,
    close_history: Vec<f64>,
}

impl SupportResistance {
    pub fn new(lookback: u32, merge_threshold_pct: f64) -> Self {
        Self {
            levels: Vec::new(),
            lookback: lookback as usize,
            merge_threshold_pct,
            high_history: Vec::with_capacity(128),
            low_history: Vec::with_capacity(128),
            close_history: Vec::with_capacity(128),
        }
    }

    pub fn update_bar(&mut self, _open: f64, high: f64, low: f64, close: f64, timestamp: i64) {
        self.high_history.push(high);
        self.low_history.push(low);
        self.close_history.push(close);

        let len = self.high_history.len();
        if len < self.lookback * 2 + 1 {
            return;
        }

        // Check if the bar at `lookback` positions ago is a pivot
        let pivot_idx = len - 1 - self.lookback;
        let pivot_high = self.high_history[pivot_idx];
        let pivot_low = self.low_history[pivot_idx];

        // For resistance: check if pivot_high is strictly greater than all others in window
        // Window extends from pivot_idx - lookback to pivot_idx + lookback (inclusive)
        let start_idx = pivot_idx.saturating_sub(self.lookback);
        let end_idx = (pivot_idx + self.lookback + 1).min(len);

        let is_resistance = self.high_history[start_idx..end_idx]
            .iter()
            .enumerate()
            .all(|(offset, &h)| {
                let actual_idx = start_idx + offset;
                actual_idx == pivot_idx || h < pivot_high
            });

        if is_resistance {
            self.add_or_merge_level(Level {
                price: pivot_high,
                kind: LevelKind::Resistance,
                strength: 1,
                last_touch: timestamp,
            });
        }

        // For support: check if pivot_low is strictly less than all others in window
        let is_support = self.low_history[start_idx..end_idx]
            .iter()
            .enumerate()
            .all(|(offset, &l)| {
                let actual_idx = start_idx + offset;
                actual_idx == pivot_idx || l > pivot_low
            });

        if is_support {
            self.add_or_merge_level(Level {
                price: pivot_low,
                kind: LevelKind::Support,
                strength: 1,
                last_touch: timestamp,
            });
        }

        if is_support {
            self.add_or_merge_level(Level {
                price: pivot_low,
                kind: LevelKind::Support,
                strength: 1,
                last_touch: timestamp,
            });
        }

        // Trim old history (keep last 200 bars)
        let max_history = 200;
        if self.high_history.len() > max_history {
            let drain = self.high_history.len() - max_history;
            self.high_history.drain(0..drain);
            self.low_history.drain(0..drain);
            self.close_history.drain(0..drain);
        }
    }

    fn add_or_merge_level(&mut self, new_level: Level) {
        let threshold = new_level.price * self.merge_threshold_pct / 100.0;

        if let Some(existing) = self.levels.iter_mut()
            .find(|l| l.kind == new_level.kind && (l.price - new_level.price).abs() <= threshold)
        {
            // Merge: update price to weighted average, increment strength
            existing.price = (existing.price * existing.strength as f64 + new_level.price)
                / (existing.strength as f64 + 1.0);
            existing.strength += 1;
            existing.last_touch = new_level.last_touch;
        } else {
            self.levels.push(new_level);
        }
    }

    pub fn near_support(&self, price: f64) -> bool {
        self.levels.iter()
            .filter(|l| l.kind == LevelKind::Support)
            .any(|l| (l.price - price).abs() / l.price < 0.005) // Within 0.5%
    }

    pub fn near_resistance(&self, price: f64) -> bool {
        self.levels.iter()
            .filter(|l| l.kind == LevelKind::Resistance)
            .any(|l| (l.price - price).abs() / l.price < 0.005)
    }

    pub fn get_levels(&self) -> &[Level] {
        &self.levels
    }
}
