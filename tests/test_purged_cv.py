import numpy as np
import pytest
from src.ml.purged_cv import PurgedTimeSeriesSplit


def test_purged_cv_splits_with_embargo():
    """Training folds are truncated by embargo samples at the end."""
    X = np.arange(100).reshape(-1, 1)
    cv = PurgedTimeSeriesSplit(n_splits=3, embargo=10)

    splits = list(cv.split(X))
    assert len(splits) == 3

    for train_idx, test_idx in splits:
        # Train indices must not overlap with test indices
        assert train_idx[-1] < test_idx[0]
        # Train indices are contiguous from 0
        assert train_idx[0] == 0
        # Gap between last train and first test >= embargo
        assert test_idx[0] - train_idx[-1] >= 10


def test_purged_cv_no_embargo_matches_sklearn():
    """With embargo=0, splits should be same as sklearn TimeSeriesSplit."""
    from sklearn.model_selection import TimeSeriesSplit
    X = np.arange(60).reshape(-1, 1)

    purged = PurgedTimeSeriesSplit(n_splits=3, embargo=0)
    sklearn_cv = TimeSeriesSplit(n_splits=3)

    for (p_train, p_test), (s_train, s_test) in zip(purged.split(X), sklearn_cv.split(X)):
        np.testing.assert_array_equal(p_train, s_train)
        np.testing.assert_array_equal(p_test, s_test)


def test_purged_cv_embargo_larger_than_fold():
    """If embargo > fold size, training set should be empty (skipped)."""
    X = np.arange(20).reshape(-1, 1)
    cv = PurgedTimeSeriesSplit(n_splits=3, embargo=100)
    splits = list(cv.split(X))
    # All folds should produce empty train sets and be skipped
    assert len(splits) == 0


def test_purged_cv_get_n_splits():
    cv = PurgedTimeSeriesSplit(n_splits=5, embargo=0)
    assert cv.get_n_splits() == 5


def test_purged_cv_negative_embargo_raises():
    with pytest.raises(ValueError, match="embargo must be >= 0"):
        PurgedTimeSeriesSplit(n_splits=3, embargo=-1)
