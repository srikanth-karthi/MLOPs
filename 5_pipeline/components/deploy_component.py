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
    import os
    import time

    import mlflow
    from mlflow import MlflowClient
    from mlflow.exceptions import MlflowException
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException

    os.environ["GIT_PYTHON_REFRESH"]   = "quiet"
    os.environ["MLFLOW_LOGGING_LEVEL"] = "WARNING"
    mlflow.set_tracking_uri(mlflow_tracking_uri)
    mlflow_client = MlflowClient()

    prev_production_version = None
    try:
        prev = mlflow_client.get_model_version_by_alias(model_name, "production")
        prev_production_version = prev.version
        print(f"Current @production: v{prev_production_version}")
    except MlflowException:
        print("No existing @production version found")

    config.load_incluster_config()
    custom_api = client.CustomObjectsApi()

    GROUP  = "serving.kserve.io"
    VER    = "v1beta1"
    PLURAL = "inferenceservices"

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

    # Patch the InferenceService status URL to the real LB endpoint so
    # KServe Models web app shows the accessible URL instead of the default
    # "fraud-detector-user.example.com" domain template value.
    v1 = client.CoreV1Api()
    try:
        svc = v1.read_namespaced_service("fraud-detector-nginx", "user")
        lb_ingress = svc.status.load_balancer.ingress
        if lb_ingress:
            lb_hostname = lb_ingress[0].hostname or lb_ingress[0].ip
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
