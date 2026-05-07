resource "kubernetes_job" "fraud_detection_training" {
  metadata {
    name      = "fraud-detection-training"
    namespace = kubernetes_namespace.mlflow.metadata[0].name
  }

  wait_for_completion = false

  spec {
    backoff_limit = 2

    template {
      metadata {}

      spec {
        restart_policy       = "Never"
        service_account_name = "mlflow"

        init_container {
          name    = "download-data"
          image   = "amazon/aws-cli:latest"
          command = ["aws", "s3", "cp", "s3://${aws_s3_bucket.mlflow.bucket}/data/creditcard.csv", "/data/creditcard.csv"]

          env {
            name  = "AWS_DEFAULT_REGION"
            value = var.aws_region
          }

          volume_mount {
            name       = "data"
            mount_path = "/data"
          }
        }

        container {
          name  = "training"
          image = "srikanthkarthi/mlops-training:latest"
          args  = ["--model", "rf", "--n_estimators", "100", "--data_path", "/data/creditcard.csv"]

          env {
            name  = "MLFLOW_TRACKING_URI"
            value = "http://mlflow.${kubernetes_namespace.mlflow.metadata[0].name}.svc.cluster.local:5000"
          }

          env {
            name  = "AWS_DEFAULT_REGION"
            value = var.aws_region
          }

          resources {
            requests = {
              cpu    = "1"
              memory = "2Gi"
            }
            limits = {
              cpu    = "2"
              memory = "4Gi"
            }
          }

          volume_mount {
            name       = "data"
            mount_path = "/data"
          }
        }

        volume {
          name = "data"
          empty_dir {}
        }
      }
    }
  }

  depends_on = [helm_release.mlflow]
}
