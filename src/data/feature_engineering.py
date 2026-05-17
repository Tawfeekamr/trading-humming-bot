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
    df['true_range'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr_14'] = df['true_range'].rolling(window=14).mean()
    df['normalized_atr'] = df['atr_14'] / df['close']
    
    # 3. Momentum & Trend Features
    # Simple Moving Averages
    df['sma_20'] = df['close'].rolling(window=20).mean()
    df['sma_50'] = df['close'].rolling(window=50).mean()
    df['trend_strength'] = (df['sma_20'] - df['sma_50']) / df['sma_50']
    
    # RSI (Relative Strength Index) — Wilder's smoothing to match src/indicators/rsi.py
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['rsi_14'] = 100 - (100 / (1 + rs))

    # 3b. Directional Features — address volatility bias
    # ADX — trend strength regardless of direction (0-100, >25 = trending)
    adx_result = ta.adx(df['high'], df['low'], df['close'], length=14)
    df['adx_14'] = adx_result['ADX_14']

    # MACD histogram — momentum acceleration (12/26/9)
    macd_result = ta.macd(df['close'], fast=12, slow=26, signal=9)
    df['macd_histogram'] = macd_result['MACDh_12_26_9']

    # Distance to VWAP — price relative to volume-weighted average
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    df['vwap'] = (df['volume'] * typical_price).cumsum() / df['volume'].cumsum()
    df['distance_to_vwap'] = (df['close'] - df['vwap']) / df['vwap']

    # On-Balance Volume — 14-period rate of change (normalized)
    df['obv'] = ta.obv(df['close'], df['volume'])
    df['obv_roc_14'] = df['obv'].pct_change(14)
    
    # Volume Features
    df['volume_sma_20'] = df['volume'].rolling(window=20).mean()
    df['volume_ratio'] = df['volume'] / df['volume_sma_20']
    
    # 4. Microstructure proxies (if tick data is unavailable)
    df['close_location_value'] = ((df['close'] - df['low']) - (df['high'] - df['close'])) / (df['high'] - df['low'] + 1e-8)
    
    # Drop rows with NaN values created by rolling windows
    df.dropna(inplace=True)
    
    return df

if __name__ == '__main__':
    # Simple test for the module
    print("Feature Engineering module loaded.")
