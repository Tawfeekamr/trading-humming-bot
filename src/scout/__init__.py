"""Scout Bot — scans the market, generates its own signals.

Design: docs/23_scout_bot.md. Backtest rules: docs/scout/PREREGISTRATION.md.
Detector, feature and ranker code is pure (bars in, decisions out) so the
backtest and the live runner use the exact same functions.
"""
