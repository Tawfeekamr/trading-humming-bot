use std::collections::VecDeque;

/// An O(1) amortized implementation of the Donchian Channel
/// using monotonic double-ended queues for rolling max/min.
pub struct DonchianChannel {
    period: usize,
    count: usize,
    // (index, value)
    max_deque: VecDeque<(usize, f64)>,
    min_deque: VecDeque<(usize, f64)>,
}

impl DonchianChannel {
    pub fn new(period: usize) -> Self {
        Self {
            period,
            count: 0,
            max_deque: VecDeque::with_capacity(period),
            min_deque: VecDeque::with_capacity(period),
        }
    }

    pub fn update(&mut self, high: f64, low: f64) {
        // Remove elements outside the window
        if let Some(&(idx, _)) = self.max_deque.front() {
            if self.count >= self.period && idx <= self.count - self.period {
                self.max_deque.pop_front();
            }
        }
        if let Some(&(idx, _)) = self.min_deque.front() {
            if self.count >= self.period && idx <= self.count - self.period {
                self.min_deque.pop_front();
            }
        }

        // Maintain monotonic property for max (descending)
        while let Some(&(_, val)) = self.max_deque.back() {
            if val <= high {
                self.max_deque.pop_back();
            } else {
                break;
            }
        }
        self.max_deque.push_back((self.count, high));

        // Maintain monotonic property for min (ascending)
        while let Some(&(_, val)) = self.min_deque.back() {
            if val >= low {
                self.min_deque.pop_back();
            } else {
                break;
            }
        }
        self.min_deque.push_back((self.count, low));

        self.count += 1;
    }

    pub fn is_initialized(&self) -> bool {
        self.count >= self.period
    }

    pub fn upper_band(&self) -> f64 {
        self.max_deque.front().map(|&(_, v)| v).unwrap_or(0.0)
    }

    pub fn lower_band(&self) -> f64 {
        self.min_deque.front().map(|&(_, v)| v).unwrap_or(0.0)
    }

    pub fn mid_band(&self) -> f64 {
        (self.upper_band() + self.lower_band()) / 2.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_donchian_channel() {
        let mut dc = DonchianChannel::new(3);
        assert!(!dc.is_initialized());

        dc.update(10.0, 5.0);
        dc.update(12.0, 6.0);
        assert!(!dc.is_initialized());

        dc.update(11.0, 4.0);
        assert!(dc.is_initialized());
        assert_eq!(dc.upper_band(), 12.0);
        assert_eq!(dc.lower_band(), 4.0);
        assert_eq!(dc.mid_band(), 8.0);

        // Window moves: [12(H), 6(L)], [11(H), 4(L)], [9(H), 3(L)] -> High=12, Low=3
        dc.update(9.0, 3.0);
        assert_eq!(dc.upper_band(), 12.0);
        assert_eq!(dc.lower_band(), 3.0);

        // Window moves: [11(H), 4(L)], [9(H), 3(L)], [15(H), 8(L)] -> High=15, Low=3
        dc.update(15.0, 8.0);
        assert_eq!(dc.upper_band(), 15.0);
        assert_eq!(dc.lower_band(), 3.0);

        // Window moves: [9(H), 3(L)], [15(H), 8(L)], [14(H), 7(L)] -> High=15, Low=3
        dc.update(14.0, 7.0);
        assert_eq!(dc.upper_band(), 15.0);
        assert_eq!(dc.lower_band(), 3.0);

        // Window moves: [15(H), 8(L)], [14(H), 7(L)], [14(H), 12(L)] -> High=15, Low=7
        dc.update(14.0, 12.0);
        assert_eq!(dc.upper_band(), 15.0);
        assert_eq!(dc.lower_band(), 7.0);
    }
}
