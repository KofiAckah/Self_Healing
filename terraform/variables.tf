variable "aws_region" {
  description = "AWS region for the lab stack."
  type        = string
  default     = "eu-west-1"
}

variable "owner" {
  description = "Owner tag value (e.g. your name / email)."
  type        = string
  default     = "techstream-lab"
}

variable "instance_type" {
  description = "EC2 instance type. t3.small handles the 8-container stack; t2.micro will struggle."
  type        = string
  default     = "t3.small"
}

variable "my_ip_cidr" {
  description = "Your public IP in CIDR form (e.g. 203.0.113.4/32). Ingress is locked to this only."
  type        = string

  validation {
    condition     = can(cidrhost(var.my_ip_cidr, 0))
    error_message = "my_ip_cidr must be valid CIDR, e.g. 203.0.113.4/32."
  }
}

variable "key_name" {
  description = "Name of an existing EC2 key pair for SSH access."
  type        = string
}

variable "anthropic_ssm_parameter_name" {
  description = <<-EOT
    Name of the SSM SecureString parameter holding ANTHROPIC_API_KEY. The
    parameter is created out-of-band (CLI/console) so the secret never lands in
    Terraform state; this stack only grants the instance read access to it.
  EOT
  type        = string
  default     = "/techstream/anthropic_api_key"
}

variable "root_volume_gb" {
  description = "Root EBS volume size in GB."
  type        = number
  default     = 30
}
