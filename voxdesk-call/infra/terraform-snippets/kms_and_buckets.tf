# Reference HCL for the Batch 03 key and bucket layout.
#
# This is a snippet, not a root module: it declares the resources an operator
# needs and the invariants that make them usable by the application, and it is
# meant to be copied into the deployment's own Terraform. Nothing here contains a
# secret, an account id, or a region literal — those are variables.
#
# The invariants worth stating out loud:
#
#   * one customer-managed key per region, rotation enabled, deletion window
#     long enough that an accidental destroy cannot become a data-loss event;
#   * one bucket per region, SSE-KMS with that region's key, Object Lock on for
#     audit archives (WORM evidence), and public access blocked on all four
#     switches — not just the ACL one;
#   * bucket policy denies any request whose TLS is not modern and any PutObject
#     that omits the KMS key, so an unencrypted write fails instead of landing;
#   * the application reads the key id (`KMS_KEY_ID`) and the region list
#     (`DATA_REGIONS`) from these outputs.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
}

variable "name_prefix" {
  description = "Prefix for every resource name, e.g. voxdesk-prod"
  type        = string
}

variable "regions" {
  description = "Regions this deployment stores data in. Must match DATA_REGIONS."
  type        = list(string)

  validation {
    condition     = length(var.regions) > 0 && length(distinct(var.regions)) == length(var.regions)
    error_message = "regions must be a non-empty list without duplicates."
  }
}

variable "audit_object_lock_days" {
  description = "Retention in days for the audit archive bucket. 0 disables Object Lock."
  type        = number
  default     = 365

  validation {
    condition     = var.audit_object_lock_days >= 0
    error_message = "audit_object_lock_days cannot be negative."
  }
}

variable "tags" {
  description = "Tags applied to every resource"
  type        = map(string)
  default     = {}
}

locals {
  # One key alias per region; the application pins a tenant to a region and uses
  # that region's key, so the alias name carries the region.
  key_aliases = { for region in var.regions : region => "${var.name_prefix}-compliance-${region}" }
}

resource "aws_kms_key" "compliance" {
  for_each = toset(var.regions)

  description              = "${var.name_prefix} compliance envelope key (${each.key})"
  key_usage                = "ENCRYPT_DECRYPT"
  customer_master_key_spec = "SYMMETRIC_DEFAULT"

  # Rotation is the difference between a key and a key you can keep using.
  enable_key_rotation = true

  # 30 days: long enough to notice and cancel an accidental destroy, short enough
  # that a decommissioned environment does not hold keys forever.
  deletion_window_in_days = 30

  tags = merge(var.tags, {
    Region  = each.key
    Purpose = "compliance-envelope"
  })
}

resource "aws_kms_alias" "compliance" {
  for_each = toset(var.regions)

  name          = "alias/${local.key_aliases[each.key]}"
  target_key_id = aws_kms_key.compliance[each.key].key_id
}

resource "aws_s3_bucket" "audit_archive" {
  for_each = toset(var.regions)

  bucket = "${var.name_prefix}-audit-${each.key}"

  # Object Lock can only be enabled at creation time. When it is on, a versioned
  # object cannot be overwritten or deleted before its retention date — which is
  # what makes an audit archive an archive rather than a folder.
  object_lock_enabled = var.audit_object_lock_days > 0

  tags = merge(var.tags, {
    Region  = each.key
    Purpose = "audit-archive"
  })
}

resource "aws_s3_bucket_versioning" "audit_archive" {
  for_each = toset(var.regions)

  bucket = aws_s3_bucket.audit_archive[each.key].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit_archive" {
  for_each = toset(var.regions)

  bucket = aws_s3_bucket.audit_archive[each.key].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.compliance[each.key].arn
    }
    # Reusing a data key for every object in a bucket is how a single leak
    # becomes a bucket-wide leak.
    bucket_key_enabled = false
  }
}

resource "aws_s3_bucket_public_access_block" "audit_archive" {
  for_each = toset(var.regions)

  bucket = aws_s3_bucket.audit_archive[each.key].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_object_lock_configuration" "audit_archive" {
  for_each = { for region in var.regions : region => region if var.audit_object_lock_days > 0 }

  bucket = aws_s3_bucket.audit_archive[each.key].id

  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.audit_object_lock_days
    }
  }
}

data "aws_iam_policy_document" "audit_archive" {
  for_each = toset(var.regions)

  statement {
    sid       = "DenyUnencryptedOrWrongKey"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.audit_archive[each.key].arn}/*"]

    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }

  statement {
    sid       = "DenyWrongKmsKey"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.audit_archive[each.key].arn}/*"]

    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [aws_kms_key.compliance[each.key].arn]
    }
  }

  statement {
    sid       = "DenyOutdatedTls"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.audit_archive[each.key].arn, "${aws_s3_bucket.audit_archive[each.key].arn}/*"]

    condition {
      test     = "NumericLessThan"
      variable = "s3:TlsVersion"
      values   = ["1.2"]
    }
  }
}

resource "aws_s3_bucket_policy" "audit_archive" {
  for_each = toset(var.regions)

  bucket = aws_s3_bucket.audit_archive[each.key].id
  policy = data.aws_iam_policy_document.audit_archive[each.key].json
}

output "kms_key_ids" {
  description = "Key id per region; feed the default region's value into KMS_KEY_ID"
  value       = { for region, key in aws_kms_key.compliance : region => key.key_id }
}

output "audit_bucket_names" {
  description = "Archive bucket per region; used as SIEM S3 sink destinations"
  value       = { for region, bucket in aws_s3_bucket.audit_archive : region => bucket.bucket }
}

output "regions" {
  description = "The region list to pass to DATA_REGIONS"
  value       = var.regions
}
