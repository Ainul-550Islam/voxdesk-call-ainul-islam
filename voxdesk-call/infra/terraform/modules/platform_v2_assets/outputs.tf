output "role_arn" {
  description = "IAM role ARN for the API workload's EKS service account."
  value       = aws_iam_role.this.arn
}

output "service_account_name" {
  description = "Kubernetes service account name trusted by the OIDC role."
  value       = var.service_account_name
}

output "namespace" {
  description = "Kubernetes namespace trusted by the OIDC role."
  value       = var.kubernetes_namespace
}

output "bucket_name" {
  description = "Encrypted brand-assets bucket name."
  value       = var.bucket_name
}

output "bucket_region" {
  description = "Region that owns the encrypted brand-assets bucket."
  value       = var.bucket_region
}

output "kms_key_arn" {
  description = "KMS key ARN required by the platform-v2 asset client."
  value       = var.kms_key_arn
}
