
# Kubeflow Notebooks (notebook-controller + jupyter-web-app)

resource "null_resource" "kubeflow_notebooks" {
  triggers = {
    notebooks_version = var.notebooks_version
    install_pass      = "2"
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile}
      kubectl apply -k "github.com/kubeflow/manifests/applications/jupyter/notebook-controller/upstream/overlays/standalone?ref=${var.notebooks_version}"
      kubectl apply -k "github.com/kubeflow/manifests/applications/jupyter/jupyter-web-app/upstream/base?ref=${var.notebooks_version}"
      kubectl wait --for=condition=available --timeout=300s deployment/notebook-controller-deployment -n notebook-controller-system
      kubectl wait --for=condition=available --timeout=300s deployment/jupyter-web-app-deployment -n kubeflow
      kubectl apply -k "github.com/kubeflow/manifests/applications/admission-webhook/upstream/base?ref=${var.notebooks_version}"
      kubectl wait --for=condition=available --timeout=180s deployment/admission-webhook-deployment -n kubeflow
      kubectl set env deployment/jupyter-web-app-deployment -n kubeflow APP_SECURE_COOKIES=false
      kubectl rollout status deployment/jupyter-web-app-deployment -n kubeflow --timeout=120s
    EOT
  }

  depends_on = [null_resource.kubeflow_pipelines]
}

resource "kubernetes_service" "jupyter_web_app_lb" {
  metadata {
    name      = "jupyter-web-app-lb"
    namespace = "kubeflow"
  }

  spec {
    type = "LoadBalancer"

    selector = {
      app                   = "jupyter-web-app"
      "kustomize.component" = "jupyter-web-app"
    }

    port {
      port        = 80
      target_port = 5000
    }
  }

  depends_on = [null_resource.kubeflow_notebooks]
}

# Notebook proxy — routes /notebook/{namespace}/{name}/... to the right pod service
# Required because Kubeflow uses Istio VirtualServices for this in a standard install;
# without Istio we deploy a tiny nginx that extracts namespace+name from the URL and proxies.

resource "null_resource" "notebook_proxy" {
  triggers = {
    version = "3"
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile}
      kubectl apply -f - <<'EOF'
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: notebook-proxy-config
  namespace: kubeflow
data:
  nginx.conf: |
    events {}
    http {
      resolver kube-dns.kube-system.svc.cluster.local valid=5s ipv6=off;
      server {
        listen 8080;
        location ~ ^/notebook/([^/]+)/([^/]+) {
          set $ns  $1;
          set $svc $2;
          proxy_pass         http://$svc.$ns.svc.cluster.local:80;
          proxy_set_header   Host $host;
          proxy_read_timeout 3600s;
          proxy_send_timeout 3600s;
          proxy_http_version 1.1;
          proxy_set_header   Upgrade    $http_upgrade;
          proxy_set_header   Connection "upgrade";
          proxy_buffering    off;
        }
      }
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: notebook-proxy
  namespace: kubeflow
spec:
  replicas: 1
  selector:
    matchLabels:
      app: notebook-proxy
  template:
    metadata:
      labels:
        app: notebook-proxy
    spec:
      containers:
      - name: nginx
        image: nginx:alpine
        ports:
        - containerPort: 8080
        volumeMounts:
        - name: config
          mountPath: /etc/nginx/nginx.conf
          subPath: nginx.conf
      volumes:
      - name: config
        configMap:
          name: notebook-proxy-config
---
apiVersion: v1
kind: Service
metadata:
  name: notebook-proxy
  namespace: kubeflow
spec:
  selector:
    app: notebook-proxy
  ports:
  - port: 80
    targetPort: 8080
EOF
      kubectl wait --for=condition=available --timeout=120s deployment/notebook-proxy -n kubeflow
    EOT
  }

  depends_on = [null_resource.kubeflow_notebooks]
}

# JupyterHub

resource "kubernetes_namespace" "jupyterhub" {
  metadata {
    name = "jhub"
    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  depends_on = [module.eks]
}

resource "helm_release" "jupyterhub" {
  name       = "jupyterhub"
  repository = "https://hub.jupyter.org/helm-chart/"
  chart      = "jupyterhub"
  version    = var.jupyterhub_chart_version
  namespace  = kubernetes_namespace.jupyterhub.metadata[0].name

  timeout = 600

  set {
    name  = "proxy.service.type"
    value = "LoadBalancer"
  }

  set {
    name  = "hub.config.Authenticator.admin_users"
    value = "{admin}"
  }

  set {
    name  = "hub.config.JupyterHub.authenticator_class"
    value = "nativeauthenticator.NativeAuthenticator"
  }

  set {
    name  = "hub.config.NativeAuthenticator.create_system_users"
    value = "true"
  }

  values = [
    <<-YAML
    singleuser:
      image:
        name: jupyter/scipy-notebook
        tag: latest
      lifecycleHooks:
        postStart:
          exec:
            command:
              - "sh"
              - "-c"
              - |
                pip install --pre "kubeflow-kale[jupyter]" "kfp[kubernetes]>=2.16.0" --quiet
    YAML
  ]

  depends_on = [module.eks, kubernetes_namespace.jupyterhub]
}

# TLS cert for admission-webhook (service.kubeflow.svc)

resource "tls_private_key" "admission_webhook" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "admission_webhook" {
  private_key_pem = tls_private_key.admission_webhook.private_key_pem

  subject {
    common_name = "service.kubeflow.svc"
  }

  dns_names = [
    "service",
    "service.kubeflow",
    "service.kubeflow.svc",
    "service.kubeflow.svc.cluster.local",
  ]

  validity_period_hours = 87600

  allowed_uses = ["key_encipherment", "digital_signature", "server_auth"]
}

resource "kubernetes_secret" "admission_webhook_certs" {
  metadata {
    name      = "webhook-certs"
    namespace = "kubeflow"
  }

  type = "Opaque"

  data = {
    "cert.pem" = tls_self_signed_cert.admission_webhook.cert_pem
    "key.pem"  = tls_private_key.admission_webhook.private_key_pem
  }

  depends_on = [null_resource.kubeflow_notebooks]
}

# Patch the mutating webhook caBundle with the self-signed CA cert

resource "null_resource" "admission_webhook_cabundle" {
  triggers = {
    cert_pem   = tls_self_signed_cert.admission_webhook.cert_pem
    install_pass = "2"
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile}
      CA_BUNDLE=$(echo -n '${base64encode(tls_self_signed_cert.admission_webhook.cert_pem)}')
      kubectl patch mutatingwebhookconfiguration mutating-webhook-configuration \
        --type='json' \
        -p="[{\"op\":\"replace\",\"path\":\"/webhooks/0/clientConfig/caBundle\",\"value\":\"$CA_BUNDLE\"}]" 2>/dev/null || true
      kubectl rollout restart deployment/deployment -n kubeflow
      kubectl wait --for=condition=available --timeout=120s deployment/deployment -n kubeflow
    EOT
  }

  depends_on = [kubernetes_secret.admission_webhook_certs]
}

# Kubeflow Central Dashboard

resource "null_resource" "kubeflow_dashboard" {
  triggers = {
    notebooks_version = var.notebooks_version
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile}
      kubectl apply -k "github.com/kubeflow/manifests/applications/centraldashboard/upstream/base?ref=${var.notebooks_version}"
      kubectl wait --for=condition=available --timeout=300s deployment/centraldashboard -n kubeflow
    EOT
  }

  depends_on = [null_resource.kubeflow_notebooks]
}

# Kubeflow Profiles Controller

resource "null_resource" "kubeflow_profiles" {
  triggers = {
    notebooks_version = var.notebooks_version
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile}
      kubectl apply -k "${path.module}/kustomize/profiles"
      kubectl wait --for=condition=available --timeout=300s deployment/profiles-deployment -n kubeflow
    EOT
  }

  depends_on = [null_resource.kubeflow_dashboard]
}

# Patch centraldashboard ConfigMap with relative NGINX paths + KServe Endpoints link

resource "kubernetes_config_map_v1_data" "centraldashboard_links" {
  metadata {
    name      = "centraldashboard-config"
    namespace = "kubeflow"
  }

  force = true

  data = {
    settings = jsonencode({
      DASHBOARD_FORCE_IFRAME = true
    })
    links = jsonencode({
      menuLinks = [
        {
          type = "item"
          link = "/pipeline/#/pipelines"
          text = "Pipelines"
          icon = "device:data-usage"
        },
        {
          type = "item"
          link = "/jupyter/"
          text = "Notebooks"
          icon = "book"
        },
        {
          type = "item"
          link = "/kserve-endpoints/"
          text = "Models"
          icon = "kubeflow:models"
        }
      ]
      externalLinks      = []
      quickLinks         = []
      documentationItems = []
    })
  }

  depends_on = [null_resource.kubeflow_dashboard]
}

# Default user profile — matches the kubeflow-userid header injected by NGINX
resource "null_resource" "default_profile" {
  triggers = {
    user_email = "user@example.com"
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile}
      kubectl apply -f - <<EOF
apiVersion: kubeflow.org/v1
kind: Profile
metadata:
  name: user
spec:
  owner:
    kind: User
    name: user@example.com
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: user-kubernetes-edit
  namespace: user
subjects:
- kind: User
  name: user@example.com
  apiGroup: rbac.authorization.k8s.io
roleRef:
  kind: ClusterRole
  name: edit
  apiGroup: rbac.authorization.k8s.io
EOF
    EOT
  }

  depends_on = [null_resource.kubeflow_profiles]
}
