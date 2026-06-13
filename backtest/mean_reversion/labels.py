# backtest/mean_reversion/labels.py
"""Forward TP/SL labeling for flush events (strategy-aligned label).

For each flush bar (drop_frac > drop_thr), take close as entry, scan forward up
to max_hold bars: label = 1 if high >= entry*(1+tp) before low <= entry*(1-stop),
else 0 (stop first, or neither within max_hold = not a winner).
"""
import pandas as pd


def label_flushes(bars: pd.DataFrame, features: pd.DataFrame, drop_thr: float,
                  tp: float, stop: float, max_hold: int = 180) -> pd.DataFrame:
    close = bars["close"]  # close-only resolution (matches the backtest's SL/TP)
    flush_mask = features["drop_frac"] > drop_thr
    rows = []
    in_flush_run = False
    for i in bars.index:
        if not flush_mask.loc[i]:
            in_flush_run = False
            continue
        pos = bars.index.get_loc(i)
        # Only label the first bar of consecutive flushes
        if in_flush_run:
            continue
        in_flush_run = True
        entry = float(close.loc[i])
        tp_px = entry * (1.0 + tp)
        sl_px = entry * (1.0 - stop)
        label = 0
        for j in range(pos + 1, min(pos + 1 + max_hold, len(bars))):
            c = float(close.iloc[j])
            if c >= tp_px:
                label = 1
                break
            if c <= sl_px:
                label = 0
                break
        rows.append({"ts": i, "entry": entry, "drop_frac": float(features.loc[i, "drop_frac"]), "label": label})
    return pd.DataFrame(rows)
