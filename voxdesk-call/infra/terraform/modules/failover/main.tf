locals {
  tags = merge(var.tags, { "voxdesk:component" = "failover", "voxdesk:name" = var.name })

  regions = {
    primary   = var.primary
    secondary = var.secondary
  }
}

data "aws_route53_zone" "this" {
  name         = var.hosted_zone_name
  private_zone = false
}

# One health check per region. It probes the region's own load balancer over
# HTTPS, so a region that is up but serving errors counts as down.
resource "aws_route53_health_check" "region" {
  for_each = local.regions

  fqdn              = each.value.dns_name
  port              = 443
  type              = "HTTPS"
  resource_path     = var.health_check_path
  failure_threshold = 3
  request_interval  = 30

  measure_latency = true

  tags = merge(local.tags, { Name = "${var.name}-${each.key}" })
}

resource "aws_route53_record" "failover" {
  for_each = local.regions

  zone_id = data.aws_route53_zone.this.zone_id
  name    = var.domain_name
  type    = "A"

  set_identifier = each.key

  failover_routing_policy {
    type = each.key == "primary" ? "PRIMARY" : "SECONDARY"
  }

  health_check_id = aws_route53_health_check.region[each.key].id

  alias {
    name                   = each.value.dns_name
    zone_id                = each.value.zone_id
    evaluate_target_health = true
  }
}

# Latency records are for the read hostname (dashboards, static assets), where a
# split between regions is harmless. The API hostname stays failover-based:
# a split-brain write path is worse than extra latency.
resource "aws_route53_record" "latency" {
  for_each = var.latency_records_enabled ? local.regions : {}

  zone_id = data.aws_route53_zone.this.zone_id
  name    = "api.${var.domain_name}"
  type    = "A"

  set_identifier = each.value.region

  latency_routing_policy {
    region = each.value.region
  }

  health_check_id = aws_route53_health_check.region[each.key].id

  alias {
    name                   = each.value.dns_name
    zone_id                = each.value.zone_id
    evaluate_target_health = true
  }
}
