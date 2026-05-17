import pandas as pd
import numpy as np

def generate_regime_labels(df: pd.DataFrame, forward_window: int = 14, trend_threshold: float = 0.02) -> pd.DataFrame:
    """
    Generates target labels for ML training based on forward-looking price action.
    Labels:
    0 = Ranging (Grid Strategy preferred)
    1 = Trending (Trend Strategy preferred)
    
    :param df: DataFrame with 'close' price.
    :param forward_window: How many periods ahead to look for price movement.
    :param trend_threshold: Percentage move required to be considered a 'trend'.
    """
    df = df.copy()
    
    # Calculate future returns over the forward window
    df['future_return'] = df['close'].shift(-forward_window) / df['close'] - 1
    
    # Calculate the max excursion (high/low) in the forward window to see if it just spiked and reverted
    df['forward_max'] = df['high'].shift(-forward_window).rolling(window=forward_window).max()
    df['forward_min'] = df['low'].shift(-forward_window).rolling(window=forward_window).min()
    
    df['max_up_move'] = df['forward_max'] / df['close'] - 1
    df['max_down_move'] = df['close'] / df['forward_min'] - 1
    
    # Label Logic:
    # If the absolute future return is greater than the threshold, we are in a trend.
    # Alternatively, if the market moves significantly in one direction but closes flat (high volatility),
    # it might be too dangerous for simple grid, but for this basic labeler, we focus on directional closes.
    
    conditions = [
        (df['future_return'].abs() > trend_threshold), # Trending
        (df['future_return'].abs() <= trend_threshold) # Ranging
    ]
    
    choices = [1, 0]
    
    df['regime_label'] = np.select(conditions, choices, default=0)
    
    # Drop rows where we can't look forward
    df.dropna(subset=['future_return'], inplace=True)
    
    return df

if __name__ == '__main__':
    print("Label Generation module loaded.")
