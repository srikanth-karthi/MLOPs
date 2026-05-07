# ── EKS Cluster ──────────────────────────────────────────────────────────────
# Uses the official terraform-aws-modules/eks/aws module (v21).
# Two managed node groups:
#   - general   : system workloads, serving pods, Kubeflow infrastructure
#   - ml-training : batch training Jobs (scales to 0 when idle)

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  # API server reachable from the public internet.
  # Set to false and use a VPN/bastion in production.
  endpoint_public_access = true

  # Grants the IAM identity running Terraform admin access to the cluster
  # so kubectl works immediately after apply without extra aws-auth config.
  enable_cluster_creator_admin_permissions = true

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # ── EKS Managed Add-ons ──────────────────────────────────────────────────
  # before_compute = true means the add-on is installed before node groups,
  # which is required for vpc-cni so nodes get IP addresses on first boot.
  addons = {
    coredns = {
      most_recent = true
    }
    kube-proxy = {
      most_recent = true
    }
    vpc-cni = {
      most_recent    = true
      before_compute = true
    }
    eks-pod-identity-agent = {
      most_recent    = true
      before_compute = true
    }
  }

  # ── Managed Node Groups ───────────────────────────────────────────────────
  eks_managed_node_groups = {

    # General-purpose nodes: runs system pods, MLflow, MinIO, Kubeflow UI,
    # and model serving Deployments.
    general = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = var.general_instance_types

      min_size     = var.general_min_size
      max_size     = var.general_max_size
      desired_size = var.general_desired_size

      # Hop limit 2 allows pods to reach IMDS for IAM credentials
      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 2
      }

      # Attach policies needed to pull images from ECR and write logs
      iam_role_additional_policies = {
        AmazonEC2ContainerRegistryReadOnly = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
        CloudWatchAgentServerPolicy        = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
        AmazonS3FullAccess                 = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
      }

      labels = {
        role = "general"
      }

      tags = {
        NodeGroup = "general"
      }
    }

    # ML training nodes: runs Kubernetes Jobs triggered by Kubeflow pipelines.
    # Scales down to 0 when no training is running (controlled by Cluster Autoscaler).
    ml-training = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = var.ml_instance_types

      min_size     = var.ml_min_size
      max_size     = var.ml_max_size
      desired_size = var.ml_desired_size

      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 2
      }

      iam_role_additional_policies = {
        AmazonEC2ContainerRegistryReadOnly = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
        AmazonS3FullAccess                 = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
      }

      # Taint so only training Jobs are scheduled here.
      # Add toleration { key = "workload", value = "ml-training", effect = "NoSchedule" }
      # to training Job pod specs.
      taints = {
        ml-training = {
          key    = "workload"
          value  = "ml-training"
          effect = "NO_SCHEDULE"
        }
      }

      labels = {
        role = "ml-training"
      }

      tags = {
        NodeGroup                                      = "ml-training"
        "k8s.io/cluster-autoscaler/enabled"           = "true"
        "k8s.io/cluster-autoscaler/${var.cluster_name}" = "owned"
      }
    }
  }
}

resource "null_resource" "scale_general_nodegroup" {
  triggers = {
    desired_size = var.general_desired_size
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-nodegroup-config \
        --cluster-name ${var.cluster_name} \
        --nodegroup-name $(aws eks list-nodegroups --cluster-name ${var.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile} --query 'nodegroups[?contains(@, `general`)] | [0]' --output text) \
        --scaling-config desiredSize=${var.general_desired_size} \
        --region ${var.aws_region} \
        --profile ${var.aws_profile}
    EOT
  }

  depends_on = [module.eks]
}
