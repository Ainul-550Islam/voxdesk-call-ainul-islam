variable "region" {
  description = "AWS region for the dev stack."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Project slug used in resource names."
  type        = string
  default     = "voxdesk"
}

variable "environment" {
  description = "Environment slug."
  type        = string
  default     = "dev"
}

variable "domain_name" {
  description = "Hostname users will reach, e.g. dev.voxdesk.example.com."
  type        = string
}

variable "hosted_zone_name" {
  description = "Route53 zone that owns domain_name."
  type        = string
}

variable "kubernetes_version" {
  description = "EKS version."
  type        = string
  default     = "1.30"
}

variable "redis_auth_token" {
  description = "AUTH token for the cache. Injected by CI from a secret store."
  type        = string
  sensitive   = true
}

variable "alarm_email_endpoints" {
  description = "Where alarms are mailed."
  type        = list(string)
  default     = []
}

variable "assets_bucket_name" {
  description = "Globally unique bucket name for brand assets."
  type        = string
}
