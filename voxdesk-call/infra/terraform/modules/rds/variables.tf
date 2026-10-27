variable "name" {
  description = "Prefix for cluster, security group and parameter group names."
  type        = string
}

variable "vpc_id" {
  description = "VPC the cluster lives in."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the DB subnet group."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to reach PostgreSQL (normally the EKS cluster security group)."
  type        = list(string)
  default     = []
}

variable "vpc_cidr_block" {
  description = "VPC CIDR allowed to reach PostgreSQL. Empty disables the CIDR rule."
  type        = string
  default     = ""
}

variable "engine_version" {
  description = "Aurora PostgreSQL engine version."
  type        = string
  default     = "16.4"
}

variable "database_name" {
  description = "Initial database name."
  type        = string
  default     = "voxdesk"
}

variable "master_username" {
  description = "Master user. The password is generated and stored by RDS in Secrets Manager."
  type        = string
  default     = "voxdesk_admin"
}

variable "instance_class" {
  description = "Instance class for Aurora instances."
  type        = string
  default     = "db.r6g.large"
}

variable "instance_count" {
  description = "Aurora instances: 1 writer, the rest readers in other AZs."
  type        = number
  default     = 2

  validation {
    condition     = var.instance_count >= 1 && var.instance_count <= 15
    error_message = "Aurora allows between 1 and 15 instances."
  }
}

variable "backup_retention_days" {
  description = "Automated backup retention."
  type        = number
  default     = 14

  validation {
    condition     = var.backup_retention_days >= 1 && var.backup_retention_days <= 35
    error_message = "Aurora backup retention is 1..35 days."
  }
}

variable "deletion_protection" {
  description = "Block `terraform destroy` on the cluster. Always true outside dev."
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = "Skip the final snapshot on destroy. Only safe in dev."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
