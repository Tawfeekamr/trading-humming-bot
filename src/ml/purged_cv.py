"""
Purged TimeSeriesSplit — López de Prado's embargo for forward-looking label leakage.

When labels depend on a forward window of size N, the last N training samples
have labels that leak information into the test fold. This splitter drops those
samples from the training set.

Usage:
    embargo = max(forward_window_across_intervals)
    cv = PurgedTimeSeriesSplit(n_splits=3, embargo=embargo)
    classifier.tune_hyperparameters(X, y, cv=cv)
"""

import numpy as np


class PurgedTimeSeriesSplit:
    """TimeSeriesSplit with embargo gap to prevent label leakage.

    Parameters
    ----------
    n_splits : int
        Number of cross-validation folds.
    embargo : int
        Number of samples to drop from the end of each training fold.
        Must be >= the forward_window used in label generation.
    """

    def __init__(self, n_splits: int = 3, embargo: int = 0):
        if embargo < 0:
            raise ValueError(f"embargo must be >= 0, got {embargo}")
        self.n_splits = n_splits
        self.embargo = embargo

    def split(self, X, y=None, groups=None):
        n_samples = len(X)
        fold_size = n_samples // (self.n_splits + 1)

        for i in range(self.n_splits):
            train_end = (i + 1) * fold_size
            test_start = train_end
            test_end = min((i + 2) * fold_size, n_samples)

            # Apply embargo: remove last `embargo` samples from training
            purged_train_end = max(0, train_end - self.embargo)
            train_indices = np.arange(0, purged_train_end)
            test_indices = np.arange(test_start, test_end)

            # Skip folds where training set would be empty
            if len(train_indices) > 0 and len(test_indices) > 0:
                yield train_indices, test_indices

    def get_n_splits(self, X=None, y=None, groups=None):
        return self.n_splits
