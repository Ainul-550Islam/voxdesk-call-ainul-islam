variable "name" {
  description = "Cluster name prefix."
  type        = string
}

variable "kubernetes_version" {
  description = "EKS control-plane version. Pinned so a plan never silently upgrades a cluster."
  type        = string
  default     = "1.30"
}

variable "vpc_id" {
  description = "VPC the cluster lives in."
  type        = string
}

variable "private_subnet_ids" {
  description = "Subnets for nodes and control-plane ENIs."
  type        = list(string)
}

variable "node_instance_types" {
  description = "Node instance types. Voice workloads need dedicated CPU for the media path."
  type        = list(string)
  default     = ["m6i.xlarge"]
}

variable "node_desired_size" {
  description = "Desired node count."
  type        = number
  default     = 3
}

variable "node_min_size" {
  description = "Minimum node count."
  type        = number
  default     = 3
}

variable "node_max_size" {
  description = "Maximum node count."
  type        = number
  default     = 12
}

variable "node_disk_size_gb" {
  description = "Root volume size for nodes."
  type        = number
  default     = 100
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the public Kubernetes API endpoint. Empty means private-only."
  type        = list(string)
  default     = []
}

variable "cluster_log_retention_days" {
  description = "Retention for control-plane logs."
  type        = number
  default     = 90
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
