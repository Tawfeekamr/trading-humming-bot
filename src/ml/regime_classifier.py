import pickle
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.calibration import CalibratedClassifierCV
import os

REGIME_RANGING = 0
REGIME_TRENDING = 1
REGIME_DANGER = 2


class RegimeClassifier:
    def __init__(self, model_path: str = 'models/regime_rf.pkl', model_type: str = 'xgboost'):
        self.model_path = model_path
        self.model_type = model_type
        self.model = self._create_default_model()
        self.calibrated_model = None
        self.is_trained = False

    def _create_default_model(self):
        if self.model_type == 'xgboost':
            from xgboost import XGBClassifier
            return XGBClassifier(
                n_estimators=300,
                max_depth=6,
                learning_rate=0.1,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                eval_metric='mlogloss',
            )
        return RandomForestClassifier(
            n_estimators=100,
            max_depth=5,
            random_state=42,
            class_weight='balanced'
        )

    @property
    def _active_model(self):
        return self.calibrated_model if self.calibrated_model is not None else self.model

    def train(self, X_train, y_train, sample_weight=None):
        print(f"Training {self.model_type} Regime Classifier on {len(X_train)} samples...")
        fit_kwargs = {}
        if sample_weight is not None and self.model_type == 'xgboost':
            fit_kwargs['sample_weight'] = sample_weight
        self.model.fit(X_train, y_train, **fit_kwargs)
        self.is_trained = True
        print("Training complete.")

    def calibrate(self, X_val, y_val):
        self.calibrated_model = CalibratedClassifierCV(
            self.model, method='isotonic', cv=5
        )
        self.calibrated_model.fit(X_val, y_val)
        print(f"Calibration complete.")

    def predict_proba(self, X):
        if not self.is_trained:
            raise ValueError("Model is not trained yet.")
        probs = self._active_model.predict_proba(X)
        classes = list(self._active_model.classes_)
        trending_idx = classes.index(1) if 1 in classes else 1
        return probs[:, trending_idx]

    def predict_proba_full(self, X):
        if not self.is_trained:
            raise ValueError("Model is not trained yet.")
        probs = self._active_model.predict_proba(X)[0]
        return {int(c): float(p) for c, p in zip(self._active_model.classes_, probs)}

    def predict_class(self, X):
        if not self.is_trained:
            raise ValueError("Model is not trained yet.")
        return int(self._active_model.predict(X)[0])

    def save_model(self):
        if not self.is_trained:
            raise ValueError("Cannot save an untrained model.")

        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        data = {
            'model': self.model,
            'model_type': self.model_type,
            'version': 4,
        }
        if self.calibrated_model is not None:
            data['calibrated_model'] = self.calibrated_model
        with open(self.model_path, 'wb') as f:
            pickle.dump(data, f)
        print(f"Model saved to {self.model_path} (type={self.model_type})")

    def load_model(self):
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"No model found at {self.model_path}")

        with open(self.model_path, 'rb') as f:
            data = pickle.load(f)
        if isinstance(data, dict) and 'model' in data:
            self.model = data['model']
            self.model_type = data.get('model_type', 'random_forest')
            self.calibrated_model = data.get('calibrated_model', None)
        else:
            # Legacy format: raw sklearn model
            self.model = data
            self.model_type = 'random_forest'
            self.calibrated_model = None
        self.is_trained = True
        print(f"Model loaded from {self.model_path} (type={self.model_type})")

    def tune_hyperparameters(self, X_train, y_train, n_iter=20, cv=3, sample_weight=None):
        from sklearn.model_selection import RandomizedSearchCV

        if self.model_type == 'xgboost':
            param_distributions = {
                'n_estimators': [200, 300, 500, 700],
                'max_depth': [4, 6, 8, 10],
                'learning_rate': [0.01, 0.05, 0.1, 0.2],
                'subsample': [0.7, 0.8, 0.9, 1.0],
                'colsample_bytree': [0.7, 0.8, 0.9],
                'min_child_weight': [1, 3, 5],
                'gamma': [0, 0.1, 0.2],
            }
            from xgboost import XGBClassifier
            estimator = XGBClassifier(
                random_state=42, eval_metric='mlogloss',
                n_jobs=1,
            )
        else:
            param_distributions = {
                'n_estimators': [200, 300, 400, 500],
                'max_depth': [8, 10, 12, 15],
                'min_samples_split': [2, 5, 10],
                'min_samples_leaf': [1, 2, 4],
                'max_features': ['sqrt', 'log2'],
                'class_weight': ['balanced', 'balanced_subsample',
                                 {0: 1, 1: 1, 2: 3}, {0: 1, 1: 1, 2: 5}, {0: 1, 1: 1, 2: 8}],
            }
            estimator = RandomForestClassifier(random_state=42, n_jobs=1)

        fit_params = {}
        if sample_weight is not None and self.model_type == 'xgboost':
            fit_params['sample_weight'] = sample_weight

        search = RandomizedSearchCV(
            estimator=estimator,
            param_distributions=param_distributions,
            n_iter=n_iter,
            cv=cv,
            scoring='f1_weighted',
            random_state=42,
            n_jobs=1,
            verbose=1,
        )
        search.fit(X_train, y_train, **fit_params)
        self.model = search.best_estimator_
        self.is_trained = True
        print(f"Best params: {search.best_params_}")
        print(f"Best CV score (weighted F1): {search.best_score_:.4f}")
        return search.best_params_
