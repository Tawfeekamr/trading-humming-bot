# src/rl/router.py
"""Routing policies for the RL evaluation pipeline.

Provides a unified interface to step the Gymnasium environment using either:
1. The trained PPO Agent.
2. The Supervised Random Forest baseline.
"""
from __future__ import annotations
import numpy as np

from src.rl.action_map import ACTION_TO_ENGINE_SIZE


def decode_routing_action(action: int) -> tuple[str, float]:
    """Decode and validate a PPO action using the canonical action map."""
    if isinstance(action, bool) or not isinstance(action, (int, np.integer)):
        raise ValueError("routing action must be an integer")
    action = int(action)
    if not 0 <= action < len(ACTION_TO_ENGINE_SIZE):
        raise ValueError(f"routing action outside 0..{len(ACTION_TO_ENGINE_SIZE) - 1}")
    engine, size_mult = ACTION_TO_ENGINE_SIZE[action]
    return engine, float(size_mult)

class RoutingPolicy:
    def predict(self, obs: np.ndarray) -> int:
        raise NotImplementedError


class PPORouter(RoutingPolicy):
    def __init__(self, model_path: str):
        from stable_baselines3 import PPO

        self.model = PPO.load(model_path)

    def predict(self, obs: np.ndarray) -> int:
        action, _states = self.model.predict(obs, deterministic=True)
        return int(action)


class SupervisedRegimeRouter(RoutingPolicy):
    def __init__(self, model_path: str):
        from src.ml.regime_classifier import RegimeClassifier

        self.clf = RegimeClassifier(model_path=model_path)
        self.clf.load_model()

    def predict(self, obs: np.ndarray) -> int:
        import pandas as pd

        # The first 14 elements of obs are exactly the 14 market features
        # required by the RegimeClassifier.
        features = obs[:14].reshape(1, -1)

        # The RegimeClassifier expects a DataFrame with the correct column names
        feature_cols = [
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

        df = pd.DataFrame(features, columns=feature_cols)
        regime = self.clf.predict_class(df)

        if regime == 0:  # RANGING
            return 1  # GRID 1.0x
        elif regime == 1:  # TRENDING
            return 4  # TREND 1.0x
        else:  # DANGER
            return 9  # FLAT / DANGER
