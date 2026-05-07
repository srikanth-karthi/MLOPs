# ── Prometheus + Grafana (kube-prometheus-stack) ──────────────────────────────

resource "helm_release" "kube_prometheus_stack" {
  name             = "kube-prometheus-stack"
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  namespace        = "monitoring"
  create_namespace = true
  version          = "70.4.2"
  timeout          = 600

  values = [<<-EOT
    grafana:
      service:
        type: LoadBalancer
      adminPassword: "mlops-admin"
      sidecar:
        dashboards:
          enabled: true
          label: grafana_dashboard
          labelValue: "1"
          folder: /var/lib/grafana/dashboards/fraud-detector
          searchNamespace: monitoring

    prometheus:
      prometheusSpec:
        serviceMonitorSelectorNilUsesHelmValues: false
        serviceMonitorNamespaceSelector: {}
        serviceMonitorSelector: {}
        retention: 15d

    alertmanager:
      enabled: false

    nodeExporter:
      enabled: true

    kubeStateMetrics:
      enabled: true
  EOT
  ]
}

# ── Grafana dashboard ConfigMap ───────────────────────────────────────────────

resource "kubernetes_config_map" "fraud_detector_dashboard" {
  metadata {
    name      = "fraud-detector-dashboard"
    namespace = "monitoring"
    labels = {
      grafana_dashboard = "1"
    }
  }

  data = {
    "fraud-detector.json" = file("${path.module}/grafana-dashboard.json")
  }
}

# ── ServiceMonitor — scrape /metrics from fraud-detector ─────────────────────
# kubernetes_manifest validates CRDs at plan time (before Helm installs them),
# so we apply the ServiceMonitor via kubectl after the Helm release is ready.

resource "null_resource" "fraud_detector_service_monitor" {
  triggers = {
    helm_release = helm_release.kube_prometheus_stack.metadata[0].revision
    namespace    = kubernetes_namespace.mlflow.metadata[0].name
  }

  provisioner "local-exec" {
    command = <<-EOF
      kubectl apply -f - <<YAML
      apiVersion: monitoring.coreos.com/v1
      kind: ServiceMonitor
      metadata:
        name: fraud-detector
        namespace: monitoring
        labels:
          release: kube-prometheus-stack
      spec:
        namespaceSelector:
          matchNames:
            - ${kubernetes_namespace.mlflow.metadata[0].name}
        selector:
          matchLabels:
            app: fraud-detector
        endpoints:
          - port: http
            path: /metrics
            interval: 15s
      YAML
    EOF
  }

  depends_on = [helm_release.kube_prometheus_stack]
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "grafana_url" {
  description = "Grafana LoadBalancer URL (may take ~2 min to provision)"
  value       = "http://<run: kubectl get svc -n monitoring kube-prometheus-stack-grafana -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'>"
}
