import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))


def test_futures_config_block_present():
    import yaml

    cfg = yaml.safe_load(pathlib.Path("config/strategy.yaml").read_text())
    f = cfg.get("signals_futures", {})
    assert f.get("enabled") is True
    # Paper simulator priced off Gate.io USDT-perp (Binance testnet retired —
    # fapi -1121 Invalid symbol on non-major coins).
    assert f.get("exchange") == "gate_io_paper_futures"
    assert f.get("leverage") == 3
    assert f.get("margin_type") == "isolated" and f.get("allow_shorts") is True
