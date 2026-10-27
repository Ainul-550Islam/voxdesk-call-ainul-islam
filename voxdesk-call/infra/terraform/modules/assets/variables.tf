variable "name" {
  description = "Prefix for the bucket and KMS key."
  type        = string
}

variable "bucket_name" {
  description = "Globally unique bucket name for tenant brand assets."
  type        = string
}

variable "allowed_origins" {
  description = "Origins allowed to PUT assets directly (browser uploads). Empty disables CORS."
  type        = list(string)
  default     = []
}

variable "expire_incomplete_uploads_days" {
  description = "Abort multipart uploads after this many days."
  type        = number
  default     = 3
}

variable "noncurrent_version_expiry_days" {
  description = "Delete superseded asset versions after this many days."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
