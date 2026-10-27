variable "name" {
  description = "Prefix for the load balancer and associated resources."
  type        = string
}

variable "vpc_id" {
  description = "VPC the load balancer lives in."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets for the ALB (needs at least two AZs)."
  type        = list(string)
}

variable "hosted_zone_name" {
  description = "Route53 zone that owns `domain_name`, e.g. voxdesk.example.com."
  type        = string
}

variable "domain_name" {
  description = "Primary hostname served by this deployment, e.g. app.voxdesk.example.com."
  type        = string
}

variable "subject_alternative_names" {
  description = "Extra hostnames on the certificate, e.g. tenant custom domains that Caddy does not terminate."
  type        = list(string)
  default     = []
}

variable "health_check_path" {
  description = "Path the target group probes."
  type        = string
  default     = "/health"
}

variable "target_port" {
  description = "Port the service listens on inside the cluster."
  type        = number
  default     = 8000
}

variable "create_dns_record" {
  description = "Create the single A record. Set false in multi-region deployments, where the failover module owns DNS."
  type        = bool
  default     = true
}

variable "waf_rate_limit_per_5min" {
  description = "Requests from one IP per 5 minutes before WAF blocks. Protects the auth surface."
  type        = number
  default     = 3000
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
