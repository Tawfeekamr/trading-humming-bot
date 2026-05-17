import pandas as pd
import numpy as np
import sys
import os

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.data.feature_engineering import calculate_technical_features
from src.data.label_generation import generate_regime_labels
from src.ml.regime_classifier import RegimeClassifier

def simulate_walk_forward(df: pd.DataFrame, initial_train_size: int = 500, step_size: int = 100):
    """
    Simulates walk-forward optimization.
    Trains on initial_train_size, tests on step_size, then rolls forward.
    """
    df_features = calculate_technical_features(df)
    df_labeled = generate_regime_labels(df_features)
    
    feature_cols = [
        'returns', 'volatility_14', 'volatility_30', 'normalized_atr',
        'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value'
    ]
    
    X = df_labeled[feature_cols]
    y = df_labeled['regime_label']
    
    total_samples = len(X)
    if total_samples <= initial_train_size:
        print("Not enough data for walk-forward testing.")
        return
        
    print(f"Starting Walk-Forward Validation. Total samples: {total_samples}")
    
    model = RegimeClassifier(model_path='models/temp_walk_forward.pkl')
    
    total_predictions = []
    total_actuals = []
    
    for start_idx in range(0, total_samples - initial_train_size, step_size):
        train_end = start_idx + initial_train_size
        test_end = min(train_end + step_size, total_samples)
        
        X_train = X.iloc[start_idx:train_end]
        y_train = y.iloc[start_idx:train_end]
        
        X_test = X.iloc[train_end:test_end]
        y_test = y.iloc[train_end:test_end]
        
        # Train on rolling window
        model.train(X_train, y_train)
        
        # Predict on next unseen block
        preds = model.predict(X_test, threshold=0.55)
        
        total_predictions.extend(preds)
        total_actuals.extend(y_test)
        
        acc = (preds == y_test).mean()
        print(f"Window {start_idx} to {train_end} -> Test Acc: {acc:.2%}")
        
    final_acc = (np.array(total_predictions) == np.array(total_actuals)).mean()
    print(f"\nOverall Walk-Forward Accuracy: {final_acc:.2%}")

if __name__ == '__main__':
    from src.ml.train_pipeline import load_dummy_data
    print("Loading data for Walk Forward testing...")
    df = load_dummy_data()
    simulate_walk_forward(df)
