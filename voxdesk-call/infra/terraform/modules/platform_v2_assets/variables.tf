variable "name" {
  description = "IAM role name for the platform-v2 workload identity."
  type        = string

  validation {
    condition     = length(var.name) >= 1 && length(var.name) <= 64
    error_message = "name must fit the AWS IAM role-name limit of 64 characters."
  }
}

variable "oidc_provider_arn" {
  description = "EKS cluster's IAM OIDC provider ARN."
  type        = string
}

variable "oidc_issuer_url" {
  description = "EKS cluster's OIDC issuer URL, including https://."
  type        = string
}

variable "kubernetes_namespace" {
  description = "Namespace of the API service account receiving the role."
  type        = string
  default     = "voxdesk"
}

variable "service_account_name" {
  description = "Kubernetes service account annotated with the role ARN."
  type        = string
  default     = "voxdesk-platform"
}

variable "bucket_name" {
  description = "Encrypted brand-assets bucket name."
  type        = string
}

variable "bucket_arn" {
  description = "Encrypted brand-assets bucket ARN."
  type        = string
}

variable "bucket_region" {
  description = "AWS region that owns the S3 bucket and KMS key."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key ARN used by the brand-assets bucket."
  type        = string
}

variable "tags" {
  description = "Extra tags for the workload IAM role."
  type        = map(string)
  default     = {}
}
