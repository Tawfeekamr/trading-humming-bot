"""Test signal pipeline with the RENDER/USDT signal from Binance Killers."""
import sys
import json
import os

sys.path.insert(0, '/app')

from dotenv import load_dotenv
load_dotenv('/app/.env')

from src.signals.signal_parser import SignalParser
from src.signals.signal_validator import SignalValidator
from src.signals.signal_risk import SignalRiskGuard

SIGNAL_TEXT = """📍SIGNAL ID: #2147📍
COIN: $RENDER/USDT (2-5x)
Direction: LONG
➖➖➖➖➖➖➖
ENTRY: 1.880 - 1.900

TARGETS: 1.980 - 2.160 - 2.250 - 2.350 - 2.500 - 2.650 - 2.800 - 3.000

STOP LOSS: 1.725

4H FVG confluent with ascending trendline support at entry.
➖➖➖➖➖➖➖
- Binance Killers®"""

def main():
    print("=" * 60)
    print("SIGNAL PIPELINE TEST — RENDER/USDT #2147")
    print("=" * 60)

    # Check API key
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("❌ FAIL: DEEPSEEK_API_KEY not set")
        return
    print(f"✅ DEEPSEEK_API_KEY: {api_key[:8]}...{api_key[-4:]}")

    # Step 1: Parse
    print("\n--- Step 1: Parse signal via DeepSeek ---")
    parser = SignalParser()
    signal = parser.parse(SIGNAL_TEXT)

    result = {
        "action": signal.action.value,
        "pair": signal.pair,
        "entry_low": signal.entry_low,
        "entry_high": signal.entry_high,
        "stop_loss": signal.stop_loss,
        "take_profits": signal.take_profits,
        "confidence": signal.confidence.value,
        "quality_score": signal.quality_score,
        "quality_reason": signal.quality_reason,
        "parse_reasoning": signal.parse_reasoning,
    }
    print(json.dumps(result, indent=2))

    # Step 2: Validate
    print("\n--- Step 2: Validate signal ---")
    config = {
        "min_rr_ratio": 1.0,
        "max_sl_distance_pct": 10.0,
        "min_quality_score": 5,
        "max_positions": 3,
        "per_trade_risk_pct": 3.0,
        "capital_pct": 100.0,
        "max_capital_usdt": 10000.0,
        "max_entry_zone_pct": 3.0,
        "tp1_close_pct": 33,
        "tp2_close_pct": 50,
        "daily_loss_limit_pct": 5.0,
        "max_trades_per_day": 10,
        "cooldown_minutes": 5,
        "use_btc_correlation_gate": False,
        "blacklisted_pairs": [],
        "enabled": True,
        "audit_mode": False,
    }
    validator = SignalValidator(config)
    valid, reason = validator.validate(signal)
    if valid:
        print(f"✅ Signal VALID")
    else:
        print(f"❌ Signal REJECTED: {reason}")

    # Step 3: Risk check
    print("\n--- Step 3: Risk guard check ---")
    risk = SignalRiskGuard(config)
    risk_ok = risk.can_trade()
    budget = risk.get_budget_for_trade(signal, 10000.0) if risk_ok else 0
    if risk_ok:
        print(f"✅ Risk check PASSED — budget: ${budget:.2f}")
    else:
        print(f"❌ Risk check FAILED")

    # Summary
    print("\n" + "=" * 60)
    if signal.action.value != "OPEN_LONG":
        print(f"❌ FAIL: Parsed as {signal.action.value}, expected OPEN_LONG")
    elif not valid:
        print(f"❌ FAIL: Signal rejected — {reason}")
    elif not risk_ok:
        print(f"❌ FAIL: Risk blocked")
    else:
        print(f"✅ SUCCESS: Signal would be traded!")
        entry = (signal.entry_low + signal.entry_high) / 2 if signal.entry_low and signal.entry_high else 0
        sl_dist = ((entry - signal.stop_loss) / entry * 100) if entry and signal.stop_loss else 0
        print(f"   Entry: {entry:.3f} | SL: {signal.stop_loss} | Risk: {sl_dist:.1f}%")
        print(f"   TP targets: {len(signal.take_profits)} levels")
        print(f"   Quality: {signal.quality_score}/10 ({signal.confidence.value})")
    print("=" * 60)

if __name__ == "__main__":
    main()
