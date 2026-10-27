output "vpc_id" {
  description = "VPC the cluster and data stores live in."
  value       = aws_vpc.this.id
}

output "cidr_block" {
  description = "VPC CIDR, used to allow in-VPC traffic in security groups."
  value       = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  description = "Public subnets — load balancers only."
  value       = [for az in local.azs : aws_subnet.public[az].id]
}

output "private_subnet_ids" {
  description = "Private subnets — nodes, pods and data stores."
  value       = [for az in local.azs : aws_subnet.private[az].id]
}

output "azs" {
  description = "Availability zones in use."
  value       = local.azs
}
