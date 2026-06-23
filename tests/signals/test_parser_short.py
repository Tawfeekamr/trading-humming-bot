import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from src.signals.signal_parser import SignalParser, SignalAction

def test_parses_short_signal():
    resp = {"action": "OPEN_SHORT", "pair": "ETH-USDT", "entry_low": 3000,
            "entry_high": 3050, "stop_loss": 3150, "take_profits": [2900, 2800],
            "confidence": "high", "quality_score": 7, "reasoning": "short"}
    p = SignalParser(api_key="fake"); p._call_glm = lambda prompt: resp
    sig = p.parse("SHORT ETH/USDT entry 3000-3050 SL 3150")
    assert sig.action == SignalAction.OPEN_SHORT
    assert sig.pair == "ETH-USDT"
