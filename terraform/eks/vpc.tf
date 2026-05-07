# ── VPC ──────────────────────────────────────────────────────────────────────
# Creates a dedicated VPC with public and private subnets across 3 AZs.
# Worker nodes live in private subnets; load balancers use public subnets.
# Tags required by the AWS Load Balancer Controller are applied to subnets.

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.cluster_name}-vpc"
  cidr = var.vpc_cidr

  azs             = var.availability_zones
  private_subnets = var.private_subnet_cidrs
  public_subnets  = var.public_subnet_cidrs

  # NAT Gateway allows private subnet nodes to reach the internet (ECR, S3, etc.)
  enable_nat_gateway     = true
  single_nat_gateway     = true   # set to false in production for HA
  enable_dns_hostnames   = true
  enable_dns_support     = true

  # Tags required by EKS so the cluster can discover subnets for load balancers
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}
