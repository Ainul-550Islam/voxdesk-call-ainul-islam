output "region" {
  description = "Region this stack lives in."
  value       = var.region
}

output "cluster_name" {
  description = "EKS cluster name for `aws eks update-kubeconfig`."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint."
  value       = module.eks.cluster_endpoint
}

output "load_balancer_dns" {
  description = "ALB DNS name, for the Helm values override."
  value       = module.alb.dns_name
}

output "database_writer_endpoint" {
  description = "Aurora writer endpoint."
  value       = module.rds.writer_endpoint
  sensitive   = true
}

output "redis_primary_endpoint" {
  description = "Cache primary endpoint."
  value       = module.redis.primary_endpoint
  sensitive   = true
}

output "assets_bucket" {
  description = "Brand asset bucket."
  value       = module.assets.bucket_name
}

output "alerts_topic_arn" {
  description = "SNS topic for alarms."
  value       = module.observability.alerts_topic_arn
}
