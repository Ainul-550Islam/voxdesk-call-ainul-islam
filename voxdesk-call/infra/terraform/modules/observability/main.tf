locals {
  tags = merge(var.tags, { "voxdesk:component" = "observability", "voxdesk:name" = var.name })

  load_balancer_dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }

  target_group_dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }
}

resource "aws_sns_topic" "alerts" {
  name = "${var.name}-alerts"

  tags = local.tags
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.alarm_email_endpoints)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_cloudwatch_metric_alarm" "target_5xx" {
  alarm_name          = "${var.name}-target-5xx"
  alarm_description   = "The application is returning 5xx to users."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = local.target_group_dimensions

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "elb_5xx" {
  alarm_name          = "${var.name}-elb-5xx"
  alarm_description   = "The load balancer itself is failing requests (no healthy target, TLS problem)."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_ELB_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = local.load_balancer_dimensions

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "p95_latency" {
  alarm_name          = "${var.name}-p95-latency"
  alarm_description   = "p95 response time is above ${var.p95_latency_seconds}s."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.p95_latency_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = local.target_group_dimensions

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  alarm_name          = "${var.name}-unhealthy-hosts"
  alarm_description   = "At least one target is failing its health check."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = local.target_group_dimensions

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "db_cpu" {
  count = var.db_cluster_identifier == "" ? 0 : 1

  alarm_name          = "${var.name}-db-cpu"
  alarm_description   = "Aurora CPU is saturated; the writer is the bottleneck."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBClusterIdentifier = var.db_cluster_identifier
  }

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "redis_evictions" {
  count = var.redis_replication_group_id == "" ? 0 : 1

  alarm_name          = "${var.name}-redis-evictions"
  alarm_description   = "Cache is evicting keys: tick locks and rate-limit counters are being lost."
  namespace           = "AWS/ElastiCache"
  metric_name         = "Evictions"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ReplicationGroupId = var.redis_replication_group_id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = local.tags
}

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = "${var.name}-platform"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Requests and errors"
          region  = "default"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_4XX_Count", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Response time"
          region = "default"
          view   = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, { stat = "p50" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, { stat = "p95" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, { stat = "p99" }],
          ]
        }
      },
    ]
  })
}
