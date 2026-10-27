variable "name" {
  description = "Prefix for the replication group and security group."
  type        = string
}

variable "vpc_id" {
  description = "VPC the cache lives in."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the cache subnet group."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to reach the cache."
  type        = list(string)
}

variable "node_type" {
  description = "Cache node type."
  type        = string
  default     = "cache.r7g.large"
}

variable "replicas_per_shard" {
  description = "Replicas per shard: 1 gives automatic failover within the region."
  type        = number
  default     = 1
}

variable "auth_token" {
  description = "AUTH token for transit encryption. Supply from a secret store; never commit a value."
  type        = string
  sensitive   = true
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
