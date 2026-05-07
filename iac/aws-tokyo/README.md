# AWS Tokyo Infrastructure as Code (IaC)

Terraform configuration to deploy the trading bot to AWS Tokyo (ap-northeast-1).

## Why Tokyo?

~35ms latency to Binance servers — critical for grid trading where order placement speed matters.

## Access: AWS Session Manager (No SSH)

This setup uses **AWS Session Manager** instead of SSH. Benefits:
- No SSH keys to manage
- No inbound ports open (zero attack surface)
- Works from any IP — no need to update security groups when your IP changes
- All sessions logged to CloudTrail for audit

## Prerequisites

1. **AWS CLI** installed and configured (`aws configure`)
2. **Terraform** >= 1.0 ([Download](https://developer.hashicorp.com/terraform/downloads))
3. **Session Manager plugin** for AWS CLI:
   ```bash
   # macOS
   brew install session-manager-plugin
   # Linux
   curl "https://session-manager-downloads.s3.amazonaws.com/plugin/latest/linux_64bit/session-manager-plugin.rpm" -o session-manager-plugin.rpm
   sudo yum install -y session-manager-plugin.rpm
   ```

## Deploy

```bash
cd iac/aws-tokyo
terraform init
terraform plan
terraform apply
```

After apply, note the outputs:
- `bot_server_public_ip` — whitelist this on Binance
- `instance_id` — the EC2 instance ID
- `ssm_connect_command` — command to connect

## Connect to the Server

```bash
# Shell access
aws ssm start-session --target i-xxxxxxxxxxxxx

# Or use the output directly:
terraform output -raw ssm_connect_command | bash
```

## Access the Streamlit Dashboard

Use port forwarding through Session Manager:

```bash
aws ssm start-session \
  --target i-xxxxxxxxxxxxx \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8502"]}'
```

Then open `https://localhost:8502` in your browser.

## Deploy the Bot

Once connected via Session Manager:

```bash
# Upload your project (from your local machine)
aws ssm put-parameter --name "/trading-bot/env" --type SecureString --value "$(cat .env)" --overwrite

# On the server
cd /home/ec2-user
git clone <your-repo-url> trading-bot
cd trading-bot

# Create .env file
aws ssm get-parameter --name "/trading-bot/env" --with-decryption --query 'Parameter.Value' --output text > .env

# Start the bot
docker-compose up -d
```

## Whitelist on Binance

1. Get the Elastic IP: `terraform output bot_server_public_ip`
2. Go to Binance → API Management → Edit restrictions
3. Add the IP to the whitelist

## Security

- **No inbound ports** — the security group allows zero ingress
- **Session Manager** — IAM-authenticated access, no SSH keys
- **Elastic IP** — stable outbound IP for Binance API whitelisting
- **All egress allowed** — Exchange APIs, Docker pulls, WebSocket connections
- Secrets are NOT managed by Terraform — use `.env` on the server or AWS Secrets Manager
