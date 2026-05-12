resource "kubernetes_namespace" "mlflow" {
  metadata {
    name = "mlflow"
  }

  depends_on = [module.eks]
}

resource "kubernetes_persistent_volume_claim" "mlflow_sqlite" {
  metadata {
    name      = "mlflow-sqlite"
    namespace = kubernetes_namespace.mlflow.metadata[0].name
  }
  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = "gp3"
    resources {
      requests = { storage = "10Gi" }
    }
  }
  depends_on = [kubernetes_namespace.mlflow]
}

resource "helm_release" "mlflow" {
  name       = "mlflow"
  repository = "https://community-charts.github.io/helm-charts"
  chart      = "mlflow"
  version    = var.mlflow_chart_version
  namespace  = kubernetes_namespace.mlflow.metadata[0].name

  set {
    name  = "backendStore.defaultSqlitePath"
    value = "/mlflow/mlruns/mlflow.db"
  }

  set {
    name  = "artifactRoot.s3.enabled"
    value = "true"
  }
  set {
    name  = "artifactRoot.s3.bucket"
    value = aws_s3_bucket.mlflow.bucket
  }
  set {
    name  = "artifactRoot.s3.path"
    value = "artifacts"
  }
  set {
    name  = "serviceAccount.create"
    value = "true"
  }
  set {
    name  = "serviceAccount.name"
    value = "mlflow"
  }
  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.mlflow.arn
  }

  values = [
    yamlencode({
      # Disable log.enabled so chart stops injecting --gunicorn-opts, switching to uvicorn
      log = { enabled = false }
      extraEnvVars = {
        AWS_DEFAULT_REGION = var.aws_region
      }
      extraArgs = {
        "allowed-hosts" = "*"
      }
      extraVolumes = [{
        name = "mlflow-sqlite"
        persistentVolumeClaim = { claimName = "mlflow-sqlite" }
      }]
      extraVolumeMounts = [{
        name      = "mlflow-sqlite"
        mountPath = "/mlflow/mlruns"
      }]
    })
  ]

  set {
    name  = "service.type"
    value = "LoadBalancer"
  }
  set {
    name  = "service.port"
    value = "5000"
  }

  depends_on = [kubernetes_namespace.mlflow, aws_iam_role_policy_attachment.mlflow_s3, kubernetes_persistent_volume_claim.mlflow_sqlite]
}
