output "dns_name" {
  description = "ALB DNS name, used by the failover health check."
  value       = aws_lb.this.dns_name
}

output "zone_id" {
  description = "ALB canonical hosted zone id."
  value       = aws_lb.this.zone_id
}

output "arn" {
  description = "ALB ARN."
  value       = aws_lb.this.arn
}

output "target_group_arn" {
  description = "Target group the service registers into."
  value       = aws_lb_target_group.api.arn
}

output "security_group_id" {
  description = "ALB security group, to allow node ingress only from the balancer."
  value       = aws_security_group.this.id
}

output "certificate_arn" {
  description = "Validated ACM certificate ARN."
  value       = aws_acm_certificate_validation.this.certificate_arn
}

output "hosted_zone_id" {
  description = "Route53 zone id for the deployment domain."
  value       = data.aws_route53_zone.this.zone_id
}

output "arn_suffix" {
  description = "ALB ARN suffix, for CloudWatch dimension values."
  value       = aws_lb.this.arn_suffix
}

output "target_group_arn_suffix" {
  description = "Target group ARN suffix, for CloudWatch dimension values."
  value       = aws_lb_target_group.api.arn_suffix
}
