variable "aws_region" {
  description = "AWS region to deploy the EKS cluster"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use for authentication"
  type        = string
  default     = "default"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
  default     = "mlops-cluster"
}

variable "kubernetes_version" {
  description = "Kubernetes version for the EKS cluster"
  type        = string
  default     = "1.33"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of availability zones to use"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (worker nodes)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (load balancers)"
  type        = list(string)
  default     = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]
}

# ── Node groups ──────────────────────────────────────────────────────────────

variable "general_instance_types" {
  description = "EC2 instance types for the general-purpose node group"
  type        = list(string)
  default     = ["m5.xlarge"]
}

variable "general_min_size" {
  description = "Minimum number of nodes in the general node group"
  type        = number
  default     = 2
}

variable "general_max_size" {
  description = "Maximum number of nodes in the general node group"
  type        = number
  default     = 6
}

variable "general_desired_size" {
  description = "Desired number of nodes in the general node group"
  type        = number
  default     = 2
}

variable "ml_instance_types" {
  description = "EC2 instance types for the ML training node group (memory/compute optimised)"
  type        = list(string)
  default     = ["m5.2xlarge"]
}

variable "ml_min_size" {
  description = "Minimum number of nodes in the ML training node group"
  type        = number
  default     = 0
}

variable "ml_max_size" {
  description = "Maximum number of nodes in the ML training node group"
  type        = number
  default     = 5
}

variable "ml_desired_size" {
  description = "Desired number of nodes in the ML training node group"
  type        = number
  default     = 1
}

variable "mlflow_chart_version" {
  description = "Version of the community-charts/mlflow Helm chart"
  type        = string
  default     = "0.7.19"
}

variable "kfp_version" {
  description = "Kubeflow Pipelines standalone version (git tag)"
  type        = string
  default     = "2.14.4"
}
