use anyhow::Result;
use crate::models::bar::Bar;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum MarketRegime {
    Ranging,   // 0
    Trending,  // 1
    Danger,    // 2
}

pub struct RegimePrediction {
    pub regime: MarketRegime,
    pub confidence: f64,
    pub probabilities: [f64; 3],
}

pub struct RegimeClassifier;

impl RegimeClassifier {
    pub fn new(model_path: &str) -> Result<Self> {
        std::fs::metadata(model_path)?;
        Ok(Self)
    }

    pub fn predict(&self, bars: &[Bar]) -> Result<RegimePrediction> {
        let _features = extract_features(bars);
        // TODO: Run ONNX inference when ort crate integration is complete
        Ok(RegimePrediction {
            regime: MarketRegime::Ranging,
            confidence: 0.5,
            probabilities: [0.5, 0.3, 0.2],
        })
    }
}

/// Extract feature vector from recent bars
/// Must match the Python training pipeline feature engineering exactly
pub fn extract_features(bars: &[Bar]) -> Vec<f64> {
    if bars.len() < 16 {
        return Vec::new();
    }

    let mut features = Vec::new();
    let close = bars.last().unwrap().close;

    // Returns at different timeframes
    let returns_1m = (close - bars[bars.len() - 2].close) / bars[bars.len() - 2].close;
    features.push(returns_1m);

    let returns_5m = (close - bars[bars.len() - 6].close) / bars[bars.len() - 6].close;
    features.push(returns_5m);

    let returns_15m = (close - bars[bars.len() - 16].close) / bars[bars.len() - 16].close;
    features.push(returns_15m);

    // Volatility (std of last 20 returns)
    let returns: Vec<f64> = bars.windows(2)
        .map(|w| (w[1].close - w[0].close) / w[0].close)
        .collect();
    let mean = returns.iter().sum::<f64>() / returns.len() as f64;
    let variance = returns.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / returns.len() as f64;
    features.push(variance.sqrt());

    // Volume ratio (current vs average)
    let avg_vol: f64 = bars.iter().rev().take(20).map(|b| b.volume).sum::<f64>() / 20.0;
    let vol_ratio = if avg_vol > 0.0 { bars.last().unwrap().volume / avg_vol } else { 1.0 };
    features.push(vol_ratio);

    // Placeholders for BB position, RSI, EMA slope (will be enhanced)
    features.push(0.5);
    features.push(50.0);
    features.push(0.0);

    features
}
