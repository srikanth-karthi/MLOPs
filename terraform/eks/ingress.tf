
# cert-manager (required by KServe webhooks)

resource "null_resource" "cert_manager" {
  triggers = {
    version = var.cert_manager_version
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile}
      kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/${var.cert_manager_version}/cert-manager.yaml
      kubectl wait --for=condition=available --timeout=300s deployment/cert-manager           -n cert-manager
      kubectl wait --for=condition=available --timeout=300s deployment/cert-manager-cainjector -n cert-manager
      kubectl wait --for=condition=available --timeout=300s deployment/cert-manager-webhook    -n cert-manager
    EOT
  }

  depends_on = [module.eks]
}

# KServe (installed into kubeflow namespace via kubeflow/manifests)

resource "null_resource" "kserve" {
  triggers = {
    notebooks_version = var.notebooks_version
    install_pass      = "4"
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile}

      # First pass: installs CRDs + controller (--force-conflicts overrides old client-side-apply ownership)
      kubectl apply --server-side --force-conflicts -k "github.com/kubeflow/manifests/applications/kserve/kserve?ref=${var.notebooks_version}" || true

      kubectl wait --for=condition=established --timeout=120s crd/inferenceservices.serving.kserve.io
      kubectl wait --for=condition=established --timeout=120s crd/clusterservingruntimes.serving.kserve.io

      # Second pass: creates ClusterServingRuntime instances now that CRDs are established
      kubectl apply --server-side --force-conflicts -k "github.com/kubeflow/manifests/applications/kserve/kserve?ref=${var.notebooks_version}"

      kubectl wait --for=condition=available --timeout=600s deployment/kserve-controller-manager -n kubeflow
    EOT
  }

  depends_on = [null_resource.cert_manager, null_resource.kubeflow_pipelines]
}

# KServe Models Web App

resource "null_resource" "kserve_models_web_app" {
  triggers = {
    notebooks_version = var.notebooks_version
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile}
      kubectl create namespace kserve --dry-run=client -o yaml | kubectl apply -f -
      kubectl apply --server-side -k "${path.module}/kustomize/kserve-models-web-app"
      kubectl wait --for=condition=available --timeout=300s deployment/kserve-models-web-app -n kserve
    EOT
  }

  depends_on = [null_resource.kserve]
}

# ExternalName service so the kubeflow-namespace Ingress can route to kserve namespace
resource "kubernetes_service" "kserve_models_proxy" {
  metadata {
    name      = "kserve-models-web-app-proxy"
    namespace = "kubeflow"
  }

  spec {
    type          = "ExternalName"
    external_name = "kserve-models-web-app.kserve.svc.cluster.local"
    port {
      port        = 80
      target_port = 5000
    }
  }

  depends_on = [null_resource.kserve_models_web_app]
}

# NGINX Ingress Controller  — single LoadBalancer for all Kubeflow services

resource "helm_release" "nginx_ingress" {
  name             = "ingress-nginx"
  repository       = "https://kubernetes.github.io/ingress-nginx"
  chart            = "ingress-nginx"
  namespace        = "ingress-nginx"
  create_namespace = true
  version          = "4.11.3"
  timeout          = 300

  set {
    name  = "controller.service.type"
    value = "LoadBalancer"
  }

  # Required to allow sub_filter snippets in Ingress annotations
  set {
    name  = "controller.allowSnippetAnnotations"
    value = "true"
  }

  depends_on = [module.eks]
}

# Ingress — services that need prefix stripping (/pipeline/, /jupyter/, etc.)

resource "kubernetes_ingress_v1" "kubeflow_services" {
  metadata {
    name      = "kubeflow-services"
    namespace = "kubeflow"
    annotations = {
      "kubernetes.io/ingress.class"                       = "nginx"
      "nginx.ingress.kubernetes.io/use-regex"             = "true"
      "nginx.ingress.kubernetes.io/rewrite-target"        = "/$2"
      "nginx.ingress.kubernetes.io/proxy-read-timeout"    = "3600"
      "nginx.ingress.kubernetes.io/proxy-send-timeout"    = "3600"
      "nginx.ingress.kubernetes.io/configuration-snippet" = "proxy_set_header kubeflow-userid \"user@example.com\";"
    }
  }

  spec {
    rule {
      http {
        path {
          path      = "/pipeline(/|$)(.*)"
          path_type = "ImplementationSpecific"
          backend {
            service {
              name = "ml-pipeline-ui"
              port { number = 80 }
            }
          }
        }

        path {
          path      = "/jupyter(/|$)(.*)"
          path_type = "ImplementationSpecific"
          backend {
            service {
              name = "jupyter-web-app-service"
              port { number = 80 }
            }
          }
        }
      }
    }
  }

  depends_on = [helm_release.nginx_ingress, null_resource.kubeflow_dashboard]
}

# Ingress — KServe Models Web App (in kserve namespace, direct service reference)

resource "kubernetes_ingress_v1" "kserve_models" {
  metadata {
    name      = "kserve-models"
    namespace = "kserve"
    annotations = {
      "kubernetes.io/ingress.class"                       = "nginx"
      "nginx.ingress.kubernetes.io/use-regex"             = "true"
      "nginx.ingress.kubernetes.io/rewrite-target"        = "/$2"
      "nginx.ingress.kubernetes.io/proxy-read-timeout"    = "3600"
      "nginx.ingress.kubernetes.io/proxy-send-timeout"    = "3600"
      "nginx.ingress.kubernetes.io/configuration-snippet" = "proxy_set_header kubeflow-userid \"user@example.com\";"
    }
  }

  spec {
    rule {
      http {
        path {
          path      = "/kserve-endpoints(/|$)(.*)"
          path_type = "ImplementationSpecific"
          backend {
            service {
              name = "kserve-models-web-app"
              port { number = 80 }
            }
          }
        }
      }
    }
  }

  depends_on = [helm_release.nginx_ingress, null_resource.kserve_models_web_app, kubernetes_ingress_v1.kubeflow_services]
}

# Ingress — Notebook pods (no rewrite; proxy extracts namespace+name from path)

resource "kubernetes_ingress_v1" "kubeflow_notebook_proxy" {
  metadata {
    name      = "kubeflow-notebook-proxy"
    namespace = "kubeflow"
    annotations = {
      "kubernetes.io/ingress.class"                       = "nginx"
      "nginx.ingress.kubernetes.io/use-regex"             = "true"
      "nginx.ingress.kubernetes.io/proxy-read-timeout"    = "3600"
      "nginx.ingress.kubernetes.io/proxy-send-timeout"    = "3600"
      "nginx.ingress.kubernetes.io/proxy-buffering"       = "off"
      "nginx.ingress.kubernetes.io/configuration-snippet" = "proxy_set_header kubeflow-userid \"user@example.com\";"
    }
  }

  spec {
    rule {
      http {
        path {
          path      = "/notebook/.+"
          path_type = "ImplementationSpecific"
          backend {
            service {
              name = "notebook-proxy"
              port { number = 80 }
            }
          }
        }
      }
    }
  }

  depends_on = [helm_release.nginx_ingress, null_resource.notebook_proxy]
}

# Ingress — Central Dashboard at root (no rewrite)

resource "kubernetes_ingress_v1" "kubeflow_dashboard" {
  metadata {
    name      = "kubeflow-dashboard"
    namespace = "kubeflow"
    annotations = {
      "kubernetes.io/ingress.class"                       = "nginx"
      "nginx.ingress.kubernetes.io/proxy-read-timeout"    = "3600"
      "nginx.ingress.kubernetes.io/configuration-snippet" = "proxy_set_header kubeflow-userid \"user@example.com\";"
    }
  }

  spec {
    rule {
      http {
        path {
          path      = "/"
          path_type = "Prefix"
          backend {
            service {
              name = "centraldashboard"
              port { number = 80 }
            }
          }
        }
      }
    }
  }

  depends_on = [helm_release.nginx_ingress, null_resource.kubeflow_dashboard]
}

# Ingress — MLMD gRPC-Web (KFP UI calls this from the browser to fetch artifact lineage)
# Without this rule NGINX returns 404 and the UI shows "Cannot get MLMD objects from Metadata store"

resource "kubernetes_ingress_v1" "kubeflow_mlmd" {
  metadata {
    name      = "kubeflow-mlmd"
    namespace = "kubeflow"
    annotations = {
      "kubernetes.io/ingress.class"                    = "nginx"
      "nginx.ingress.kubernetes.io/backend-protocol"   = "GRPC"
      "nginx.ingress.kubernetes.io/proxy-read-timeout" = "3600"
      "nginx.ingress.kubernetes.io/proxy-send-timeout" = "3600"
      "nginx.ingress.kubernetes.io/configuration-snippet" = "proxy_set_header kubeflow-userid \"user@example.com\";"
    }
  }

  spec {
    rule {
      http {
        path {
          path      = "/ml_metadata.MetadataStoreService/"
          path_type = "Prefix"
          backend {
            service {
              name = "metadata-envoy-service"
              port { number = 9090 }
            }
          }
        }
      }
    }
  }

  depends_on = [helm_release.nginx_ingress, null_resource.kubeflow_pipelines]
}

# KServe InferenceService for fraud-detector (replaces custom deployment)

resource "null_resource" "fraud_detector_inference_service" {
  triggers = {
    kserve_version = var.notebooks_version
    image          = "srikanthkarthi/mlops-serving:latest"
    namespace      = "user"
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile}
      kubectl wait --for=condition=established --timeout=120s crd/inferenceservices.serving.kserve.io
      kubectl apply -f - <<EOF
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: fraud-detector
  namespace: user
  annotations:
    serving.kserve.io/deploymentMode: RawDeployment
spec:
  predictor:
    serviceAccountName: default-editor
    minReplicas: 1
    maxReplicas: 10
    scaleTarget: 60
    scaleMetric: cpu
    containers:
    - name: kserve-container
      image: srikanthkarthi/mlops-serving:latest
      ports:
      - containerPort: 8080
        protocol: TCP
      env:
      - name: MLFLOW_TRACKING_URI
        value: "http://mlflow.mlflow.svc.cluster.local:5000"
      - name: AWS_DEFAULT_REGION
        value: "${var.aws_region}"
      - name: MODEL_NAME
        value: "fraud-detector"
      - name: MODEL_ALIAS
        value: "production"
      resources:
        requests:
          cpu: "500m"
          memory: "512Mi"
        limits:
          cpu: "1"
          memory: "1Gi"
      readinessProbe:
        httpGet:
          path: /health
          port: 8080
        initialDelaySeconds: 30
        periodSeconds: 5
EOF
    EOT
  }

  depends_on = [null_resource.kserve, helm_release.mlflow]
}
