#!/usr/bin/env python3
"""
Test script to verify the grid state machine bug fix.
This tests the specific scenario where REACTIVATING should not be overwritten by ACTIVE.
"""

from src.grid.grid_state import GridStateMachine, GridState

def test_reactivating_not_overwritten():
    """
    Test the bug scenario: when both Check 2 and Check 3 conditions are met,
    REACTIVATING should be set and NOT overwritten by ACTIVE.

    Scenario: RSI < 35 (oversold) AND price near lower BB AND price > EMA200 AND rsi < 70
    """
    sm = GridStateMachine()

    # Set initial state to PAUSED
    sm.state = GridState.PAUSED

    # Scenario: Both Check 2 and Check 3 conditions are met
    # Check 2: rsi < rsi_oversold (35) and price <= bb_lower * 1.02
    # Check 3: price > ema_200 and rsi < rsi_overbought (70)

    price = 100_500      # > EMA200 (100k) and near lower BB
    ema_200 = 100_000
    bb_lower = 99_000    # Lower BB (99k * 1.02 = 100,980)
    rsi = 30             # < 35 (oversold) AND < 70 (not overbought)

    result = sm.evaluate(
        price=price,
        rsi=rsi,
        ema_200=ema_200,
        bb_lower=bb_lower,
        bb_upper=105_000,
        rsi_overbought=70.0,
        rsi_oversold=35.0
    )

    print(f"Test scenario:")
    print(f"  Price: {price} (>{ema_200} EMA200)")
    print(f"  RSI: {rsi} (<35 oversold AND <70 not overbought)")
    print(f"  BB Lower: {bb_lower}")
    print(f"  Result: {result.value}")

    # The bug would cause ACTIVE to be set (Check 3 overwrites Check 2)
    # The fix should result in REACTIVATING (Check 2 takes precedence)
    expected = GridState.REACTIVATING

    if result == expected:
        print(f"✓ PASS: State is {expected.value} (REACTIVATING takes precedence)")
        return True
    else:
        print(f"✗ FAIL: Expected {expected.value}, got {result.value}")
        print(f"  This indicates Check 3 overwrote Check 2's REACTIVATING state")
        return False

def test_all_three_checks():
    """Test that all three state transitions work correctly."""

    sm = GridStateMachine()

    # Test 1: PAUSED when overbought or below EMA200
    sm.state = GridState.ACTIVE
    result = sm.evaluate(price=99_000, rsi=75, ema_200=100_000,
                        bb_lower=95_000, bb_upper=105_000)
    assert result == GridState.PAUSED, f"Expected PAUSED, got {result.value}"
    print("✓ Test 1: PAUSED when RSI > 70")

    # Test 2: REACTIVATING when oversold near lower BB
    sm.state = GridState.PAUSED
    result = sm.evaluate(price=96_000, rsi=30, ema_200=90_000,
                        bb_lower=95_000, bb_upper=105_000)
    assert result == GridState.REACTIVATING, f"Expected REACTIVATING, got {result.value}"
    print("✓ Test 2: REACTIVATING when oversold near lower BB")

    # Test 3: ACTIVE when above EMA200 and RSI normal
    sm.state = GridState.PAUSED
    result = sm.evaluate(price=101_000, rsi=50, ema_200=100_000,
                        bb_lower=95_000, bb_upper=105_000)
    assert result == GridState.ACTIVE, f"Expected ACTIVE, got {result.value}"
    print("✓ Test 3: ACTIVE when above EMA200 and RSI normal")

    return True

if __name__ == "__main__":
    print("Testing Grid State Machine Bug Fix")
    print("=" * 60)

    print("\n1. Testing REACTIVATING not overwritten by ACTIVE:")
    print("-" * 60)
    test1_pass = test_reactivating_not_overwritten()

    print("\n2. Testing all state transitions:")
    print("-" * 60)
    test2_pass = test_all_three_checks()

    print("\n" + "=" * 60)
    if test1_pass and test2_pass:
        print("✓ ALL TESTS PASSED")
        exit(0)
    else:
        print("✗ SOME TESTS FAILED")
        exit(1)
