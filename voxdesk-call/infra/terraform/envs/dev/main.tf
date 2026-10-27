locals {
  name = "${var.project}-${var.environment}"

  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Batch       = "07"
  }

  az_count = 2
}

module "network" {
  source = "../../modules/network"

  name               = local.name
  cidr_block         = "10.20.0.0/16"
  az_count           = local.az_count
  single_nat_gateway = true

  tags = local.common_tags
}

module "eks" {
  source = "../../modules/eks"

  name                = "${local.name}-cluster"
  kubernetes_version  = var.kubernetes_version
  vpc_id              = module.network.vpc_id
  private_subnet_ids  = module.network.private_subnet_ids
  node_instance_types = ["m6i.large"]
  node_desired_size   = 2
  node_min_size       = 2
  node_max_size       = 4
  node_disk_size_gb   = 50

  cluster_log_retention_days = 14

  tags = local.common_tags
}

module "rds" {
  source = "../../modules/rds"

  name                       = local.name
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.eks.cluster_security_group_id]
  vpc_cidr_block             = module.network.cidr_block

  instance_class        = "db.r6g.large"
  instance_count        = 1
  backup_retention_days = 1
  deletion_protection   = false
  skip_final_snapshot   = true

  tags = local.common_tags
}

module "redis" {
  source = "../../modules/redis"

  name                       = local.name
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.eks.cluster_security_group_id]
  node_type                  = "cache.t4g.medium"
  replicas_per_shard         = 0
  auth_token                 = var.redis_auth_token

  tags = local.common_tags
}

module "alb" {
  source = "../../modules/alb"

  name                    = local.name
  vpc_id                  = module.network.vpc_id
  public_subnet_ids       = module.network.public_subnet_ids
  hosted_zone_name        = var.hosted_zone_name
  domain_name             = var.domain_name
  waf_rate_limit_per_5min = 600

  tags = local.common_tags
}

module "assets" {
  source = "../../modules/assets"

  name            = local.name
  bucket_name     = var.assets_bucket_name
  allowed_origins = ["https://${var.domain_name}"]

  tags = local.common_tags
}

module "observability" {
  source = "../../modules/observability"

  name                       = local.name
  alb_arn_suffix             = module.alb.arn_suffix
  target_group_arn_suffix    = module.alb.target_group_arn_suffix
  db_cluster_identifier      = module.rds.cluster_identifier
  redis_replication_group_id = "${local.name}-redis"
  alarm_email_endpoints      = var.alarm_email_endpoints

  tags = local.common_tags
}
