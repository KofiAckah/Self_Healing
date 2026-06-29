# Security group: all ingress is locked to your IP only. The monitoring UIs are
# not exposed to the world.
resource "aws_security_group" "techstream" {
  name        = "techstream-self-healing"
  description = "TechStream self-healing lab — ingress restricted to a single IP"
  vpc_id      = data.aws_vpc.default.id

  tags = {
    Name = "techstream-self-healing"
  }
}

locals {
  # port -> human description, for the per-port ingress rules.
  ingress_ports = {
    22   = "SSH"
    3000 = "Grafana"
    5000 = "TechStream app"
    9090 = "Prometheus"
    9093 = "AlertManager"
  }
}

resource "aws_vpc_security_group_ingress_rule" "from_my_ip" {
  for_each = local.ingress_ports

  security_group_id = aws_security_group.techstream.id
  description       = each.value
  cidr_ipv4         = var.my_ip_cidr
  from_port         = each.key
  to_port           = each.key
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "all_outbound" {
  security_group_id = aws_security_group.techstream.id
  description       = "Allow all outbound (pull images, reach SSM, Claude API)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
