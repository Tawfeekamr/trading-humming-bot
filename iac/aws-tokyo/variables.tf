variable "key_pair_name" {
  description = "The name of the AWS Key Pair to use for SSH access."
  type        = string
  default     = "bot-key"
}

variable "my_ip" {
  description = "Your public IP address (CIDR notation, e.g. 203.0.113.5/32)"
  type        = string
  default     = "0.0.0.0/0"  # Will be overridden in production
}
