locals {
  tags = merge(var.tags, { "voxdesk:component" = "rds", "voxdesk:name" = var.name })
}

resource "aws_kms_key" "storage" {
  description             = "${var.name} Aurora storage encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = merge(local.tags, { Name = "${var.name}-rds" })
}

resource "aws_kms_alias" "storage" {
  name          = "alias/${var.name}-rds"
  target_key_id = aws_kms_key.storage.key_id
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-aurora"
  subnet_ids = var.subnet_ids

  tags = merge(local.tags, { Name = "${var.name}-aurora" })
}

resource "aws_security_group" "this" {
  name        = "${var.name}-aurora"
  description = "PostgreSQL access for ${var.name}"
  vpc_id      = var.vpc_id

  tags = merge(local.tags, { Name = "${var.name}-aurora" })
}

resource "aws_vpc_security_group_ingress_rule" "from_sg" {
  for_each = toset(var.allowed_security_group_ids)

  security_group_id            = aws_security_group.this.id
  referenced_security_group_id = each.value
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL from a workload security group"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "from_vpc" {
  count = var.vpc_cidr_block == "" ? 0 : 1

  security_group_id = aws_security_group.this.id
  cidr_ipv4         = var.vpc_cidr_block
  from_port         = 5432
  to_port           = 5432
  ip_protocol       = "tcp"
  description       = "PostgreSQL from inside the VPC"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"

  tags = local.tags
}

resource "aws_rds_cluster_parameter_group" "this" {
  name        = "${var.name}-aurora-pg16"
  family      = "aurora-postgresql16"
  description = "VoxDesk ${var.name} Aurora PostgreSQL parameters"

  parameter {
    name  = "log_min_duration_statement"
    value = "500"
  }

  parameter {
    name  = "log_statement"
    value = "ddl"
  }

  tags = local.tags
}

resource "aws_rds_cluster" "this" {
  cluster_identifier = "${var.name}-aurora"

  engine         = "aurora-postgresql"
  engine_version = var.engine_version
  engine_mode    = "provisioned"

  database_name   = var.database_name
  master_username = var.master_username

  # RDS generates the password and keeps it in Secrets Manager, so no password
  # ever passes through a plan file or CI variable.
  manage_master_user_password = true

  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.this.id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.this.name

  storage_encrypted = true
  kms_key_id        = aws_kms_key.storage.arn

  backup_retention_period      = var.backup_retention_days
  preferred_backup_window      = "18:00-19:00"
  preferred_maintenance_window = "sun:19:00-sun:20:00"

  copy_tags_to_snapshot           = true
  deletion_protection             = var.deletion_protection
  skip_final_snapshot             = var.skip_final_snapshot
  final_snapshot_identifier       = var.skip_final_snapshot ? null : "${var.name}-aurora-final"
  enabled_cloudwatch_logs_exports = ["postgresql"]

  apply_immediately = false

  tags = local.tags
}

resource "aws_rds_cluster_instance" "this" {
  count = var.instance_count

  identifier           = "${var.name}-aurora-${count.index}"
  cluster_identifier   = aws_rds_cluster.this.id
  instance_class       = var.instance_class
  engine               = aws_rds_cluster.this.engine
  engine_version       = aws_rds_cluster.this.engine_version
  db_subnet_group_name = aws_db_subnet_group.this.name

  # Instances must be spread across AZs; the cluster decides placement, but the
  # promotion tier is what makes failover deterministic.
  promotion_tier               = count.index
  performance_insights_enabled = true
  auto_minor_version_upgrade   = false

  tags = local.tags
}
