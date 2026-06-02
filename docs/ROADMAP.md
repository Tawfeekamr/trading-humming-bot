# Trading Bot Roadmap

*Last updated: 2026-06-02*

## ✅ Completed

- [x] Risk management foundation — PositionGuard, CircuitBreaker, RiskManager (7 tests, full coverage)
- [x] Signal Copy Engine (Engine 3) — live with 4 Telegram channels
- [x] Bar buffer persistence — warm restart with instant indicator warmup
- [x] Signal message queue persistence — JSONL-backed queue, no messages lost on restart
- [x] Zero-downtime CI/CD deploys — container swap with health checks
- [x] Docker build cache auto-cleanup — prevents disk overflow
- [x] Per-pair ML infrastructure (Phase 1)

---

## 🎯 Phase 2 — Quick Wins

### 1. Train Per-Pair Models for BNB, ETH, DOGE, XRP
- Use existing `train_pipeline.py`
- Per-pair ATR danger override, staleness detection
- Spec: `docs/superpowers/specs/2026-05-22-ml-multi-pair-design.md`

### 2. Wire Regime Confidence → Position Sizing
- Modify `position_manager.py` to scale size based on ML confidence
- Higher confidence → fuller position, lower → reduced exposure

### 3. Schedule Weekly Auto-Retraining
- Cron job + `train_pipeline.py`
- Keep models fresh with new market data automatically

### 4. Log ML Signals to Trade Journal
- Log `ml_regime` + `ml_confidence` alongside each trade
- Enables accuracy analysis — which regimes produce best/worst results

---

## 🔮 Phase 3 — Future

- **Dynamic grid spacing via ML** — highest ROI, training data already exists
- **AI trend entry scoring** — replace hardcoded point system with learned weights
- **NLP Telegram bot** — natural language queries ("how's DOGE doing?", "show me today's P&L")
