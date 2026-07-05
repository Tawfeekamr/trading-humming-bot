use crate::models::bar::Bar;

pub fn extract_14_features(bars: &[Bar]) -> Vec<f64> {
    if bars.len() < 100 {
        return vec![0.0; 14];
    }
    
    let close = bars.last().unwrap().close;
    let high = bars.last().unwrap().high;
    let low = bars.last().unwrap().low;
    let volume = bars.last().unwrap().volume;
    let prev_close = bars[bars.len() - 2].close;

    // 1. returns
    let returns = if prev_close > 0.0 { (close - prev_close) / prev_close } else { 0.0 };

    // 2. volatility_ratio
    let ret_seq: Vec<f64> = bars.windows(2).map(|w| {
        if w[0].close > 0.0 { (w[1].close - w[0].close) / w[0].close } else { 0.0 }
    }).collect();
    
    let std_dev = |data: &[f64]| -> f64 {
        let mean = data.iter().sum::<f64>() / data.len() as f64;
        let variance = data.iter().map(|&x| (x - mean).powi(2)).sum::<f64>() / (data.len() - 1).max(1) as f64;
        variance.sqrt()
    };
    
    let vol_14 = std_dev(&ret_seq[ret_seq.len().saturating_sub(14)..]);
    let vol_30 = std_dev(&ret_seq[ret_seq.len().saturating_sub(30)..]);
    let volatility_ratio = vol_14 / (vol_30 + 1e-8);

    // 3. normalized_atr
    let true_range = |b: &Bar, prev_b: &Bar| -> f64 {
        let hl = b.high - b.low;
        let hc = (b.high - prev_b.close).abs();
        let lc = (b.low - prev_b.close).abs();
        hl.max(hc).max(lc)
    };
    let trs: Vec<f64> = bars.windows(2).map(|w| true_range(&w[1], &w[0])).collect();
    let atr_14 = trs[trs.len().saturating_sub(14)..].iter().sum::<f64>() / 14.0;
    let normalized_atr = atr_14 / (close + 1e-8);

    // 4. trend_strength
    let sma_20 = bars[bars.len().saturating_sub(20)..].iter().map(|b| b.close).sum::<f64>() / 20.0;
    let sma_50 = bars[bars.len().saturating_sub(50)..].iter().map(|b| b.close).sum::<f64>() / 50.0;
    let trend_strength = (sma_20 - sma_50) / (sma_50 + 1e-8);

    // 5. rsi_14
    let mut avg_gain = 0.0;
    let mut avg_loss = 0.0;
    for i in bars.len().saturating_sub(15)..bars.len() {
        let change = bars[i].close - bars[i - 1].close;
        let gain = if change > 0.0 { change } else { 0.0 };
        let loss = if change < 0.0 { -change } else { 0.0 };
        if i == bars.len().saturating_sub(15) {
            avg_gain = gain;
            avg_loss = loss;
        } else {
            avg_gain = (avg_gain * 13.0 + gain) / 14.0;
            avg_loss = (avg_loss * 13.0 + loss) / 14.0;
        }
    }
    let rsi_14 = if avg_loss == 0.0 { 100.0 } else { 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) };

    // 6. volume_ratio
    let vol_sma_20 = bars[bars.len().saturating_sub(20)..].iter().map(|b| b.volume).sum::<f64>() / 20.0;
    let volume_ratio = volume / (vol_sma_20 + 1e-8);

    // 7. close_location_value
    let clv = if high - low > 0.0 { ((close - low) - (high - close)) / (high - low) } else { 0.0 };

    // 8. adx_14 (Simplified estimation or use exact if available)
    // To match exact Python pandas_ta ADX requires full Wilder's smoothing. 
    // We will do a basic approximation here, but since this is for ML feature matching, 
    // we should ideally use the exact same calculation.
    let adx_14 = 25.0; // Placeholder for now, must use real ADX

    // 9. macd_histogram
    let ema = |data: &[f64], period: usize| -> f64 {
        let alpha = 2.0 / (period as f64 + 1.0);
        data.iter().fold(data[0], |acc, &val| val * alpha + acc * (1.0 - alpha))
    };
    let closes: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let macd_line: Vec<f64> = closes.windows(26).map(|w| ema(w, 12) - ema(w, 26)).collect();
    let signal_line = ema(&macd_line[macd_line.len().saturating_sub(9)..], 9);
    let macd_histogram = macd_line.last().unwrap() - signal_line;

    // 10. distance_to_vwap
    let mut sum_tp_vol = 0.0;
    let mut sum_vol = 0.0;
    for b in &bars[bars.len().saturating_sub(50)..] {
        let tp = (b.high + b.low + b.close) / 3.0;
        sum_tp_vol += tp * b.volume;
        sum_vol += b.volume;
    }
    let vwap = if sum_vol > 0.0 { sum_tp_vol / sum_vol } else { close };
    let distance_to_vwap = (close - vwap) / (vwap + 1e-8);

    // 11. obv_roc_14
    let mut obv = vec![0.0; bars.len()];
    for i in 1..bars.len() {
        obv[i] = obv[i-1] + if bars[i].close > bars[i-1].close {
            bars[i].volume
        } else if bars[i].close < bars[i-1].close {
            -bars[i].volume
        } else {
            0.0
        };
    }
    let mut obv_diff = vec![0.0; bars.len()];
    for i in 14..bars.len() {
        obv_diff[i] = obv[i] - obv[i-14];
    }
    let obv_diff_50 = &obv_diff[obv_diff.len().saturating_sub(50)..];
    let obv_mean = obv_diff_50.iter().sum::<f64>() / 50.0;
    let obv_std = std_dev(obv_diff_50);
    let obv_roc_14 = (obv_diff.last().unwrap() - obv_mean) / (obv_std + 1e-8);

    // 12. choppiness_index
    let atr_sum_14 = atr_14 * 14.0;
    let hh = bars[bars.len().saturating_sub(14)..].iter().map(|b| b.high).fold(0.0f64, f64::max);
    let ll = bars[bars.len().saturating_sub(14)..].iter().map(|b| b.low).fold(f64::MAX, f64::min);
    let choppiness_index = 100.0 * (atr_sum_14 / (hh - ll + 1e-8)).log10() / 14.0f64.log10();

    // 13. fractal_dimension_index (Higuchi over 30 periods)
    let higuchi_fd = |x: &[f64], kmax: usize| -> f64 {
        let n = x.len();
        if n < kmax * 2 { return 1.5; }
        let mut lk = vec![0.0; kmax];
        let mut lnk = vec![0.0; kmax];
        for k in 1..=kmax {
            let mut lm = Vec::new();
            for m in 0..k {
                let mut length = 0.0;
                let mut count = 0;
                let mut i = m;
                while i + k < n {
                    length += (x[i + k] - x[i]).abs();
                    count += 1;
                    i += k;
                }
                if count > 0 {
                    let norm = (n as f64 - 1.0) / (count as f64 * k as f64);
                    lm.push(length * norm / k as f64);
                }
            }
            lk[k - 1] = if lm.is_empty() { f64::NAN } else { lm.iter().sum::<f64>() / lm.len() as f64 };
            lnk[k - 1] = (1.0 / k as f64).ln();
        }
        // Simple linear regression of ln(Lk) vs ln(1/k)
        let mut valid_lnk = Vec::new();
        let mut valid_loglk = Vec::new();
        for i in 0..kmax {
            if lk[i].is_finite() && lk[i] > 0.0 {
                valid_lnk.push(lnk[i]);
                valid_loglk.push(lk[i].ln());
            }
        }
        if valid_lnk.len() < 2 { return 1.5; }
        let mean_x = valid_lnk.iter().sum::<f64>() / valid_lnk.len() as f64;
        let mean_y = valid_loglk.iter().sum::<f64>() / valid_loglk.len() as f64;
        let cov = valid_lnk.iter().zip(valid_loglk.iter()).map(|(x, y)| (x - mean_x) * (y - mean_y)).sum::<f64>();
        let var_x = valid_lnk.iter().map(|x| (x - mean_x).powi(2)).sum::<f64>();
        let fd = cov / var_x;
        fd.clamp(1.0, 2.0)
    };
    let closes_30: Vec<f64> = bars[bars.len().saturating_sub(30)..].iter().map(|b| b.close).collect();
    let fractal_dimension_index = higuchi_fd(&closes_30, 5);

    // 14. aroon_oscillator (25)
    let mut highest_idx = 0;
    let mut lowest_idx = 0;
    let window = 25;
    for i in 0..=window {
        let idx = bars.len() - 1 - i;
        if bars[idx].high >= bars[bars.len() - 1 - highest_idx].high { highest_idx = i; }
        if bars[idx].low <= bars[bars.len() - 1 - lowest_idx].low { lowest_idx = i; }
    }
    let aroon_up = 100.0 * (window as f64 - highest_idx as f64) / window as f64;
    let aroon_down = 100.0 * (window as f64 - lowest_idx as f64) / window as f64;
    let aroon_oscillator = aroon_up - aroon_down;

    vec![
        returns,
        volatility_ratio,
        normalized_atr,
        trend_strength,
        rsi_14,
        volume_ratio,
        clv,
        adx_14,
        macd_histogram,
        distance_to_vwap,
        obv_roc_14,
        choppiness_index,
        fractal_dimension_index,
        aroon_oscillator,
    ]
}
