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

output "assets_bucket" {
  description = "Primary-region encrypted brand-asset bucket."
  value       = module.assets.bucket_name
}

output "primary_platform_assets_irsa" {
  description = "Primary-region IRSA identity and shared brand-asset storage location."
  value = {
    namespace       = module.platform_v2_assets_primary.namespace
    service_account = module.platform_v2_assets_primary.service_account_name
    role_arn        = module.platform_v2_assets_primary.role_arn
    bucket_name     = module.platform_v2_assets_primary.bucket_name
    bucket_region   = module.platform_v2_assets_primary.bucket_region
    kms_key_arn     = module.platform_v2_assets_primary.kms_key_arn
  }
}

output "secondary_platform_assets_irsa" {
  description = "Secondary-region IRSA identity; it uses the same primary-region asset bucket."
  value = {
    namespace       = module.platform_v2_assets_secondary.namespace
    service_account = module.platform_v2_assets_secondary.service_account_name
    role_arn        = module.platform_v2_assets_secondary.role_arn
    bucket_name     = module.platform_v2_assets_secondary.bucket_name
    bucket_region   = module.platform_v2_assets_secondary.bucket_region
    kms_key_arn     = module.platform_v2_assets_secondary.kms_key_arn
  }
}

output "primary_platform_assets_helm_values" {
  description = "Helm values fragment for the primary EKS API deployment."
  value = {
    serviceAccount = {
      create = true
      name   = module.platform_v2_assets_primary.service_account_name
      annotations = {
        "eks.amazonaws.com/role-arn" = module.platform_v2_assets_primary.role_arn
      }
    }
    platform = {
      brandingAssetBucket   = module.platform_v2_assets_primary.bucket_name
      brandingAssetRegion   = module.platform_v2_assets_primary.bucket_region
      brandingAssetKmsKeyId = module.platform_v2_assets_primary.kms_key_arn
    }
  }
}

output "secondary_platform_assets_helm_values" {
  description = "Helm values fragment for the secondary EKS API deployment."
  value = {
    serviceAccount = {
      create = true
      name   = module.platform_v2_assets_secondary.service_account_name
      annotations = {
        "eks.amazonaws.com/role-arn" = module.platform_v2_assets_secondary.role_arn
      }
    }
    platform = {
      brandingAssetBucket   = module.platform_v2_assets_secondary.bucket_name
      brandingAssetRegion   = module.platform_v2_assets_secondary.bucket_region
      brandingAssetKmsKeyId = module.platform_v2_assets_secondary.kms_key_arn
    }
  }
}
