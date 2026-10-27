variable "primary_region" {
  description = "Region that serves traffic while it is healthy."
  type        = string
  default     = "ap-south-1"
}

variable "secondary_region" {
  description = "Standby region that takes over when the primary fails its health check."
  type        = string
  default     = "ap-southeast-1"
}

variable "project" {
  description = "Project slug used in resource names."
  type        = string
  default     = "voxdesk"
}

variable "environment" {
  description = "Environment slug."
  type        = string
  default     = "prod"
}

variable "domain_name" {
  description = "Hostname users will reach, e.g. app.voxdesk.example.com."
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
  description = "AUTH token for the cache. Injected by CI from the secret store."
  type        = string
  sensitive   = true
}

variable "alarm_email_endpoints" {
  description = "Where alarms are mailed."
  type        = list(string)
  default     = []
}

variable "assets_bucket_name" {
  description = "Globally unique bucket name for brand assets (primary region only)."
  type        = string
}

variable "latency_records_enabled" {
  description = "Publish latency records for api.<domain> in addition to the failover record."
  type        = bool
  default     = true
}
