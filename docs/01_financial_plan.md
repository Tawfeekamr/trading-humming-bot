# 💰 Project Financial Plan
### TA-Enhanced BTC/USDT Grid Bot — Minimum Cost Strategy
> Dubai, UAE · Binance FZE · Hummingbot v2 · Railway

---

## 🎯 Goal: Run a Professional Algo Bot for Under $15/Month

Every decision in this document is optimized for **minimum spend** while keeping the project fully functional and professional.

---

## 📊 Phase-by-Phase Cost Summary

| Phase | Duration | Monthly Cost | What You're Doing |
|-------|----------|-------------|-------------------|
| **Phase 1** — Local Dev | Month 1 | **$0** | Build + paper trade on your laptop |
| **Phase 2** — Railway Paper | Month 2 | **$3–5** | Paper trade on Railway 24/7 |
| **Phase 3** — Live Micro | Month 3 | **$11–15** | Live trading with $200 capital |
| **Phase 4** — Full Deploy | Month 4+ | **$11–15** | Scale capital, fees self-funded |

> **Total cost to reach live trading: under $50 across 3 months.**

---

## 🆓 Phase 1 — Zero Cost (Month 1)

Run everything locally on your laptop. No cloud, no fees, no credit card.

### What Costs $0 Forever

| Tool | Free Tier | Notes |
|------|-----------|-------|
| **Hummingbot** | ✅ Free forever | Open source, GPL-3.0 |
| **Binance API** | ✅ Free | API access has no charge |
| **Binance Testnet** | ✅ Free | Real market data, fake money |
| **Google Sheets** | ✅ Free | Personal Google account |
| **Google Sheets API** | ✅ Free | Up to 300 requests/min |
| **Telegram Bot** | ✅ Free | Unlimited messages |
| **GitHub** | ✅ Free | Private repos included |
| **SQLite** | ✅ Free | Runs inside your bot |
| **Python + all libraries** | ✅ Free | All open source |
| **Railway trial credits** | ✅ $5 free | Enough for 1 month testing |

### Phase 1 Actions (Save Money While You Build)

```
✅ Run bot locally — no server needed during development
✅ Use Binance testnet — trade BTC/USDT with fake money, real prices
✅ Test all Telegram alerts locally before deploying
✅ Build and validate your strategy for FREE before spending anything
✅ Paper trade for 30+ days minimum — this is non-negotiable
```

**Month 1 total: $0.00**

---

## 🚂 Phase 2 — Railway Minimum Cost (~$3–5/month)

When you're ready to run 24/7 without your laptop on.

### Railway Hobby Plan — Minimize Usage

Railway charges for actual resource consumption. A Hummingbot grid bot is extremely lightweight.

| Resource | Your Bot Uses | Railway Cost |
|----------|--------------|-------------|
| RAM | ~180–220 MB | Very low |
| CPU | Spikes on candle close (1h) | Near zero between spikes |
| Bandwidth | Minimal (WebSocket + REST) | Negligible |
| **Estimated total** | | **~$3–5/month** |

### Railway $5 Credit Offset

Railway Hobby plan gives **$5 free credit/month**:

```
Your usage:        ~$5–8/month
Free credits:      -$5/month
────────────────────────────
Out of pocket:     $0–3/month for bot only
```

### Deploy Only What You Need (Don't Over-Deploy)

| Service | Deploy When | Monthly Cost |
|---------|------------|-------------|
| Bot (Hummingbot) | Month 2 ✅ | ~$3 |
| Streamlit dashboard | Month 3+ (optional) | +$2 |
| **Minimum viable** | Bot only | **~$3/month** |

> 💡 **Skip the dashboard in early months.** Telegram alerts give you everything you need to monitor the bot. Add the dashboard only when you have consistent trade data worth visualizing.

**Phase 2 total: ~$3–5/month**

---

## 📈 Phase 3 — Live Trading Cost Minimization

### The Fee Problem (And How to Solve It)

Binance fees are your biggest ongoing cost. Here's how to cut them to the bone.

#### Step 1 — Enable BNB Fee Discount (Save 25% Instantly)

```
1. Buy ~$10–20 worth of BNB on Binance
2. Go to: Profile → Fee Rate → Enable "Use BNB to pay fees"
3. Done — all fees now 0.075% instead of 0.100%

Savings on $1,000 capital: $18/month → $13.50/month
Savings on $500 capital:   $9/month  → $6.75/month
One-time BNB cost:         ~$10–20
BNB pays for itself in:    < 1 month
```

#### Step 2 — Start with Minimum Capital ($200)

```
Capital:        $200 USDT
Grid levels:    10
Avg order size: $20
Avg trades/day: 15
Fee per trade:  $20 × 0.075% = $0.015
Daily fees:     15 × $0.015  = $0.225
Monthly fees:   $0.225 × 30  = $6.75/month
```

At $200 capital, fees are only **$6.75/month with BNB discount.**
Your bot needs to make just **$6.75 + $3 Railway = $9.75/month** to break even.
That's a **4.9% monthly return** — very achievable for a well-tuned grid bot.

#### Step 3 — Optimize Grid Spacing to Reduce Overtrading

Tighter grids = more trades = more fees. Wider grids = fewer trades = less fees but bigger profit per trade.

**Fee-optimized grid config:**

```yaml
# config/strategy.yaml — minimum fee settings
grid:
  levels: 8                  # Fewer levels = fewer orders = lower fees
  capital_usdt: 200          # Start minimum
  order_refresh_time: 60     # Refresh every 60s (not 30s) = less API calls

indicators:
  atr:
    spacing_multiplier: 0.8  # Wider spacing = fewer fills = lower fees
                              # Default 0.5, increase to 0.8 reduces trades ~40%
```

**Fee impact of spacing multiplier:**

| ATR Multiplier | Trades/Day | Monthly Fees ($200) | Monthly Fees ($1,000) |
|---------------|-----------|--------------------|-----------------------|
| 0.3 (tight) | 30 | $13.50 | $67.50 |
| 0.5 (default) | 20 | $9.00 | $45.00 |
| **0.8 (wide)** | **12** | **$5.40** | **$27.00** |
| 1.2 (very wide) | 7 | $3.15 | $15.75 |

> ⚠️ Very wide spacing means fewer profits too. Find the sweet spot during backtesting. Start at **0.8** and adjust based on results.

#### Step 4 — Pause Grid Aggressively (Save Fees During Bad Conditions)

The TA filters already do this — when RSI > 70 or price < EMA200, the grid pauses and **zero fees are paid**. This is one of the biggest cost advantages of a TA-enhanced grid vs a plain grid bot.

```
Plain grid bot:     Pays fees 24/7 regardless of conditions
TA-enhanced grid:   Pauses ~30-40% of the time = 30-40% fewer fees
```

On $1,000 capital: saves ~$22–30/month in fees vs plain grid.

---

## 💸 Full Cost Model — 3 Capital Scenarios

### Scenario A: Minimum Starter ($200 capital)

| Cost Item | Monthly | Notes |
|-----------|---------|-------|
| Railway (bot only) | $3 | After $5 credit offset |
| Binance fees | $5.40 | 12 trades/day × $20 × 0.075% × 30 |
| BNB (one-time) | $1.50 | Amortized over 12 months |
| Google Sheets API | $0 | Free |
| Telegram | $0 | Free |
| **Total** | **$9.90/month** | |
| **Break-even PnL needed** | **+$9.90/month** | = 4.95% monthly return |

### Scenario B: Recommended ($500 capital)

| Cost Item | Monthly | Notes |
|-----------|---------|-------|
| Railway (bot only) | $3 | After $5 credit offset |
| Binance fees | $13.50 | 15 trades/day × $30 × 0.075% × 30 |
| BNB (one-time) | $1.50 | Amortized |
| **Total** | **$18/month** | |
| **Break-even PnL needed** | **+$18/month** | = 3.6% monthly return |

### Scenario C: Growth ($1,000 capital)

| Cost Item | Monthly | Notes |
|-----------|---------|-------|
| Railway (bot + dashboard) | $5 | Worth adding at this scale |
| Binance fees | $27 | 12 trades/day × $50 × 0.075% × 30 |
| BNB (one-time) | $1.50 | Amortized |
| **Total** | **$33.50/month** | |
| **Break-even PnL needed** | **+$33.50/month** | = 3.35% monthly return |

> 📊 **Break-even gets easier as capital grows** — fees as a % of capital actually decrease at scale.

---

## 🔧 One-Time Setup Costs

| Item | Cost | Notes |
|------|------|-------|
| BNB for fee discount | $10–20 | Buy once, refills automatically from trading profits |
| Railway Hobby plan | $5/month | No setup fee, cancel anytime |
| Google Cloud project | $0 | Free tier covers all our usage |
| Domain (optional) | $0 | Railway gives free subdomain for dashboard |
| **Total one-time** | **$10–20** | |

---

## 📅 12-Month Cost Projection

| Month | Phase | Capital | Fixed Costs | Trading Fees | Total | Notes |
|-------|-------|---------|-------------|-------------|-------|-------|
| 1 | Local dev | $0 | $0 | $0 | **$0** | Build + paper trade locally |
| 2 | Railway paper | $0 | $3 | $0 | **$3** | 24/7 paper on Railway |
| 3 | Live micro | $200 | $3 | $5.40 | **$8.40** + $15 BNB | Go live with $200 |
| 4 | Live micro | $200 | $3 | $5.40 | **$8.40** | Validate results |
| 5 | Scale up | $500 | $3 | $13.50 | **$16.50** | Scale if profitable |
| 6 | Growth | $500 | $3 | $13.50 | **$16.50** | |
| 7 | Growth | $1,000 | $5 | $27 | **$32** | Add dashboard |
| 8–12 | Steady | $1,000 | $5 | $27 | **$32/mo** | Fees from profits |
| **Year 1 Total** | | | | | **~$220** | All-in for full year |

> 💡 From Month 5 onward, trading profits should cover fees. **The bot pays for itself.**

---

## 🚨 Cost Risk Factors

### What Could Make Costs Higher

| Risk | Impact | Mitigation |
|------|--------|-----------|
| BTC crashes below grid range | Capital loss (not a fee) | Circuit breaker halts at -10% drawdown |
| Overtrading (too tight grid) | 2–3× higher fees | Set ATR multiplier ≥ 0.8 |
| Railway usage spike | +$2–5 one month | Set Railway spend limit in dashboard |
| BNB price drops | Slightly higher fees | Keep $10–20 BNB buffer |
| Google API quota exceeded | Free tier is very generous | 300 req/min — bot uses <10/min |

### Set a Railway Spend Limit

Railway lets you set a hard monthly spending cap:
```
Railway → Settings → Billing → Set Spend Limit → $10
```
This guarantees you never get surprised by a large bill.

---

## ✅ Minimum Cost Checklist

Before going live, confirm every item:

```
Infrastructure
□ Railway Hobby plan activated ($5 free credit applied)
□ Railway spend limit set to $10/month
□ Bot-only deployed (no dashboard until Month 4+)
□ GitHub repo private (free on GitHub)

Binance Fee Reduction
□ BNB purchased (~$15 worth)
□ "Pay fees with BNB" enabled in Binance settings
□ Verified fee rate shows 0.075% in Binance fee schedule
□ API key: trade permission only (no withdrawal)

Strategy Fee Optimization
□ Grid levels set to 8 (not 10) during initial live phase
□ ATR spacing multiplier set to 0.8
□ Order refresh time set to 60s (not 30s)
□ RSI pause threshold: 70 (pauses grid = zero fees during overbought)
□ EMA 200 trend filter active (pauses in downtrend = zero fees)

Monitoring (All Free)
□ Telegram daily summary enabled
□ Google Sheets sync active (free)
□ Trade journal logging net PnL after fees
□ Break-even alert configured (notify if fees > 30% of gross PnL)
```

---

## 📌 Bottom Line

```
Minimum viable monthly cost:    $9.90  (with $200 capital, BNB discount)
Recommended monthly cost:       $18    (with $500 capital, BNB discount)
Maximum monthly cost (scaled):  $33.50 (with $1,000 capital + dashboard)

Year 1 all-in cost:             ~$220
Monthly from Month 5 onward:    Self-funded by trading profits
```

> The most expensive part of this project is **your time learning and tuning the strategy** — not the infrastructure. The infrastructure costs less than a Netflix subscription.

---

*TA-Enhanced BTC/USDT Grid Bot · Financial Plan v1.0 · Dubai, UAE · April 2026*