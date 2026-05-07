# ── MLflow on EKS ─────────────────────────────────────────────────────────────
# Uses the community Helm chart.
# Artifact store  → S3 bucket (via IRSA — no static credentials needed)
# Backend store   → SQLite on a PersistentVolume (simple, good enough for dev)

resource "kubernetes_namespace" "mlflow" {
  metadata {
    name = "mlflow"
  }

  depends_on = [module.eks]
}

resource "helm_release" "mlflow" {
  name       = "mlflow"
  repository = "https://community-charts.github.io/helm-charts"
  chart      = "mlflow"
  version    = var.mlflow_chart_version
  namespace  = kubernetes_namespace.mlflow.metadata[0].name

  # ── Backend store (SQLite on a PVC) ────────────────────────────────────────
  set {
    name  = "backendStore.defaultSqlitePath"
    value = "/mlflow/mlruns/mlflow.db"
  }

  # ── Artifact store (S3) ────────────────────────────────────────────────────
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
  # ── Service account with IRSA annotation (no static AWS keys needed) ───────
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
    })
  ]

  # ── Expose via LoadBalancer so you can reach the UI from your laptop ────────
  set {
    name  = "service.type"
    value = "LoadBalancer"
  }
  set {
    name  = "service.port"
    value = "5000"
  }

  depends_on = [kubernetes_namespace.mlflow, aws_iam_role_policy_attachment.mlflow_s3]
}
