use anyhow::{anyhow, Result};
use crate::models::bar::Bar;
use ort::session::Session;
use ort::value::Tensor;
use crate::ml::features::extract_14_features;

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

pub struct RegimeClassifier {
    session: Session,
}

impl RegimeClassifier {
    pub fn new(model_path: &str) -> Result<Self> {
        if !std::path::Path::new(model_path).exists() {
            return Err(anyhow!("Model not found at path: {}", model_path));
        }

        let session = Session::builder()?.commit_from_file(model_path)?;
        Ok(Self { session })
    }

    pub fn predict(&mut self, bars: &[Bar]) -> Result<RegimePrediction> {
        let features = extract_14_features(bars);
        
        if features.iter().all(|&x| x == 0.0) {
            return Ok(RegimePrediction {
                regime: MarketRegime::Ranging,
                confidence: 0.0,
                probabilities: [1.0, 0.0, 0.0],
            });
        }
        
        let shape = vec![1, features.len() as i64];
        let tensor = Tensor::from_array((shape, features))?;
        let inputs = ort::inputs!["float_input" => tensor];
        let outputs = self.session.run(inputs)?;
        
        let (_, label_slice) = outputs["output_label"].try_extract_tensor::<i64>()?;
        let regime_idx = label_slice[0];
        
        let (_, probs_slice) = outputs["probabilities"].try_extract_tensor::<f32>()?;
        let p0 = probs_slice[0] as f64;
        let p1 = probs_slice[1] as f64;
        let p2 = probs_slice[2] as f64;
        
        let regime = match regime_idx {
            0 => MarketRegime::Ranging,
            1 => MarketRegime::Trending,
            2 => MarketRegime::Danger,
            _ => MarketRegime::Ranging,
        };
        
        let confidence = match regime_idx {
            0 => p0,
            1 => p1,
            2 => p2,
            _ => p0,
        };
        
        Ok(RegimePrediction {
            regime,
            confidence,
            probabilities: [p0, p1, p2],
        })
    }
}

