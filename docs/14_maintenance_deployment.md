# Maintenance & Zero-Impact Deployment Guide

This guide explains how to perform updates to the bot with minimal risk and downtime, ensuring that your capital and open positions are protected during transitions.

---

## 1. Achieving "Zero-Impact" Downtime ⚡

Since a deployment kills the bot process, you want to ensure the bot isn't "surprised" by the shutdown.

### The "Clean Swap" Workflow (Recommended)
Instead of letting the CI/CD pipeline abruptly kill the bot, follow these steps:

1.  **Manual Pause**: Send `/pause` via Telegram. 
    - This instructs the bot to cancel all active limit orders on Binance and move to a "Holding USDT" state.
2.  **Verify**: Wait for the Telegram confirmation: *"⏸️ Grid manually paused. All orders cancelled."*
3.  **Deploy**: Push your code to GitHub. The CI/CD will swap the containers.
4.  **Resume**: Once the new bot is live (you'll get the "Bot Started" alert), send `/resume` via Telegram.
    - The bot will recalculate indicators and place fresh orders based on the latest market price and your new code.

### Faster Swaps (Technical Optimization)
In `docker-compose.yml`, we can minimize the time between "Down" and "Up":
- **Pre-build**: The GitHub Action builds the image *before* logging into the server.
- **Selective Restart**: Instead of `docker compose down`, use:
  ```bash
  docker compose up -d --no-deps --build bot
  ```
  This only restarts the bot service, keeping the Dashboard and other infrastructure running.

---

## 2. When is the "Best Time" to Deploy? ⏰

Deploying during high volatility increases the risk of "slippage" (the price moving significantly while the bot is offline).

### ✅ Ideal Conditions
- **Low Volatility**: Check the ATR (Average True Range). If ATR is low/flat, it's a safe time.
- **RSI Neutral (40–60)**: Avoid deploying when the bot is in a `REACTIVATING` or `PAUSED` state due to extreme RSI, as the re-entry logic might trigger immediately upon restart.
- **Weekend/Consolidation**: Markets typically move slower on weekends or during mid-session consolidation.

### ❌ Times to Avoid
- **Major News Events**: Avoid 30 minutes before/after FOMC meetings, CPI data releases, or major Binance maintenance windows.
- **Exchange Volatility**: If the Bollinger Bands are widening rapidly, stay online.
- **High RSI (>70)**: If the market is parabolic, a restart might miss a critical "Pause" trigger if the code logic changed.

---

## 3. Maintenance Mode Strategy 🛠️

### 3.1 Using the "Manual Pause" as Maintenance Mode
Your bot already has a built-in Maintenance Mode: the `_manual_pause` flag.
- **Effect**: It stops the `on_tick` loop from placing orders but keeps the `CandleFeed` and `Telegram` services alive.
- **Persistence**: Currently, `_manual_pause` is stored in memory. 
  - *Future Enhancement*: Save the "Manual Pause" state to the database so that if the bot restarts unexpectedly, it stays paused until you say otherwise.

### 3.2 Scheduled Maintenance
If you plan to have the bot offline for more than an hour:
1. Send `/pause`.
2. Stop the container: `docker compose stop bot`.
3. Perform your infrastructure updates.
4. Start the container: `docker compose start bot`.
5. Send `/resume`.

---

## 4. Post-Deployment Checklist ✅

After every deployment, perform these "Pulse Checks" via Telegram:
1. **Check Connectivity**: Did you get the `📡 Telegram Command Handler Online` ping?
2. **Verify Status**: Send `/status`. Is the bot in the expected state (`ACTIVE` or `PAUSED`)?
3. **Inspect Logs**: Send `/logs`. Are there any `ERROR` or `WARNING` messages immediately after startup?
4. **Confirm Orders**: (Optional) Log into your Binance account to verify that the limit orders appear on the "Open Orders" tab exactly where you expect them.
