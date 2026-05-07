output "bot_server_public_ip" {
  description = "The Elastic IP address of the bot server. WHITELIST THIS ON BINANCE."
  value       = aws_eip.bot_eip.public_ip
}

output "instance_id" {
  description = "The EC2 instance ID — use with Session Manager."
  value       = aws_instance.bot_server.id
}

output "ssm_connect_command" {
  description = "Connect to the server via Session Manager."
  value       = "aws ssm start-session --target ${aws_instance.bot_server.id}"
}

output "dashboard_port_forward_command" {
  description = "Access the Streamlit dashboard via port forwarding."
  value       = "aws ssm start-session --target ${aws_instance.bot_server.id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"8502\"]}'"
}
