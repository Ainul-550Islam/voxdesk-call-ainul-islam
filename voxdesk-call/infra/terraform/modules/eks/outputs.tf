output "cluster_name" {
  description = "EKS cluster name, used by the deployment pipeline."
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint."
  value       = aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority_data" {
  description = "CA bundle for kubeconfig."
  value       = aws_eks_cluster.this.certificate_authority[0].data
}

output "cluster_security_group_id" {
  description = "Security group shared by nodes and control plane."
  value       = aws_security_group.cluster.id
}

output "node_role_arn" {
  description = "Node instance role."
  value       = aws_iam_role.node.arn
}

output "oidc_provider_arn" {
  description = "IRSA provider ARN, for per-service-account roles (ALB controller, external-dns)."
  value       = aws_iam_openid_connect_provider.this.arn
}

output "oidc_issuer_url" {
  description = "Issuer URL, used as the IRSA condition value."
  value       = aws_eks_cluster.this.identity[0].oidc[0].issuer
}
