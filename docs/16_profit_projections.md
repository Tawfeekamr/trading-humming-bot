# 16 — Realistic Profit Projections

> **Date:** 2026-05-09  
> **BTC Price:** ~$103,000 (at time of analysis)  
> **Model:** Based on actual strategy parameters from `config/strategy.yaml` and live bot data

---

## ⚠️ Honest Disclaimer

**This is a grid trading bot, not a money printer.** The numbers below are modeled from your actual code parameters with realistic 1h market assumptions. Grid bots are **not** consistently profitable in all regimes — they make money in ranging markets and lose money in trending markets. The projections below reflect this reality and the massive impact of exchange fees.

---

## How the Math Works

### Your Strategy Parameters

| Parameter | Value | Source |
|-----------|-------|--------|
| Grid levels | 6 per side (12 total orders) | `strategy.yaml` |
| Min USDT reserve | $100 | `strategy.yaml` |
| ATR spacing multiplier | 1.5× | `strategy.yaml` |
| Fee rate | 0.075% per trade (maker, BNB discount) | Code default |
| Max BTC exposure | 80% | `strategy.yaml` |

### Grid Spacing Calculation

Your grid manager uses `min(ATR × 1.5, BB_half_width / 7)` — whichever is smaller.
Based on **1h candles** at $103K BTC, the volatility is tighter than daily metrics:

| Market Condition | ATR (1h) | ATR × 1.5 | BB Half-Width | BB / 7 | **Actual Spacing** |
|:---|:---:|:---:|:---:|:---:|:---:|
| Low volatility | $200 | $300 | $250 | **$35** | **$35** |
| Normal | $350 | $525 | $400 | **$57** | **$57** |
| High volatility | $500 | $750 | $600 | **$85** | **$85** |

> The spacing is almost always **BB-capped** (not ATR-driven) because `BB_half / 7` is much smaller than `ATR × 1.5`. The grid is packed tightly within the Bollinger Bands, resulting in ~$55 average spacing.

### The Fee Trap & Multi-Level Swings

Because the grid spacing is so tight (~$57), a single 1-level round-trip actually **loses money** due to fees.
```
Order Value = ($100,000 - $100) / 12 = $8,325
1-Level Gross Profit = $8,325 × ($57 ÷ $103,000) = $4.60
Fees per RT = $8,325 × 2 × 0.00075 = $12.49
Net per 1-level RT = $4.60 - $12.49 = -$7.89 ❌
```

**The bot is only profitable because it captures multi-level round-trips.** Live data shows the average round-trip spread is ~4 levels (~$230 to $400 spread).

```
4-Level Gross Profit = $8,325 × ($228 ÷ $103,000) = $18.43
Fees per RT = $12.49
Net per 4-level RT = $18.43 - $12.49 = $5.94 ✅
```

---

## 📊 Profit Projections by Capital (Multi-Level Model)

*Assuming an average spread of 4 levels per round-trip and accounting for pauses during trending markets.*

### Scenario 1: 📊 Normal Market (Base Case)

> Mixed conditions — moderate volatility, some trends, some ranging. Bot active ~55% of the time.

| Capital | Order Size | Net per RT | Effective RTs/Day | Monthly P&L | Yearly % | After Infra |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **$1,000** | $75 | $0.05 | 0.8 | $1.20 | 1.4% | −$28.80 |
| **$25,000** | $2,075 | $1.48 | 0.8 | $35.52 | 1.7% | +$5.52 |
| **$50,000** | $4,158 | $2.96 | 0.8 | $71.04 | 1.7% | +$41.04 |
| **$100,000** | $8,325 | $5.94 | 0.8 | $142.56 | 1.7% | +$112.56 |
| **$500,000** | $41,658 | $29.70 | 0.8 | $712.80 | 1.7% | +$682.80 |

### Scenario 2: 🟢 Ranging Bull Market (Optimistic)

> Oscillating within Bollinger Bands with high frequency. This is the ideal scenario.

| Capital | Order Size | Net per RT | Effective RTs/Day | Monthly P&L | Yearly % | After Infra |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **$1,000** | $75 | $0.09 | 2.0 | $5.40 | 6.4% | −$24.60 |
| **$25,000** | $2,075 | $2.61 | 2.0 | $156.60 | 7.5% | +$126.60 |
| **$50,000** | $4,158 | $5.23 | 2.0 | $313.80 | 7.5% | +$283.80 |
| **$100,000** | $8,325 | $10.48 | 2.0 | $628.80 | 7.5% | +$598.80 |
| **$500,000** | $41,658 | $52.41 | 2.0 | $3,144.60 | 7.5% | +$3,114.60 |

---

## 💸 Fee Impact Analysis

Fees remain the **single biggest drag** on this strategy. Even on profitable multi-level round-trips, fees consume ~65-70% of your gross profits.

| Action | Fee Reduction | Monthly Savings ($100K) |
|:---|:---:|:---:|
| VIP 1 (>$1M/mo volume) | 0.06% → 20% less | ~$60 |
| VIP 2 (>$5M/mo volume) | 0.04% → 47% less | ~$140 |
| Market maker program | 0.02% → 73% less | ~$220 |

---

## 🏦 Infrastructure Cost Impact

AWS Tokyo `t3.medium`: **~$30/month**

| Capital | Infra as % of Capital | Break-Even Bar |
|:---:|:---:|:---|
| $1,000 | 3.0%/month | ❌ Infra cost exceeds any realistic profit |
| $25,000 | 0.12%/month | ⚠️ Marginal — covers infra but low net return |
| $50,000 | 0.06%/month | ✅ Manageable |
| $100,000 | 0.03%/month | ✅ Negligible |

---

## 🔑 Key Conclusions

### 1. $1,000 Capital — Not Viable for Income
Infrastructure costs alone ($360/year) far exceed any possible profit. You'll make ~$15-60/year gross, losing money after server costs. It is only useful for testing.

### 2. $25,000 Capital — True Break-Even Territory
You generate enough gross profit to cover your AWS servers, netting a few dollars a month. 

### 3. $50,000–$100,000 — Reasonable Starting Point  
Returns of $400–$1,300/year after infra. This is comparable to a 1-2% savings account but with significant volatility risk. 

### 4. Spacing Needs Widening
Because 1-level round trips guarantee a net loss at $57 spacing, the strategy heavily relies on the market swinging 3-4 levels to turn a profit. Reducing the number of levels (e.g. from 6 to 4) would widen the spacing naturally, making 1-level or 2-level swings profitable and dramatically reducing the fee-to-gross ratio.

---

## 🔧 How to Improve Profitability

| Priority | Action | Expected Impact |
|:---|:---|:---|
| **HIGH** | Widen grid spacing (reduce levels to 4) | Guaranteed profit on smaller swings, lower fee ratio |
| **HIGH** | Get VIP fee tier or apply for market maker program | 20–73% fee reduction |
| **MEDIUM** | Add volume profile or VWAP to filter low-quality zones | Higher win rate |
| **MEDIUM** | Add MACD trend confirmation filter | Avoid trading against strong trends |
| **LOW** | Add multi-pair support (ETH, SOL) | Diversification, more opportunities |
