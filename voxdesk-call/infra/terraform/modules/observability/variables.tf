variable "name" {
  description = "Prefix for alarms and the dashboard."
  type        = string
}

variable "alb_arn_suffix" {
  description = "Load balancer ARN suffix (CloudWatch dimension)."
  type        = string
}

variable "target_group_arn_suffix" {
  description = "Target group ARN suffix (CloudWatch dimension)."
  type        = string
}

variable "db_cluster_identifier" {
  description = "Aurora cluster identifier for DB alarms. Empty disables them."
  type        = string
  default     = ""
}

variable "redis_replication_group_id" {
  description = "ElastiCache replication group id. Empty disables Redis alarms."
  type        = string
  default     = ""
}

variable "alarm_email_endpoints" {
  description = "Addresses subscribed to the alert topic."
  type        = list(string)
  default     = []
}

variable "p95_latency_seconds" {
  description = "Target p95 response time before the latency alarm fires."
  type        = number
  default     = 2.5
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
