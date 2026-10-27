variable "name" {
  description = "Prefix for every resource name and the Name tag."
  type        = string
}

variable "cidr_block" {
  description = "VPC CIDR. /16 leaves room for 3 AZs at /20 each."
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to spread subnets across."
  type        = number
  default     = 3

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 6
    error_message = "az_count must be between 2 and 6: a single-AZ VPC cannot survive an AZ failure."
  }
}

variable "single_nat_gateway" {
  description = "One NAT gateway instead of one per AZ. Cheaper, but an AZ failure takes egress with it."
  type        = bool
  default     = false
}

variable "flow_log_retention_days" {
  description = "Retention for VPC flow logs."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Extra tags applied to every resource."
  type        = map(string)
  default     = {}
}
