//! Pure order-book price-sanity filtering.
use std::collections::{HashMap, VecDeque};

use crate::config::PriceIntegrityConfig;
use crate::connector::types::OrderBook;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FilterDecision {
    Accept,
    SuspectNewVerify,
    HoldSuspect,
    HardReject,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VerifyResult {
    Confirmed,
    Denied,
    Unavailable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Status {
    Trusted,
    Suspect,
}

#[derive(Debug, Clone)]
struct PairState {
    last_good_mid: f64,
    last_good_book: OrderBook,
    status: Status,
    window: VecDeque<f64>,
    recover_count: u32,
    pending_suspect_book: Option<OrderBook>,
}

impl PairState {
    fn new(book: &OrderBook, mid: f64, capacity: usize) -> Self {
        let mut window = VecDeque::with_capacity(capacity);
        window.push_back(mid);
        Self {
            last_good_mid: mid,
            last_good_book: book.clone(),
            status: Status::Trusted,
            window,
            recover_count: 0,
            pending_suspect_book: None,
        }
    }

    fn push(&mut self, mid: f64, capacity: usize) {
        if capacity > 0 && self.window.len() >= capacity {
            self.window.pop_front();
        }
        if capacity > 0 {
            self.window.push_back(mid);
        }
    }

    fn stdev(&self) -> f64 {
        let count = self.window.len() as f64;
        if count < 2.0 {
            return 0.0;
        }
        let mean = self.window.iter().sum::<f64>() / count;
        let variance = self
            .window
            .iter()
            .map(|value| (value - mean).powi(2))
            .sum::<f64>()
            / count;
        variance.sqrt()
    }
}

pub struct PriceFilter {
    states: HashMap<String, PairState>,
}

impl Default for PriceFilter {
    fn default() -> Self {
        Self::new()
    }
}

impl PriceFilter {
    pub fn new() -> Self {
        Self {
            states: HashMap::new(),
        }
    }

    pub fn is_suspect(&self, symbol: &str) -> bool {
        self.states
            .get(symbol)
            .is_some_and(|state| state.status == Status::Suspect)
    }

    pub fn last_good(&self, symbol: &str) -> Option<f64> {
        self.states.get(symbol).map(|state| state.last_good_mid)
    }

    fn last_good_book(&self, symbol: &str) -> Option<&OrderBook> {
        self.states.get(symbol).map(|state| &state.last_good_book)
    }

    /// Observe one complete order book and classify its derived mid-price.
    ///
    /// Every level is validated before a mid is computed. A book with an empty
    /// side, an invalid price or quantity, or a crossed spread is never allowed
    /// to seed or alter per-symbol state.
    pub fn observe(
        &mut self,
        symbol: &str,
        book: &OrderBook,
        cfg: &PriceIntegrityConfig,
    ) -> FilterDecision {
        let Some(mid) = validated_mid(book) else {
            if let Some(state) = self.states.get_mut(symbol) {
                state.recover_count = 0;
            }
            return FilterDecision::HardReject;
        };

        let capacity = cfg.stdev_window as usize;
        let state = match self.states.get_mut(symbol) {
            Some(state) => state,
            None => {
                self.states
                    .insert(symbol.to_owned(), PairState::new(book, mid, capacity));
                return FilterDecision::Accept;
            }
        };

        let floor = cfg.min_deviation_pct / 100.0 * state.last_good_mid;
        let band = (cfg.stdev_k * state.stdev()).max(floor);
        let deviation = (mid - state.last_good_mid).abs();

        match state.status {
            Status::Trusted if deviation <= band => {
                state.last_good_mid = mid;
                state.last_good_book = book.clone();
                state.push(mid, capacity);
                FilterDecision::Accept
            }
            Status::Trusted => {
                state.status = Status::Suspect;
                state.recover_count = 0;
                state.pending_suspect_book = Some(book.clone());
                FilterDecision::SuspectNewVerify
            }
            Status::Suspect if deviation <= band => {
                state.recover_count = state.recover_count.saturating_add(1);
                if state.recover_count >= cfg.recover_consecutive_ticks {
                    state.status = Status::Trusted;
                    state.last_good_mid = mid;
                    state.last_good_book = book.clone();
                    state.push(mid, capacity);
                    state.recover_count = 0;
                    state.pending_suspect_book = None;
                    FilterDecision::Accept
                } else {
                    FilterDecision::HoldSuspect
                }
            }
            Status::Suspect => {
                state.recover_count = 0;
                FilterDecision::HoldSuspect
            }
        }
    }

    /// Apply the cross-source verification result for the in-flight suspect.
    pub fn resolve_verify(
        &mut self,
        symbol: &str,
        result: &VerifyResult,
        suspect_mid: f64,
        cfg: &PriceIntegrityConfig,
    ) {
        let Some(state) = self.states.get_mut(symbol) else {
            return;
        };
        if state.status != Status::Suspect {
            return;
        }

        match result {
            VerifyResult::Confirmed => {
                let Some(pending_book) = state.pending_suspect_book.as_ref() else {
                    return;
                };
                let Some(pending_mid) = validated_mid(pending_book) else {
                    return;
                };
                let scalar_matches_book = suspect_mid.is_finite()
                    && suspect_mid > 0.0
                    && (suspect_mid - pending_mid).abs()
                        <= 1e-9 * pending_mid.abs().max(suspect_mid.abs()).max(1.0);
                if !scalar_matches_book {
                    return;
                }

                state.status = Status::Trusted;
                state.last_good_mid = pending_mid;
                state.last_good_book = state.pending_suspect_book.take().expect("pending book checked");
                state.push(pending_mid, cfg.stdev_window as usize);
                state.recover_count = 0;
            }
            VerifyResult::Denied | VerifyResult::Unavailable => {}
        }
    }
}

/// Return the validated mid-price of a complete order book.
///
/// This is the same calculation used by [`PriceFilter::observe`], so callers
/// that launch verification cannot accidentally use an unchecked scalar mid.
pub fn validated_mid(book: &OrderBook) -> Option<f64> {
    if book.bids.is_empty() || book.asks.is_empty() {
        return None;
    }

    let best_bid = book
        .bids
        .iter()
        .try_fold(f64::NEG_INFINITY, |best, &(price, quantity)| {
            valid_level(price, quantity).then_some(best.max(price))
        })?;
    let best_ask = book
        .asks
        .iter()
        .try_fold(f64::INFINITY, |best, &(price, quantity)| {
            valid_level(price, quantity).then_some(best.min(price))
        })?;

    if best_bid >= best_ask {
        return None;
    }
    let mid = (best_bid + best_ask) / 2.0;
    (mid.is_finite() && mid > 0.0).then_some(mid)
}

fn valid_level(price: f64, quantity: f64) -> bool {
    price.is_finite() && price > 0.0 && quantity.is_finite() && quantity > 0.0
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::PriceIntegrityConfig;
    use crate::connector::types::OrderBook;

    fn cfg() -> PriceIntegrityConfig {
        PriceIntegrityConfig::default()
    }

    fn book(symbol: &str, mid: f64) -> OrderBook {
        OrderBook {
            symbol: symbol.to_owned(),
            bids: vec![(mid - 0.1, 1.0)],
            asks: vec![(mid + 0.1, 1.0)],
            timestamp: 1,
        }
    }

    #[test]
    fn warmup_first_book_is_accepted_and_seeds_last_good() {
        let mut filter = PriceFilter::new();
        let incoming = book("BNB-USDT", 580.0);

        assert_eq!(filter.observe("BNB-USDT", &incoming, &cfg()), FilterDecision::Accept);
        assert_eq!(filter.last_good("BNB-USDT"), Some(580.0));
        assert!(!filter.is_suspect("BNB-USDT"));
    }

    #[test]
    fn in_band_books_are_accepted() {
        let mut filter = PriceFilter::new();
        let config = cfg();

        for mid in [580.0, 580.1, 579.9, 580.2] {
            assert_eq!(filter.observe("BNB-USDT", &book("BNB-USDT", mid), &config), FilterDecision::Accept);
        }
    }

    #[test]
    fn huge_spike_transitions_to_suspect_and_keeps_last_good_book() {
        let mut filter = PriceFilter::new();
        let config = cfg();
        for _ in 0..4 {
            filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config);
        }

        let mut phantom = book("BNB-USDT", 497.0);
        phantom.timestamp = 99;
        assert_eq!(filter.observe("BNB-USDT", &phantom, &config), FilterDecision::SuspectNewVerify);
        assert!(filter.is_suspect("BNB-USDT"));
        assert_eq!(filter.last_good("BNB-USDT"), Some(580.0));
        assert_eq!(filter.last_good_book("BNB-USDT").unwrap().timestamp, 1);
    }

    #[test]
    fn while_suspect_out_of_band_books_hold_and_reset_recovery() {
        let mut filter = PriceFilter::new();
        let config = cfg();
        for _ in 0..4 {
            filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config);
        }
        filter.observe("BNB-USDT", &book("BNB-USDT", 497.0), &config);

        assert_eq!(filter.observe("BNB-USDT", &book("BNB-USDT", 496.0), &config), FilterDecision::HoldSuspect);
        assert!(filter.is_suspect("BNB-USDT"));
    }

    #[test]
    fn self_heal_clears_suspect_after_n_in_band_books() {
        let mut filter = PriceFilter::new();
        let config = cfg();
        for _ in 0..4 {
            filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config);
        }
        filter.observe("BNB-USDT", &book("BNB-USDT", 497.0), &config);

        assert_eq!(filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config), FilterDecision::HoldSuspect);
        assert_eq!(filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config), FilterDecision::HoldSuspect);
        assert!(filter.is_suspect("BNB-USDT"));
        assert_eq!(filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config), FilterDecision::Accept);
        assert!(!filter.is_suspect("BNB-USDT"));
    }

    #[test]
    fn resolve_verify_confirmed_accepts_a_real_move() {
        let mut filter = PriceFilter::new();
        let config = cfg();
        for _ in 0..4 {
            filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config);
        }
        filter.observe("BNB-USDT", &book("BNB-USDT", 596.0), &config);

        filter.resolve_verify("BNB-USDT", &VerifyResult::Confirmed, 596.0, &config);
        assert!(!filter.is_suspect("BNB-USDT"));
        assert_eq!(filter.last_good("BNB-USDT"), Some(596.0));
    }
    #[test]
    fn resolve_verify_rejects_a_mismatched_scalar_for_pending_book() {
        let mut filter = PriceFilter::new();
        let config = cfg();
        filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config);
        filter.observe("BNB-USDT", &book("BNB-USDT", 596.0), &config);

        filter.resolve_verify("BNB-USDT", &VerifyResult::Confirmed, 999.0, &config);
        assert!(filter.is_suspect("BNB-USDT"));
        assert_eq!(filter.last_good("BNB-USDT"), Some(580.0));
    }


    #[test]
    fn resolve_verify_denied_keeps_last_good_and_stays_suspect() {
        let mut filter = PriceFilter::new();
        let config = cfg();
        for _ in 0..4 {
            filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config);
        }
        filter.observe("BNB-USDT", &book("BNB-USDT", 497.0), &config);

        filter.resolve_verify("BNB-USDT", &VerifyResult::Denied, 497.0, &config);
        assert!(filter.is_suspect("BNB-USDT"));
        assert_eq!(filter.last_good("BNB-USDT"), Some(580.0));
    }

    #[test]
    fn resolve_verify_unavailable_keeps_last_good_and_stays_suspect() {
        let mut filter = PriceFilter::new();
        let config = cfg();
        filter.observe("ETH-USDT", &book("ETH-USDT", 3000.0), &config);
        filter.observe("ETH-USDT", &book("ETH-USDT", 2800.0), &config);

        filter.resolve_verify("ETH-USDT", &VerifyResult::Unavailable, 2800.0, &config);
        assert!(filter.is_suspect("ETH-USDT"));
        assert_eq!(filter.last_good("ETH-USDT"), Some(3000.0));
    }

    #[test]
    fn hard_reject_breaks_a_suspect_recovery_streak() {
        let mut filter = PriceFilter::new();
        let config = cfg();
        filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config);
        filter.observe("BNB-USDT", &book("BNB-USDT", 497.0), &config);
        assert_eq!(
            filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config),
            FilterDecision::HoldSuspect
        );

        let malformed = OrderBook {
            symbol: "BNB-USDT".into(),
            bids: vec![(581.0, 1.0)],
            asks: vec![(580.0, 1.0)],
            timestamp: 2,
        };
        assert_eq!(filter.observe("BNB-USDT", &malformed, &config), FilterDecision::HardReject);

        assert_eq!(
            filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config),
            FilterDecision::HoldSuspect
        );
        assert_eq!(
            filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config),
            FilterDecision::HoldSuspect
        );
        assert_eq!(
            filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config),
            FilterDecision::Accept
        );
    }

    #[test]
    fn malformed_books_are_hard_rejected_without_state() {
        let mut filter = PriceFilter::new();
        let config = cfg();
        let cases = [
            OrderBook { symbol: "BNB-USDT".into(), bids: vec![], asks: vec![(1.0, 1.0)], timestamp: 1 },
            OrderBook { symbol: "BNB-USDT".into(), bids: vec![(1.0, 1.0)], asks: vec![], timestamp: 1 },
            OrderBook { symbol: "BNB-USDT".into(), bids: vec![(f64::NAN, 1.0)], asks: vec![(2.0, 1.0)], timestamp: 1 },
            OrderBook { symbol: "BNB-USDT".into(), bids: vec![(1.0, f64::INFINITY)], asks: vec![(2.0, 1.0)], timestamp: 1 },
            OrderBook { symbol: "BNB-USDT".into(), bids: vec![(0.0, 1.0)], asks: vec![(2.0, 1.0)], timestamp: 1 },
            OrderBook { symbol: "BNB-USDT".into(), bids: vec![(1.0, -1.0)], asks: vec![(2.0, 1.0)], timestamp: 1 },
            OrderBook { symbol: "BNB-USDT".into(), bids: vec![(2.0, 1.0)], asks: vec![(2.0, 1.0)], timestamp: 1 },
            OrderBook { symbol: "BNB-USDT".into(), bids: vec![(3.0, 1.0)], asks: vec![(2.0, 1.0)], timestamp: 1 },
        ];

        for malformed in cases {
            assert_eq!(filter.observe("BNB-USDT", &malformed, &config), FilterDecision::HardReject);
            assert_eq!(filter.last_good("BNB-USDT"), None);
        }
    }

    #[test]
    fn july_31_bnb_phantom_is_blocked_without_poisoning_last_good() {
        let mut filter = PriceFilter::new();
        let config = cfg();
        let good = book("BNB-USDT", 583.99);
        filter.observe("BNB-USDT", &good, &config);

        let phantom = book("BNB-USDT", 497.0);
        assert_eq!(filter.observe("BNB-USDT", &phantom, &config), FilterDecision::SuspectNewVerify);
        assert_eq!(filter.last_good("BNB-USDT"), Some(583.99));
        assert!(filter.is_suspect("BNB-USDT"));
    }
    #[test]
    fn best_bid_and_ask_are_computed_from_all_valid_levels() {
        let mut filter = PriceFilter::new();
        let incoming = OrderBook {
            symbol: "BNB-USDT".into(),
            bids: vec![(99.0, 1.0), (100.0, 2.0)],
            asks: vec![(102.0, 1.0), (101.0, 2.0)],
            timestamp: 1,
        };

        assert_eq!(validated_mid(&incoming), Some(100.5));
        assert_eq!(filter.observe("BNB-USDT", &incoming, &cfg()), FilterDecision::Accept);
        assert_eq!(filter.last_good("BNB-USDT"), Some(100.5));
    }

    #[test]
    fn rolling_stdev_allows_a_move_that_low_stdev_would_flag() {
        let mut low_stdev = PriceFilter::new();
        let mut high_stdev = PriceFilter::new();
        let mut config = cfg();
        config.stdev_k = 1.0;

        for _ in 0..4 {
            low_stdev.observe("LOW-USDT", &book("LOW-USDT", 100.0), &config);
        }
        assert_eq!(
            low_stdev.observe("LOW-USDT", &book("LOW-USDT", 100.6), &config),
            FilterDecision::SuspectNewVerify
        );

        for mid in [100.0, 100.4, 100.8, 101.2, 101.6] {
            assert_eq!(
                high_stdev.observe("HIGH-USDT", &book("HIGH-USDT", mid), &config),
                FilterDecision::Accept
            );
        }
        assert_eq!(
            high_stdev.observe("HIGH-USDT", &book("HIGH-USDT", 102.0), &config),
            FilterDecision::Accept
        );
    }


    #[test]
    fn pairs_are_isolated() {
        let mut filter = PriceFilter::new();
        let config = cfg();
        filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config);
        filter.observe("ETH-USDT", &book("ETH-USDT", 3000.0), &config);
        for _ in 0..3 {
            filter.observe("BNB-USDT", &book("BNB-USDT", 580.0), &config);
        }
        filter.observe("BNB-USDT", &book("BNB-USDT", 497.0), &config);

        assert!(filter.is_suspect("BNB-USDT"));
        assert!(!filter.is_suspect("ETH-USDT"));
    }
}
