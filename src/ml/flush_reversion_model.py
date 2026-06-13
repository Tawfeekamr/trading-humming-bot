# src/ml/flush_reversion_model.py
"""Per-pair RandomForest flush-reversion classifier (mirrors regime_classifier.py).

Predicts P(TP before SL) at flush time. Walk-forward: train on first
(1-test_frac), evaluate on the rest (chronological — no shuffling across split).
"""
import os
import pickle
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score

from src.ml.flush_features import features_at_flush, FEATURE_COLUMNS


def build_dataset(bars: pd.DataFrame, features: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Join per-flush feature vectors with labels -> ML-ready DataFrame (keeps `ts`)."""
    rows = []
    for _, lab in labels.iterrows():
        vec = features_at_flush(bars, features, idx=lab["ts"])
        vec["ts"] = lab["ts"]
        vec["label"] = int(lab["label"])
        rows.append(vec)
    return pd.DataFrame(rows)


class FlushReversionClassifier:
    def __init__(self, random_state: int = 42):
        self.model = RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=2,
            class_weight="balanced", random_state=random_state, n_jobs=1,
        )
        self.trained = False

    def fit(self, X: pd.DataFrame, y):
        self.model.fit(X[FEATURE_COLUMNS], y)
        self.trained = True
        return self

    def predict_proba(self, X: pd.DataFrame):
        if not self.trained:
            raise ValueError("Model not trained")
        return self.model.predict_proba(X[FEATURE_COLUMNS])[:, list(self.model.classes_).index(1)]

    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"model": self.model, "features": FEATURE_COLUMNS, "version": 1}, f)

    @classmethod
    def load(cls, path: str):
        with open(path, "rb") as f:
            data = pickle.load(f)
        clf = cls()
        clf.model = data["model"]
        clf.trained = True
        return clf


def walk_forward_evaluate(dataset: pd.DataFrame, test_frac: float = 1 / 3) -> dict:
    """Train on first (1-test_frac), evaluate on the rest. Chronological split by `ts`."""
    if "ts" in dataset.columns:
        dataset = dataset.sort_values("ts").reset_index(drop=True)
    n = len(dataset)
    split = int(n * (1 - test_frac))
    train, test = dataset.iloc[:split], dataset.iloc[split:]
    if train.empty or test.empty:
        return {"oos_accuracy": 0.0, "oos_precision": 0.0, "oos_recall": 0.0, "oos_auc": 0.0, "n_test": 0}
    clf = FlushReversionClassifier()
    clf.fit(train, train["label"])
    p = clf.predict_proba(test)
    yhat = (p >= 0.5).astype(int)
    y = test["label"].values
    metrics = {
        "oos_accuracy": float(accuracy_score(y, yhat)),
        "oos_precision": float(precision_score(y, yhat, zero_division=0)),
        "oos_recall": float(recall_score(y, yhat, zero_division=0)),
        "n_test": int(len(test)),
    }
    try:
        metrics["oos_auc"] = float(roc_auc_score(y, p))
    except ValueError:
        metrics["oos_auc"] = 0.0
    return metrics
