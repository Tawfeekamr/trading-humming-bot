# AWS Tokyo Infrastructure as Code (IaC)

This directory contains the Terraform configuration to deploy your trading bot to AWS Tokyo (ap-northeast-1).

## 🚀 Deployment Instructions

1.  **Install Terraform**: [Download here](https://developer.hashicorp.com/terraform/downloads).
2.  **AWS CLI**: Ensure you have an AWS account and the [AWS CLI configured](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-configure.html) with credentials.
3.  **Initialize**:
    ```bash
    terraform init
    ```
4.  **Plan**:
    ```bash
    terraform plan
    ```
5.  **Apply**:
    ```bash
    terraform apply
    ```

## 🔒 Security Notes
- The server is configured with a **Security Group** that only allows SSH from your IP.
- An **Elastic IP** is created. **You must whitelist this IP in your Binance API settings.**
- Secrets (API Keys) are NOT managed by Terraform. Use a `.env` file on the server or AWS Secrets Manager.

## 🛠️ After Deployment
Once the server is up:
1.  Connect via SSH: `ssh -i keys/bot-key.pem ec2-user@<ELASTIC_IP>`
2.  The server comes pre-installed with **Docker** and **Docker Compose**.
3.  Clone your repo and run `docker-compose up -d`.
