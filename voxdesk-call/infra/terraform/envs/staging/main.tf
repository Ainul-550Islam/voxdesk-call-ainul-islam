locals {
  name = "${var.project}-${var.environment}"

  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Batch       = "07"
  }

  az_count = 3
}

module "network" {
  source = "../../modules/network"

  name               = local.name
  cidr_block         = "10.30.0.0/16"
  az_count           = local.az_count
  single_nat_gateway = false

  tags = local.common_tags
}

module "eks" {
  source = "../../modules/eks"

  name                = "${local.name}-cluster"
  kubernetes_version  = var.kubernetes_version
  vpc_id              = module.network.vpc_id
  private_subnet_ids  = module.network.private_subnet_ids
  node_instance_types = ["m6i.xlarge"]
  node_desired_size   = 3
  node_min_size       = 3
  node_max_size       = 9

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
  instance_count        = 2
  backup_retention_days = 7
  deletion_protection   = true

  tags = local.common_tags
}

module "redis" {
  source = "../../modules/redis"

  name                       = local.name
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.eks.cluster_security_group_id]
  node_type                  = "cache.r7g.large"
  replicas_per_shard         = 1
  auth_token                 = var.redis_auth_token

  tags = local.common_tags
}

module "alb" {
  source = "../../modules/alb"

  name                      = local.name
  vpc_id                    = module.network.vpc_id
  public_subnet_ids         = module.network.public_subnet_ids
  hosted_zone_name          = var.hosted_zone_name
  domain_name               = var.domain_name
  subject_alternative_names = ["*.${var.domain_name}"]
  waf_rate_limit_per_5min   = 2000

  tags = local.common_tags
}

module "assets" {
  source = "../../modules/assets"

  name            = local.name
  bucket_name     = var.assets_bucket_name
  allowed_origins = ["https://${var.domain_name}"]

  tags = local.common_tags
}

module "platform_v2_assets" {
  source = "../../modules/platform_v2_assets"

  name                 = "${local.name}-platform-assets"
  oidc_provider_arn    = module.eks.oidc_provider_arn
  oidc_issuer_url      = module.eks.oidc_issuer_url
  kubernetes_namespace = var.kubernetes_namespace
  service_account_name = var.platform_service_account_name
  bucket_name          = module.assets.bucket_name
  bucket_arn           = module.assets.bucket_arn
  bucket_region        = var.region
  kms_key_arn          = module.assets.kms_key_arn

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
