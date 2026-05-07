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
    canary_wait_seconds: int = 120,
) -> str:
    """
    Canary deployment:
      1. Create fraud-detector-canary (1 replica, loads new @production model).
      2. Wait up to canary_wait_seconds for it to become Ready.
      3. If healthy → rolling restart stable deployment + delete canary.
      4. If timeout → delete canary and raise (stable keeps serving old model).
    """
    import time
    from datetime import datetime, timezone

    from kubernetes import client, config
    from kubernetes.client.rest import ApiException

    config.load_incluster_config()
    apps_v1 = client.AppsV1Api()
    core_v1  = client.CoreV1Api()

    canary_name = f"{deployment_name}-canary"

    # ── Read stable deployment to clone its pod spec ──────────────────────────
    stable = apps_v1.read_namespaced_deployment(deployment_name, namespace)
    pod_spec = stable.spec.template.spec

    # ── Delete any leftover canary from a previous failed run ────────────────
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

    # ── Create canary deployment ─────────────────────────────────────────────
    canary = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name=canary_name,
            namespace=namespace,
            labels={
                "app":           deployment_name,   # joins the Service
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

    # ── Wait for canary pod to become Ready ───────────────────────────────────
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
        raise RuntimeError(
            f"Canary did not become ready within {canary_wait_seconds}s — "
            "stable deployment unchanged, new model NOT promoted."
        )

    print(f"Canary is healthy. Promoting {model_name} v{model_version} to stable ...")

    # ── Promote: rolling restart stable deployment ────────────────────────────
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

    # ── Clean up canary ───────────────────────────────────────────────────────
    apps_v1.delete_namespaced_deployment(
        canary_name, namespace,
        body=client.V1DeleteOptions(propagation_policy="Foreground"),
    )

    print(f"Promoted '{model_name}' v{model_version} — canary deleted, stable rolling.")
    return deployment_name
