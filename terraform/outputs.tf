output "instance_id" {
  description = "EC2 instance ID."
  value       = aws_instance.techstream.id
}

output "public_ip" {
  description = "Public IP of the lab instance."
  value       = aws_instance.techstream.public_ip
}

output "ssh_command" {
  description = "Convenience SSH command."
  value       = "ssh -i <path-to-${var.key_name}.pem> ec2-user@${aws_instance.techstream.public_ip}"
}

output "urls" {
  description = "Monitoring endpoints (reachable from your IP only)."
  value = {
    app          = "http://${aws_instance.techstream.public_ip}:5000"
    prometheus   = "http://${aws_instance.techstream.public_ip}:9090"
    alertmanager = "http://${aws_instance.techstream.public_ip}:9093"
    grafana      = "http://${aws_instance.techstream.public_ip}:3000"
  }
}
