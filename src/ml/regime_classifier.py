import pickle
import numpy as np
from sklearn.ensemble import RandomForestClassifier
import os

REGIME_RANGING = 0
REGIME_TRENDING = 1
REGIME_DANGER = 2


class RegimeClassifier:
    def __init__(self, model_path: str = 'models/regime_rf.pkl'):
        self.model_path = model_path
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=5,
            random_state=42,
            class_weight='balanced'
        )
        self.is_trained = False

    def train(self, X_train, y_train):
        """
        Trains the Random Forest model on the provided features (X) and labels (y).
        """
        print(f"Training Regime Classifier on {len(X_train)} samples...")
        self.model.fit(X_train, y_train)
        self.is_trained = True
        print("Training complete.")

    def predict_proba(self, X):
        """Returns probability of trending (class 1). Backward compat for 2/3-class models."""
        if not self.is_trained:
            raise ValueError("Model is not trained yet.")
        probs = self.model.predict_proba(X)
        classes = list(self.model.classes_)
        trending_idx = classes.index(1) if 1 in classes else 1
        return probs[:, trending_idx]

    def predict_proba_full(self, X):
        """Returns full probability breakdown {0: p_ranging, 1: p_trending, 2: p_danger}."""
        if not self.is_trained:
            raise ValueError("Model is not trained yet.")
        probs = self.model.predict_proba(X)[0]
        return {int(c): float(p) for c, p in zip(self.model.classes_, probs)}

    def predict_class(self, X):
        """Returns the class with highest probability (0, 1, or 2)."""
        if not self.is_trained:
            raise ValueError("Model is not trained yet.")
        return int(self.model.predict(X)[0])

    def save_model(self):
        """Saves the trained model to disk."""
        if not self.is_trained:
            raise ValueError("Cannot save an untrained model.")

        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        with open(self.model_path, 'wb') as f:
            pickle.dump(self.model, f)
        print(f"Model saved to {self.model_path}")

    def load_model(self):
        """Loads a trained model from disk."""
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"No model found at {self.model_path}")

        with open(self.model_path, 'rb') as f:
            self.model = pickle.load(f)
        self.is_trained = True
        print(f"Model loaded from {self.model_path}")

    def tune_hyperparameters(self, X_train, y_train, n_iter=20, cv=3):
        """Find optimal hyperparameters via randomized search."""
        from sklearn.model_selection import RandomizedSearchCV

        param_distributions = {
            'n_estimators': [200, 300, 400, 500],
            'max_depth': [8, 10, 12, 15],
            'min_samples_split': [2, 5, 10],
            'min_samples_leaf': [1, 2, 4],
            'max_features': ['sqrt', 'log2'],
            'class_weight': ['balanced', 'balanced_subsample'],
        }
        search = RandomizedSearchCV(
            estimator=RandomForestClassifier(random_state=42, n_jobs=1),
            param_distributions=param_distributions,
            n_iter=n_iter,
            cv=cv,
            scoring='f1_weighted',
            random_state=42,
            n_jobs=1,
            verbose=1,
        )
        search.fit(X_train, y_train)
        self.model = search.best_estimator_
        self.is_trained = True
        print(f"Best params: {search.best_params_}")
        print(f"Best CV score (weighted F1): {search.best_score_:.4f}")
        return search.best_params_
