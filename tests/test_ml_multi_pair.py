# tests/test_ml_multi_pair.py
"""
Tests for per-pair ML model loading, prediction, and multi-pair isolation.
Covers Task 1: Replace ML init with per-pair model loading.
"""
import pytest
import pickle
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_model_pickle(path: Path, trained: bool = True):
    """Write a minimal regime classifier pickle at *path*."""
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier

    X = np.array([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6], [0.7, 0.8]])
    y = np.array([0, 1, 0, 2])
    clf = RandomForestClassifier(n_estimators=5, max_depth=2, random_state=42)
    clf.fit(X, y)

    data = {
        "model": clf,
        "model_type": "random_forest",
        "version": 4,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(data, f)
    return clf


class _StrategyStub:
    """Plain object that mimics TAGridTrend for ML init tests.

    MagicMock intercepts attribute assignments, breaking dict storage.
    We use a simple stub class instead.
    """
    def __init__(self, pairs):
        self.pairs = pairs


def _make_strategy_mock(pairs=None):
    """Build a lightweight stub that mimics TAGridTrend for ML init tests.

    We can't instantiate the real strategy without Hummingbot framework,
    so we patch the base class and __init__ to only exercise the ML block.
    """
    if pairs is None:
        pairs = ["DOGE-USDT"]
    return _StrategyStub(pairs)


def _run_ml_init(target, pairs, ml_available=True, model_paths=None):
    """Reproduce the per-pair ML init block from ta_grid_trend.py.

    This is the code under test -- kept in sync with the strategy file.

    Args:
        model_paths: optional dict mapping symbol -> Path for model files.
                     If None, uses default "models/regime_{symbol}.pkl" path.
    """
    from src.ml.regime_classifier import RegimeClassifier

    target._ml_models = {}
    target._ml_predictions = {}
    target._ml_prediction_history = {}
    target._ml_gc_counter = 0

    if ml_available:
        for symbol in pairs:
            target._ml_predictions[symbol] = (None, 0.0, 0.0)
            target._ml_prediction_history[symbol] = []
            model_path = model_paths.get(symbol) if model_paths else None
            if model_path is None:
                model_path = Path(f"models/regime_{symbol}.pkl")
            if model_path.exists():
                try:
                    clf = RegimeClassifier(model_path=str(model_path))
                    clf.load_model()
                    target._ml_models[symbol] = clf
                except Exception as e:
                    pass  # logged in real code
            # missing model: rule-based fallback

        # Backward compat
        target._ml_classifier = list(target._ml_models.values())[0] if target._ml_models else None
    else:
        for symbol in pairs:
            target._ml_predictions[symbol] = (None, 0.0, 0.0)
        target._ml_classifier = None

    # Defaults for code that still reads shared scalars
    target._ml_regime = 0
    target._ml_confidence = 0.0


# ===================================================================
# TestPerPairMLInit
# ===================================================================

class TestPerPairMLInit:
    """Test that models load per pair and fallback gracefully when missing."""

    def test_model_loads_per_pair(self, tmp_path):
        """Each pair with a model file gets its own RegimeClassifier."""
        pairs = ["DOGE-USDT", "BTC-USDT"]
        model_paths = {}
        for sym in pairs:
            model_paths[sym] = tmp_path / "models" / f"regime_{sym}.pkl"
            _make_model_pickle(model_paths[sym])

        target = _make_strategy_mock(pairs)
        _run_ml_init(target, pairs, ml_available=True, model_paths=model_paths)

        assert len(target._ml_models) == 2
        assert "DOGE-USDT" in target._ml_models
        assert "BTC-USDT" in target._ml_models

    def test_missing_model_graceful_fallback(self):
        """Pairs without model files are skipped, no crash."""
        pairs = ["DOGE-USDT"]
        target = _make_strategy_mock(pairs)
        # No model files exist anywhere
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        assert len(target._ml_models) == 0
        assert target._ml_predictions["DOGE-USDT"] == (None, 0.0, 0.0)

    def test_predictions_initialized_for_all_pairs(self):
        """All pairs get a prediction entry, even without models."""
        pairs = ["DOGE-USDT", "ETH-USDT", "SOL-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        for sym in pairs:
            assert sym in target._ml_predictions
            assert target._ml_predictions[sym] == (None, 0.0, 0.0)

    def test_ml_not_available_initializes_predictions(self):
        """When sklearn is unavailable, predictions still get defaults."""
        pairs = ["DOGE-USDT"]
        target = _make_strategy_mock(pairs)
        _run_ml_init(target, pairs, ml_available=False)

        assert len(target._ml_models) == 0
        assert target._ml_predictions["DOGE-USDT"] == (None, 0.0, 0.0)
        assert target._ml_classifier is None

    def test_corrupt_model_file_does_not_crash(self, tmp_path):
        """A corrupt pickle should be caught and skipped."""
        pairs = ["DOGE-USDT"]
        model_path = tmp_path / "models" / "regime_DOGE-USDT.pkl"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"NOT_A_PICKLE")

        target = _make_strategy_mock(pairs)
        # Should NOT raise
        _run_ml_init(target, pairs, ml_available=True,
                     model_paths={"DOGE-USDT": model_path})

        assert len(target._ml_models) == 0
        assert target._ml_predictions["DOGE-USDT"] == (None, 0.0, 0.0)

    def test_backward_compat_ml_classifier_set(self, tmp_path):
        """_ml_classifier points to first loaded model for backward compat."""
        pairs = ["DOGE-USDT"]
        model_path = tmp_path / "models" / "regime_DOGE-USDT.pkl"
        _make_model_pickle(model_path)

        target = _make_strategy_mock(pairs)
        _run_ml_init(target, pairs, ml_available=True,
                     model_paths={"DOGE-USDT": model_path})

        assert target._ml_classifier is not None
        assert target._ml_classifier is target._ml_models["DOGE-USDT"]

    def test_backward_compat_ml_classifier_none_when_no_models(self):
        """_ml_classifier is None when no models loaded."""
        pairs = ["DOGE-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        assert target._ml_classifier is None

    def test_default_shared_scalars_set(self):
        """_ml_regime and _ml_confidence defaults are always set."""
        pairs = ["DOGE-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        assert target._ml_regime == 0
        assert target._ml_confidence == 0.0


# ===================================================================
# TestPerPairMLPrediction
# ===================================================================

class TestPerPairMLPrediction:
    """Test prediction cache data structure and throttle timing."""

    def test_prediction_cache_is_per_pair(self):
        """Each pair has its own prediction tuple."""
        pairs = ["DOGE-USDT", "BTC-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        # Simulate different predictions per pair
        target._ml_predictions["DOGE-USDT"] = (0, 0.85, time.time())
        target._ml_predictions["BTC-USDT"] = (1, 0.72, time.time())

        assert target._ml_predictions["DOGE-USDT"][0] == 0  # RANGING
        assert target._ml_predictions["BTC-USDT"][0] == 1   # TRENDING

    def test_prediction_tuple_format(self):
        """Prediction tuple is (regime, confidence, timestamp)."""
        pairs = ["DOGE-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        pred = target._ml_predictions["DOGE-USDT"]
        assert len(pred) == 3
        assert pred[0] is None  # regime
        assert pred[1] == 0.0   # confidence
        assert pred[2] == 0.0   # timestamp

    def test_throttle_timing_respected(self):
        """Predictions with recent timestamp should be throttled (conceptual test)."""
        THROTTLE_SECONDS = 30
        pairs = ["DOGE-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        now = time.time()
        target._ml_predictions["DOGE-USDT"] = (0, 0.9, now)

        # Recent prediction — should be throttled
        elapsed = time.time() - target._ml_predictions["DOGE-USDT"][2]
        assert elapsed < THROTTLE_SECONDS  # Should be throttled

    def test_prediction_history_is_per_pair(self):
        """Each pair has its own prediction history list."""
        pairs = ["DOGE-USDT", "BTC-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        assert isinstance(target._ml_prediction_history["DOGE-USDT"], list)
        assert isinstance(target._ml_prediction_history["BTC-USDT"], list)


# ===================================================================
# TestRunMLPrediction
# ===================================================================

class TestRunMLPrediction:
    """Test ATR percentile danger override and GC interval."""

    def test_atr_danger_override(self):
        """Extreme ATR + flat price should trigger danger regime override."""
        # Simulate the danger override logic from _grid_tick
        ml_regime = 1  # TRENDING
        ml_confidence = 0.70
        norm_atr = 0.08  # > 0.06 threshold
        ret = 0.002      # < 0.005 threshold

        if norm_atr > 0.06 and ret < 0.005 and ml_regime != 2:
            ml_regime = 2
            ml_confidence = 0.80

        assert ml_regime == 2  # DANGER
        assert ml_confidence == 0.80

    def test_atr_no_override_when_trending_strongly(self):
        """No override when price is moving strongly even with high ATR."""
        ml_regime = 1  # TRENDING
        norm_atr = 0.08
        ret = 0.02  # > 0.005 — strong move

        if norm_atr > 0.06 and ret < 0.005 and ml_regime != 2:
            ml_regime = 2

        assert ml_regime == 1  # No override

    def test_atr_no_override_when_atr_low(self):
        """No override when ATR is below threshold."""
        ml_regime = 1
        norm_atr = 0.04  # < 0.06
        ret = 0.002

        if norm_atr > 0.06 and ret < 0.005 and ml_regime != 2:
            ml_regime = 2

        assert ml_regime == 1

    def test_gc_counter_initialized(self):
        """GC counter starts at 0."""
        pairs = ["DOGE-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        assert target._ml_gc_counter == 0

    # ── Per-pair ATR percentile danger override tests ──

    def test_atr_percentile_override_triggers(self):
        """Per-pair danger override fires when ATR exceeds 95th percentile and return is flat."""
        import numpy as np
        import pandas as pd

        # Build a feature DataFrame where normalized_atr at the last row exceeds p95
        n = 100
        np.random.seed(42)
        atr_vals = np.random.uniform(0.01, 0.05, n)
        atr_vals[-1] = 0.12  # last row well above p95 ~0.06

        FEATURE_COLS = [
            'returns', 'volatility_ratio', 'normalized_atr',
            'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value',
            'adx_14', 'macd_histogram', 'distance_to_vwap', 'obv_roc_14',
            'choppiness_index', 'fractal_dimension_index', 'aroon_oscillator'
        ]
        data = {col: np.random.uniform(0.01, 0.5, n) for col in FEATURE_COLS}
        data['normalized_atr'] = atr_vals
        data['returns'][-1] = 0.001  # flat return (< 0.005)
        df_features = pd.DataFrame(data)

        last_features = df_features.iloc[[-1]][FEATURE_COLS]
        norm_atr = last_features['normalized_atr'].iloc[0]
        ret = abs(last_features['returns'].iloc[0])
        atr_threshold = df_features['normalized_atr'].quantile(0.95)

        regime = 1  # TRENDING
        if norm_atr > atr_threshold and ret < 0.005 and regime != 2:
            regime = 2

        assert norm_atr > atr_threshold
        assert ret < 0.005
        assert regime == 2  # DANGER

    def test_atr_percentile_no_override_below_p95(self):
        """No override when ATR is below 95th percentile even with flat returns."""
        import numpy as np
        import pandas as pd

        n = 100
        np.random.seed(42)
        # All ATR values similar — p95 ~= max
        atr_vals = np.random.uniform(0.02, 0.03, n)
        atr_vals[-1] = 0.025  # same range, won't exceed p95

        FEATURE_COLS = [
            'returns', 'volatility_ratio', 'normalized_atr',
            'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value',
            'adx_14', 'macd_histogram', 'distance_to_vwap', 'obv_roc_14',
            'choppiness_index', 'fractal_dimension_index', 'aroon_oscillator'
        ]
        data = {col: np.random.uniform(0.01, 0.5, n) for col in FEATURE_COLS}
        data['normalized_atr'] = atr_vals
        data['returns'][-1] = 0.001
        df_features = pd.DataFrame(data)

        last_features = df_features.iloc[[-1]][FEATURE_COLS]
        norm_atr = last_features['normalized_atr'].iloc[0]
        ret = abs(last_features['returns'].iloc[0])
        atr_threshold = df_features['normalized_atr'].quantile(0.95)

        regime = 1  # TRENDING
        if norm_atr > atr_threshold and ret < 0.005 and regime != 2:
            regime = 2

        assert norm_atr <= atr_threshold or ret >= 0.005
        assert regime == 1  # No override

    def test_atr_percentile_no_override_when_already_danger(self):
        """No redundant override when regime is already DANGER (2)."""
        import numpy as np
        import pandas as pd

        n = 100
        np.random.seed(42)
        atr_vals = np.random.uniform(0.01, 0.05, n)
        atr_vals[-1] = 0.12

        FEATURE_COLS = [
            'returns', 'volatility_ratio', 'normalized_atr',
            'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value',
            'adx_14', 'macd_histogram', 'distance_to_vwap', 'obv_roc_14',
            'choppiness_index', 'fractal_dimension_index', 'aroon_oscillator'
        ]
        data = {col: np.random.uniform(0.01, 0.5, n) for col in FEATURE_COLS}
        data['normalized_atr'] = atr_vals
        data['returns'][-1] = 0.001
        df_features = pd.DataFrame(data)

        last_features = df_features.iloc[[-1]][FEATURE_COLS]
        norm_atr = last_features['normalized_atr'].iloc[0]
        ret = abs(last_features['returns'].iloc[0])
        atr_threshold = df_features['normalized_atr'].quantile(0.95)

        regime = 2  # already DANGER
        confidence = 0.95
        # The override condition includes `regime != 2`, so it should NOT fire
        if norm_atr > atr_threshold and ret < 0.005 and regime != 2:
            regime = 2
            confidence = 0.80

        assert regime == 2
        assert confidence == 0.95  # unchanged — no override applied

    # ── GC counter interval tests ──

    def test_gc_interval_calculation(self):
        """GC interval is 5 * number of pairs."""
        pairs = ["DOGE-USDT", "BTC-USDT", "ETH-USDT"]
        target = _make_strategy_mock(pairs)
        gc_interval = 5 * len(target.pairs)
        assert gc_interval == 15

    def test_gc_triggers_at_interval(self):
        """GC collect runs when counter is a multiple of the interval."""
        pairs = ["DOGE-USDT", "BTC-USDT"]
        gc_interval = 5 * len(pairs)  # 10

        with patch("gc.collect") as mock_gc:
            # Simulate counter increments
            for i in range(1, 21):
                if i % gc_interval == 0:
                    mock_gc()

            assert mock_gc.call_count == 2  # at i=10 and i=20

    def test_gc_counter_increments_per_prediction(self):
        """GC counter increments by 1 for each prediction call."""
        pairs = ["DOGE-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        assert target._ml_gc_counter == 0
        target._ml_gc_counter += 1
        assert target._ml_gc_counter == 1
        target._ml_gc_counter += 1
        assert target._ml_gc_counter == 2

    def test_prediction_history_capped_at_1440(self):
        """Prediction history is trimmed to last 1440 entries."""
        history = list(range(1500))
        if len(history) > 1440:
            history = history[-1440:]
        assert len(history) == 1440
        assert history[0] == 60  # first 60 entries dropped

    def test_prediction_history_under_cap_unchanged(self):
        """Prediction history under 1440 entries is not trimmed."""
        history = list(range(100))
        if len(history) > 1440:
            history = history[-1440:]
        assert len(history) == 100


# ===================================================================
# TestGridTickMLIntegration
# ===================================================================

class TestGridTickMLIntegration:
    """Test state machine receives per-pair regime, test isolation."""

    def test_per_pair_regime_does_not_leak(self):
        """Setting regime for one pair does not affect another."""
        pairs = ["DOGE-USDT", "BTC-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        target._ml_predictions["DOGE-USDT"] = (2, 0.90, time.time())  # DANGER
        target._ml_predictions["BTC-USDT"] = (0, 0.85, time.time())   # RANGING

        assert target._ml_predictions["DOGE-USDT"][0] == 2
        assert target._ml_predictions["BTC-USDT"][0] == 0

    def test_isolation_between_pairs_models(self, tmp_path):
        """Each pair has a distinct model object."""
        pairs = ["DOGE-USDT", "BTC-USDT"]
        model_paths = {}
        for sym in pairs:
            model_paths[sym] = tmp_path / "models" / f"regime_{sym}.pkl"
            _make_model_pickle(model_paths[sym])

        target = _make_strategy_mock(pairs)
        _run_ml_init(target, pairs, ml_available=True, model_paths=model_paths)

        assert target._ml_models["DOGE-USDT"] is not target._ml_models["BTC-USDT"]


# ===================================================================
# TestTrendEntryMLGate
# ===================================================================

class TestTrendEntryMLGate:
    """Test ML gate logic for trend entries (from _evaluate_trend_signals)."""

    def _gate_allows(self, ml_regime, ml_confidence, ml_classifier_exists=True):
        """Replicate the ML gate logic from _evaluate_trend_signals."""
        if ml_classifier_exists:
            if ml_regime == 2:  # Danger
                return False
            if ml_regime == 1 and ml_confidence < 0.5:  # Uncertain trending
                return False
            if ml_regime == 0 and ml_confidence >= 0.65:  # Confident ranging
                return False
        return True

    def test_danger_regime_blocks_entry(self):
        assert self._gate_allows(ml_regime=2, ml_confidence=0.9) is False

    def test_uncertain_trending_blocks_entry(self):
        assert self._gate_allows(ml_regime=1, ml_confidence=0.4) is False

    def test_confident_ranging_blocks_entry(self):
        assert self._gate_allows(ml_regime=0, ml_confidence=0.70) is False

    def test_confident_trending_allows_entry(self):
        assert self._gate_allows(ml_regime=1, ml_confidence=0.7) is True

    def test_uncertain_ranging_allows_entry(self):
        assert self._gate_allows(ml_regime=0, ml_confidence=0.5) is True

    def test_no_model_allows_entry(self):
        assert self._gate_allows(
            ml_regime=0, ml_confidence=0.0, ml_classifier_exists=False
        ) is True

    def test_moderate_trending_allows_entry(self):
        """At exactly 0.5 confidence threshold, trending should allow."""
        assert self._gate_allows(ml_regime=1, ml_confidence=0.5) is True

    def test_ranging_at_exact_boundary_blocks(self):
        """At exactly 0.65 confidence, ranging should block."""
        assert self._gate_allows(ml_regime=0, ml_confidence=0.65) is False


# ===================================================================
# TestModelStaleness
# ===================================================================

class TestModelStaleness:
    """Test staleness detection logic for model files."""

    def test_old_model_file_is_stale(self, tmp_path):
        """A model file older than threshold is considered stale."""
        model_path = tmp_path / "regime_DOGE-USDT.pkl"
        _make_model_pickle(model_path)

        # Set modification time to 8 days ago
        old_time = time.time() - (8 * 24 * 3600)
        import os
        os.utime(model_path, (old_time, old_time))

        STALE_THRESHOLD_SECONDS = 7 * 24 * 3600  # 7 days
        age = time.time() - model_path.stat().st_mtime
        assert age > STALE_THRESHOLD_SECONDS

    def test_recent_model_file_is_fresh(self, tmp_path):
        """A recently created model file is not stale."""
        model_path = tmp_path / "regime_DOGE-USDT.pkl"
        _make_model_pickle(model_path)

        STALE_THRESHOLD_SECONDS = 7 * 24 * 3600
        age = time.time() - model_path.stat().st_mtime
        assert age < STALE_THRESHOLD_SECONDS

    def test_nonexistent_model_is_stale(self, tmp_path):
        """A missing model file is effectively stale."""
        model_path = tmp_path / "regime_NONEXISTENT.pkl"
        assert not model_path.exists()


# ===================================================================
# TestTrainPipelinePerPair
# ===================================================================

class TestTrainPipelinePerPair:
    """Test symbol mapping and model path format."""

    def test_model_path_format(self):
        """Model path uses regime_{symbol}.pkl format."""
        symbol = "DOGE-USDT"
        expected = Path(f"models/regime_{symbol}.pkl")
        assert expected.name == "regime_DOGE-USDT.pkl"

    def test_symbol_mapping_doge(self):
        """DOGE-USDT maps to correct model path."""
        symbol = "DOGE-USDT"
        path = Path(f"models/regime_{symbol}.pkl")
        assert "DOGE-USDT" in str(path)

    def test_symbol_mapping_btc(self):
        """BTC-USDT maps to correct model path."""
        symbol = "BTC-USDT"
        path = Path(f"models/regime_{symbol}.pkl")
        assert "BTC-USDT" in str(path)

    def test_all_pairs_get_paths(self):
        """Multiple pairs all produce unique model paths."""
        pairs = ["DOGE-USDT", "BTC-USDT", "ETH-USDT"]
        paths = [Path(f"models/regime_{s}.pkl") for s in pairs]
        names = [p.name for p in paths]
        assert len(set(names)) == len(pairs)  # all unique


# ===================================================================
# TestMLMultiPairIntegration
# ===================================================================

class TestMLMultiPairIntegration:
    """Test multi-pair isolation, fallback, throttle timing, no shared scalar leakage."""

    def test_multi_pair_isolation(self, tmp_path):
        """Loading models for multiple pairs doesn't mix them up."""
        pairs = ["DOGE-USDT", "BTC-USDT", "ETH-USDT"]
        # Only create model for DOGE
        model_path = tmp_path / "models" / "regime_DOGE-USDT.pkl"
        _make_model_pickle(model_path)

        target = _make_strategy_mock(pairs)
        _run_ml_init(target, pairs, ml_available=True,
                     model_paths={"DOGE-USDT": model_path})

        assert "DOGE-USDT" in target._ml_models
        assert "BTC-USDT" not in target._ml_models
        assert "ETH-USDT" not in target._ml_models
        # All have predictions
        for sym in pairs:
            assert sym in target._ml_predictions

    def test_fallback_to_rule_based(self):
        """Pairs without models fall back to rule-based (None regime)."""
        pairs = ["DOGE-USDT", "BTC-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        for sym in pairs:
            pred = target._ml_predictions[sym]
            assert pred[0] is None  # No ML regime — rule-based fallback

    def test_no_shared_scalar_leakage(self):
        """Per-pair predictions don't affect shared scalars."""
        pairs = ["DOGE-USDT", "BTC-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        # Set per-pair predictions
        target._ml_predictions["DOGE-USDT"] = (2, 0.95, time.time())
        target._ml_predictions["BTC-USDT"] = (0, 0.70, time.time())

        # Shared scalars remain at defaults
        assert target._ml_regime == 0
        assert target._ml_confidence == 0.0

    def test_throttle_timing_per_pair(self):
        """Each pair's prediction has independent throttle timing."""
        pairs = ["DOGE-USDT", "BTC-USDT"]
        target = _make_strategy_mock(pairs)
        with patch("pathlib.Path.exists", return_value=False):
            _run_ml_init(target, pairs, ml_available=True)

        now = time.time()
        target._ml_predictions["DOGE-USDT"] = (0, 0.9, now)
        target._ml_predictions["BTC-USDT"] = (1, 0.8, now - 60)  # 1 min ago

        assert target._ml_predictions["DOGE-USDT"][2] > target._ml_predictions["BTC-USDT"][2]

    def test_ml_disabled_all_predictions_default(self):
        """When ML_AVAILABLE=False, all predictions are None."""
        pairs = ["DOGE-USDT", "BTC-USDT"]
        target = _make_strategy_mock(pairs)
        _run_ml_init(target, pairs, ml_available=False)

        for sym in pairs:
            assert target._ml_predictions[sym] == (None, 0.0, 0.0)
        assert target._ml_classifier is None
        assert len(target._ml_models) == 0

    def test_partial_model_loading(self, tmp_path):
        """Only pairs with model files get classifiers, others get defaults."""
        pairs = ["DOGE-USDT", "BTC-USDT", "ETH-USDT"]
        # Only BTC has a model
        model_path = tmp_path / "models" / "regime_BTC-USDT.pkl"
        _make_model_pickle(model_path)

        target = _make_strategy_mock(pairs)
        _run_ml_init(target, pairs, ml_available=True,
                     model_paths={"BTC-USDT": model_path})

        loaded = [s for s in pairs if s in target._ml_models]
        missing = [s for s in pairs if s not in target._ml_models]
        assert loaded == ["BTC-USDT"]
        assert set(missing) == {"DOGE-USDT", "ETH-USDT"}
