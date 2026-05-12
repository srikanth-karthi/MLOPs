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
    namespace: str = "mlflow",
    canary_wait_seconds: int = 120,
) -> str:
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

    prev_production_version = None
    try:
        prev = mlflow_client.get_model_version_by_alias(model_name, "production")
        prev_production_version = prev.version
        print(f"Current @production: v{prev_production_version}")
    except MlflowException:
        print("No existing @production version found")

    config.load_incluster_config()
    apps_v1 = client.AppsV1Api()
    core_v1  = client.CoreV1Api()

    canary_name = f"{deployment_name}-canary"

    stable = apps_v1.read_namespaced_deployment(deployment_name, namespace)
    pod_spec = stable.spec.template.spec

    try:
        apps_v1.delete_namespaced_deployment(
            canary_name, namespace,
            body=client.V1DeleteOptions(propagation_policy="Foreground"),
        )
        time.sleep(5)
        print(f"Deleted stale canary deployment '{canary_name}'")
    except ApiException as e:
        if e.status != 404:
            raise

    # Bootstrap: if no @production exists yet, set it now so the canary pod can load the model.
    # On failure we'll delete the alias instead of reverting.
    bootstrapping = prev_production_version is None
    if bootstrapping:
        mlflow_client.set_registered_model_alias(model_name, "production", model_version)
        print(f"No existing @production — set to v{model_version} before canary start (bootstrap mode)")

    canary = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name=canary_name,
            namespace=namespace,
            labels={
                "app":           deployment_name,
                "version":       "canary",
                "model-version": model_version,
            },
            annotations={"model-name": model_name, "model-version": model_version},
        ),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(
                match_labels={"app": deployment_name, "version": "canary"}
            ),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"app": deployment_name, "version": "canary"}
                ),
                spec=pod_spec,
            ),
        ),
    )
    apps_v1.create_namespaced_deployment(namespace, canary)
    print(f"Canary deployment '{canary_name}' created — waiting for readiness ...")

    deadline = time.time() + canary_wait_seconds
    ready = False
    while time.time() < deadline:
        dep = apps_v1.read_namespaced_deployment(canary_name, namespace)
        if dep.status.ready_replicas and dep.status.ready_replicas >= 1:
            ready = True
            break
        time.sleep(10)

    if not ready:
        apps_v1.delete_namespaced_deployment(
            canary_name, namespace,
            body=client.V1DeleteOptions(propagation_policy="Foreground"),
        )
        if bootstrapping:
            mlflow_client.delete_registered_model_alias(model_name, "production")
            print("Canary failed (bootstrap) — removed @production alias, no stable to revert to")
        elif prev_production_version:
            mlflow_client.set_registered_model_alias(model_name, "production", prev_production_version)
            print(f"Canary failed — reverted @production to v{prev_production_version}")
        raise RuntimeError(
            f"Canary did not become ready within {canary_wait_seconds}s — "
            f"@production reverted to v{prev_production_version}, stable deployment unchanged."
        )

    mlflow_client.set_registered_model_alias(model_name, "production", model_version)
    if prev_production_version and prev_production_version != model_version:
        mlflow_client.set_model_version_tag(model_name, prev_production_version, "archived", "true")
    print(f"Canary is healthy. Promoting {model_name} v{model_version} to @production ...")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    apps_v1.patch_namespaced_deployment(
        deployment_name,
        namespace,
        body={
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": now,
                            "model-version": model_version,
                        }
                    }
                }
            }
        },
    )

    apps_v1.delete_namespaced_deployment(
        canary_name, namespace,
        body=client.V1DeleteOptions(propagation_policy="Foreground"),
    )

    print(f"Promoted '{model_name}' v{model_version} — canary deleted, stable rolling.")
    return deployment_name
