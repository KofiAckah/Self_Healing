data "aws_caller_identity" "current" {}

# Latest Amazon Linux 2023 AMI (x86_64), resolved at plan time.
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

# Use the account's default VPC for this single-instance lab.
data "aws_vpc" "default" {
  default = true
}
