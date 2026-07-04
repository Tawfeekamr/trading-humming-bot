//! In-sample / out-of-sample validation for the 1h engines.
use crate::models::bar::Bar;

/// Contiguous IS/OOS split. IS = first (1 - oos_frac) of bars, OOS = the rest.
/// Empty input → two empty vecs. No shared bars.
pub fn split_is_oos(bars: &[Bar], oos_frac: f64) -> (Vec<Bar>, Vec<Bar>) {
    if bars.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let split = (bars.len() as f64 * (1.0 - oos_frac.clamp(0.0, 1.0))).round() as usize;
    let split = split.min(bars.len());
    let (is_b, oos_b) = bars.split_at(split);
    (is_b.to_vec(), oos_b.to_vec())
}
