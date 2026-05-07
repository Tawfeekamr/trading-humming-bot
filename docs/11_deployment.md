# Deployment Guide

End-to-end setup: infrastructure, CI/CD, and running the bot.

## Architecture

```
GitHub Push ──► GitHub Actions ──► AWS SSM ──► EC2 (Tokyo)
                  │                              │
                  ├─ Test stage                  ├─ Bot container (Hummingbot)
                  └─ Deploy stage                ├─ Dashboard container (Streamlit)
                      │                          └─ Nginx reverse proxy
                      ├─ AWS credentials
                      └─ Telegram notification
```

## Prerequisites

- AWS account with access key for SSM and EC2
- GitHub repository with the codebase
- Telegram bot token and chat ID (from @BotFather and @userinfobot)
- Domain name (optional, for HTTPS dashboard)

## Infrastructure (Terraform)

### Resources Created

| Resource | Type | Details |
|----------|------|---------|
| VPC | `aws_vpc` | 10.0.0.0/16, DNS hostnames enabled |
| Subnet | `aws_subnet` | 10.0.1.0/24, ap-northeast-1a (Tokyo) |
| Internet Gateway | `aws_internet_gateway` | Public internet access |
| Security Group | `aws_security_group` | Inbound: 80, 443. Outbound: all. No SSH. |
| IAM Role | `aws_iam_role` | AmazonSSMManagedInstanceCore (Session Manager) |
| EC2 Instance | `aws_instance` | t3.medium, AL2023, 20GB gp3 |
| Elastic IP | `aws_eip` | Static IP for Binance API whitelist |

### Deploy Infrastructure

```bash
cd iac/aws-tokyo
terraform init
terraform apply
```

Outputs: `instance_id`, `ssm_connect_command`, `dashboard_port_forward_command`

### Access the Instance

No SSH — use AWS Session Manager:

```bash
aws ssm start-session --target <instance-id> --region ap-northeast-1
```

## Docker Containers

### Bot Container (`Dockerfile`)

- Base: `hummingbot/hummingbot:latest`
- Uses conda environment for Python 3.13 + hummingbot
- Installs project dependencies via `/opt/conda/envs/hummingbot/bin/pip`
- Copies `src/`, `scripts/ta_grid_btcusdt.py`, `conf/scripts/ta_grid_btcusdt.yml`
- Entrypoint: `docker-entrypoint.sh` (initializes password file on first run)
- Runs Hummingbot quickstart in headless mode with v2 strategy config

### Dashboard Container (`Dockerfile.dashboard`)

- Base: `python:3.12-slim`
- Installs `requirements.txt` (streamlit, plotly, pandas, etc.)
- Runs `streamlit run app.py` on port 8502
- Reads `.env` via `python-dotenv` for credentials

### Docker Compose

```yaml
services:
  bot:
    build: { context: ., dockerfile: Dockerfile }
    env_file: .env.docker
    restart: unless-stopped
    volumes:
      - ./data:/home/hummingbot/data
      - ./logs:/home/hummingbot/logs
      - ./.env:/home/hummingbot/.env

  dashboard:
    build: { context: ., dockerfile: Dockerfile.dashboard }
    env_file: .env.docker
    restart: unless-stopped
    ports: ["8502:8502"]
    volumes:
      - ./data:/app/data
      - ./.env:/app/.env
```

**Volumes**:
- `data/` — SQLite database (shared between containers)
- `logs/` — Event logs and Hummingbot logs
- `.env` — Secrets loaded via python-dotenv (avoids Docker `$` interpolation)

**Note**: Config files (`src/`, `scripts/`, `conf/scripts/`) are baked into the Docker image, not mounted as volumes. They update on each CI/CD rebuild.

## Environment Variables

### `.env.docker` (safe for Docker compose)
```
ENV=paper
LOG_LEVEL=INFO
CONFIG_PASSWORD=tradingbot
```

### `.env` (secrets — mounted as volume, never committed)
```
# Mode
ENV=paper

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-...
TELEGRAM_CHAT_ID=123456789

# Dashboard Auth
DASHBOARD_USERNAME=amro
DASHBOARD_PASSWORD_HASH=$2b$12$...    # bcrypt hash
COOKIE_SECRET=random-string

# Binance (live mode only)
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
```

### Generating Dashboard Password Hash

```python
from streamlit_authenticator import Authenticate
# Or use bcrypt directly:
import bcrypt
hash = bcrypt.hashpw(b"your-password", bcrypt.gensalt(12))
print(hash.decode())
```

## CI/CD Pipeline (GitHub Actions)

### Stages

```
Push to main
    │
    ▼
┌─────────────┐
│  Run Tests   │  pytest tests/ -v --tb=short
│  (ubuntu)    │  Python 3.12 + pip cache
└──────┬──────┘
       │ (only push to main, not PRs)
       ▼
┌─────────────┐
│ Deploy to    │  AWS SSM send-command
│  AWS Tokyo   │  1. git pull origin main
│              │  2. chmod 777 data logs
│              │  3. docker compose down
│              │  4. docker compose up -d --build
│              │  5. Poll status (10min timeout)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Telegram    │  Success or failure notification
│  Notify      │  with commit message + SHA
└─────────────┘
```

### Required GitHub Secrets

| Secret | Description |
|--------|-------------|
| `AWS_ACCESS_KEY_ID` | IAM user with SSM + EC2 permissions |
| `AWS_SECRET_ACCESS_KEY` | Corresponding secret key |
| `EC2_INSTANCE_ID` | Instance ID (e.g., `i-0eafde6592d97eab2`) |
| `TELEGRAM_BOT_TOKEN` | For deploy notifications |
| `TELEGRAM_CHAT_ID` | For deploy notifications |

### Setting Up the IAM User

```bash
aws iam create-user --user-name github-actions
aws iam attach-user-policy --user-name github-actions --policy-arn arn:aws:iam::aws:policy/AmazonSSMFullAccess
aws iam attach-user-policy --user-name github-actions --policy-arn arn:aws:iam::aws:policy/AmazonEC2ReadOnlyAccess
aws iam create-access-key --user-name github-actions
```

## Nginx Reverse Proxy

Installed on EC2 instance. Proxies port 80 → Streamlit on 8502.

Config stored in SSM Parameter Store (`/trading-bot/nginx-config`) to avoid shell escaping issues with `$` characters.

### SSL with Let's Encrypt

Once DNS propagates:

```bash
aws ssm send-command \
  --instance-ids <instance-id> \
  --document-name "AWS-RunShellScript" \
  --parameters commands='["sudo certbot --nginx -d dashboard.yourdomain.com --non-interactive --agree-tos --email you@email.com"]'
```

## DNS Setup

Point a subdomain to the Elastic IP:

```
Type: A Record
Name: dashboard
Value: <elastic-ip>
TTL: 300
```

Propagation can take up to 48 hours. Check at https://dnschecker.org.

## Manual Operations

### Check Container Status

```bash
aws ssm send-command --instance-ids <id> --document-name "AWS-RunShellScript" \
  --parameters commands='["docker ps --format \"{{.Names}} {{.Status}}\""]'
```

### View Bot Logs

```bash
aws ssm send-command --instance-ids <id> --document-name "AWS-RunShellScript" \
  --parameters commands='["docker logs trading-humming-bot-bot-1 --tail 50"]'
```

### Rebuild Containers Manually

```bash
aws ssm send-command --instance-ids <id> --document-name "AWS-RunShellScript" \
  --parameters commands='[
    "cd /home/ec2-user/trading-humming-bot && git fetch origin && git reset --hard origin/main",
    "cd /home/ec2-user/trading-humming-bot && docker compose down",
    "chmod 777 /home/ec2-user/trading-humming-bot/data /home/ec2-user/trading-humming-bot/logs",
    "cd /home/ec2-user/trading-humming-bot && docker compose up -d --build"
  ]'
```

### Check Event Logs

```bash
aws ssm send-command --instance-ids <id> --document-name "AWS-RunShellScript" \
  --parameters commands='["docker exec trading-humming-bot-bot-1 cat /home/hummingbot/logs/events_$(date -u +%Y-%m-%d).jsonl"]'
```

## Switching to Live Trading

1. Change `exchange` in config or `TAGridConfig` from `binance_paper_trade` to `binance`
2. Add `BINANCE_API_KEY` and `BINANCE_API_SECRET` to `.env`
3. Set `ENV=live` in `.env.docker`
4. Whitelist the Elastic IP on Binance
5. Use API key with trade permission only (no withdrawals)
6. Rebuild containers

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `unable to open database file` | data/ directory permissions | `chmod 777 data logs` |
| `No module named hummingbot` | conda not activated in CMD | Source conda.sh in entrypoint |
| `StrategyV2Base not found` | Old script with ScriptStrategyBase | Use StrategyV2Base pattern |
| `order_tracker not writable` | Name collision with base class | Use `_grid_order_tracker` |
| `Decimal has no attribute 'available'` | v2 returns Decimal not object | Use `hasattr` check |
| Dashboard 502 | Nginx up but Streamlit down | Check dashboard container logs |
| MQTT connection failed | No MQTT broker configured | Non-critical, strategy still runs |
