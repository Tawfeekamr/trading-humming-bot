# Trading Basics — For Bot Builders

A plain-English reference covering the fundamentals of trading, from someone building an automated trading system. No jargon without explanation.

---

## Table of Contents
1. [Spot vs Futures vs Options](#1-spot-vs-futures-vs-options)
2. [Long vs Short](#2-long-vs-short)
3. [Leverage & Margin](#3-leverage--margin)
4. [Orders: Market, Limit, Stop](#4-orders-market-limit-stop)
5. [Bid, Ask, Spread](#5-bid-ask-spread)
6. [Position Sizing & Risk Management](#6-position-sizing--risk-management)
7. [Technical Indicators (what your bot uses)](#7-technical-indicators)
8. [Trading Strategies (what your bot does)](#8-trading-strategies)
9. [Fees & Slippage](#9-fees--slippage)
10. [Market Hours & Sessions](#10-market-hours--sessions)
11. [Crypto vs Stocks vs Commodities](#11-crypto-vs-stocks-vs-commodities)
12. [Paper Trading vs Live Trading](#12-paper-trading-vs-live-trading)
13. [Key Formulas](#13-key-formulas)
14. [Glossary](#14-glossary)

---

## 1. Spot vs Futures vs Options

### Spot (what your bot does now)
```
You have $1,000. You buy BTC at $60,000. You now OWN 0.0167 BTC.
Price goes to $70,000? Your BTC is worth $1,167. Profit: $167.
Price goes to $50,000? Your BTC is worth $833. Loss: $167.
You can hold forever. You can never lose more than you spent.
```

### Futures (leveraged contract)
```
You have $1,000. You buy a BTC futures contract (10x leverage).
You now CONTROL $10,000 worth of BTC ($10,000 / $60,000 = 0.167 BTC).
Price goes to $70,000 (+17%)? Profit: $1,670 (+167%). You doubled your money.
Price goes to $54,000 (-10%)? Loss: -$1,670. You're WIPED OUT.
Liquidation: the exchange auto-sells your position when losses exceed your $1,000.
```

### Options (right but not obligation)
```
You pay $200 for a "call option" to buy BTC at $65,000 next month.
Price goes to $80,000? You exercise: buy at $65k, sell at $80k. Profit: $15k - $200 = $14,800.
Price stays at $60,000? Your option expires worthless. Loss: $200 (only what you paid).
Options = limited downside (you can only lose the premium), unlimited upside.
But: most options expire worthless (~70%).
```

### Comparison Table
| | Spot | Futures | Options |
|---|---|---|---|
| **Leverage** | 1x (none) | 2-100x | Varies |
| **Max loss** | What you invested | More than you invested (margin call) | Only the premium paid |
| **Can short?** | No | Yes | Yes (put options) |
| **Expiry?** | Never | Yes (monthly/quarterly) | Yes (specific date) |
| **Complexity** | Simple | Medium | High |
| **Good for bots?** | ✅ Start here | ⚠️ After proven edge | ❌ Advanced only |

---

## 2. Long vs Short

### Long (buy low, sell high)
```
Buy AAPL at $150 → sell at $160 → profit $10/share
You profit when price goes UP.
This is what your bot does (long-only crypto).
```

### Short (sell high, buy low)
```
Borrow AAPL at $160 → sell it → price drops to $150 → buy back → return it
Profit: $10/share
You profit when price goes DOWN.
Requires a margin account. Unlimited loss potential (price can go to infinity).
```

### Short squeeze
```
If many traders are short and price suddenly rises, they all must buy back
at the same time → drives price even higher → forces more shorts to buy back.
Example: GameStop 2021 — shorts lost $20+ billion in days.
```

---

## 3. Leverage & Margin

### What is leverage?
```
Leverage = borrowed money to control a bigger position.

1x leverage: $1,000 controls $1,000 of assets (spot)
10x leverage: $1,000 controls $10,000 of assets (futures)
50x leverage: $1,000 controls $50,000 of assets (crypto futures)

Leverage = Total Position Size / Your Capital
```

### What is margin?
```
Margin = the money you must deposit to open a leveraged position.
It's your "skin in the game."

CL (oil) futures: 1 contract = 1,000 barrels × $78 = $78,000
Initial margin: ~$6,600 (what you deposit)
Leverage: $78,000 / $6,600 = ~12x

If your position loses money and your margin drops below
"maintenance margin" (~$6,000), you get a MARGIN CALL:
- Deposit more money, OR
- Your position is liquidated (auto-closed at a loss)
```

### Why leverage kills
```
Your bot has a real edge: +2% per trade (after fees)
Market random noise: ±3% per day

At 1x:  Net per trade = +2% ± 3%  →  profitable over 100 trades ✅
At 10x: Net per trade = +20% ± 30%  →  bankrupt before edge shows ❌

Leverage amplifies EVERYTHING — including the noise that destroys you.
Only use leverage when your edge > noise (rare) and only at 2-3x.
```

---

## 4. Orders: Market, Limit, Stop

### Market Order (instant fill)
```
"I want to buy NOW at whatever price."
Buy 100 DOGE at market → fills instantly at the best available price.
Pro: guaranteed execution.
Con: you might get a worse price than expected (slippage).
Your bot uses MARKET for signal entries.
```

### Limit Order (specified price)
```
"I want to buy DOGE at $0.11, no higher."
Places an order at the exchange. Waits until price hits $0.11.
Pro: you control the exact price.
Con: might never fill if price doesn't reach your limit.
Your grid strategy uses LIMIT for buy/sell levels.
```

### Stop-Loss Order (automatic exit)
```
"I bought at $100. Sell if it drops to $90."
A resting order that activates when price hits $90.
Protects against large losses.
Your bot uses stop-loss on every position (SL in signals).
```

### Trailing Stop
```
"I bought at $100. Keep moving the stop up as price rises."
Price → $110, stop moves to $104.50 (5.5% trail)
Price → $120, stop moves to $114.50
Price → $115 → STOP TRIGGERED → sell at $114.50
Locks in profit while giving the trade room to run.
Your trend strategy uses trailing stops.
```

### Order Types Summary
| Order | Purpose | When to use |
|---|---|---|
| Market | Get in/out NOW | Signal entries, urgent exits |
| Limit | Control entry/exit price | Grid levels, patient entries |
| Stop-loss | Automatic loss limiter | Every position (mandatory) |
| Trailing stop | Lock profits while trending | Trend/swing strategies |
| Stop-limit | Stop triggers a limit order | Avoiding slippage on stops |

---

## 5. Bid, Ask, Spread

```
                ORDER BOOK
BIDS (buyers)           ASKS (sellers)
$99.95  ← 500 shares    $100.05  → 300 shares
$99.90  ← 200 shares    $100.10  → 500 shares
$99.85  ← 1,000 shares  $100.15  → 200 shares

Bid = highest price a buyer will pay ($99.95)
Ask = lowest price a seller will accept ($100.05)
Spread = Ask - Bid = $0.10

When you BUY at market → you pay the Ask ($100.05)
When you SELL at market → you get the Bid ($99.95)
That $0.10 gap is the "cost" of using a market order.
```

### Why spread matters for your bot
```
Crypto DOGE spread: ~$0.0002 (0.2%) → $10,000 trade costs $20 in spread
Stock AAPL spread: ~$0.01 (0.01%) → $10,000 trade costs $1 in spread
Illiquid stock spread: ~$0.50 (1%) → $10,000 trade costs $100 in spread

Wide spread = more expensive to trade. Your grid strategy
needs the spread < grid spacing to be profitable.
```

---

## 6. Position Sizing & Risk Management

### The Golden Rule
```
NEVER risk more than 1-2% of your total capital on a single trade.

$10,000 account → max risk per trade = $100-200

Risk = Distance from entry to stop-loss × position size
Entry: $100
Stop-loss: $95 (5% below)
Risk per share: $5
Max risk: $200
→ Position size = $200 / $5 = 40 shares ($4,000 invested)
```

### Your bot's position sizing
```
The bot uses risk-based sizing:
  risk_amount = capital × risk_per_trade_pct (default 2%)
  sl_distance = entry - stop_loss
  position_size = risk_amount / sl_distance

Example:
  Capital: $10,000
  Risk per trade: 2% = $200
  Entry: $0.12, SL: $0.09 (25% stop distance)
  Position size: $200 / 0.25 = $800 worth of crypto

If SL hits: lose $200 (2% of account) → survive to trade another day.
```

### The 2% Rule explained
```
If you risk 2% per trade and lose 10 in a row:
  After 10 losses: $10,000 × 0.98^10 = $8,171 (down 18%)
  Recoverable — you need a +22% run to get back.

If you risk 10% per trade and lose 10 in a row:
  After 10 losses: $10,000 × 0.90^10 = $3,487 (down 65%)
  Need +187% to recover. Effectively game over.

Losing 50% requires +100% to recover. Losing 90% requires +900%.
This is why position sizing is MORE important than strategy.
```

---

## 7. Technical Indicators

### What your bot's indicators measure

| Indicator | What it measures | Range | Signal |
|---|---|---|---|
| **RSI** (14) | Overbought/oversold | 0-100 | <30 = oversold, >70 = overbought |
| **ADX** (14) | Trend strength (NOT direction) | 0-100 | >25 = trending, <20 = ranging |
| **ATR** (14) | Volatility (average range) | Any | Used for SL/TP placement |
| **MACD** (12,26,9) | Momentum (fast vs slow EMA) | Any | Histogram >0 = bullish momentum |
| **EMA** (20, 50, 200) | Average price over N periods | Price | Price above EMA = uptrend |
| **Bollinger Bands** | Volatility envelope | Price | Squeeze = low vol, expansion = move coming |
| **Donchian** (20) | Recent high/low range | Price | Breakout above upper = new high |
| **Choppiness** | Range-bound vs trending | 0-100 | >61.8 = choppy (good for grid), <38.2 = trending |
| **VWAP** | Volume-weighted average | Price | Price above VWAP = buyers in control |

### How indicators work together
```
No single indicator is reliable. Your bot combines them:

Trend strategy: ADX (trend strength) + EMA (direction) + RSI (not overbought) + MACD (momentum) + Volume (confirmation)
   = needs 5/9 factors aligned before entering. This is "confluence."

Grid strategy: Choppiness > 50 (range-bound) + ADX < 25 (no strong trend)
   = only trades when market is bouncing sideways.

Swing strategy: ADX < 22 (ranging) + near lower Donchian band + RSI oversold
   = buys the bottom of the range.
```

### Indicator limitations
```
Indicators are DERIVED from price — they lag.
They tell you what HAS happened, not what WILL happen.
A signal that worked 100 times can fail on the 101st.
This is why your bot needs risk management (stop-losses) on every trade.
```

---

## 8. Trading Strategies

### What your bot's 4 strategies do

**Grid** — profits from a ranging market
```
Place buy orders below price, sell orders above.
Price bounces up and down → buy low, sell high → collect the spread.
Works when: market is choppy (high Choppiness, low ADX).
Fails when: market trends strongly in one direction → grid accumulates losers.

Example:
  Price at $100. Grid: buy at $98, $96, $94. Sell at $102, $104, $106.
  Price bounces 98→102→96→104 → 4 fills × $2 profit = $8.
  Price drops to $80 → all buys filled, sitting on heavy losses.
```

**Trend Following** — profits from sustained directional moves
```
Wait for a strong trend (ADX > 25, EMA aligned, volume confirmation).
Enter with the trend. Trail the stop upward. Exit when trend breaks.
Works when: market is trending hard (low Choppiness, high ADX).
Fails when: market chops → false entries, quick stop-outs.

Example:
  NVDA starts trending. Entry at $400. Trail stop at $380, $420, $450...
  NVDA runs to $500. Exit at $470 (trail triggered). Profit: $70/share.
```

**Swing** — profits from range reversals
```
Wait for price to hit the bottom of a range (lower Donchian band).
Buy the bounce. Exit at mid-range or upper band.
Works when: market ranges in a channel.
Fails when: range breaks downward → "catching a falling knife."
```

**Mean Reversion** — profits from extreme moves reverting
```
Wait for a sharp price drop (flash crash). Buy the bounce.
Works when: markets overreact and snap back.
Fails when: the drop is justified (bad news) → keeps falling.
```

### Strategy selection by market condition
```
Strong uptrend (ADX > 30, price above EMA-200):
  → TREND strategy ✅   Grid ❌   Swing ❌   MR ❌

Ranging/choppy (ADX < 20, Choppiness > 50):
  → GRID strategy ✅   Trend ❌   Swing ✅   MR ✅

Volatile crash (sharp -5%+ move):
  → MR strategy ✅     (but dangerous)

No clear edge:
  → CASH (don't trade)
```

---

## 9. Fees & Slippage

### Fees
```
Spot crypto (Binance): 0.1% maker / 0.1% taker
  → $10,000 trade costs $10

Stocks (IBKR): $0.005/share (max 1% of trade value)
  → 100 shares of $50 stock = $0.50 commission

Futures (IBKR): $0.85/contract
  → 1 CL contract = $0.85 (very cheap)

Options (IBKR): $0.65/contract
```

### Slippage
```
You place a market order to buy 1,000 DOGE at $0.12.
But the order book only has 500 at $0.12, next 300 at $0.121, 200 at $0.122.
You get: 500 @ $0.12 + 300 @ $0.121 + 200 @ $0.122 = average $0.1208.
Expected: $0.12. Actual: $0.1208. Slippage: $0.0008 × 1,000 = $0.80.

Slippage is worse with:
  - Large orders (you eat through the order book)
  - Illiquid markets (thin order book)
  - Fast-moving markets (price moves while your order executes)
  - Market orders (you accept any price)

Paper trading has ZERO slippage — fills at the exact price.
This is why paper P&L is always better than live P&L.
```

### Total cost per round-trip trade
```
Entry fee:     0.1%
Exit fee:      0.1%
Slippage:      0.05-0.5% (depends on liquidity)
Spread:        0.05-0.2%
Total:         ~0.3-0.9% per round trip

Your strategy MUST make more than this to be profitable.
A grid that captures 1% per cycle nets only 0.1-0.7% after costs.
```

---

## 10. Market Hours & Sessions

### Crypto (your bot now)
```
24/7/365 — never closes.
Peak volume: US + Europe overlap (1-5 PM UTC).
Weekend volume lower → wider spreads → worse execution.
Your bot trades around the clock.
```

### US Stocks (with IBKR)
```
Pre-market:  4:00 AM - 9:30 AM ET (low liquidity)
Regular:     9:30 AM - 4:00 PM ET (main session)
After-hours: 4:00 PM - 8:00 PM ET (low liquidity)
Closed: Weekends + US holidays

Your equity scanner runs AFTER close (4:00 PM ET).
Entry at next day's open (9:30 AM ET).
```

### Futures (oil, gold, S&P)
```
CME Globex: Sunday 6 PM → Friday 5 PM ET (nearly 24h)
Daily break: 5 PM - 6 PM ET
High volume: US session (9:30 AM - 4 PM ET) + London (3 AM - 11 AM ET)
```

### PDT Rule (Pattern Day Trader)
```
If you make more than 3 day trades in 5 business days:
  AND your account is under $25,000:
    → Account is restricted for 90 days.

Solution: Hold positions overnight (swing trading).
Your strategies hold 2-10 days → PDT is NOT an issue.
```

---

## 11. Crypto vs Stocks vs Commodities

| Feature | Crypto | Stocks | Oil Futures |
|---|---|---|---|
| **Volatility** | 5-20%/day | 1-3%/day | 2-5%/day |
| **24/7?** | ✅ | ❌ (9:30-4 ET) | ~23h weekdays |
| **Min capital** | $10 | $1/share | ~$6,600/contract |
| **Shorting** | Hard (spot) | Margin account | Easy |
| **Liquidity** | High (BTC/ETH) | Very high (AAPL) | Very high (CL) |
| **Regulation** | Low | High (SEC) | High (CFTC) |
| **Tax** | Varies | Capital gains | 60/40 (US) |
| **Best strategy** | Grid/Swing (high vol) | Trend (fundamentals) | Trend (OPEC moves) |

---

## 12. Paper Trading vs Live Trading

### Paper (simulated)
```
Fake money. Real market data. Instant fills at displayed price.
No slippage, no emotions, no real loss.

Pros: Test strategies risk-free. Validate logic.
Cons: Zero slippage → inflated P&L. No fear → reckless sizing.
      Orders don't move the market → unrealistic for large sizes.

YOUR BOT IS HERE NOW.
```

### Live (real money)
```
Real money. Real fills (with slippage). Real emotions.

Pros: Real data. Real feedback. Real profits.
Cons: Slippage eats edge. Fear/greed corrupt decisions.
      A 20% drawdown feels VERY different with real money.

The gap between paper and live:
  Paper P&L × 0.6-0.8 = expected live P&L (rule of thumb)
  Your +$1,200 paper signal P&L → expect $700-950 live.
```

### Transition checklist (paper → live)
```
1. ✅ 3+ months of paper trading
2. ✅ Positive risk-adjusted return (Sharpe > 0.5)
3. ✅ Max drawdown < 15% in paper
4. ✅ All bugs fixed (Decimal fix, entry-zone, dedup — done ✅)
5. ✅ Risk management proven (stop-losses, max positions, daily loss limits)
6. Start with 10-20% of intended capital. Scale up only if profitable.
```

---

## 13. Key Formulas

### Profit/Loss
```
Long P&L = (Sell Price - Buy Price) × Quantity - Fees
Short P&L = (Sell Price - Buy Price) × Quantity - Fees  (sell first, buy back)

Example: Buy 100 DOGE at $0.12, sell at $0.14
  P&L = ($0.14 - $0.12) × 100 = $2.00 - fees
```

### Percentage Return
```
Return % = (Profit / Capital Risked) × 100

Example: $1,000 capital, buy $500 DOGE, sell for $550
  Profit = $50
  Return = ($50 / $1,000) × 100 = 5% (on total account)
  Return = ($50 / $500) × 100 = 10% (on position)
```

### Risk-Reward Ratio (R:R)
```
R:R = Potential Profit / Potential Loss

Entry: $100. TP: $110. SL: $95.
Reward = $110 - $100 = $10
Risk = $100 - $95 = $5
R:R = 10/5 = 2.0 (or "2R")

Your bot requires R:R ≥ 1.5 to enter.
Meaning: for every $1 risked, aim for $1.50+ profit.
At R:R = 2.0, you only need 34% win rate to break even.
```

### Win Rate vs R:R (breakeven)
```
Breakeven win rate = 1 / (1 + R:R)

R:R 1.0 → need 50% win rate
R:R 1.5 → need 40% win rate
R:R 2.0 → need 33% win rate
R:R 3.0 → need 25% win rate

Your trend strategy: R:R ~2.0 → needs >33% wins to profit.
Your grid strategy: R:R ~0.5 (small wins, big losses) → needs >67% wins.
```

### Sharpe Ratio (risk-adjusted return)
```
Sharpe = (Average Return - Risk-Free Rate) / Std Dev of Returns

Sharpe > 1.0 = good
Sharpe > 2.0 = excellent
Sharpe < 0 = losing money

Your bot's thesis RL agent optimizes for Sharpe (risk-adjusted),
NOT raw P&L. A smooth 10%/year (Sharpe 2) beats a volatile 30%/year (Sharpe 0.5).
```

### Compounding
```
Monthly return: 5%
After 12 months: $1,000 × 1.05^12 = $1,796 (+80%)

Monthly return: 5%, but one -30% month:
$1,000 × 1.05^11 × 0.70 = $1,257 (+26%)

One bad month destroys months of gains.
This is why DRAWDOWN CONTROL matters more than maximizing returns.
```

---

## 14. Glossary

| Term | Meaning |
|---|---|
| **Ask** | Lowest price a seller will accept |
| **Bid** | Highest price a buyer will pay |
| **Bull/Bear** | Bull = expects price up, Bear = expects down |
| **Contango** | Futures price > spot price (normal market, roll cost) |
| **Drawdown** | Peak-to-trough decline in account value |
| **Fill** | An order that executed (got filled) |
| **Liquidation** | Forced close of a leveraged position (margin call) |
| **Liquidity** | How easily you can buy/sell without moving price |
| **Long** | Bought, hoping price goes up |
| **Margin** | Collateral deposited for leveraged trading |
| **Maker** | Order that adds liquidity (limit order resting on book) |
| **Taker** | Order that removes liquidity (market order hitting the book) |
| **OHLCV** | Open, High, Low, Close, Volume — the standard bar data |
| **Position** | An open trade (long or short) |
| **R:R** | Risk-to-reward ratio |
| **Rollover** | Closing an expiring futures contract, opening the next one |
| **Sharpe** | Risk-adjusted return metric |
| **Short** | Sold borrowed shares, hoping price goes down |
| **Slippage** | Difference between expected price and actual fill price |
| **Spot** | Buying the actual asset (not a contract) |
| **Spread** | Difference between bid and ask |
| **Stop-loss** | Automatic order to exit at a specific price (limits loss) |
| **Take-profit** | Automatic order to exit at a specific price (locks gain) |
| **TP1, TP2, TP3** | Multiple take-profit levels (partial exits) |
| **Trailing stop** | A stop-loss that moves up as price rises |
| **Volatility** | How much price swings (measured by ATR or stddev) |
| **Volume** | Number of shares/coins traded in a period |
| **Whipsaw** | Price quickly reverses, triggering entry then stop-loss |
