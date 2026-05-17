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


def load_real_data(symbol: str = "SOLUSDT", intervals: list[str] = None, candles_per_interval: int = 1000):
    """Fetches real OHLCV data from Binance public API across multiple timeframes."""
    from src.data.candle_feed import CandleFeed

    if intervals is None:
        intervals = ["1h"]

    frames = []
    for interval in intervals:
        try:
            feed = CandleFeed(symbol=symbol, interval=interval)
            df = feed.fetch_candles(limit=candles_per_interval)
            if not df.empty:
                df = df.astype(float)
                frames.append(df)
                print(f"  {interval}: {len(df)} candles ({df.index[0]} → {df.index[-1]})")
        except Exception as e:
            print(f"  {interval}: FAILED - {e}")

    if not frames:
        raise RuntimeError("No candle data fetched from any interval")

    return pd.concat(frames).drop_duplicates()


def main():
    print("Fetching real SOL/USDT market data from Binance...")
    interval_configs = {
        "15m": {"forward_window": 48, "trend_threshold": 0.015},   # 48 x 15m = 12h lookahead
        "1H":  {"forward_window": 12, "trend_threshold": 0.02},    # 12 x 1h  = 12h lookahead
        "4H":  {"forward_window": 6,  "trend_threshold": 0.025},   # 6 x 4h   = 24h lookahead
        "1d":  {"forward_window": 5,  "trend_threshold": 0.03},    # 5 x 1d   = 5d  lookahead
    }

    datasets = []
    for interval, cfg in interval_configs.items():
        df = load_real_data("SOLUSDT", intervals=[interval], candles_per_interval=1000)
        datasets.append((interval, cfg, df))

    feature_cols = [
        'returns', 'volatility_14', 'volatility_30', 'normalized_atr',
        'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value'
    ]

    all_X_train, all_y_train, all_X_test, all_y_test = [], [], [], []

    for name, cfg, df in datasets:
        print(f"\n--- Processing {name} data ({len(df)} candles) ---")
        df_features = calculate_technical_features(df)
        print(f"  After feature engineering: {len(df_features)} rows")

        df_labeled = generate_regime_labels(
            df_features, forward_window=cfg["forward_window"], trend_threshold=cfg["trend_threshold"]
        )
        print(f"  After labeling: {len(df_labeled)} rows")
        print(f"  Label distribution: {df_labeled['regime_label'].value_counts().to_dict()}")

        X = df_labeled[feature_cols]
        y = df_labeled['regime_label']
        split = int(len(X) * 0.8)
        all_X_train.append(X.iloc[:split])
        all_y_train.append(y.iloc[:split])
        all_X_test.append(X.iloc[split:])
        all_y_test.append(y.iloc[split:])

    X_train = pd.concat(all_X_train)
    y_train = pd.concat(all_y_train)
    X_test = pd.concat(all_X_test)
    y_test = pd.concat(all_y_test)

    print(f"\n--- Training ---")
    print(f"  Train: {len(X_train)} samples (trending: {sum(y_train==1)}, ranging: {sum(y_train==0)})")
    print(f"  Test:  {len(X_test)} samples (trending: {sum(y_test==1)}, ranging: {sum(y_test==0)})")

    classifier = RegimeClassifier(model_path='models/regime_rf_v1.pkl')
    classifier.train(X_train, y_train)

    print("\n--- Evaluation on Test Set ---")
    y_pred = classifier.predict(X_test, threshold=0.55)
    print(f"Accuracy: {accuracy_score(y_test, y_pred):.4f}")
    print(classification_report(y_test, y_pred, target_names=["Ranging", "Trending"]))

    # Show per-feature importance
    importances = list(zip(feature_cols, classifier.model.feature_importances_))
    importances.sort(key=lambda x: x[1], reverse=True)
    print("--- Feature Importances ---")
    for feat, imp in importances:
        print(f"  {feat:25s} {imp:.4f}")

    classifier.save_model()
    print("\nPipeline complete. Model saved to models/regime_rf_v1.pkl")


if __name__ == '__main__':
    main()
