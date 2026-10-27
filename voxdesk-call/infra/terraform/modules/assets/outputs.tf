output "bucket_name" {
  description = "Bucket that holds brand assets."
  value       = aws_s3_bucket.this.bucket
}

output "bucket_arn" {
  description = "Bucket ARN, for the upload role policy."
  value       = aws_s3_bucket.this.arn
}

output "kms_key_arn" {
  description = "KMS key used for object encryption."
  value       = aws_kms_key.this.arn
}
