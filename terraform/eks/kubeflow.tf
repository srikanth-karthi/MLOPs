# ── Kubeflow Pipelines (standalone) ──────────────────────────────────────────
# KFP has no official Helm chart, so we apply the upstream kustomize manifests
# via a local-exec provisioner. The null_resource re-runs only when kfp_version
# changes.

resource "null_resource" "kubeflow_pipelines" {
  triggers = {
    kfp_version = var.kfp_version
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region}
      kubectl apply -k "github.com/kubeflow/pipelines/manifests/kustomize/cluster-scoped-resources?ref=${var.kfp_version}"
      kubectl wait --for condition=established --timeout=60s crd/applications.app.k8s.io
      kubectl apply -k "github.com/kubeflow/pipelines/manifests/kustomize/env/platform-agnostic?ref=${var.kfp_version}"
    EOT
  }

  depends_on = [module.eks, kubernetes_storage_class.gp3]
}

resource "tls_private_key" "cache_server" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "cache_server" {
  private_key_pem = tls_private_key.cache_server.private_key_pem

  subject {
    common_name = "cache-server.kubeflow.svc"
  }

  dns_names             = ["cache-server.kubeflow.svc", "cache-server.kubeflow"]
  validity_period_hours = 87600 # 10 years

  allowed_uses = ["key_encipherment", "digital_signature", "server_auth"]
}

resource "kubernetes_secret" "cache_server_tls" {
  metadata {
    name      = "webhook-server-tls"
    namespace = "kubeflow"
  }

  type = "kubernetes.io/tls"

  data = {
    "tls.crt" = tls_self_signed_cert.cache_server.cert_pem
    "tls.key" = tls_private_key.cache_server.private_key_pem
  }

  depends_on = [null_resource.kubeflow_pipelines]
}

resource "null_resource" "compile_pipeline" {
  triggers = {
    pipeline_hash           = filesha256("${path.module}/../../5_pipeline/pipeline.py")
    train_hash              = filesha256("${path.module}/../../5_pipeline/components/train_component.py")
    evaluate_hash           = filesha256("${path.module}/../../5_pipeline/components/evaluate_component.py")
    register_hash           = filesha256("${path.module}/../../5_pipeline/components/register_component.py")
    deploy_hash             = filesha256("${path.module}/../../5_pipeline/components/deploy_component.py")
  }

  provisioner "local-exec" {
    working_dir = "${path.module}/../../5_pipeline"
    command     = "python pipeline.py"
  }
}

resource "kubernetes_service" "kubeflow_ui_lb" {
  metadata {
    name      = "ml-pipeline-ui-lb"
    namespace = "kubeflow"
  }

  spec {
    type = "LoadBalancer"

    selector = {
      app = "ml-pipeline-ui"
    }

    port {
      port        = 80
      target_port = 3000
    }
  }

  depends_on = [null_resource.kubeflow_pipelines]
}
