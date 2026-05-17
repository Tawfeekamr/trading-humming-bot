import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score
import os
import sys

# Support running as `python -m src.ml.train_pipeline` or as a script
if __name__ == '__main__' and __package__ is None:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.data.feature_engineering import calculate_technical_features
from src.data.label_generation import generate_regime_labels
from src.ml.regime_classifier import RegimeClassifier

def load_dummy_data():
    """Generates dummy OHLCV data for testing the pipeline."""
    import numpy as np
    dates = pd.date_range(start='2023-01-01', periods=1000, freq='1h')
    np.random.seed(42)
    close = np.random.randn(1000).cumsum() + 100
    wicks_up = np.random.rand(1000) * 2
    wicks_down = np.random.rand(1000) * 2
    df = pd.DataFrame({
        'open': close + np.random.randn(1000) * 0.5,
        'high': close + wicks_up,
        'low': close - wicks_down,
        'close': close,
        'volume': np.random.randint(100, 1000, size=1000)
    }, index=dates)
    # Guarantee valid OHLCV: high >= max(open,close), low <= min(open,close)
    df['high'] = df[['open', 'close', 'high']].max(axis=1)
    df['low'] = df[['open', 'close', 'low']].min(axis=1)
    return df

def main():
    print("Loading data...")
    # In a real scenario, this loads from a database or CSV
    df = load_dummy_data()
    
    print("Engineering features...")
    df_features = calculate_technical_features(df)
    
    print("Generating labels...")
    df_labeled = generate_regime_labels(df_features, forward_window=12, trend_threshold=0.015)
    
    # Define features to use
    feature_cols = [
        'returns', 'volatility_14', 'volatility_30', 'normalized_atr',
        'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value'
    ]
    
    X = df_labeled[feature_cols]
    y = df_labeled['regime_label']
    
    # Split data (time-series split is better, but using train_test_split for simplicity here without shuffle)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
    
    print("Initializing classifier...")
    classifier = RegimeClassifier(model_path='models/regime_rf_v1.pkl')
    
    classifier.train(X_train, y_train)
    
    print("\nEvaluating on Test Set...")
    y_pred = classifier.predict(X_test, threshold=0.55)
    
    print("Accuracy:", accuracy_score(y_test, y_pred))
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred))
    
    # Save the model
    classifier.save_model()
    print("Pipeline execution complete.")

if __name__ == '__main__':
    main()
