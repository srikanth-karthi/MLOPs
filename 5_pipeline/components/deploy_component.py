from kfp import dsl


@dsl.component(
    base_image="srikanthkarthi/mlops-pipeline-base:latest",
    packages_to_install=["kubernetes==32.0.1"],
)
def deploy(
    model_name: str,
    model_version: str,
    deployment_name: str = "fraud-detector",
    namespace: str = "mlflow",
) -> str:
    """Trigger a rolling restart of the serving Deployment to pick up new @production model."""
    from datetime import datetime, timezone
    from kubernetes import client, config

    config.load_incluster_config()
    apps_v1 = client.AppsV1Api()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": now
                    }
                }
            }
        }
    }

    apps_v1.patch_namespaced_deployment(
        name=deployment_name,
        namespace=namespace,
        body=patch,
    )

    print(f"Triggered rollout restart of {deployment_name} in {namespace}")
    print(f"New model: {model_name} v{model_version} @production")
    return deployment_name
