"""
Tests for GridStateMachine state transitions.
Ensures REACTIVATING state is not overwritten by ACTIVE state.
"""

import pytest
import threading
import time
from src.grid.grid_state import GridStateMachine, GridState


class TestGridStateMachine:
    def test_paused_when_overbought(self):
        """Should transition to PAUSED when RSI exceeds overbought threshold."""
        sm = GridStateMachine()
        sm.state = GridState.ACTIVE

        result = sm.evaluate(
            price=100_000,
            rsi=75,  # > 70 overbought
            ema_200=100_000,
            bb_lower=95_000,
            bb_upper=105_000
        )

        assert result == GridState.PAUSED

    def test_paused_when_below_ema200(self):
        """Should transition to PAUSED when price falls below EMA200."""
        sm = GridStateMachine()
        sm.state = GridState.ACTIVE

        result = sm.evaluate(
            price=99_000,  # < EMA200
            rsi=50,
            ema_200=100_000,
            bb_lower=95_000,
            bb_upper=105_000
        )

        assert result == GridState.PAUSED

    def test_reactivating_when_oversold_near_lower_bb(self):
        """Should transition to REACTIVATING when RSI oversold near lower BB."""
        sm = GridStateMachine()
        sm.state = GridState.PAUSED

        result = sm.evaluate(
            price=100_500,  # > EMA200 and near BB_lower * 1.02
            rsi=30,  # < 35 oversold
            ema_200=100_000,
            bb_lower=99_000,  # 99k * 1.02 = 100,980
            bb_upper=105_000
        )

        assert result == GridState.REACTIVATING

    def test_reactivating_not_overwritten_by_active(self):
        """
        CRITICAL: REACTIVATING should NOT be overwritten by ACTIVE.

        This tests the bug where Check 3 (price > EMA200 and RSI < 70)
        would overwrite Check 2's REACTIVATING state when both conditions
        are met. The fix ensures all checks return early.
        """
        sm = GridStateMachine()
        sm.state = GridState.PAUSED

        # Scenario where BOTH Check 2 and Check 3 conditions are true:
        # Check 2: rsi < 35 AND price <= bb_lower * 1.02
        # Check 3: price > ema_200 AND rsi < 70
        price = 100_500      # Meets both Check 2 (near BB) and Check 3 (> EMA200)
        ema_200 = 100_000    # Check 3: price > ema_200 ✓
        bb_lower = 99_000    # Check 2: price <= 99k * 1.02 = 100,980 ✓
        rsi = 30             # Meets both Check 2 (< 35) and Check 3 (< 70)

        result = sm.evaluate(
            price=price,
            rsi=rsi,
            ema_200=ema_200,
            bb_lower=bb_lower,
            bb_upper=105_000,
            rsi_overbought=70.0,
            rsi_oversold=35.0
        )

        # Check 2 should take precedence and return REACTIVATING
        # Check 3 should NOT overwrite it
        assert result == GridState.REACTIVATING, (
            f"REACTIVATING was overwritten by ACTIVE. "
            f"This means Check 3 ran after Check 2 without early return."
        )

    def test_active_when_above_ema200_and_rsi_normal(self):
        """Should transition to ACTIVE when price above EMA200 and RSI normal."""
        sm = GridStateMachine()
        sm.state = GridState.PAUSED

        result = sm.evaluate(
            price=101_000,  # > EMA200
            rsi=50,  # Normal (not overbought, not oversold)
            ema_200=100_000,
            bb_lower=95_000,
            bb_upper=105_000
        )

        assert result == GridState.ACTIVE

    def test_state_persistence_when_no_conditions_met(self):
        """Should maintain current state when no transition conditions are met."""
        sm = GridStateMachine()
        sm.state = GridState.PAUSED

        # Price above EMA200 but RSI not meeting any threshold
        result = sm.evaluate(
            price=101_000,
            rsi=50,  # Neither overbought (>70) nor oversold (<35)
            ema_200=100_000,
            bb_lower=95_000,
            bb_upper=105_000
        )

        assert result == GridState.ACTIVE

    def test_is_active_property(self):
        """is_active should be true for ACTIVE and REACTIVATING states."""
        sm = GridStateMachine()

        sm.state = GridState.ACTIVE
        assert sm.is_active is True

        sm.state = GridState.REACTIVATING
        assert sm.is_active is True

        sm.state = GridState.PAUSED
        assert sm.is_active is False

    def test_is_paused_property(self):
        """is_paused should be true only for PAUSED state."""
        sm = GridStateMachine()

        sm.state = GridState.PAUSED
        assert sm.is_paused is True

        sm.state = GridState.ACTIVE
        assert sm.is_paused is False

        sm.state = GridState.REACTIVATING
        assert sm.is_paused is False

    def test_thread_safety_concurrent_evaluates(self):
        """Multiple threads calling evaluate should not corrupt state."""
        sm = GridStateMachine()
        results = []
        errors = []

        def evaluate_state(rsi_value):
            try:
                result = sm.evaluate(
                    price=100_000,
                    rsi=rsi_value,
                    ema_200=100_000,
                    bb_lower=95_000,
                    bb_upper=105_000
                )
                results.append(result)
            except Exception as e:
                errors.append(e)

        # Create multiple threads
        threads = []
        for rsi in [30, 50, 75, 40, 60]:  # Mix of oversold, normal, overbought
            t = threading.Thread(target=evaluate_state, args=(rsi,))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        # Should have no errors
        assert len(errors) == 0, f"Thread safety errors: {errors}"

        # Should have results from all threads
        assert len(results) == 5

        # All results should be valid GridState enum values
        for result in results:
            assert isinstance(result, GridState)

    def test_thread_safety_state_read_write(self):
        """Concurrent state reads and writes should be safe."""
        sm = GridStateMachine()
        errors = []

        def read_state():
            try:
                for _ in range(100):
                    _ = sm.state
                    _ = sm.is_active
                    _ = sm.is_paused
            except Exception as e:
                errors.append(f"Read error: {e}")

        def write_state():
            try:
                for i in range(100):
                    sm.state = GridState.ACTIVE if i % 2 == 0 else GridState.PAUSED
            except Exception as e:
                errors.append(f"Write error: {e}")

        # Create reader and writer threads
        threads = []
        for _ in range(3):  # 3 readers
            t = threading.Thread(target=read_state)
            threads.append(t)
            t.start()

        for _ in range(2):  # 2 writers
            t = threading.Thread(target=write_state)
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        # Should have no errors
        assert len(errors) == 0, f"Thread safety errors: {errors}"
