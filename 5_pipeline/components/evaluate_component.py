from kfp import dsl


@dsl.component(base_image="srikanthkarthi/mlops-pipeline-base:latest")
def evaluate(
    mlflow_tracking_uri: str,
    run_id: str,
    min_auprc: float,
) -> bool:
    """Quality gate: returns True only if AUPRC meets the minimum threshold."""
    import mlflow

    import os
    os.environ["GIT_PYTHON_REFRESH"]   = "quiet"
    os.environ["MLFLOW_LOGGING_LEVEL"] = "WARNING"
    mlflow.set_tracking_uri(mlflow_tracking_uri)
    client = mlflow.MlflowClient()

    run = client.get_run(run_id)
    auprc = run.data.metrics.get("auprc", 0.0)

    approved = auprc >= min_auprc
    print(f"AUPRC={auprc:.4f}  threshold={min_auprc}  approved={approved}")
    return approved
