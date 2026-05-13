module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  endpoint_public_access = true

  enable_cluster_creator_admin_permissions = true

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

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

  eks_managed_node_groups = {

    general = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = var.general_instance_types

      min_size     = var.general_min_size
      max_size     = var.general_max_size
      desired_size = var.general_desired_size

      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = 50
            volume_type           = "gp3"
            delete_on_termination = true
          }
        }
      }

      # Hop limit 2 allows pods to reach IMDS for IAM credentials
      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 2
      }

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

    ml-training = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = var.ml_instance_types

      min_size     = var.ml_min_size
      max_size     = var.ml_max_size
      desired_size = var.ml_desired_size

      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = 80
            volume_type           = "gp3"
            delete_on_termination = true
          }
        }
      }

      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 2
      }

      iam_role_additional_policies = {
        AmazonEC2ContainerRegistryReadOnly = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
        AmazonS3FullAccess                 = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
      }

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
        NodeGroup                                       = "ml-training"
        "k8s.io/cluster-autoscaler/enabled"             = "true"
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
