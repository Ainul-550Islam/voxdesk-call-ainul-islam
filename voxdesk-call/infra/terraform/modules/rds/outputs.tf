output "cluster_arn" {
  description = "Aurora cluster ARN."
  value       = aws_rds_cluster.this.arn
}

output "cluster_identifier" {
  description = "Aurora cluster identifier."
  value       = aws_rds_cluster.this.cluster_identifier
}

output "writer_endpoint" {
  description = "Writer endpoint (DNS resolves to the current writer after failover)."
  value       = aws_rds_cluster.this.endpoint
}

output "reader_endpoint" {
  description = "Reader endpoint for read-only traffic."
  value       = aws_rds_cluster.this.reader_endpoint
}

output "port" {
  description = "PostgreSQL port."
  value       = aws_rds_cluster.this.port
}

output "security_group_id" {
  description = "Security group attached to the cluster."
  value       = aws_security_group.this.id
}

output "master_user_secret_arn" {
  description = "ARN of the RDS-managed master credential in Secrets Manager."
  value       = aws_rds_cluster.this.master_user_secret[0].secret_arn
}
