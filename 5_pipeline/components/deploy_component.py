from kfp import dsl


@dsl.component(
    base_image="srikanthkarthi/mlops-pipeline-base:latest",
    packages_to_install=["kubernetes==32.0.1"],
)
def deploy(
    model_name: str,
    model_version: str,
    mlflow_tracking_uri: str,
    deployment_name: str = "fraud-detector",
    namespace: str = "user",
    canary_wait_seconds: int = 120,
) -> str:
    import json
    import os
    import time
    from datetime import datetime, timezone

    import mlflow
    from mlflow import MlflowClient
    from mlflow.exceptions import MlflowException
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException

    os.environ["GIT_PYTHON_REFRESH"]   = "quiet"
    os.environ["MLFLOW_LOGGING_LEVEL"] = "WARNING"
    mlflow.set_tracking_uri(mlflow_tracking_uri)
    mlflow_client = MlflowClient()

    def _get_nginx_lb_hostname(v1):
        """Read the real NGINX LB hostname from the ingress-nginx namespace."""
        svcs = v1.list_namespaced_service("ingress-nginx")
        for svc in svcs.items:
            ingress_list = (svc.status.load_balancer.ingress or []) if svc.status.load_balancer else []
            if ingress_list:
                return ingress_list[0].hostname or ingress_list[0].ip
        return None

    def _ensure_kserve_ingress_config(v1, apps_v1, lb_hostname):
        """
        Ensure inferenceservice-config has the correct ingress settings for
        path-based NGINX routing (no Istio, no example.com domain).

        Patches the configmap and restarts kserve-controller-manager only if
        something changed. Idempotent — safe to call on every deploy.

        Required RBAC for pipeline-runner:
          - configmaps: get, patch  (kubeflow namespace)
          - deployments: patch      (kubeflow namespace)
        """
        try:
            cm = v1.read_namespaced_config_map("inferenceservice-config", "kubeflow")
            ingress = json.loads(cm.data.get("ingress", "{}"))

            desired = {
                "ingressDomain":          lb_hostname,
                "domainTemplate":         "{{ .IngressDomain }}/{{ .Name }}",
                "ingressClassName":       "nginx",
                "disableIngressCreation": True,
                "disableIstioVirtualHost": True,
            }

            changed = any(ingress.get(k) != v for k, v in desired.items())
            if not changed:
                print("inferenceservice-config already correct — no patch needed")
                return

            ingress.update(desired)
            cm.data["ingress"] = json.dumps(ingress)
            v1.patch_namespaced_config_map("inferenceservice-config", "kubeflow", cm)
            print(f"Patched inferenceservice-config: ingressDomain={lb_hostname}, "
                  f"disableIngressCreation=true, disableIstioVirtualHost=true")

            # Rolling restart so the controller picks up the new config
            restart_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            apps_v1.patch_namespaced_deployment(
                "kserve-controller-manager", "kubeflow",
                {"spec": {"template": {"metadata": {"annotations": {
                    "kubectl.kubernetes.io/restartedAt": restart_ts
                }}}}},
            )
            print("Triggered kserve-controller-manager restart")

            # Wait for controller to be available again before proceeding
            deadline = time.time() + 120
            while time.time() < deadline:
                dep = apps_v1.read_namespaced_deployment(
                    "kserve-controller-manager", "kubeflow"
                )
                if (dep.status.available_replicas or 0) >= 1:
                    print("kserve-controller-manager ready")
                    return
                time.sleep(5)
            print("Warning: kserve-controller-manager did not become ready within 120s — continuing anyway")

        except ApiException as e:
            # RBAC or transient error — log and continue; status URL patch will still be attempted
            print(f"Warning: could not patch inferenceservice-config ({e.status}): {e.reason}")

    prev_production_version = None
    try:
        prev = mlflow_client.get_model_version_by_alias(model_name, "production")
        prev_production_version = prev.version
        print(f"Current @production: v{prev_production_version}")
    except MlflowException:
        print("No existing @production version found")

    config.load_incluster_config()
    v1        = client.CoreV1Api()
    apps_v1   = client.AppsV1Api()
    custom_api = client.CustomObjectsApi()

    GROUP  = "serving.kserve.io"
    VER    = "v1beta1"
    PLURAL = "inferenceservices"

    # Step 0: ensure KServe is configured for path-based NGINX routing.
    # On a fresh cluster KServe installs with ingressDomain=example.com — this
    # fixes it automatically so the status URL shows the real endpoint.
    lb_hostname = _get_nginx_lb_hostname(v1)
    if lb_hostname:
        _ensure_kserve_ingress_config(v1, apps_v1, lb_hostname)
    else:
        print("Warning: could not resolve NGINX LB hostname — skipping config patch")

    bootstrapping = prev_production_version is None
    if bootstrapping:
        mlflow_client.set_registered_model_alias(model_name, "production", model_version)
        print(f"No existing @production — set to v{model_version} (bootstrap mode)")

    isvc = {
        "apiVersion": f"{GROUP}/{VER}",
        "kind": "InferenceService",
        "metadata": {
            "name": deployment_name,
            "namespace": namespace,
            "annotations": {"serving.kserve.io/deploymentMode": "RawDeployment"},
        },
        "spec": {
            "predictor": {
                "serviceAccountName": "default-editor",
                "minReplicas": 1,
                "maxReplicas": 10,
                "scaleTarget": 60,
                "scaleMetric": "cpu",
                "containers": [{
                    "name": "kserve-container",
                    "image": "srikanthkarthi/mlops-serving:latest",
                    "ports": [{"containerPort": 8080, "protocol": "TCP"}],
                    "env": [
                        {"name": "MLFLOW_TRACKING_URI", "value": mlflow_tracking_uri},
                        {"name": "MODEL_NAME",          "value": model_name},
                        {"name": "MODEL_ALIAS",         "value": "production"},
                    ],
                    "resources": {
                        "requests": {"cpu": "500m", "memory": "512Mi"},
                        "limits":   {"cpu": "1",    "memory": "1Gi"},
                    },
                    "readinessProbe": {
                        "httpGet": {"path": "/health", "port": 8080},
                        "initialDelaySeconds": 30,
                        "periodSeconds": 5,
                    },
                }],
            }
        },
    }

    try:
        existing = custom_api.get_namespaced_custom_object(
            GROUP, VER, namespace, PLURAL, deployment_name
        )
        existing["spec"] = isvc["spec"]
        custom_api.replace_namespaced_custom_object(
            GROUP, VER, namespace, PLURAL, deployment_name, existing
        )
        print(f"Updated InferenceService '{deployment_name}' in '{namespace}'")
    except ApiException as e:
        if e.status == 404:
            custom_api.create_namespaced_custom_object(GROUP, VER, namespace, PLURAL, isvc)
            print(f"Created InferenceService '{deployment_name}' in '{namespace}'")
        else:
            raise

    # Wait for Ready condition
    deadline = time.time() + canary_wait_seconds
    ready = False
    while time.time() < deadline:
        obj = custom_api.get_namespaced_custom_object(
            GROUP, VER, namespace, PLURAL, deployment_name
        )
        for cond in obj.get("status", {}).get("conditions", []):
            if cond.get("type") == "Ready" and cond.get("status") == "True":
                ready = True
                break
        if ready:
            break
        time.sleep(10)

    if not ready:
        if bootstrapping:
            mlflow_client.delete_registered_model_alias(model_name, "production")
            print("Not ready (bootstrap) — removed @production alias")
        elif prev_production_version:
            mlflow_client.set_registered_model_alias(
                model_name, "production", prev_production_version
            )
            print(f"Not ready — reverted @production to v{prev_production_version}")
        raise RuntimeError(
            f"InferenceService '{deployment_name}' not ready within {canary_wait_seconds}s"
        )

    mlflow_client.set_registered_model_alias(model_name, "production", model_version)
    if prev_production_version and prev_production_version != model_version:
        mlflow_client.set_model_version_tag(
            model_name, prev_production_version, "archived", "true"
        )

    # Patch the InferenceService status URL to the real LB path so the
    # KServe Models Web App shows a clickable, working link.
    # lb_hostname was resolved at the top of this function.
    try:
        if lb_hostname:
            external_url = f"http://{lb_hostname}/{deployment_name}"
            custom_api.patch_namespaced_custom_object_status(
                GROUP, VER, namespace, PLURAL, deployment_name,
                {"status": {"url": external_url}},
            )
            print(f"Patched InferenceService status URL → {external_url}")
    except Exception as e:
        print(f"Warning: could not patch status URL: {e}")

    print(f"Deployed '{model_name}' v{model_version} → KServe InferenceService in '{namespace}'")
    return deployment_name
