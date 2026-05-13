# Aggregated ClusterRoles for Kubeflow profile-based RBAC
# Component ClusterRoles (notebooks, KServe, pipelines) carry aggregate-to-kubeflow-* labels;
# these aggregation ClusterRoles collect them so the profile controller's RoleBindings resolve.

resource "kubernetes_cluster_role" "kubeflow_admin" {
  metadata {
    name = "kubeflow-admin"
    labels = {
      "rbac.authorization.kubeflow.org/aggregate-to-kubeflow-admin" = "true"
    }
  }
  aggregation_rule {
    cluster_role_selectors {
      match_labels = {
        "rbac.authorization.kubeflow.org/aggregate-to-kubeflow-admin" = "true"
      }
    }
  }
}

resource "kubernetes_cluster_role" "kubeflow_edit" {
  metadata {
    name = "kubeflow-edit"
    labels = {
      "rbac.authorization.kubeflow.org/aggregate-to-kubeflow-edit" = "true"
    }
  }
  aggregation_rule {
    cluster_role_selectors {
      match_labels = {
        "rbac.authorization.kubeflow.org/aggregate-to-kubeflow-edit" = "true"
      }
    }
  }
}

resource "kubernetes_cluster_role" "kubeflow_view" {
  metadata {
    name = "kubeflow-view"
    labels = {
      "rbac.authorization.kubeflow.org/aggregate-to-kubeflow-view" = "true"
    }
  }
  aggregation_rule {
    cluster_role_selectors {
      match_labels = {
        "rbac.authorization.kubeflow.org/aggregate-to-kubeflow-view" = "true"
      }
    }
  }
}

# RBAC allowing the KFP pipeline-runner to manage deployments (used by deploy_component.py)

resource "kubernetes_cluster_role" "kfp_deploy" {
  metadata {
    name = "kfp-deployment-patcher"
  }
  rule {
    api_groups = ["apps"]
    resources  = ["deployments"]
    verbs      = ["get", "list", "create", "patch", "delete"]
  }
  rule {
    api_groups = [""]
    resources  = ["pods"]
    verbs      = ["get", "list", "watch"]
  }
}

resource "kubernetes_cluster_role_binding" "kfp_deploy" {
  metadata {
    name = "kfp-deployment-patcher"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.kfp_deploy.metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = "pipeline-runner"
    namespace = "kubeflow"
  }
}

# Allow pipeline-runner to manage KServe InferenceServices in the user namespace

resource "kubernetes_cluster_role" "kfp_inferenceservice" {
  metadata {
    name = "kfp-inferenceservice-manager"
  }
  rule {
    api_groups = ["serving.kserve.io"]
    resources  = ["inferenceservices"]
    verbs      = ["get", "list", "create", "update", "patch", "delete"]
  }
}

resource "kubernetes_role_binding" "kfp_inferenceservice_user" {
  metadata {
    name      = "kfp-inferenceservice-manager"
    namespace = "user"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.kfp_inferenceservice.metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = "pipeline-runner"
    namespace = "kubeflow"
  }

  depends_on = [null_resource.default_profile]
}
