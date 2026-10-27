locals {
  name = "${var.project}-${var.environment}"

  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Batch       = "07"
    Region      = "multi"
  }

  az_count = 3
}

# ---------------------------------------------------------------- primary ---
module "primary_network" {
  source = "../../modules/network"

  providers = {
    aws = aws.primary
  }

  name               = "${local.name}-p"
  cidr_block         = "10.40.0.0/16"
  az_count           = local.az_count
  single_nat_gateway = false

  tags = local.common_tags
}

module "primary_eks" {
  source = "../../modules/eks"

  providers = {
    aws = aws.primary
  }

  name                = "${local.name}-p"
  kubernetes_version  = var.kubernetes_version
  vpc_id              = module.primary_network.vpc_id
  private_subnet_ids  = module.primary_network.private_subnet_ids
  node_instance_types = ["m6i.2xlarge"]
  node_desired_size   = 6
  node_min_size       = 4
  node_max_size       = 24

  tags = local.common_tags
}

module "primary_rds" {
  source = "../../modules/rds"

  providers = {
    aws = aws.primary
  }

  name                       = "${local.name}-p"
  vpc_id                     = module.primary_network.vpc_id
  subnet_ids                 = module.primary_network.private_subnet_ids
  allowed_security_group_ids = [module.primary_eks.cluster_security_group_id]
  vpc_cidr_block             = module.primary_network.cidr_block

  instance_class        = "db.r6g.xlarge"
  instance_count        = 3
  backup_retention_days = 35
  deletion_protection   = true

  tags = local.common_tags
}

module "primary_redis" {
  source = "../../modules/redis"

  providers = {
    aws = aws.primary
  }

  name                       = "${local.name}-p"
  vpc_id                     = module.primary_network.vpc_id
  subnet_ids                 = module.primary_network.private_subnet_ids
  allowed_security_group_ids = [module.primary_eks.cluster_security_group_id]
  node_type                  = "cache.r7g.xlarge"
  replicas_per_shard         = 2
  auth_token                 = var.redis_auth_token

  tags = local.common_tags
}

module "primary_alb" {
  source = "../../modules/alb"

  providers = {
    aws = aws.primary
  }

  name                      = "${local.name}-p"
  vpc_id                    = module.primary_network.vpc_id
  public_subnet_ids         = module.primary_network.public_subnet_ids
  hosted_zone_name          = var.hosted_zone_name
  domain_name               = var.domain_name
  subject_alternative_names = ["*.${var.domain_name}"]
  create_dns_record         = false
  waf_rate_limit_per_5min   = 3000

  tags = local.common_tags
}

# -------------------------------------------------------------- secondary ---
# Same shape, smaller floor: the standby region only has to hold traffic until
# the primary comes back, so it runs a thinner node group and a smaller cache.
module "secondary_network" {
  source = "../../modules/network"

  providers = {
    aws = aws.secondary
  }

  name               = "${local.name}-s"
  cidr_block         = "10.50.0.0/16"
  az_count           = local.az_count
  single_nat_gateway = false

  tags = local.common_tags
}

module "secondary_eks" {
  source = "../../modules/eks"

  providers = {
    aws = aws.secondary
  }

  name                = "${local.name}-s"
  kubernetes_version  = var.kubernetes_version
  vpc_id              = module.secondary_network.vpc_id
  private_subnet_ids  = module.secondary_network.private_subnet_ids
  node_instance_types = ["m6i.xlarge"]
  node_desired_size   = 4
  node_min_size       = 3
  node_max_size       = 18

  tags = local.common_tags
}

module "secondary_rds" {
  source = "../../modules/rds"

  providers = {
    aws = aws.secondary
  }

  name                       = "${local.name}-s"
  vpc_id                     = module.secondary_network.vpc_id
  subnet_ids                 = module.secondary_network.private_subnet_ids
  allowed_security_group_ids = [module.secondary_eks.cluster_security_group_id]
  vpc_cidr_block             = module.secondary_network.cidr_block

  instance_class        = "db.r6g.large"
  instance_count        = 2
  backup_retention_days = 14
  deletion_protection   = true

  tags = local.common_tags
}

module "secondary_redis" {
  source = "../../modules/redis"

  providers = {
    aws = aws.secondary
  }

  name                       = "${local.name}-s"
  vpc_id                     = module.secondary_network.vpc_id
  subnet_ids                 = module.secondary_network.private_subnet_ids
  allowed_security_group_ids = [module.secondary_eks.cluster_security_group_id]
  node_type                  = "cache.r7g.large"
  replicas_per_shard         = 1
  auth_token                 = var.redis_auth_token

  tags = local.common_tags
}

module "secondary_alb" {
  source = "../../modules/alb"

  providers = {
    aws = aws.secondary
  }

  name                      = "${local.name}-s"
  vpc_id                    = module.secondary_network.vpc_id
  public_subnet_ids         = module.secondary_network.public_subnet_ids
  hosted_zone_name          = var.hosted_zone_name
  domain_name               = var.domain_name
  subject_alternative_names = ["*.${var.domain_name}"]
  create_dns_record         = false
  waf_rate_limit_per_5min   = 3000

  tags = local.common_tags
}

# ------------------------------------------------------------------ shared ---
module "failover" {
  source = "../../modules/failover"

  providers = {
    aws = aws.primary
  }

  name                    = local.name
  hosted_zone_name        = var.hosted_zone_name
  domain_name             = var.domain_name
  latency_records_enabled = var.latency_records_enabled

  primary = {
    region   = var.primary_region
    dns_name = module.primary_alb.dns_name
    zone_id  = module.primary_alb.zone_id
  }

  secondary = {
    region   = var.secondary_region
    dns_name = module.secondary_alb.dns_name
    zone_id  = module.secondary_alb.zone_id
  }

  tags = local.common_tags
}

module "assets" {
  source = "../../modules/assets"

  providers = {
    aws = aws.primary
  }

  name            = local.name
  bucket_name     = var.assets_bucket_name
  allowed_origins = ["https://${var.domain_name}"]

  tags = local.common_tags
}

module "observability_primary" {
  source = "../../modules/observability"

  providers = {
    aws = aws.primary
  }

  name                       = "${local.name}-p"
  alb_arn_suffix             = module.primary_alb.arn_suffix
  target_group_arn_suffix    = module.primary_alb.target_group_arn_suffix
  db_cluster_identifier      = module.primary_rds.cluster_identifier
  redis_replication_group_id = "${local.name}-p-redis"
  alarm_email_endpoints      = var.alarm_email_endpoints

  tags = local.common_tags
}

module "observability_secondary" {
  source = "../../modules/observability"

  providers = {
    aws = aws.secondary
  }

  name                       = "${local.name}-s"
  alb_arn_suffix             = module.secondary_alb.arn_suffix
  target_group_arn_suffix    = module.secondary_alb.target_group_arn_suffix
  db_cluster_identifier      = module.secondary_rds.cluster_identifier
  redis_replication_group_id = "${local.name}-s-redis"
  alarm_email_endpoints      = var.alarm_email_endpoints

  tags = local.common_tags
}
