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

# kubectl apply instead of kubernetes_manifest: avoids CRD validation at plan time
resource "null_resource" "fraud_detector_service_monitor" {
  triggers = {
    helm_release = helm_release.kube_prometheus_stack.metadata[0].revision
    namespace    = kubernetes_namespace.mlflow.metadata[0].name
  }

  provisioner "local-exec" {
    command = <<-EOF
      kubectl apply -f - <<YAML
      ---
      apiVersion: v1
      kind: Service
      metadata:
        name: fraud-detector-metrics
        namespace: user
        labels:
          serving.kserve.io/inferenceservice: fraud-detector
      spec:
        selector:
          serving.kserve.io/inferenceservice: fraud-detector
        ports:
          - name: metrics
            port: 8080
            targetPort: 8080
            protocol: TCP
      ---
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
            - user
        selector:
          matchLabels:
            serving.kserve.io/inferenceservice: fraud-detector
        endpoints:
          - port: metrics
            path: /metrics
            interval: 15s
      YAML
    EOF
  }

  depends_on = [helm_release.kube_prometheus_stack]
}

output "grafana_url" {
  description = "Grafana LoadBalancer URL (may take ~2 min to provision)"
  value       = "http://<run: kubectl get svc -n monitoring kube-prometheus-stack-grafana -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'>"
}
