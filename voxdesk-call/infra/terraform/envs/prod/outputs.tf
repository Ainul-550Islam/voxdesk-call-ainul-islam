output "failover_fqdn" {
  description = "Hostname that fails over between regions."
  value       = module.failover.failover_fqdn
}

output "primary_cluster_name" {
  description = "Primary EKS cluster name."
  value       = module.primary_eks.cluster_name
}

output "secondary_cluster_name" {
  description = "Secondary EKS cluster name."
  value       = module.secondary_eks.cluster_name
}

output "primary_load_balancer_dns" {
  description = "Primary ALB DNS name."
  value       = module.primary_alb.dns_name
}

output "secondary_load_balancer_dns" {
  description = "Secondary ALB DNS name."
  value       = module.secondary_alb.dns_name
}

output "primary_database_writer_endpoint" {
  description = "Primary Aurora writer endpoint."
  value       = module.primary_rds.writer_endpoint
  sensitive   = true
}

output "secondary_database_writer_endpoint" {
  description = "Secondary Aurora writer endpoint."
  value       = module.secondary_rds.writer_endpoint
  sensitive   = true
}

output "health_check_ids" {
  description = "Route53 health checks gating each region."
  value = {
    primary   = module.failover.primary_health_check_id
    secondary = module.failover.secondary_health_check_id
  }
}
