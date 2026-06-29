resource "aws_instance" "techstream" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  key_name               = var.key_name
  vpc_security_group_ids = [aws_security_group.techstream.id]
  iam_instance_profile   = aws_iam_instance_profile.techstream.name

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {})

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  # Require IMDSv2 (token-based metadata) — defends the instance role creds
  # against SSRF-style metadata theft.
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  tags = {
    Name = "techstream-self-healing"
  }
}
