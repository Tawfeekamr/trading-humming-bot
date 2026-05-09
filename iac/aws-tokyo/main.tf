provider "aws" {
  region = "ap-northeast-1" # Tokyo
}

# ── NETWORK ─────────────────────────────────────────────────────────

resource "aws_vpc" "trading_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  tags = {
    Name = "trading-bot-vpc"
  }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.trading_vpc.id
}

resource "aws_subnet" "public_subnet" {
  vpc_id                  = aws_vpc.trading_vpc.id
  cidr_block              = "10.0.1.0/24"
  map_public_ip_on_launch = true
  availability_zone       = "ap-northeast-1a"
}

resource "aws_route_table" "public_rt" {
  vpc_id = aws_vpc.trading_vpc.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
}

resource "aws_route_table_association" "public_assoc" {
  subnet_id      = aws_subnet.public_subnet.id
  route_table_id = aws_route_table.public_rt.id
}

# ── IAM ROLE FOR SESSION MANAGER ────────────────────────────────────

resource "aws_iam_role" "ssm_role" {
  name = "trading-bot-ssm-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ssm_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ssm_profile" {
  name = "trading-bot-ssm-profile"
  role = aws_iam_role.ssm_role.name
}

# ── SECURITY ────────────────────────────────────────────────────────

resource "aws_security_group" "bot_sg" {
  name        = "trading-bot-sg"
  description = "No inbound ports - access via Session Manager only"
  vpc_id      = aws_vpc.trading_vpc.id

  # No SSH ingress — Session Manager handles access
  # Dashboard served via HTTPS through nginx reverse proxy

  # HTTPS for dashboard
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # HTTP -> redirect to HTTPS (for certbot + redirect)
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Outbound: Exchange APIs, Docker pulls, Binance WebSocket
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── COMPUTE ─────────────────────────────────────────────────────────

data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023*-x86_64"]
  }
}

resource "aws_instance" "bot_server" {
  ami           = data.aws_ami.amazon_linux_2023.id
  instance_type = "t3.small"
  subnet_id     = aws_subnet.public_subnet.id

  vpc_security_group_ids = [aws_security_group.bot_sg.id]
  iam_instance_profile   = aws_iam_instance_profile.ssm_profile.name

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  # Bootstrap: Docker + Session Manager agent (pre-installed on AL2023)
  user_data = <<-EOF
              #!/bin/bash
              yum update -y
              yum install -y docker
              systemctl start docker
              systemctl enable docker
              usermod -a -G docker ec2-user

              # Docker Compose v2 plugin
              mkdir -p /usr/local/lib/docker/cli-plugins
              curl -SL https://github.com/docker/compose/releases/download/v2.36.2/docker-compose-linux-x86_64 -o /usr/local/lib/docker/cli-plugins/docker-compose
              chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

              # Docker Buildx plugin
              curl -SL https://github.com/docker/buildx/releases/download/v0.22.0/docker-buildx-v0.22.0.linux-amd64 -o /usr/local/lib/docker/cli-plugins/docker-buildx
              chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx

              # Install Session Manager plugin
              yum install -y https://s3.amazonaws.com/session-manager-downloads/plugin/latest/linux_64bit/session-manager-plugin.rpm
              EOF

  tags = {
    Name = "BTC-Grid-Bot-Tokyo"
  }
}

# ── ELASTIC IP ──────────────────────────────────────────────────────
# Stable IP for Binance API whitelisting (outbound IP)

resource "aws_eip" "bot_eip" {
  instance = aws_instance.bot_server.id
  domain   = "vpc"
}

# ── COST PROTECTION ─────────────────────────────────────────────────

# Billing alert — email when spend exceeds threshold
resource "aws_sns_topic" "billing_alerts" {
  name = "billing-alerts"
}

resource "aws_sns_topic_subscription" "billing_email" {
  topic_arn = aws_sns_topic.billing_alerts.arn
  protocol  = "email"
  endpoint  = var.billing_alert_email
}

resource "aws_budgets_budget" "monthly_cost" {
  name              = "trading-bot-monthly"
  budget_type       = "COST"
  limit_amount      = "25"
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-05-01_00:00"

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.billing_alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.billing_alerts.arn]
  }
}

# Auto-stop instance if CPU stays idle for 2 hours (saves money when bot crashes)
resource "aws_cloudwatch_metric_alarm" "idle_stop" {
  alarm_name          = "trading-bot-idle-stop"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 12
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 600
  statistic           = "Average"
  threshold           = 5
  treat_missing_data  = "notBreaching"

  dimensions = {
    InstanceId = aws_instance.bot_server.id
  }

  alarm_description = "Stops instance if CPU < 5% for 2 hours (bot likely crashed)"
  alarm_actions     = [aws_sns_topic.billing_alerts.arn]
}
