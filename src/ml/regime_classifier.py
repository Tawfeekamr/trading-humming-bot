import pickle
import numpy as np
from sklearn.ensemble import RandomForestClassifier
import os

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
        """
        Returns the probability of the current regime being 'Trending' (Class 1).
        """
        if not self.is_trained:
            raise ValueError("Model is not trained yet.")
        
        # predict_proba returns array of [prob_class_0, prob_class_1]
        probs = self.model.predict_proba(X)
        return probs[:, 1] # Return probability of regime 1 (trending)
        
    def predict(self, X, threshold=0.6):
        """
        Predicts the regime based on a confidence threshold.
        """
        probs = self.predict_proba(X)
        return (probs >= threshold).astype(int)
        
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
