output "bot_server_public_ip" {
  description = "The Elastic IP address of the bot server. WHITELIST THIS ON BINANCE."
  value       = aws_eip.bot_eip.public_ip
}

output "ssh_command" {
  description = "The command to connect to your server."
  value       = "ssh -i keys/${var.key_pair_name}.pem ec2-user@${aws_eip.bot_eip.public_ip}"
}
