import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score
import os
import sys
import urllib.request
import json

# Support running as `python -m src.ml.train_pipeline` or as a script
if __name__ == '__main__' and __package__ is None:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.data.feature_engineering import calculate_technical_features
from src.data.label_generation import generate_regime_labels
from src.ml.regime_classifier import RegimeClassifier


def load_real_data(symbol: str = "SOLUSDT", intervals: list[str] = None, candles_per_interval: int = 1000):
    """Fetches real OHLCV data from Binance public API across multiple timeframes."""
    if intervals is None:
        intervals = ["1h"]

    frames = []
    for interval in intervals:
        try:
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={candles_per_interval}"
            req = urllib.request.Request(url, headers={"User-Agent": "train-pipeline"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                klines = json.loads(resp.read().decode())

            df = pd.DataFrame(klines, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "taker_buy_base", "taker_buy_quote", "ignore",
            ])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df[["open", "high", "low", "close", "volume"]].astype(float)
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
        "15m": {"forward_window": 48, "trend_threshold": 0.015, "trend_atr_k": 1.2},   # 48 x 15m = 12h lookahead
        "1h":  {"forward_window": 12, "trend_threshold": 0.02,  "trend_atr_k": 1.5},    # 12 x 1h  = 12h lookahead
        "4h":  {"forward_window": 6,  "trend_threshold": 0.025, "trend_atr_k": 1.5},   # 6 x 4h   = 24h lookahead
        "1d":  {"forward_window": 5,  "trend_threshold": 0.03,  "trend_atr_k": 2.0},    # 5 x 1d   = 5d  lookahead
    }

    datasets = []
    for interval, cfg in interval_configs.items():
        df = load_real_data("SOLUSDT", intervals=[interval], candles_per_interval=1000)
        datasets.append((interval, cfg, df))

    feature_cols = [
        'returns', 'volatility_14', 'volatility_30', 'normalized_atr',
        'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value',
        'adx_14', 'macd_histogram', 'distance_to_vwap', 'obv_roc_14'
    ]

    all_X_train, all_y_train, all_X_test, all_y_test = [], [], [], []

    for name, cfg, df in datasets:
        print(f"\n--- Processing {name} data ({len(df)} candles) ---")
        df_features = calculate_technical_features(df)
        print(f"  After feature engineering: {len(df_features)} rows")

        df_labeled = generate_regime_labels(
            df_features, forward_window=cfg["forward_window"], trend_threshold=cfg["trend_threshold"],
            atr_column='atr_14', trend_atr_k=cfg["trend_atr_k"]
        )
        print(f"  After labeling: {len(df_labeled)} rows")
        print(f"  Label distribution: {df_labeled['regime_label'].value_counts().to_dict()}")

        X = df_labeled[feature_cols]
        y = df_labeled['regime_label']
        # 60/20/20 split: train on oldest, test on newest
        train_end = int(len(X) * 0.8)
        all_X_train.append(X.iloc[:train_end])
        all_y_train.append(y.iloc[:train_end])
        all_X_test.append(X.iloc[train_end:])
        all_y_test.append(y.iloc[train_end:])

    X_trainval = pd.concat(all_X_train)
    y_trainval = pd.concat(all_y_train)
    X_test = pd.concat(all_X_test)
    y_test = pd.concat(all_y_test)

    print(f"\n--- Hyperparameter Tuning ---")
    n_classes = y_trainval.nunique()
    for c in sorted(y_trainval.unique()):
        name = {0: "ranging", 1: "trending", 2: "danger"}.get(c, f"class_{c}")
        print(f"  Train+Val: {sum(y_trainval == c)} {name}")
    for c in sorted(y_test.unique()):
        name = {0: "ranging", 1: "trending", 2: "danger"}.get(c, f"class_{c}")
        print(f"  Test:      {sum(y_test == c)} {name}")

    classifier = RegimeClassifier(model_path='models/regime_rf_v3.pkl')
    classifier.tune_hyperparameters(X_trainval, y_trainval, n_iter=20, cv=3)

    print("\n--- Evaluation on Test Set ---")
    y_pred = classifier.model.predict(X_test)
    print(f"Accuracy: {accuracy_score(y_test, y_pred):.4f}")
    target_names = ["Ranging", "Trending", "Danger"] if n_classes == 3 else ["Ranging", "Trending"]
    print(classification_report(y_test, y_pred, target_names=target_names))

    # Show per-feature importance
    importances = list(zip(feature_cols, classifier.model.feature_importances_))
    importances.sort(key=lambda x: x[1], reverse=True)
    print("--- Feature Importances ---")
    for feat, imp in importances:
        print(f"  {feat:25s} {imp:.4f}")

    classifier.save_model()
    print(f"\nPipeline complete. Model saved to {classifier.model_path}")


if __name__ == '__main__':
    main()
