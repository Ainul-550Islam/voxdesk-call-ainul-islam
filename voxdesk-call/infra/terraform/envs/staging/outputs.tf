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

output "platform_assets_irsa" {
  description = "IRSA identity and storage location for the platform-v2 API deployment."
  value = {
    namespace       = module.platform_v2_assets.namespace
    service_account = module.platform_v2_assets.service_account_name
    role_arn        = module.platform_v2_assets.role_arn
    bucket_name     = module.platform_v2_assets.bucket_name
    bucket_region   = module.platform_v2_assets.bucket_region
    kms_key_arn     = module.platform_v2_assets.kms_key_arn
  }
}

output "platform_assets_helm_values" {
  description = "Values fragment for infra/helm/voxdesk to activate the IRSA-backed S3 asset store."
  value = {
    serviceAccount = {
      create = true
      name   = module.platform_v2_assets.service_account_name
      annotations = {
        "eks.amazonaws.com/role-arn" = module.platform_v2_assets.role_arn
      }
    }
    platform = {
      brandingAssetBucket   = module.platform_v2_assets.bucket_name
      brandingAssetRegion   = module.platform_v2_assets.bucket_region
      brandingAssetKmsKeyId = module.platform_v2_assets.kms_key_arn
    }
  }
}

output "alerts_topic_arn" {
  description = "SNS topic for alarms."
  value       = module.observability.alerts_topic_arn
}
