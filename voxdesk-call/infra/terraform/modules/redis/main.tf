locals {
  tags = merge(var.tags, { "voxdesk:component" = "redis", "voxdesk:name" = var.name })
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name}-redis"
  subnet_ids = var.subnet_ids

  tags = merge(local.tags, { Name = "${var.name}-redis" })
}

resource "aws_security_group" "this" {
  name        = "${var.name}-redis"
  description = "Redis access for ${var.name}"
  vpc_id      = var.vpc_id

  tags = merge(local.tags, { Name = "${var.name}-redis" })
}

resource "aws_vpc_security_group_ingress_rule" "from_sg" {
  for_each = toset(var.allowed_security_group_ids)

  security_group_id            = aws_security_group.this.id
  referenced_security_group_id = each.value
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  description                  = "Redis from a workload security group"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"

  tags = local.tags
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.name}-redis"
  description          = "VoxDesk ${var.name} rate limits, tick locks and websocket fan-out"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.node_type
  port           = 6379

  num_cache_clusters         = var.replicas_per_shard + 1
  automatic_failover_enabled = var.replicas_per_shard > 0
  multi_az_enabled           = var.replicas_per_shard > 0

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.this.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = var.auth_token

  maintenance_window       = "sun:20:30-sun:21:30"
  snapshot_window          = "17:00-18:00"
  snapshot_retention_limit = 3

  apply_immediately = false

  tags = local.tags
}
