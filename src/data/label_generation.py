import pandas as pd
import numpy as np

def generate_regime_labels(df: pd.DataFrame, forward_window: int = 14, trend_threshold: float = 0.02,
                          atr_column: str = 'atr_14', trend_atr_k: float = 1.5) -> pd.DataFrame:
    """
    Generates target labels for ML training based on forward-looking price action.
    Labels:
    0 = Ranging (Grid Strategy preferred)
    1 = Trending (Trend Strategy preferred)
    2 = Danger (Whipsaw — both engines should pause)

    :param df: DataFrame with 'close' price.
    :param forward_window: How many periods ahead to look for price movement.
    :param trend_threshold: Percentage move required to be considered a 'trend' (static fallback).
    :param atr_column: Column name for ATR values. When present, uses dynamic threshold.
    :param trend_atr_k: Multiplier for ATR-based dynamic threshold (k * ATR/close).
    """
    df = df.copy()

    # Calculate future returns over the forward window
    df['future_return'] = df['close'].shift(-forward_window) / df['close'] - 1

    # Calculate the max excursion (high/low) in the forward window to see if it just spiked and reverted
    df['forward_max'] = df['high'].shift(-forward_window).rolling(window=forward_window).max()
    df['forward_min'] = df['low'].shift(-forward_window).rolling(window=forward_window).min()

    df['max_up_move'] = df['forward_max'] / df['close'] - 1
    df['max_down_move'] = df['close'] / df['forward_min'] - 1

    # Use ATR-based dynamic threshold if available, otherwise fall back to static
    if atr_column in df.columns:
        dynamic_threshold = df[atr_column] / df['close'] * trend_atr_k
        dynamic_threshold = dynamic_threshold.clip(lower=0.005, upper=0.10)
    else:
        dynamic_threshold = trend_threshold

    # Danger: both directions have large excursions (whipsaw) but net move is small
    danger_threshold = dynamic_threshold * 0.8
    is_trending = df['future_return'].abs() > dynamic_threshold
    is_danger_excursion = (
        (df['max_up_move'] > danger_threshold) &
        (df['max_down_move'] > danger_threshold) &
        ~is_trending
    )
    # High volatility + flat net return = whipsaw (top 10% ATR only, stricter)
    is_danger_volatility = pd.Series(False, index=df.index)
    if atr_column in df.columns:
        atr_pct = df[atr_column] / df['close']
        vol_threshold = atr_pct.quantile(0.90)
        is_danger_volatility = (atr_pct > vol_threshold) & (df['future_return'].abs() < dynamic_threshold * 0.15)
    is_danger = is_danger_excursion | is_danger_volatility
    is_ranging = ~is_trending & ~is_danger

    # Priority: Danger > Trending > Ranging
    conditions = [is_danger, is_trending, is_ranging]
    choices = [2, 1, 0]

    df['regime_label'] = np.select(conditions, choices, default=0)

    # Drop rows where we can't look forward
    df.dropna(subset=['future_return'], inplace=True)

    return df

if __name__ == '__main__':
    print("Label Generation module loaded.")
