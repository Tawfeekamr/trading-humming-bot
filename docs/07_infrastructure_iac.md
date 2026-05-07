# 🛠️ Infrastructure as Code (IaC): Zero-Maintenance Setup

> "I don't want to babysit the infra." — The Goal.

This document outlines how to configure your infrastructure so it is **self-healing**, **auto-deploying**, and **hands-off**.

---

## 🏗️ 1. The Stack: Railway + GitHub Actions

We use **Railway** for hosting because it handles Docker, SSL, and secrets natively with zero server management. We use **GitHub Actions** to automate the testing and deployment lifecycle.

---

## 🔄 2. Self-Healing & Auto-Restart

Grid bots must be online 24/7. Railway handles this automatically:

### 2.1 Restart Policy
Railway's default behavior is to restart a crashed service. If your bot crashes due to a network error or API timeout:
- Railway detects the non-zero exit code.
- It automatically restarts the container.
- **Hands-off**: You don't need to manually click "restart".

### 2.2 Health Checks
If you deploy the Streamlit dashboard, Railway will use the HTTP health check. If the dashboard stops responding, Railway will redeploy/restart the service.

---

## 🚀 3. Auto-Deployment (CI/CD)

Every time you improve your strategy and push to GitHub, the infrastructure updates itself.

### 3.1 GitHub Workflow (`.github/workflows/deploy.yml`)
Create this file to ensure every push is "safe" before it goes live.

```yaml
name: Strategy Safety Check
on: [push]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run Indicator Tests
        run: pytest tests/test_indicators.py
      - name: Run Risk Management Tests
        run: pytest tests/test_circuit_breaker.py
```

### 3.2 Railway Integration
Railway connects directly to your GitHub repo.
- **Trigger**: Success of the GitHub Action above.
- **Action**: Railway pulls the new code, builds the Docker image, and rolls over the deployment with zero downtime.

---

## 🔒 4. Zero-Touch Secret Management

Stop managing `.env` files.

- **Storage**: Paste all keys from `.env.example` into **Railway → Service → Variables**.
- **Security**: Railway encrypts these at rest. They are injected as environment variables at runtime.
- **Maintenance**: When your Binance API key expires (every 90 days), just update the variable in Railway; the bot will auto-redeploy with the new key.

---

## 📡 5. Monitoring (The "Check-In" System)

Instead of you checking the bot, the bot checks in with you.

### 5.1 Telegram Pulse
The `pnl_reporter.py` script ensures you get:
- **Instant Alerts**: Filled orders and circuit breaker triggers.
- **Hourly Summary**: "I'm still running and here is the result."
- **Daily Report**: Emailed/Sent at midnight UTC.

### 5.2 Google Sheets Log
The `sheets_sync.py` script acts as your permanent database. Even if Railway were to disappear, every trade record is safe in Google Sheets.

---

## ⚙️ 6. Zero-Maintenance Settings

Apply these to your `config/strategy.yaml` to reduce infrastructure "noise":

| Parameter | Recommended | Why? |
|-----------|-------------|------|
| `order_refresh_time` | `60` | Reduces API rate limit risks and CPU usage. |
| `log_level` | `INFO` | Keeps logs clean; only shows important events. |
| `max_drawdown_pct` | `10.0` | Prevents "babysitting" during a crash; it just stops. |

---

## 🛠️ Step-by-Step Hands-Off Setup

1. **Connect GitHub**: Connect your repo to Railway.
2. **Environment**: Set `ENV=paper` in Railway variables initially.
3. **Cron Jobs**: Railway doesn't need cron; the `pnl_reporter.py` uses an internal scheduler (`APScheduler`).
4. **Walk Away**: Push your code and monitor Telegram. Only intervene if the 🚨 alert triggers.

---

*IaC Guide v1.0 · Generated May 2026*
