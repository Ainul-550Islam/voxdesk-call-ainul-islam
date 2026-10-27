variable "name" {
  description = "Prefix for health checks and records."
  type        = string
}

variable "hosted_zone_name" {
  description = "Route53 zone owning `domain_name`."
  type        = string
}

variable "domain_name" {
  description = "Hostname users resolve, e.g. app.voxdesk.example.com."
  type        = string
}

variable "health_check_path" {
  description = "Path probed on each region's load balancer."
  type        = string
  default     = "/health"
}

variable "primary" {
  description = "Primary region endpoint: region, dns_name, zone_id."
  type = object({
    region   = string
    dns_name = string
    zone_id  = string
  })
}

variable "secondary" {
  description = "Secondary region endpoint: region, dns_name, zone_id."
  type = object({
    region   = string
    dns_name = string
    zone_id  = string
  })
}

variable "failover_ttl" {
  description = "TTL on the failover records. 60s is the shortest honest value: resolvers cache longer."
  type        = number
  default     = 60
}

variable "latency_records_enabled" {
  description = "Also publish latency records for api.<domain> so clients prefer the nearer region."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
