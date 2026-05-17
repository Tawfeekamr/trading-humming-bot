# AI Integration Implementation Plan
**Date:** May 2026
**Model:** GLM-5.1 via Z.ai API (OpenAI-compatible)
**Server:** AWS EC2 `t3.small` (2 vCPU, 2 GB RAM) — Tokyo

---

## Executive Summary

This document outlines how to integrate AI capabilities into the SOL/USDT dual-engine
trading bot using the GLM-5.1 model from Z.ai. The plan is split into 3 phases,
ordered by implementation complexity and risk. All AI features run as **lightweight
API calls** from the existing EC2 instance — no GPU or instance upgrade required.

### Architecture Principle

```
┌─────────────────────────────────────────────────────┐
│  EC2 t3.small (existing)                            │
│                                                     │
│  ta_grid_trend.py                                   │
│    ├── Grid Engine (unchanged)                      │
│    ├── Trend Engine (unchanged)                     │
│    └── NEW: AI Module                               │
│          ├── src/ai/advisor.py     ← GLM-5.1 calls  │
│          ├── src/ai/sentiment.py   ← News API calls │
│          └── src/ai/models/       ← .pkl files      │
│                                                     │
│  Outbound only:                                     │
│    → https://api.z.ai/api/paas/v1  (GLM-5.1)       │
│    → https://cryptopanic.com/api/  (sentiment)      │
└─────────────────────────────────────────────────────┘
```

---

## Phase 1 — AI Telegram Advisor (1–2 days)

### Goal
Add `/ask` and `/ai` commands to the existing Telegram bot so the operator can
query GLM-5.1 about the bot's current state, market conditions, and whether to
take manual action.

### Use Cases

| Command | Example | What GLM-5.1 Receives |
|---------|---------|----------------------|
| `/ask <question>` | `/ask should I increase capital?` | Live indicators + PnL + question |
| `/ai` | `/ai` | Full market snapshot → auto-analysis |
| `/ai risk` | `/ai risk` | Position exposure + drawdown data |

### New Files

```
src/
└── ai/
    ├── __init__.py
    ├── glm_client.py          # Z.ai API wrapper
    └── advisor.py             # Builds context, calls GLM, formats response
```

### Implementation Details

#### 1. `src/ai/glm_client.py` — Z.ai API Client

```python
"""
GLM-5.1 client via Z.ai OpenAI-compatible API.
Uses httpx for async HTTP calls (no heavy SDK dependency).
"""
import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class GLMClient:
    BASE_URL = "https://api.z.ai/api/paas/v1"
    MODEL = "glm-5.1"
    TIMEOUT = 30  # seconds

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def chat(self, system_prompt: str, user_message: str,
                   temperature: float = 0.3, max_tokens: int = 1024) -> Optional[str]:
        """Send a chat completion request to GLM-5.1."""
        payload = {
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/chat/completions",
                    headers=self._headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"GLM API call failed: {e}")
            return None
```

#### 2. `src/ai/advisor.py` — Trading Advisor

```python
"""
AI Trading Advisor — builds context from live bot state and queries GLM-5.1.
"""

SYSTEM_PROMPT = """You are an expert crypto trading advisor embedded in a 
SOL/USDT dual-engine bot (Grid + Trend). You analyze live market data and 
bot state to provide actionable recommendations.

Rules:
- Be concise. Max 200 words.
- Always cite specific numbers from the data provided.
- Clearly state your confidence level (Low/Medium/High).
- If the data suggests danger, say so directly.
- Never recommend specific buy/sell prices — the bot handles execution.
- Format for Telegram (use emoji, bold with <b>tags</b>).
"""

class TradingAdvisor:
    def __init__(self, glm_client, strategy):
        self._glm = glm_client
        self._strategy = strategy  # Reference to TAGridTrendStrategy

    def _build_context(self) -> str:
        """Collect live bot state into a text snapshot for GLM."""
        s = self._strategy
        ind = s._cached_indicators
        if not ind:
            return "No indicator data available yet."
        
        bb, rsi, ema, atr, price = ind
        journal = s.journal
        today = journal.summary_today()
        alltime = journal.summary_all_time()
        
        return f"""
LIVE MARKET DATA (SOL/USDT):
- Current Price: ${price:,.2f}
- RSI(14): {rsi:.1f}
- EMA-200: ${ema:,.2f} ({'ABOVE' if price > ema else 'BELOW'} trend)
- Bollinger Bands: Lower=${bb.lower:,.2f} | Mid=${bb.mid:,.2f} | Upper=${bb.upper:,.2f}
- ATR(14): ${atr:.2f}
- BB Width: ${bb.upper - bb.lower:.2f}

GRID ENGINE:
- State: {s.state_machine.state.value}
- Capital: ${s.capital_usdt:,.2f}
- Open Buys: {len(s._open_buys)}
- Unmatched Sells: {len(s._unmatched_sells)}
- Buy Spacing: ${s._active_buy_spacing:.2f}
- Sell Spacing: ${s._active_sell_spacing:.2f}
- Circuit Breaker: {'HALTED' if s.grid_circuit_breaker.halted else 'OK'}

TREND ENGINE:
- Enabled: {s._trend_enabled}
- Open Positions: {s._position_manager.open_count}
- Last Signal Score: {s._last_trend_score.total if s._last_trend_score else 'N/A'}/7
- Circuit Breaker: {'HALTED' if s._trend_breaker.halted else 'OK'}

P&L SUMMARY:
- Today: Net ${today['net_pnl'] or 0:.2f} | Trades: {today['total_trades'] or 0} | Win Rate: {today['win_rate']}%
- All-Time: Net ${alltime['net_pnl'] or 0:.2f} | Trades: {alltime['total_trades'] or 0}

MODE: {s.env.upper()}
"""

    async def ask(self, question: str) -> str:
        """Answer a specific user question about the bot."""
        context = self._build_context()
        user_msg = f"{context}\n\nOPERATOR QUESTION: {question}"
        response = await self._glm.chat(SYSTEM_PROMPT, user_msg)
        return response or "⚠️ AI advisor unavailable — GLM API call failed."

    async def auto_analysis(self) -> str:
        """Generate a full market analysis without a specific question."""
        context = self._build_context()
        user_msg = f"""{context}

Generate a brief trading analysis covering:
1. Current market regime (trending/ranging/volatile)
2. Grid engine health assessment
3. Trend engine opportunity assessment
4. Top risk concern right now
5. One actionable recommendation
"""
        response = await self._glm.chat(SYSTEM_PROMPT, user_msg)
        return response or "⚠️ AI advisor unavailable — GLM API call failed."
```

#### 3. Wire into Telegram Commands

Add to `src/notifications/telegram_commands.py`:

```python
# In TelegramCommandHandler.__init__:
from src.ai.glm_client import GLMClient
from src.ai.advisor import TradingAdvisor

api_key = os.environ.get("ZHIPU_API_KEY", "")
if api_key:
    self._advisor = TradingAdvisor(GLMClient(api_key), strategy)
else:
    self._advisor = None

# New command handlers:
async def _handle_ask(self, question: str) -> str:
    if not self._advisor:
        return "⚠️ AI not configured. Set ZHIPU_API_KEY in .env"
    return await self._advisor.ask(question)

async def _handle_ai(self, sub_command: str = "") -> str:
    if not self._advisor:
        return "⚠️ AI not configured. Set ZHIPU_API_KEY in .env"
    if sub_command == "risk":
        return await self._advisor.ask("Analyze my current risk exposure and suggest adjustments")
    return await self._advisor.auto_analysis()
```

#### 4. Environment Variable

Add to `.env`:
```
ZHIPU_API_KEY=your_z_ai_api_key_here
```

### Expected Result

```
You → /ask should I increase grid capital?

Bot → 🤖 AI Analysis (GLM-5.1):

📊 Current State: Grid is ACTIVE with RSI at 52.3 — healthy 
ranging conditions.

💰 Capital Efficiency: Your $5,000 grid capital is deploying 
$4,900 across 2 levels with buy spacing of $2.41.

📈 Recommendation: <b>Hold current capital</b>. 
BB width is $8.20 (moderate), and grid fill rate is 
adequate. Increasing capital would tighten spacing 
and risk more fee-trap trades.

⚠️ Risk Note: EMA-200 is at $168.50 and price is 
$172.30 — only 2.2% above trend support. A dip below 
EMA could trigger a state pause.

Confidence: <b>Medium</b>
```

### Dependencies

```
# Add to requirements.txt
httpx==0.28.1
```

### Estimated Cost

| Usage | Monthly Cost |
|-------|-------------|
| 50 `/ask` queries/day × 30 days × ~800 tokens avg | ~$1–3 |
| Auto-analysis on state changes (~10/day) | ~$0.50 |
| **Total** | **~$2–4/month** |

---

## Phase 2 — AI-Gated Trade Decisions (3–5 days)

### Goal
Before the Trend Engine opens a position, ask GLM-5.1 to review the signal
and either approve or reject it. This acts as a **second opinion** layer on
top of the existing scoring system.

### Use Cases

| Scenario | Current Behavior | AI-Enhanced Behavior |
|----------|-----------------|---------------------|
| Trend score = 5/7 | Auto-enter position | Ask GLM: "Score 5/7 with RSI 65, price near resistance — enter?" |
| Trend score = 3/7 (borderline) | Auto-enter position | Ask GLM: likely rejects due to low confidence |
| Grid state change | Immediate pause/resume | GLM confirms: "RSI 71 but rising volume — brief spike, don't pause yet" |

### New Files

```
src/
└── ai/
    └── trade_gate.py          # Reviews signals before execution
```

### Implementation Details

#### `src/ai/trade_gate.py` — Signal Review Gate

```python
GATE_PROMPT = """You are a risk-aware trade gating system for a SOL/USDT trend bot.
You receive a proposed trade entry with market context. Respond with ONLY a JSON object:

{"decision": "APPROVE" | "REJECT", "reason": "one sentence", "confidence": 0.0-1.0}

Rules:
- REJECT if RSI > 65 (overbought zone)
- REJECT if price is within 0.5% of resistance
- REJECT if recent trades show 3+ consecutive losses
- APPROVE if score >= 4 and price is above EMA-200 with room to run
- When in doubt, REJECT — capital preservation > opportunity
"""

class TradeGate:
    def __init__(self, glm_client):
        self._glm = glm_client

    async def review_entry(self, signal_context: dict) -> dict:
        """Returns {"decision": "APPROVE"/"REJECT", "reason": str, "confidence": float}"""
        user_msg = json.dumps(signal_context, indent=2)
        response = await self._glm.chat(GATE_PROMPT, user_msg, temperature=0.1)
        try:
            return json.loads(response)
        except (json.JSONDecodeError, TypeError):
            # Fail-safe: reject if AI response is malformed
            return {"decision": "REJECT", "reason": "AI response parse error", "confidence": 0.0}
```

#### Integration Point in `ta_grid_trend.py`

```python
# In _evaluate_trend_signals(), after confirm_entry():
if confirmed:
    if self._trade_gate and self._ai_gating_enabled:
        gate_result = await self._trade_gate.review_entry({
            "signal_score": score.total,
            "signal_details": score.details,
            "price": self._last_price,
            "rsi": rsi_value,
            "ema_200": ema_value,
            "bb_upper": bb.upper,
            "bb_lower": bb.lower,
            "atr": atr_value,
            "recent_pnl": last_5_trades_pnl,
            "open_positions": self._position_manager.open_count,
        })
        if gate_result["decision"] == "REJECT":
            logger.info(f"AI GATE REJECTED entry: {gate_result['reason']}")
            # Notify via Telegram
            return
    self._open_trend_position(candles, score)
```

#### Configuration

```yaml
# config/strategy.yaml — add under trend:
trend:
  ai_gating: true              # Enable AI trade review
  ai_gate_min_score: 3         # Only review borderline signals (score 3-4)
  ai_gate_always_above: 5      # Auto-approve if score >= 5
```

### Safety Design

```
Signal Score ≥ 5  →  Auto-approve (skip AI, too slow for strong signals)
Signal Score 3–4  →  Ask GLM-5.1 for review (~2-3 sec latency)
Signal Score < 3  →  Auto-reject (existing behavior, no AI needed)

If GLM API fails  →  Fall back to existing behavior (approve if score ≥ 3)
```

### Risk Considerations

> ⚠️ **Latency:** GLM API calls take 2–5 seconds. For trend entries on 1h 
> candles this is acceptable. Do NOT use this for grid orders (latency-sensitive).

> ⚠️ **Fail-Safe:** If the API is down, the gate falls through to APPROVE. 
> The existing scoring system is the primary filter; AI is supplementary.

---

## Phase 3 — AI Market Sentiment & Smart Alerts (1 week)

### Goal
Add real-time crypto news/sentiment monitoring that can:
1. Pause the grid during extreme negative sentiment events
2. Widen trend stop-losses during high-uncertainty periods
3. Send proactive Telegram alerts about relevant market events

### New Files

```
src/
└── ai/
    ├── sentiment.py           # CryptoPanic API + GLM classification
    └── smart_alerts.py        # Proactive AI-driven notifications
```

### Data Sources

| Source | Cost | Data |
|--------|------|------|
| RSS News Feeds | Free | CoinDesk/Cointelegraph news (via RSS) |
| Binance WebSocket | Free (already connected) | Volume spikes, Price velocity, Announcements |
| Alternative.me API | Free | Fear & Greed Index (sentiment tracking) |

> [!NOTE]
> The **Fear & Greed Index** will be fetched from **Alternative.me**, which is the industry standard free source. CoinGecko's public API (Demo tier) is also available for free with a 30 calls/min limit, but we prioritize the most reliable zero-cost sources first.

### Implementation Details

#### `src/ai/sentiment.py` — Sentiment Monitor

```python
"""
Monitors crypto news sentiment and provides a risk score.
Runs on a 5-minute polling loop in a background thread.
"""
import httpx
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class SentimentMonitor:
    RSS_FEEDS = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss"
    ]
    FNG_URL = "https://api.alternative.me/fng/?limit=1"

    def __init__(self, glm_client, currencies: list[str] = None):
        self._glm = glm_client
        self._currencies = currencies or ["SOL"]
        self._last_score: float = 0.0      # -1.0 (bearish) to +1.0 (bullish)
        self._breaking_news: bool = False
        self._last_headlines: list[str] = []

    @property
    def score(self) -> float:
        return self._last_score

    @property
    def is_danger(self) -> bool:
        return self._last_score < -0.5 or self._breaking_news

    async def poll(self) -> dict:
        """Fetch latest news from RSS and Fear & Greed index."""
        headlines = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                for url in self.RSS_FEEDS:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        # Simple RSS parsing (titles only)
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(resp.text)
                        for item in root.findall(".//item")[:5]:
                            title = item.find("title").text
                            headlines.append(title)
                
                # Fetch Fear & Greed index
                fng_resp = await client.get(self.FNG_URL)
                fng_data = fng_resp.json()
                fng_value = int(fng_data["data"][0]["value"])
                fng_class = fng_data["data"][0]["value_classification"]
        except Exception as e:
            logger.error(f"Sentiment data fetch failed: {e}")
            return {"score": self._last_score, "error": str(e)}

        if not headlines:
            return {"score": 0.0, "headlines": [], "fng": fng_value}

        self._last_headlines = headlines

        # Use GLM to classify overall sentiment
        prompt = f"""Rate the overall crypto market sentiment based on news and Fear & Greed Index.
Return ONLY a JSON object: {{"score": -1.0 to 1.0, "breaking": true/false, "summary": "one sentence"}}

Fear & Greed Index: {fng_value} ({fng_class})
Headlines:
{chr(10).join(f'- {h}' for h in headlines)}
"""
        response = await self._glm.chat(
            "You are a financial sentiment classifier. Be precise and conservative.",
            prompt, temperature=0.1, max_tokens=200
        )

        try:
            import json
            result = json.loads(response)
            self._last_score = float(result.get("score", 0.0))
            self._breaking_news = bool(result.get("breaking", False))
            return result
        except (json.JSONDecodeError, TypeError):
            return {"score": 0.0, "error": "parse_failed"}
```

#### Integration Points

```python
# In _grid_tick() — before placing orders:
if self._sentiment and self._sentiment.is_danger:
    logger.warning(f"Sentiment danger: score={self._sentiment.score:.2f}")
    self._cancel_all_orders("sentiment_danger")
    # Alert operator
    msg = (
        f"🚨 <b>SENTIMENT ALERT — Grid Paused</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📰 Score: {self._sentiment.score:.2f} (danger < -0.5)\n"
        f"📋 Headlines:\n"
        + "\n".join(f"  • {h}" for h in self._sentiment._last_headlines[:3])
    )
    await self.telegram.send(msg)
    return

# In _evaluate_trend_signals() — widen stops during uncertainty:
if self._sentiment and abs(self._sentiment.score) > 0.3:
    # High sentiment = widen stops by 50% to avoid premature exit
    sl_buffer_multiplier = 1.5
```

#### Background Polling Loop

```python
# In TAGridTrendStrategy.__init__:
if os.environ.get("ZHIPU_API_KEY"):
    from src.ai.sentiment import SentimentMonitor
    self._sentiment = SentimentMonitor(self._glm_client, currencies=["SOL"])
    # Poll every 5 minutes in background
    threading.Thread(target=self._sentiment_poll_loop, daemon=True).start()

def _sentiment_poll_loop(self):
    """Background thread: polls sentiment every 5 minutes."""
    loop = asyncio.new_event_loop()
    while True:
        try:
            result = loop.run_until_complete(self._sentiment.poll())
            logger.info(f"Sentiment update: score={result.get('score', 'N/A')}")
        except Exception as e:
            logger.error(f"Sentiment poll error: {e}")
        time_mod.sleep(300)  # 5 minutes
```

---

## Phase 4 — ML Regime Detector & Anomaly Detection (2 weeks)

### Goal
Train lightweight ML models **offline** (on local machine) and deploy them as
`.pkl` files to the EC2 server for real-time inference.

### 4A. Regime Detector (replaces `GridStateMachine.evaluate()`)

#### Training Pipeline (local)

```python
# Train on your trade journal data
# Input features: RSI, ATR, BB_width, EMA_slope, volume, hour_of_day
# Label: was the next grid cycle profitable? (binary)

from sklearn.ensemble import GradientBoostingClassifier
import joblib

model = GradientBoostingClassifier(n_estimators=100, max_depth=4)
model.fit(X_train, y_train)
joblib.dump(model, "src/ai/models/regime_detector.pkl")
```

#### Inference on Server

```python
# In grid_state.py or a new src/ai/regime.py:
import joblib

class MLRegimeDetector:
    def __init__(self, model_path: str = "src/ai/models/regime_detector.pkl"):
        self._model = joblib.load(model_path)
    
    def predict(self, rsi, atr, bb_width, ema_slope, volume, hour) -> str:
        features = [[rsi, atr, bb_width, ema_slope, volume, hour]]
        proba = self._model.predict_proba(features)[0]
        if proba[1] > 0.65:
            return "grid_favorable"
        elif proba[1] < 0.35:
            return "grid_unfavorable"
        return "neutral"
```

#### Dependencies for Server

```
# Add to requirements.txt
scikit-learn==1.6.1
joblib==1.5.1
```

**RAM impact:** ~50 MB (model is <5 MB, sklearn runtime ~45 MB)

### 4B. Anomaly Detector (enhances `CircuitBreaker`)

#### Training

```python
from sklearn.ensemble import IsolationForest

# Train on "normal" market microstructure data
# Features: spread, volume_ratio, price_velocity, candle_body_ratio
model = IsolationForest(contamination=0.05, n_estimators=100)
model.fit(X_normal)
joblib.dump(model, "src/ai/models/anomaly_detector.pkl")
```

#### Integration

```python
# In circuit_breaker.py:
class SmartCircuitBreaker(CircuitBreaker):
    def __init__(self, *args, anomaly_model_path=None, **kwargs):
        super().__init__(*args, **kwargs)
        if anomaly_model_path:
            self._anomaly = joblib.load(anomaly_model_path)
        else:
            self._anomaly = None

    def check_with_anomaly(self, current_equity, market_features) -> bool:
        # Standard drawdown check
        if self.check(current_equity):
            return True
        # AI anomaly check
        if self._anomaly:
            score = self._anomaly.decision_function([market_features])[0]
            if score < -0.5:  # Anomaly detected
                self._halted = True
                return True
        return False
```

---

## File Structure Summary

After all phases:

```
src/
├── ai/
│   ├── __init__.py
│   ├── glm_client.py              # Phase 1 — Z.ai API wrapper
│   ├── advisor.py                  # Phase 1 — /ask and /ai commands
│   ├── trade_gate.py               # Phase 2 — Signal review gate
│   ├── sentiment.py                # Phase 3 — News sentiment monitor
│   ├── smart_alerts.py             # Phase 3 — Proactive notifications
│   ├── regime.py                   # Phase 4 — ML regime prediction
│   └── models/
│       ├── regime_detector.pkl     # Phase 4 — Trained offline
│       └── anomaly_detector.pkl    # Phase 4 — Trained offline
├── grid/          (unchanged)
├── indicators/    (unchanged)
├── risk/          (circuit_breaker.py enhanced in Phase 4)
├── trend/         (trend_manager.py enhanced in Phase 2)
└── notifications/ (telegram_commands.py enhanced in Phase 1)
```

---

## Configuration Summary

### `.env` additions

```bash
# AI / GLM-5.1
ZHIPU_API_KEY=your_z_ai_api_key_here

# Sentiment (optional — Phase 3)
CRYPTOPANIC_API_KEY=your_key_here          # Optional, free tier works without key
```

### `config/strategy.yaml` additions

```yaml
# AI Configuration
ai:
  enabled: true
  provider: "zhipu"                        # z.ai GLM-5.1
  model: "glm-5.1"
  
  advisor:
    enabled: true                          # Phase 1 — Telegram /ask and /ai
    max_tokens: 1024
    temperature: 0.3
  
  trade_gate:
    enabled: false                         # Phase 2 — disabled by default, enable when ready
    min_score_to_review: 3                 # Only review borderline signals
    auto_approve_above: 5                  # Skip AI for strong signals
    fallback_on_error: "approve"           # "approve" or "reject" if API fails
  
  sentiment:
    enabled: false                         # Phase 3 — disabled by default
    poll_interval_sec: 300                 # 5 minutes
    danger_threshold: -0.5                 # Pause grid below this
    currencies: ["SOL"]

  regime_detector:
    enabled: false                         # Phase 4 — disabled until model trained
    model_path: "src/ai/models/regime_detector.pkl"
    confidence_threshold: 0.65
```

### `requirements.txt` additions

```
# Phase 1 — AI Advisor
httpx==0.28.1

# Phase 4 — ML Models (optional, only if using regime/anomaly detection)
scikit-learn==1.6.1
joblib==1.5.1
```

---

## Server Resource Impact

| Phase | RAM Impact | CPU Impact | Network |
|-------|-----------|------------|---------|
| Phase 1 (Advisor) | +5 MB | Negligible | ~50 API calls/day |
| Phase 2 (Gate) | +2 MB | Negligible | ~10 API calls/day |
| Phase 3 (Sentiment) | +10 MB | Negligible | 288 polls/day + ~288 API calls |
| Phase 4 (ML Models) | +50 MB | <1ms per prediction | None |
| **Total** | **~67 MB** | **Negligible** | **~$2-4/month** |

Current server: 2 GB RAM → **3.3% additional usage**. No upgrade needed.

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| GLM API downtime | All AI features have fallback to existing rule-based logic |
| Slow API response (>5s) | Timeout at 30s, trend entries proceed without AI review |
| Wrong AI recommendation | AI is advisory/gating only — never initiates trades directly |
| Cost overrun | Temperature 0.1–0.3, max_tokens capped, polling intervals enforced |
| Hallucinated trade advice | System prompt explicitly forbids price targets; bot handles execution |

---

## Implementation Timeline

```
Week 1:
  ├── Day 1-2:  Phase 1 — GLM client + /ask + /ai commands
  ├── Day 3:    Test on paper trading
  └── Day 4-5:  Phase 2 — Trade gate (disabled by default)

Week 2:
  ├── Day 1-3:  Phase 3 — Sentiment monitor
  └── Day 4-5:  Integration testing, deploy to EC2

Week 3-4:
  ├── Collect training data from live bot
  └── Phase 4 — Train regime/anomaly models locally, deploy .pkl files

Ongoing:
  ├── Monitor AI gate approval/rejection ratio
  ├── Retrain models monthly with new trade data
  └── Tune sentiment thresholds based on false positive rate
```
