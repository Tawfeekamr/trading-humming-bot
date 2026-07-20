from src.data.feature_contract import (
    FEATURE_SCHEMA_VERSION,
    MARKET_FEATURE_COLS,
    TIME_FEATURE_COLS,
    assert_market_feature_contract,
)
from src.ml.regime_classifier import RegimeClassifier


class _PickleableModel:
    classes_ = [0, 1, 2]

    def predict_proba(self, X):
        return [[0.1, 0.8, 0.1]]

    def predict(self, X):
        return [1]


def test_market_feature_contract_is_canonical_14_columns():
    assert FEATURE_SCHEMA_VERSION >= 1
    assert MARKET_FEATURE_COLS == [
        "returns",
        "volatility_ratio",
        "normalized_atr",
        "trend_strength",
        "rsi_14",
        "volume_ratio",
        "close_location_value",
        "adx_14",
        "macd_histogram",
        "distance_to_vwap",
        "obv_roc_14",
        "choppiness_index",
        "fractal_dimension_index",
        "aroon_oscillator",
    ]
    assert TIME_FEATURE_COLS == ["hour_sin", "hour_cos", "day_of_week"]


def test_assert_market_feature_contract_rejects_reordered_columns():
    reordered = list(reversed(MARKET_FEATURE_COLS))
    try:
        assert_market_feature_contract(reordered)
    except ValueError as exc:
        assert "feature contract mismatch" in str(exc)
    else:
        raise AssertionError("reordered feature columns must be rejected")


def test_regime_classifier_persists_feature_contract(tmp_path):

    path = tmp_path / "regime.pkl"
    clf = RegimeClassifier(model_path=str(path), model_type="random_forest")
    clf.model = _PickleableModel()
    clf.is_trained = True
    clf.feature_columns = list(MARKET_FEATURE_COLS)
    clf.feature_schema_version = FEATURE_SCHEMA_VERSION
    clf.save_model()

    loaded = RegimeClassifier(model_path=str(path), model_type="random_forest")
    loaded.load_model()

    assert loaded.feature_columns == MARKET_FEATURE_COLS
    assert loaded.feature_schema_version == FEATURE_SCHEMA_VERSION
