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

resource "kubernetes_deployment" "fraud_detector" {
  metadata {
    name      = "fraud-detector"
    namespace = kubernetes_namespace.mlflow.metadata[0].name
  }

  spec {
    replicas = 1

    selector {
      match_labels = { app = "fraud-detector" }
    }

    strategy {
      type = "RollingUpdate"
      rolling_update {
        max_surge       = 1
        max_unavailable = 0
      }
    }

    template {
      metadata {
        labels = { app = "fraud-detector" }
      }

      spec {
        service_account_name = "mlflow"

        container {
          name  = "serving"
          image = "srikanthkarthi/mlops-serving:latest"

          port { container_port = 8080 }

          env {
            name  = "MLFLOW_TRACKING_URI"
            value = "http://mlflow.${kubernetes_namespace.mlflow.metadata[0].name}.svc.cluster.local:5000"
          }
          env {
            name  = "AWS_DEFAULT_REGION"
            value = var.aws_region
          }
          env {
            name  = "MODEL_NAME"
            value = "fraud-detector"
          }
          env {
            name  = "MODEL_ALIAS"
            value = "production"
          }

          resources {
            requests = {
              cpu    = "500m"
              memory = "512Mi"
            }
            limits = {
              cpu    = "1"
              memory = "1Gi"
            }
          }

          readiness_probe {
            http_get {
              path = "/health"
              port = 8080
            }
            initial_delay_seconds = 30
            period_seconds        = 5
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 8080
            }
            initial_delay_seconds = 60
            period_seconds        = 15
          }
        }
      }
    }
  }

  depends_on = [helm_release.mlflow]
}

resource "kubernetes_service" "fraud_detector" {
  metadata {
    name      = "fraud-detector"
    namespace = kubernetes_namespace.mlflow.metadata[0].name
    labels    = { app = "fraud-detector" }
  }

  spec {
    type     = "LoadBalancer"
    selector = { app = "fraud-detector" }

    port {
      name        = "http"
      port        = 80
      target_port = 8080
    }
  }

  depends_on = [kubernetes_deployment.fraud_detector]
}

resource "kubernetes_horizontal_pod_autoscaler_v2" "fraud_detector" {
  metadata {
    name      = "fraud-detector"
    namespace = kubernetes_namespace.mlflow.metadata[0].name
  }

  spec {
    scale_target_ref {
      api_version = "apps/v1"
      kind        = "Deployment"
      name        = kubernetes_deployment.fraud_detector.metadata[0].name
    }

    min_replicas = 1
    max_replicas = 10

    metric {
      type = "Resource"
      resource {
        name = "cpu"
        target {
          type                = "Utilization"
          average_utilization = 60
        }
      }
    }
  }

  depends_on = [kubernetes_deployment.fraud_detector]
}
