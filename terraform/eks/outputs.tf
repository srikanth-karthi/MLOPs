output "cluster_name" {
  description = "Name of the EKS cluster"
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "API server endpoint URL"
  value       = module.eks.cluster_endpoint
}

output "cluster_version" {
  description = "Kubernetes version running on the cluster"
  value       = module.eks.cluster_version
}

output "cluster_certificate_authority_data" {
  description = "Base64-encoded certificate authority data for the cluster"
  value       = module.eks.cluster_certificate_authority_data
  sensitive   = true
}

output "cluster_iam_role_arn" {
  description = "IAM role ARN used by the EKS control plane"
  value       = module.eks.cluster_iam_role_arn
}

output "node_security_group_id" {
  description = "Security group ID attached to all worker nodes"
  value       = module.eks.node_security_group_id
}

output "vpc_id" {
  description = "ID of the VPC created for the cluster"
  value       = module.vpc.vpc_id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets (worker nodes)"
  value       = module.vpc.private_subnets
}

output "public_subnet_ids" {
  description = "IDs of the public subnets (load balancers)"
  value       = module.vpc.public_subnets
}

output "configure_kubectl" {
  description = "Run this command to configure kubectl after apply"
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

output "mlflow_s3_bucket" {
  description = "S3 bucket used by MLflow for artifact storage"
  value       = aws_s3_bucket.mlflow.bucket
}

output "mlflow_iam_role_arn" {
  description = "IAM role ARN assumed by MLflow pods via IRSA"
  value       = aws_iam_role.mlflow.arn
}

output "mlflow_url" {
  description = "Get the MLflow UI URL after apply (LoadBalancer takes ~2 min to provision)"
  value       = "kubectl get svc -n mlflow mlflow -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'"
}
