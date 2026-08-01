import pickle
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.calibration import CalibratedClassifierCV
import os

from src.ml.model_metadata import (
    canonical_feature_contract_hash,
    metadata_for_classifier,
    read_metadata,
    write_artifact_with_metadata,
)

REGIME_RANGING = 0
REGIME_TRENDING = 1
REGIME_DANGER = 2


class RegimeClassifier:
    def __init__(self, model_path: str = 'models/regime_rf.pkl', model_type: str = 'random_forest'):
        self.model_path = model_path
        self.model_type = model_type
        self.model = None
        self.calibrated_model = None
        self.is_trained = False
        self.feature_columns = None
        self.feature_schema_version = None
        self.feature_contract_hash = None
        self.pair = None
        self.timeframe = None
        self.train_start = None
        self.train_end = None
        self.label_params = {}
        self.class_distribution = {}
        self.metrics = {}
        self.source_commit = None
        self.training_samples = None
        self.metadata = None

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
        if self.model is None:
            self.model = self._create_default_model()
        print(f"Training {self.model_type} Regime Classifier on {len(X_train)} samples...")
        fit_kwargs = {}
        if sample_weight is not None and self.model_type == 'xgboost':
            fit_kwargs['sample_weight'] = sample_weight
        self.model.fit(X_train, y_train, **fit_kwargs)
        self.training_samples = len(X_train)
        labels, counts = np.unique(np.asarray(y_train), return_counts=True)
        total = float(counts.sum())
        self.class_distribution = {
            int(label): float(count / total) for label, count in zip(labels, counts)
        }
        self.is_trained = True
        print("Training complete.")

    def calibrate(self, X_val, y_val, cv="prefit"):
        n_samples = len(X_val)
        # Isotonic can overfit on small samples; sigmoid is more stable
        method = 'isotonic' if n_samples >= 500 else 'sigmoid'
        # cv='prefit': the estimator is already fit; fit ONE calibrator on the
        # full held-out X_val (no k-fold refit). Standard prefit calibration, and
        # it avoids pickling k copies of the forest (cv=5 -> 6 forests ≈ 95MB;
        # prefit -> 1 forest ≈ 16MB). Pass cv=5 for the k-fold ensemble behaviour.
        self.calibrated_model = CalibratedClassifierCV(
            self.model, method=method, cv=cv
        )
        self.calibrated_model.fit(X_val, y_val)
        print(f"Calibration complete (method={method}, cv={cv}, n_samples={n_samples}).")

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

        if self.feature_contract_hash is None:
            self.feature_contract_hash = canonical_feature_contract_hash(self.feature_columns)
        metadata = metadata_for_classifier(self)
        data = {
            'model': self.model,
            'model_type': self.model_type,
            'version': 5,
            'feature_columns': self.feature_columns,
            'feature_schema_version': self.feature_schema_version,
            'feature_contract_hash': self.feature_contract_hash,
            'class_distribution': self.class_distribution,
            'training_samples': self.training_samples,
        }
        if self.calibrated_model is not None:
            data['calibrated_model'] = self.calibrated_model
        artifact = pickle.dumps(data)
        write_artifact_with_metadata(self.model_path, artifact, metadata)
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
            self.feature_columns = data.get('feature_columns', None)
            self.feature_schema_version = data.get('feature_schema_version', None)
            self.feature_contract_hash = data.get('feature_contract_hash', None)
            self.class_distribution = data.get('class_distribution', {})
            self.training_samples = data.get('training_samples', None)
        else:
            # Legacy format: raw sklearn model
            self.model = data
            self.model_type = 'random_forest'
            self.calibrated_model = None
        manifest_path = f"{self.model_path}.metadata.json"
        if os.path.exists(manifest_path):
            manifest = read_metadata(self.model_path)
            self.metadata = manifest
            self.pair = manifest["pair"]
            self.timeframe = manifest["timeframe"]
            self.train_start = manifest["train_start"]
            self.train_end = manifest["train_end"]
            self.feature_contract_hash = manifest["feature_contract_hash"]
            self.label_params = manifest["label_params"]
            self.class_distribution = manifest["class_distribution"]
            self.metrics = manifest["metrics"]
            self.source_commit = manifest["source_commit"]
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
