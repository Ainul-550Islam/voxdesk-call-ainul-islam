output "primary_health_check_id" {
  description = "Health check gating the primary record."
  value       = aws_route53_health_check.region["primary"].id
}

output "secondary_health_check_id" {
  description = "Health check gating the secondary record."
  value       = aws_route53_health_check.region["secondary"].id
}

output "failover_fqdn" {
  description = "Hostname that fails over between the two regions."
  value       = var.domain_name
}
