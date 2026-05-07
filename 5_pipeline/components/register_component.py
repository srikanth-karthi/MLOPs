from kfp import dsl


@dsl.component(base_image="srikanthkarthi/mlops-pipeline-base:latest")
def register(
    mlflow_tracking_uri: str,
    run_id: str,
    model_name: str,
) -> str:
    """Register the run's model as @production in the MLflow Model Registry."""
    import mlflow
    from mlflow import MlflowClient
    from mlflow.exceptions import MlflowException

    import os
    os.environ["GIT_PYTHON_REFRESH"]   = "quiet"
    os.environ["MLFLOW_LOGGING_LEVEL"] = "WARNING"
    mlflow.set_tracking_uri(mlflow_tracking_uri)
    client = MlflowClient()

    # Track current production version for archiving
    prev_version = None
    try:
        prev = client.get_model_version_by_alias(model_name, "production")
        prev_version = prev.version
    except MlflowException:
        pass

    mv = mlflow.register_model(f"runs:/{run_id}/model", model_name)
    version = mv.version

    client.set_registered_model_alias(model_name, "staging", version)
    client.set_registered_model_alias(model_name, "production", version)

    if prev_version and prev_version != version:
        client.set_model_version_tag(model_name, prev_version, "archived", "true")

    print(f"Registered '{model_name}' v{version} @production")
    return version
