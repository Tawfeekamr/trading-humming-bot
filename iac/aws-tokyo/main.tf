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

# ── SECURITY ────────────────────────────────────────────────────────

resource "aws_security_group" "bot_sg" {
  name        = "trading-bot-sg"
  description = "Allow SSH and Dashboard access"
  vpc_id      = aws_vpc.trading_vpc.id

  # SSH Access (Restrict to your IP)
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  # Streamlit Dashboard Port
  ingress {
    from_port   = 8501
    to_port     = 8501
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  # Outbound to Exchange APIs
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── COMPUTE ─────────────────────────────────────────────────────────

# Get latest Amazon Linux 2023 AMI
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
  instance_type = "t3.medium"
  subnet_id     = aws_subnet.public_subnet.id
  key_name      = var.key_pair_name

  vpc_security_group_ids = [aws_security_group.bot_sg.id]

  root_block_device {
    volume_size = 20 # 20GB is plenty for logs/DB
    volume_type = "gp3"
  }

  # Bootstrap: Install Docker & Docker Compose
  user_data = <<-EOF
              #!/bin/bash
              yum update -y
              yum install -y docker
              systemctl start docker
              systemctl enable docker
              usermod -a -G docker ec2-user
              
              # Install Docker Compose
              curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
              chmod +x /usr/local/bin/docker-compose
              EOF

  tags = {
    Name = "BTC-Grid-Bot-Tokyo"
  }
}

# ── ELASTIC IP ──────────────────────────────────────────────────────
# Stable IP for Binance Whitelisting

resource "aws_eip" "bot_eip" {
  instance = aws_instance.bot_server.id
  domain   = "vpc"
}
