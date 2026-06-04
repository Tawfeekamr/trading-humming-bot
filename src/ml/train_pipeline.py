import pandas as pd
import numpy as np
from sklearn.metrics import classification_report, accuracy_score, confusion_matrix
import argparse
import os
import sys
import time
import urllib.request
import urllib.error
import json

# Support running as `python -m src.ml.train_pipeline` or as a script
if __name__ == '__main__' and __package__ is None:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.data.feature_engineering import calculate_technical_features
from src.data.label_generation import generate_regime_labels
from src.ml.regime_classifier import RegimeClassifier
from src.ml.purged_cv import PurgedTimeSeriesSplit


def load_real_data(symbol: str = "SOLUSDT", intervals: list[str] = None, candles_per_interval: int = 1000):
    """Fetches real OHLCV data from Binance public API. Paginates automatically for >1500 candles."""
    if intervals is None:
        intervals = ["1h"]

    INTERVAL_MS = {
        "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
        "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
        "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
        "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
    }

    frames = []
    for interval in intervals:
        try:
            all_klines = []
            remaining = candles_per_interval
            end_time = None  # None = fetch from now backwards

            while remaining > 0:
                batch = min(remaining, 1000)
                url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={batch}"
                if end_time is not None:
                    url += f"&endTime={end_time}"

                req = urllib.request.Request(url, headers={"User-Agent": "train-pipeline"})
                try:
                    time.sleep(0.5)
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        klines = json.loads(resp.read().decode())
                except urllib.error.HTTPError as he:
                    if he.code == 429:
                        retry_after = int(he.headers.get("Retry-After", 10))
                        print(f"  Rate limited (HTTP 429). Sleeping {retry_after}s...")
                        time.sleep(retry_after)
                        continue
                    else:
                        print(f"  HTTP error {he.code}: {he.reason}. Aborting interval.")
                        break
                except Exception as e:
                    print(f"  Network error: {e}. Retrying in 5s...")
                    time.sleep(5)
                    continue

                if not klines:
                    break
                all_klines.extend(klines)
                remaining -= len(klines)

                if len(klines) < batch:
                    break  # no more data available

                # Set end_time to before the earliest candle we just fetched
                end_time = int(klines[0][0]) - 1

            df = pd.DataFrame(all_klines, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "taker_buy_base", "taker_buy_quote", "ignore",
            ])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df[["open", "high", "low", "close", "volume"]].astype(float)
            df = df[~df.index.duplicated(keep='first')]
            frames.append(df)
            print(f"  {interval}: {len(df)} candles fetched")
        except Exception as e:
            print(f"  {interval}: FAILED - {e}")

    if not frames:
        raise RuntimeError("No candle data fetched from any interval")

    return pd.concat(frames).drop_duplicates()


def main():
    parser = argparse.ArgumentParser(description='ML Regime Classifier Training Pipeline')
    parser.add_argument('--timeframe', type=str, default=None,
                        help='Train on a single timeframe only (e.g., 1h). Default: all timeframes.')
    parser.add_argument('--candles', type=int, default=1000,
                        help='Number of candles per timeframe (default: 1000). Use 2000+ for single-TF training.')
    parser.add_argument('--pair', type=str, default="SOL-USDT",
                        help='Trading pair for per-pair model training (e.g., BNB-USDT). '
                             'Default: SOL-USDT (legacy behavior)')
    parser.add_argument('--output', type=str, default=None,
                        help="Output path for new model (e.g. models/regime_ETH-USDT.pkl.new). "
                             "Defaults to standard path with .new suffix.")
    args = parser.parse_args()

    symbol = args.pair.replace("-", "")  # BNB-USDT -> BNBUSDT
    print(f"Fetching real {args.pair} market data from Binance...")
    interval_configs = {
        "15m": {"forward_window": 48, "trend_threshold": 0.015, "trend_atr_k": 1.2},
        "1h":  {"forward_window": 12, "trend_threshold": 0.02,  "trend_atr_k": 1.5},
        "4h":  {"forward_window": 6,  "trend_threshold": 0.025, "trend_atr_k": 1.5},
        "1d":  {"forward_window": 5,  "trend_threshold": 0.03,  "trend_atr_k": 2.0},
    }

    if args.timeframe:
        if args.timeframe not in interval_configs:
            print(f"Error: unknown timeframe '{args.timeframe}'. Choose from: {list(interval_configs.keys())}")
            return
        interval_configs = {args.timeframe: interval_configs[args.timeframe]}
        print(f"  Single-timeframe mode: {args.timeframe}")

    datasets = []
    max_forward_window = 0
    for interval, cfg in interval_configs.items():
        df = load_real_data(symbol, intervals=[interval], candles_per_interval=args.candles)
        datasets.append((interval, cfg, df))
        max_forward_window = max(max_forward_window, cfg["forward_window"])

    feature_cols = [
        'returns', 'volatility_ratio', 'normalized_atr',
        'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value',
        'adx_14', 'macd_histogram', 'distance_to_vwap', 'obv_roc_14',
        'choppiness_index', 'fractal_dimension_index', 'aroon_oscillator'
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

    model_path = f'models/regime_{args.pair}.pkl'
    output_path = args.output if args.output else model_path.replace(".pkl", ".pkl.new")
    classifier = RegimeClassifier(model_path=output_path, model_type='random_forest')
    embargo = max_forward_window
    print(f"  Embargo gap: {embargo} samples (max forward_window across intervals)")
    best_params = classifier.tune_hyperparameters(X_trainval, y_trainval, n_iter=20, cv=PurgedTimeSeriesSplit(n_splits=3, embargo=embargo))

    print("\n--- Evaluation on Test Set ---")
    y_pred = classifier.model.predict(X_test)
    print(f"Accuracy: {accuracy_score(y_test, y_pred):.4f}")
    target_names = ["Ranging", "Trending", "Danger"] if n_classes == 3 else ["Ranging", "Trending"]
    print(classification_report(y_test, y_pred, target_names=target_names))

    # Confusion matrix
    cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2] if n_classes == 3 else [0, 1])
    print("\n--- Confusion Matrix (rows=true, cols=predicted) ---")
    labels_str = ["Ranging", "Trending", "Danger"] if n_classes == 3 else ["Ranging", "Trending"]
    header = "true\\pred".ljust(12) + "  ".join(f"{l:>10s}" for l in labels_str)
    print(f"  {header}")
    for i, row in enumerate(cm):
        row_str = "  ".join(f"{v:>10d}" for v in row)
        print(f"  {labels_str[i]:<12s} {row_str}")

    # Regime transition accuracy
    if len(y_test) > 1:
        y_true_arr = y_test.values if hasattr(y_test, 'values') else y_test
        y_pred_arr = y_pred
        transition_mask = y_true_arr[1:] != y_true_arr[:-1]
        n_transitions = transition_mask.sum()
        if n_transitions > 0:
            transition_correct = (y_pred_arr[1:][transition_mask] == y_true_arr[1:][transition_mask]).sum()
            transition_acc = transition_correct / n_transitions
            print(f"\n--- Regime Transition Accuracy ---")
            print(f"  Transitions detected: {n_transitions}/{len(y_true_arr)-1} bars")
            print(f"  Transition accuracy:  {transition_acc:.4f} ({transition_correct}/{n_transitions})")
        else:
            print(f"\n--- Regime Transition Accuracy ---")
            print(f"  No regime transitions in test set (all one class)")

    # DANGER-specific metrics
    if n_classes == 3 and 2 in y_test.values:
        danger_mask = y_test == 2
        n_danger = danger_mask.sum()
        danger_correct = (y_pred[danger_mask.values] == 2).sum()
        print(f"\n--- DANGER Class Breakdown ---")
        print(f"  DANGER samples in test: {n_danger}")
        print(f"  DANGER recall:          {danger_correct}/{n_danger} ({danger_correct/n_danger:.2%})")
        danger_fn = (y_pred[danger_mask.values] != 2).sum()
        print(f"  DANGER false negatives: {danger_fn} (missed danger events)")

    # Show per-feature importance
    importances = list(zip(feature_cols, classifier.model.feature_importances_))
    importances.sort(key=lambda x: x[1], reverse=True)
    print("--- Feature Importances ---")
    for feat, imp in importances:
        print(f"  {feat:25s} {imp:.4f}")

    classifier.save_model()
    print(f"\nPipeline complete. Model saved to {classifier.model_path} for {args.pair}")


if __name__ == '__main__':
    main()
