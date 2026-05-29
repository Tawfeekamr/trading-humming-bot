use crate::models::bar::Bar;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Pattern {
    BullishEngulfing,
    BearishEngulfing,
    Hammer,
    InvertedHammer,
    Doji,
    None,
}

pub struct CandlestickPatterns {
    body_ratio_threshold: f64,
}

impl CandlestickPatterns {
    pub fn new(body_ratio_threshold: f64) -> Self { Self { body_ratio_threshold } }
    pub fn detect(&self, current: &Bar, previous: Option<&Bar>) -> Pattern {
        // Check specific single-candle patterns before doji (they're more specific)
        if self.is_hammer(current) { return Pattern::Hammer; }
        if self.is_inverted_hammer(current) { return Pattern::InvertedHammer; }
        if self.is_doji(current) { return Pattern::Doji; }
        if let Some(prev) = previous {
            if self.is_bullish_engulfing(current, prev) { return Pattern::BullishEngulfing; }
            if self.is_bearish_engulfing(current, prev) { return Pattern::BearishEngulfing; }
        }
        Pattern::None
    }
    fn is_doji(&self, bar: &Bar) -> bool {
        let range = bar.high - bar.low;
        if range < 1e-10 { return true; }
        bar.body_size() / range < self.body_ratio_threshold
    }
    fn is_hammer(&self, bar: &Bar) -> bool {
        let range = bar.high - bar.low;
        if range < 1e-10 { return false; }
        let body_ratio = bar.body_size() / range;
        body_ratio < 0.4 && bar.lower_wick() / range >= 0.5 && bar.upper_wick() / range < 0.15
    }
    fn is_inverted_hammer(&self, bar: &Bar) -> bool {
        let range = bar.high - bar.low;
        if range < 1e-10 { return false; }
        let body_ratio = bar.body_size() / range;
        body_ratio < 0.4 && bar.upper_wick() / range >= 0.5 && bar.lower_wick() / range < 0.15
    }
    fn is_bullish_engulfing(&self, current: &Bar, previous: &Bar) -> bool {
        if previous.is_bullish() || !current.is_bullish() { return false; }
        current.close >= previous.open && current.open <= previous.close
    }
    fn is_bearish_engulfing(&self, current: &Bar, previous: &Bar) -> bool {
        if !previous.is_bullish() || current.is_bullish() { return false; }
        current.open >= previous.close && current.close <= previous.open
    }
}
