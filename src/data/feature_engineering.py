import pandas as pd
import numpy as np
import pandas_ta as ta

def calculate_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes technical indicators and statistical features to be used as inputs for the ML regime classifier.
    Expects a DataFrame with 'open', 'high', 'low', 'close', 'volume' columns.
    """
    df = df.copy()
    
    # 1. Basic Price Features
    df['returns'] = df['close'].pct_change()
    df['log_returns'] = np.log(df['close'] / df['close'].shift(1))
    
    # 2. Volatility Features
    df['volatility_14'] = df['returns'].rolling(window=14).std()
    df['volatility_30'] = df['returns'].rolling(window=30).std()
    df['volatility_ratio'] = df['volatility_14'] / (df['volatility_30'] + 1e-8)
    df['true_range'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr_14'] = df['true_range'].rolling(window=14).mean()
    df['normalized_atr'] = df['atr_14'] / (df['close'] + 1e-8)
    
    # 3. Momentum & Trend Features
    # Simple Moving Averages
    df['sma_20'] = df['close'].rolling(window=20).mean()
    df['sma_50'] = df['close'].rolling(window=50).mean()
    df['trend_strength'] = (df['sma_20'] - df['sma_50']) / (df['sma_50'] + 1e-8)
    
    # RSI (Relative Strength Index) — Wilder's smoothing to match src/indicators/rsi.py
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-8)
    df['rsi_14'] = 100 - (100 / (1 + rs))

    # 3b. Directional Features — address volatility bias
    # ADX — trend strength regardless of direction (0-100, >25 = trending)
    adx_result = ta.adx(df['high'], df['low'], df['close'], length=14)
    df['adx_14'] = adx_result['ADX_14']

    # MACD histogram — momentum acceleration (12/26/9)
    macd_result = ta.macd(df['close'], fast=12, slow=26, signal=9)
    df['macd_histogram'] = macd_result['MACDh_12_26_9']

    # Distance to VWAP — price relative to volume-weighted average (rolling for consistent inference)
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    tp_vol = df['volume'] * typical_price
    df['vwap'] = tp_vol.rolling(window=50, min_periods=1).sum() / df['volume'].rolling(window=50, min_periods=1).sum()
    df['distance_to_vwap'] = (df['close'] - df['vwap']) / (df['vwap'] + 1e-8)

    # On-Balance Volume — 14-period differencing (z-scored for stability)
    # Using pct_change on cumulative OBV is fragile near zero crossings.
    # Differencing OBV gives volume-flow change, then z-score normalizes it.
    df['obv'] = ta.obv(df['close'], df['volume'])
    obv_diff = df['obv'].diff(14)
    obv_mean = obv_diff.rolling(window=50, min_periods=14).mean()
    obv_std = obv_diff.rolling(window=50, min_periods=14).std()
    df['obv_roc_14'] = (obv_diff - obv_mean) / (obv_std + 1e-8)
    
    # Volume Features
    df['volume_sma_20'] = df['volume'].rolling(window=20).mean()
    df['volume_ratio'] = df['volume'] / (df['volume_sma_20'] + 1e-8)
    
    # 4. Microstructure proxies (if tick data is unavailable)
    df['close_location_value'] = ((df['close'] - df['low']) - (df['high'] - df['close'])) / (df['high'] - df['low'] + 1e-8)

    # 5. Regime-specific indicators
    # Choppiness Index — high values (>61.8) = ranging, low values (<38.2) = trending
    period = 14
    atr_sum = df['true_range'].rolling(window=period).sum()
    hh = df['high'].rolling(window=period).max()
    ll = df['low'].rolling(window=period).min()
    df['choppiness_index'] = 100 * np.log10(atr_sum / (hh - ll + 1e-8)) / np.log10(period)

    # Fractal Dimension Index — ~1.0 = trend, ~1.5 = random walk, ~2.0 = reversal
    positive_changes = (df['close'].diff() > 0).rolling(window=period).sum()
    negative_changes = (df['close'].diff() < 0).rolling(window=period).sum()
    trailing_range = (df['high'].rolling(window=period).max() - df['low'].rolling(window=period).min()) / df['close']
    df['fractal_dimension_index'] = 1.0 + np.log(positive_changes + negative_changes + 1e-8) / (np.log(2 * period) + 1e-8) - np.log(trailing_range + 1e-8) / (np.log(2 * period) + 1e-8)

    # Aroon Oscillator — positive = uptrend, negative = downtrend, near-zero = ranging
    aroon_result = ta.aroon(df['high'], df['low'], length=25)
    df['aroon_oscillator'] = aroon_result['AROONOSC_25']

    # Replace inf values (from division by zero) then drop rows with NaN
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df.dropna(inplace=True)
    
    return df

if __name__ == '__main__':
    # Simple test for the module
    print("Feature Engineering module loaded.")
